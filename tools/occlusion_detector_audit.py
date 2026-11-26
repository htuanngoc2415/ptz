"""Read-only pre/postprocess audit on source-verified frames, never camera control."""
import argparse,json
from pathlib import Path
import cv2
import torch
from ultralytics import YOLO
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import offline_guard,digest
from ai.overlap_policy import overlap_geometry


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--limit',type=int,default=12)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    with offline_guard():
        cv2.setNumThreads(1);torch.set_num_threads(1)
        meta=json.loads((a.cache/'manifest.json').read_text());source=Path(meta['video'])
        if digest(a.cache/'observations.jsonl')!=meta['observations_sha256']:raise ValueError('Invalid cache')
        _,_,frames=load_capture(source);mapping={r['frame_id']:r for r in frames}
        rows=[json.loads(l) for l in (a.cache/'observations.jsonl').read_text().splitlines()]
        selected=[r for r in rows if any(overlap_geometry(x['bbox'],y['bbox'])['overlap']>=.15 for i,x in enumerate(r['candidates']) for y in r['candidates'][i+1:])][:a.limit]
        weight=Path(meta['config']['PERSON_MODEL_PATH'])
        if not weight.is_file():raise FileNotFoundError('Local person model missing')
        model=YOLO(str(weight));audits=[]
        for r in selected:
            im=cv2.imread(str(verified_image(source,mapping[r['frame_id']])))
            if model.predictor is None:model.predict(im,conf=.1,device='cpu',verbose=False)
            predictor=model.predictor;original=predictor.postprocess;captured={}
            def inspect(preds,*args,**kwargs):
                tensor=preds[0] if isinstance(preds,(list,tuple)) else preds
                captured['raw_shape']=list(tensor.shape) if hasattr(tensor,'shape') else None
                if isinstance(tensor,torch.Tensor) and tensor.ndim==3 and tensor.shape[-1]==6:
                    data=tensor[0].detach().cpu().numpy()
                    person=data[data[:,5]==0]
                    captured.update(prepost_person_rows=len(person),above_low_threshold=int((person[:,4]>=.1).sum()),
                        above_acquire_threshold=int((person[:,4]>=meta['config']['PERSON_CONF_THRESHOLD']).sum()),
                        decoded_person_scores=person[:,4].tolist())
                else:captured['prepost_person_rows']=None
                return original(preds,*args,**kwargs)
            predictor.postprocess=inspect
            try:result=model.predict(im,conf=.1,iou=meta['config']['PERSON_IOU_THRESHOLD'],device='cpu',verbose=False)[0]
            finally:predictor.postprocess=original
            boxes=[dict(bbox=b.xyxy[0].cpu().tolist(),confidence=float(b.conf[0])) for b in result.boxes if int(b.cls[0])==0]
            end2end=bool(getattr(predictor.model,'end2end',False))
            audit=dict(frame_id=r['frame_id'],end2end=end2end,
                nms_suppression_count=0 if end2end else None,
                nms_claim='No traditional NMS in end2end output' if end2end else 'Cannot attribute losses without full NMS tracing',
                historical_detections=len(r['detections']),historical_tracklets=len(r['candidates']),postprocess_person_count=len(boxes),boxes=boxes,**captured)
            audits.append(audit)
            for i,b in enumerate(boxes):
                x1,y1,x2,y2=map(int,b['bbox']);cv2.rectangle(im,(x1,y1),(x2,y2),(0,255,255),2)
                cv2.putText(im,f"{i} conf={b['confidence']:.2f}",(x1,max(15,y1)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,255,255),1)
            cv2.imwrite(str(a.out/f"frame_{r['frame_id']}.jpg"),im)
        summary=dict(model_sha256=digest(weight),source_sha256=meta['source_capture_sha256'],samples=audits,
            warning='Decoded pre-postprocess tensors are not every backbone proposal. Cannot infer whether a missing person existed before end2end model top-k.')
        (a.out/'audit.json').write_text(json.dumps(summary,indent=2));print('Audited',len(audits),'overlap frames')

if __name__=='__main__':main()

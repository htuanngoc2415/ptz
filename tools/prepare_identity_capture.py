"""Build a Phase 1 evidence cache from finalized recorded frames + manual labels.

Uses recorded detection/tracklet IDs and real OSNet inference, not new YOLO/MOT
predictions. Candidate IDs and receive timestamps stay unchanged for annotations.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.identity_dataset import load_capture, verified_image
from tools.identity_replay import digest, offline_guard, write_json


def prepare(capture, output, device='cpu'):
    import cv2
    import numpy as np
    import torch
    import config
    from person_search.reid import OSNetReID
    from ai.hybrid_reid import HybridReIDTargetSelector
    from ai.reid.gallery import TargetEmbeddingGallery
    capture,output = Path(capture),Path(output)
    meta,summary,rows = load_capture(capture)
    root = Path(__file__).resolve().parents[1]
    for relative, checksum in meta['baseline_source_sha256'].items():
        if digest(root/relative) != checksum:
            raise ValueError('Baseline source differs from capture: '+relative)
    for path,checksum in meta.get('model_sha256',{}).items():
        if digest(path) != checksum:
            raise ValueError('Model weights differ from capture')
    cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    for k,v in meta['config'].items():
        setattr(cfg,k,v)
    output.mkdir(parents=True,exist_ok=False)
    cv2.setNumThreads(1);torch.set_num_threads(1)
    class RecordingReID:
        def __init__(self):
            self.model = OSNetReID(cfg.REID_MODEL_PATH,device=device,batch_size=cfg.REID_BATCH_SIZE)
            self.calls = []
        def extract_embeddings(self, crops):
            result = self.model.extract_embeddings(crops)
            self.calls.append(result.detach().cpu().numpy().copy())
            return result
    reid = RecordingReID()
    scorer = HybridReIDTargetSelector(cfg,reid)
    refs = torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device=device)
    scorer.embedding = refs[0]
    scorer.gallery = TargetEmbeddingGallery(refs)
    scorer.error = None
    with (output/'observations.jsonl').open('x') as f:
        for row in rows:
            frame = cv2.imread(str(verified_image(capture,row)))
            if frame is None or list(frame.shape) != row['frame_shape']:
                raise ValueError('Image decode/shape mismatch')
            candidates = [dict(c) for c in row['candidates']]
            reid.calls.clear()
            start = time.perf_counter()
            if candidates:
                scorer.frame_id = row['frame_id']
                scorer._scores(frame,candidates)
            elapsed = (time.perf_counter()-start)*1000
            norm_index = 0
            for index,c in enumerate(candidates):
                vector = reid.calls[0][index]
                if c.get('normalized_similarity') is not None:
                    if c['normalized_similarity'] > c['raw_similarity']:
                        vector = reid.calls[1][norm_index]
                    norm_index += 1
                c.update(embedding=vector.tolist(),quality_approved=False,occluded=False)
            result = {k:row[k] for k in ('frame_id','timestamp','frame_shape','detections','detection_ms','observation_age_s')}
            result.update(candidates=candidates,reid_ms=elapsed)
            f.write(json.dumps(result,allow_nan=False)+'\n')
    checksum = digest(output/'observations.jsonl')
    write_json(output/'manifest.json',dict(schema=1,evidence='recorded_live_tracklets_offline_osnet',
        video=str(capture.resolve()),reference=str((capture/'target_reference.png').resolve()),
        observations_sha256=checksum, reference_embeddings=meta['reference_embeddings'],
        baseline_source_sha256=meta['baseline_source_sha256'],config=meta['config'],
        model_sha256=meta.get('model_sha256',{}),source_capture_sha256=summary['frames_sha256'],
        timestamp_source=meta['timestamp_source'],device=device,samples=len(rows),
        timing_scope='live detector/observation age; offline recomputed all-candidate OSNet latency'))
    if (capture/'annotations.json').exists():
        labels = json.loads((capture/'annotations.json').read_text())
        if labels.get('source') != 'manual' or labels.get('capture_sha256') != summary['frames_sha256']:
            raise ValueError('Annotation provenance mismatch')
        labels['observations_sha256'] = checksum
        write_json(output/'annotations.json',labels)
    return dict(output=str(output),frames=len(rows),annotations=(output/'annotations.json').exists())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cpu')
    args = p.parse_args()
    with offline_guard():
        print(json.dumps(prepare(args.capture,args.output,args.device),indent=2))


if __name__ == '__main__':
    main()

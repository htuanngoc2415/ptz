from pathlib import Path
import json,collections,datetime
import numpy as np, cv2
from tools.identity_dataset import load_capture,verified_image
out=Path('docs/identity_capture_audit_20261119'); reports=[]
logs=[json.loads(l) for l in Path('logs/ptz_20261119_081710.jsonl').read_text().splitlines()]; metrics=[r for r in logs if r['event']=='FRAME_METRICS']
def stats(a):
 return dict(n=len(a),median=float(np.median(a)),p95=float(np.percentile(a,95)),max=float(max(a))) if len(a) else None
for directory in sorted(Path('logs/identity_capture').glob('capture_*')):
 meta,summary,rows=load_capture(directory)
 for r in rows:
  im=cv2.imread(str(verified_image(directory,r)));assert list(im.shape)==r['frame_shape']
 labels=[json.loads(l) for l in (directory/'auto_labels.jsonl').read_text().splitlines()];assert labels==[r['auto_annotation'] for r in rows]
 start,end=rows[0]['timestamp'],rows[-1]['timestamp']
 report=dict(capture=str(directory),summary=summary,created=datetime.datetime.fromtimestamp(meta['created_wall_time_ns']/1e9,datetime.timezone(datetime.timedelta(hours=7))).isoformat(),start=start,end=end,duration_s=end-start,shape=rows[0]['frame_shape'],interval_s=stats(np.diff([r['timestamp'] for r in rows])),age_ms=stats([r['observation_age_s']*1000 for r in rows]),detector_ms=stats([r['detection_ms'] for r in rows]),states=dict(collections.Counter(r['state'] for r in rows)),labels=dict(collections.Counter(r['auto_annotation']['selection_status'] for r in rows)),track_ids=list(set(c['track_id'] for r in rows for c in r['candidates'])))
 reports.append(report)
 tiles=[]
 for i in np.linspace(0,len(rows)-1,8,dtype=int):
  r=rows[i];im=cv2.imread(str(directory/r['image']))
  for c in r['candidates']:
   x1,y1,x2,y2=map(int,c['bbox']);color=(0,255,0) if c['candidate_id']==r['auto_annotation']['selected_candidate'] else (0,180,255)
   cv2.rectangle(im,(x1,y1),(x2,y2),color,2)
  cv2.putText(im,f"frame {r['frame_id']} +{r['timestamp']-start:.2f}s",(8,22),0,.6,(255,255,255),2)
  tiles.append(cv2.resize(im,(352,240)))
 cv2.imwrite(str(out/(directory.name+'_contact.jpg')),np.vstack([np.hstack(tiles[:4]),np.hstack(tiles[4:])]))
inside=lambda r:any(x['start']<=r['monotonic_ts']<=x['end'] for x in reports)
comparison={name:{key:stats([r[key] for r in metrics if inside(r)==flag]) for key in ['frame_age_ms','yolo_ms','reid_ms','face_ms']} for name,flag in [('record_intervals',True),('outside_record_intervals',False)]}
(out/'audit.json').write_text(json.dumps(dict(captures=reports,metrics=comparison,events=dict(collections.Counter(r['event'] for r in logs))),indent=2))
print((out/'audit.json').read_text())

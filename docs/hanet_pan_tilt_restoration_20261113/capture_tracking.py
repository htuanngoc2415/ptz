"""Offline before/after evidence; synthetic scores are not accuracy ground truth."""
from pathlib import Path
import sys, json, hashlib
from unittest.mock import patch
from contextlib import ExitStack
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root),str(root/'test')]
import config
import test_root_cause_revision as replay

out=Path(sys.argv[1])
with ExitStack() as guard:
    guard.enter_context(patch('socket.create_connection',side_effect=AssertionError('NO LIVE SOCKET')))
    guard.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('NO LIVE HTTP')))
    case=replay.DetectionReplayTests();case.setUp()
    frames=[]
    def feed(label, persons):
        chosen,tracks=case.feed(persons)
        frames.append(dict(label=label,time=round(case.t,3),stage=case.selector.stage,
            locked=case.selector.locked,hold=case.selector.crossing_hold,
            reacquiring=case.selector.reacquiring,track_id=case.selector.track_id,
            selected_box=list(chosen['bbox']) if chosen else None,
            track_count=len(tracks),reid_score_calls=case.selector._scores.call_count,
            reason=case.selector.transition_reason))
    for _ in range(5):feed('acquire',[replay.detection()])
    for _ in range(6):feed('distinct_overlap',[replay.detection(),replay.detection(101,identity='other')])
    for _ in range(8):feed('separated',[replay.detection()])
    for _ in range(100):feed('full_loss',[])
    for _ in range(8):feed('reacquire',[replay.detection()])
    transitions=[]
    for i,row in enumerate(frames):
        state=(row['stage'],row['locked'],row['hold'],row['reacquiring'])
        if i==0 or state!=last:transitions.append(row)
        last=state
    prefixes=('REID','TRACK','BYTE','YOLO','PERSON','TARGET','OVERLAP','FACE','SCRFD','ARCFACE','PIPELINE','MODEL','AI_','DETECTION','INFERENCE','AUTO_PAN','AUTO_ZOOM','MAX_CONTROL')
    effective={k:getattr(config,k) for k in dir(config) if k.isupper() and k.startswith(prefixes)
               and not any(secret in k for secret in ('PASSWORD','TOKEN','SECRET','CREDENTIAL'))}
    def digest(p):
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        return h.hexdigest()
    result=dict(scope='127 synthetic detection observations through real ByteTrack/selector; mocked appearance scores, no inference or camera',
                frames=frames,transitions=transitions,effective_config=effective,
                model_sha256={str(p.relative_to(root)):digest(p) for p in sorted((root/'models').rglob('*')) if p.is_file()},
                source_config_sha256={n:digest(root/n) for n in ('config.py','camera.json')})
    out.write_text(json.dumps(result,indent=2,default=lambda v:v.item() if hasattr(v,'item') else str(v))+'\n')
    print('Saved',out,'frames',len(frames),'transitions',len(transitions))

"""Identical synthetic inputs across source trees; recorded geometry only, no camera."""
import argparse,sys,json,socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);a=p.parse_args();sys.path.insert(0,a.source)
def blocked(*a,**k):raise RuntimeError('No network')
socket.socket.connect=blocked
import config,numpy as np
from ai.hybrid_reid import HybridReIDTargetSelector
c=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});c.REID_SEARCH_INTERVAL=0;c.REID_VERIFY_INTERVAL=100
frame=np.zeros((400,400,3),np.uint8)
def target(i=1):return dict(track_id=i,bbox=(100,100,200,300),confidence=.9)
def competitor(severe=False):return dict(track_id=2,bbox=(102,100,202,300) if severe else (196,100,216,300),confidence=.9)
def new():
 s=HybridReIDTargetSelector(c,Mock());s.embedding=np.ones(512);s.error=None;s._scores=Mock(side_effect=lambda f,ps:[.95 if x['track_id']!=2 else .1 for x in ps]);s._start_verification=Mock();return s
r={}
for case in ('no_overlap','minor_acquisition','minor_continuity','severe','switch_id'):
 s=new();outputs=[]
 for t in (0.,.1,.2):
  people=[target()]+([competitor()] if case=='minor_acquisition' else [])
  q=s.select_person(frame,people,t);s.finish_frame(q,None)
 for t in (.3,.5,.7,.9):
  people=[target(3 if case=='switch_id' else 1)]+([] if case=='no_overlap' else [competitor(case=='severe')])
  q=s.select_person(frame,people,t);s.finish_frame(q,None);outputs.append(q is not None)
 r[case]=dict(selected=outputs,locked=s.locked,hold=s.crossing_hold,track_id=s.track_id)
Path(a.output).write_text(json.dumps(r,indent=2));print(json.dumps(r))

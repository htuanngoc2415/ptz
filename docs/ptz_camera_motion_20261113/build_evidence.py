"""Rebuild compact evidence from the saved audit/replay and reviewed MOV keyframes."""
import datetime,hashlib,json,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
def read(name):return json.loads((HERE/name).read_text())
def write(name,data):(HERE/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

before,after=read('tracking_before.json'),read('tracking_after.json')
checkpoint=read('checkpoint.json')
changed=[n for n,h in checkpoint['source_sha256'].items() if sha(ROOT/n)!=h]
write('scope_verification.json',dict(existing_files_checked=len(checkpoint['source_sha256']),changed_existing_files=changed,
    identical_replay_frames=before['frames']==after['frames'],frame_count=len(before['frames']),
    identical_transitions=before['transitions']==after['transitions'],transition_count=len(before['transitions']),
    identical_effective_config=before['effective_config']==after['effective_config'],effective_config_count=len(before['effective_config']),
    identical_model_hashes=before['model_sha256']==after['model_sha256'],model_count=len(before['model_sha256']),
    camera_json_unchanged=before['source_config_sha256']['camera.json']==after['source_config_sha256']['camera.json']))

timeline=read('timeline.json');offset=timeline['clock_mapping']['wall_epoch_minus_monotonic_median']
losses=[]
for n,row in enumerate(timeline['losses']):
    late=n>=5
    losses.append(dict(row,confirmed_mechanism='PERSON_OVERLAP gate -> UNCERTAIN -> HOLD timeout' if late else 'HOLD timeout',
        root_causes=['TRUE_PERSON_OVERLAP','REID_IDENTITY_AMBIGUITY'] if late else ['UNKNOWN'],
        root_certainty='PROBABLE' if late else 'UNKNOWN',
        camera_motion_association_failure='UNVERIFIED',
        limitation='Sparse monitor annotations, no aligned raw-frame identity GT' if late else 'Detailed latency trace/raw frames unavailable'))
write('root_cause_review.json',losses)

summary={}
for name,data in read('synthetic_ab.json').items():
    if not isinstance(data,dict):continue
    estimates=[e for e in data['estimates'] if e and e['valid']]
    expected_dx=0 if name=='stationary' else 25
    summary[name]=dict(frames=16,valid_pairs=len(estimates),
        translation_dx_absolute_error_mean_px=statistics.mean(abs(e['dx']-expected_dx) for e in estimates),
        inlier_ratio_mean=statistics.mean(e['inlier_ratio'] for e in estimates),
        reprojection_error_mean_px=statistics.mean(e['reprojection_error'] for e in estimates),
        gmc_mean_ms=data['gmc_mean_ms'],gmc_p95_ms=data['gmc_p95_ms'],
        branches={k:{n:v for n,v in b.items() if n!='outputs'} for k,b in data['branches'].items()})
write('synthetic_summary.json',summary)

# Manual visual references from full-resolution keyframes, not copied tracker IDs.
# Boxes enclose approximate visible bodies in the 1920x1080 monitor recording.
specs=[
 (118,'11:24:17',[1295,345,1580,985],[1205,165,1460,565]),
 (122,'11:24:21',[1150,350,1420,1020],[1145,220,1355,580]),
 (125,'11:24:24',[1160,390,1400,1050],[1145,255,1360,570]),
 (128,'11:24:27',[1190,395,1460,1065],[1150,270,1370,610]),
 (132,'11:24:31',[1030,395,1285,1060],[1145,275,1360,650]),
 (138,'11:24:37',None,[1140,280,1365,650]),
]
rows=[]
for sec,osd,black,red in specs:
    rows.append(dict(video_seconds=sec,osd_time_read= '2026-11-13 '+osd,image=f'monitor_{sec}s.jpg',
        image_sha256=sha(HERE/f'monitor_{sec}s.jpg'),
        persons=[dict(visual_label='dark_shirt_light_trousers',visible=black is not None,bbox=black,
                      visibility_note='Lower body clipped by displayed image border' if black else 'Not visible in displayed camera view'),
                 dict(visual_label='red_jacket_seated',visible=True,bbox=red,visibility_note='Lower body partly occluded by furniture/foreground')],
        intended_target_identity=None,system_track_id_used=False))
write('monitor_annotations.json',dict(source='/mnt/files/Project/ptz/13_11.mov',resolution=[1920,1080],
    annotation_type='Manual visual keyframes reviewed by coding agent; approximate visible-body boxes',
    independent_review_required=True,exhaustive_person_annotations=False,
    coordinate_space='monitor recording pixels; NOT raw camera pixels',
    time_mapping='Video position and read OSD second only; clock offset and GUI delay uncalibrated',
    valid_for_raw_replay_ground_truth=False,selected_target_identity_unknown=True,frames=rows))

# Command exposure timeline; no inferred physical-motion trace.
start,end=timeline['window'];fig,ax=plt.subplots(figsize=(13,3.8))
for a,b,code in timeline['command_intervals']:
    y=.9 if 'Zoom' in code else 1.5
    ax.broken_barh([(a-start,b-a)],(y,.22),facecolors='#7c3aed' if 'Zoom' in code else '#2563eb')
for kind,y,color in [('HOLD',.55,'#ea580c'),('FULL_LOSS',.15,'#dc2626')]:
    ts=[e['monotonic']-start for e in timeline['events'] if e['kind']==kind]
    ax.scatter(ts,[y]*len(ts),s=36,c=color,marker='|',linewidths=2)
ax.set_yticks([.15,.55,1.01,1.61],['FULL_LOSS','HOLD','Zoom commands','Pan/tilt commands'])
ax.set_xlabel('Seconds since application start (11:20:25 local; mapped monotonic)')
ax.set_title('Command dispatch exposure vs state transitions — actual camera motion unavailable')
ax.set_xlim(0,end-start);ax.grid(axis='x',alpha=.2)
fig.tight_layout();fig.savefig(HERE/'command_timeline.png',dpi=160);plt.close(fig)
print('Built scope verification, root-cause review, synthetic summary, visual references and timeline plot')

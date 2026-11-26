from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
out=Path(__file__).resolve().parent
log=[json.loads(l) for l in Path('logs/identity_shadow/shadow_d3b836c5ae5c492ba7ddb95282250359.jsonl').read_text().splitlines()]
rows=[r for r in log if r['event']=='SHADOW_COMPARISON'];t0=log[0]['monotonic_ts'];times=np.array([r['timestamp']-t0 for r in rows]);dense=[json.loads(l) for l in (out/'dense_diagnostic.jsonl').read_text().splitlines()]
fig,axes=plt.subplots(3,1,figsize=(13,8),sharex=True,gridspec_kw={'height_ratios':[1.1,1.4,1]})
ax=axes[0]
for y,label,data,time,field in [(2,'Live baseline proposal',rows,times,lambda r:r['baseline_selected']),(1,'Live shadow',rows,times,lambda r:r['decision']['selected_candidate']),(0,'Dense offline diagnostic',dense,[r['timestamp']-t0 for r in dense],lambda r:r['selected_candidate'])]:
 colors=['#008b74' if field(r) is not None else '#cecece' for r in data]
 ax.scatter(time,[y]*len(data),c=colors,marker='|',s=170 if y else 80)
ax.set_yticks([0,1,2],['Dense offline diagnostic','Live shadow','Live baseline proposal']);ax.set_ylim(-.5,2.5);ax.set_title('Recorded run 2026-11-19 17:32:04 — green: selected; gray: HOLD',loc='left')
ax=axes[1];ax.scatter(times,[r['decision']['top1'] for r in rows],s=12,color='#386cb0',label='Best reference similarity')
thresholds=[]
for r in rows:
 a=next((a for a in r['decision']['candidate_audit'] if 'reference_threshold' in a),{})
 thresholds.append(a.get('reference_threshold',float('nan')))
ax.scatter(times,thresholds,s=9,marker='_',color='#d35400',label='Active reference threshold');ax.set_ylabel('Cosine similarity');ax.legend(loc='lower left',ncol=2);ax.grid(alpha=.2)
ax=axes[2];ax.plot(times[1:],np.diff(times),color='#6c3483',lw=.8,marker='.',ms=3,label='Gap between completed samples');ax.axhline(1,color='#d35400',ls='--',label='Confirmation max gap: 1 s');ax.set_ylabel('Sample gap (s)');ax.set_xlabel('Seconds after shadow start');ax.legend(loc='upper left',ncol=2);ax.grid(alpha=.2)
for ax in axes:
 for r in log:
  if r['event']=='SHADOW_RESET':ax.axvline(r['monotonic_ts']-t0,color='#111111',ls=':',alpha=.25,lw=.8)
ax.set_xlim(0,log[-1]['monotonic_ts']-t0)
fig.text(.01,.01,'Dense diagnostic uses recorded evidence and fixed reset boundaries; not live performance or ground truth. Dotted lines: reset log times.',fontsize=9)
fig.tight_layout(rect=[0,.03,1,1]);fig.savefig(out/'timeline.png',dpi=160);fig.savefig(out/'timeline.pdf');plt.close(fig)

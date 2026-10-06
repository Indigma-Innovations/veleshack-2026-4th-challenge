"""Regenerate the committed evidence figures; optional Matplotlib + NumPy."""
from pathlib import Path
import csv
import json
OUT = Path(__file__).resolve().parents[1]
def load(path):
    return json.loads((OUT/path).read_text(encoding='utf-8'))
# Plot exact archived pairs; no new samples or fitted model.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
rows=list(csv.DictReader((OUT/'evidence/paired-scores.csv').open(encoding='utf-8')))
x=np.array([float(r['lookahead_score']) for r in rows]);y=np.array([float(r['cycle_aware_score']) for r in rows]);g=100*(y/x-1)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(12.8,5.3),layout='constrained')
colors=np.where(y>x,'#0a817b','#dd804a')
axes[0].scatter(x,y,c=colors,s=14,alpha=.6,edgecolors='none')
low=min(x.min(),y.min())-.3;high=max(x.max(),y.max())+.3
axes[0].plot([low,high],[low,high],'--',color='#596475',lw=1)
axes[0].set(xlim=(low,high),ylim=(low,high),xlabel='Lookahead Planner score',ylabel='Cycle-Aware Planner score',title='Same-seed cumulative scores')
axes[0].text(.03,.97,'Cycle-Aware wins: 620 / 1000\nLookahead wins: 380 / 1000\nBoth beat all bots: 999 / 1000',transform=axes[0].transAxes,va='top',bbox=dict(facecolor='white',alpha=.92,edgecolor='none'))
axes[1].hist(g,bins=np.arange(-34,44,2),color='#0a817b',edgecolor='white',lw=.5)
axes[1].axvline(0,color='#596475',ls='--',lw=1)
axes[1].axvline(g.mean(),color='#ba5432',lw=1.8,label=f'Mean gain: {g.mean():.3f}%')
axes[1].set(xlabel='Cycle-Aware gain over Lookahead (%)',ylabel='Paired seeds',title='Distribution of paired relative gains')
axes[1].legend(frameon=False)
axes[1].text(.98,.95,'Bootstrap 95% interval\n+0.980% to +1.811%',ha='right',va='top',transform=axes[1].transAxes)
for ax in axes:ax.grid(alpha=.14);ax.set_axisbelow(True)
fig.suptitle('CardanoEdge - 1,000 paired graded simulations',fontsize=17,fontweight='bold')
fig.savefig(OUT/'evidence/figures/paired-scores.png',dpi=180) if (OUT/'evidence/figures').exists() else None
if not (OUT/'evidence/figures').exists():
    (OUT/'evidence/figures').mkdir();fig.savefig(OUT/'evidence/figures/paired-scores.png',dpi=180)
plt.close(fig)
best=load('evidence/best-run/results.json')
fig,ax=plt.subplots(figsize=(10.4,5),layout='constrained')
teams=[r['team'] for r in best['leaderboard']]
for team in teams:
    values=[(s['settled_rounds'],next(r['score'] for r in s['leaderboard'] if r['team']==team)) for s in best['snapshots'] if any(r['team']==team for r in s['leaderboard'])]
    ax.plot([p[0] for p in values],[p[1] for p in values],label=team,lw=2.7 if team=='CardanoEdge' else 1.6)
ax.set(xlabel='Settled round',ylabel='Cumulative utility',title='Best recorded normal graded run - seed 16001')
ax.legend(frameon=False);ax.grid(alpha=.2)
fig.savefig(OUT/'evidence/figures/cumulative-score.png',dpi=180);plt.close(fig)

"""Bot-only counterfactual on the same public seeds; no HTTP fault simulation."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena.config import ScenarioConfig
from arena.state import Arena
from baselines.bot import STRATEGIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'evidence/paired-swarm.csv')
    parser.add_argument('--output', type=Path, default=ROOT/'runs/swarm-counterfactual')
    args = parser.parse_args()
    paired = list(csv.DictReader(args.input.open(encoding='utf-8')))
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for row in paired:
        seed = int(row['seed'])
        config = ScenarioConfig.from_yaml(ROOT/'arena/scenarios/graded.yaml')
        config.seed = seed
        arena = Arena(config)
        bots = [(arena.register('bot-'+name),fn) for name,fn in STRATEGIES.items()]
        total_lsw = 0.0
        for _ in range(config.total_rounds):
            state = arena.open_round()
            for node, fn in bots:
                if node.admissible(config.battery_cutoff,config.kappa_bar):
                    arena.submit(node,state.index,fn(node.budget,dict(state.prices),dict(state.capacities),node.profile(),[]))
            arena.settle();total_lsw += state.lsw
        record = {**row,'bots_only_score':sum(r['score'] for r in arena.leaderboard()),'bots_only_lsw':total_lsw}
        for label in ['lookahead','cycle_aware']:
            record[label+'_neighbors_delta_vs_absent'] = float(row[label+'_neighbors_score']) - record['bots_only_score']
            record[label+'_lsw_delta_vs_absent'] = float(row[label+'_lsw_total']) - record['bots_only_lsw']
        records.append(record)
    with (args.output/'scores.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    fields=[k for k in records[0] if k!='seed']
    summary={'engine':'in-process-no-faults','seeds':len(records),'seed_range':[int(paired[0]['seed']),int(paired[-1]['seed'])],
        'input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'mean':{k:statistics.mean(float(r[k]) for r in records) for k in fields},
        'note':'Three original bots retain identical seeded devices/capacities/wallets. Their price and battery paths change when a fourth bidder participates. LSW sums logs over participating nodes, so differing participant populations limit welfare interpretation.'}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()

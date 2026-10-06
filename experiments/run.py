"""Deterministic strategy experiments using the organisers' unchanged arena engine.

No HTTP, sleeps or fault injection: strategy measurements are separate from
the local process launcher and supplied conformance/resilience tests.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import math
import os
import random
from pathlib import Path
import statistics
import sys
import time
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent-template"), str(ROOT)]

from arena.config import ScenarioConfig
from arena.state import Arena
from baselines.bot import STRATEGIES as BOTS
import strategy
from experiments.reference_policies import POLICIES, PUBLIC_MARKET_POLICIES


def simulate(name: str, seed: int, team: str = "CardanoEdge", scenario: str = "graded",
             rounds: int | None = None, history_lag: int = 1,
             market_dropout: float = 0.0, bot_history: str = "empty") -> dict:
    # Explicit configuration: ambient ARENA_* experiment variables do not leak in.
    config = ScenarioConfig.from_yaml(ROOT / "arena/scenarios" / f"{scenario}.yaml")
    config.seed = seed
    if rounds is not None:
        config.total_rounds = rounds
    config.validate()
    arena = Arena(config)
    bots = [(arena.register(f"bot-{bot}"), fn) for bot, fn in BOTS.items()]
    node = arena.register(team)
    fn = POLICIES[name]
    agents = [*bots, (node, fn)]
    histories = {n.node_id: [] for n, _ in agents}
    trace = []
    decision_times = []
    observation_rng = random.Random(seed ^ 8213)
    for _ in range(config.total_rounds):
        state = arena.open_round()
        # Capture the documented public endpoint BEFORE any node submits.
        public_nodes = deepcopy(arena.swarm())
        # Strategies see only their own public profile, own collected results,
        # budget, current capacities, and previous clearing prices.
        for current, decide in agents:
            if not current.admissible(config.battery_cutoff, config.kappa_bar):
                continue
            history = histories[current.node_id]
            # The shipped bot runner bids before reading a result, then updates
            # last_bid_round. With immediate round transitions it never reads
            # the preceding settled result. Reproduce that observed HTTP path;
            # do not accidentally strengthen the opponents in simulation.
            if current.is_baseline and bot_history == "empty":
                history = []
            if current is node and history_lag:
                history = [entry for entry in history if entry['round'] < state.index - history_lag]
            profile = current.profile()
            if current is node and name in PUBLIC_MARKET_POLICIES:
                profile["_market"] = {"round": state.index, "total_rounds": config.total_rounds,
                    "nodes": [n for n in public_nodes if n["node_id"] != current.node_id]}
                if observation_rng.random() < market_dropout:
                    profile["_market"]["nodes"] = []
            decision_started = time.perf_counter()
            bid = decide(current.budget, dict(state.prices), dict(state.capacities),
                         profile, deepcopy(history))
            if current is node:
                decision_times.append(1000 * (time.perf_counter() - decision_started))
            if set(bid) != {"compute", "energy", "security"}:
                raise AssertionError(f"{name}: incorrect resource keys: {bid}")
            if any(not math.isfinite(v) or v < 0 for v in bid.values()):
                raise AssertionError(f"{name}: invalid bid: {bid}")
            if sum(bid.values()) > current.budget + 1e-6:
                raise AssertionError(f"{name}: over budget: {bid}")
            ack = arena.submit(current, state.index, bid)
            if ack["warnings"]:
                raise AssertionError(f"{name}: protocol warnings: {ack['warnings']}")
        arena.settle()
        for current, _ in agents:
            result = arena.result_for(current, state.index)
            if result["participated"]:
                histories[current.node_id].append(result)
        trace.append({"round": state.index, "battery": node.battery,
                      "result": arena.result_for(node, state.index),
                      "leaderboard": arena.leaderboard(), "lsw": state.lsw})
    board = arena.leaderboard()
    row = next(r for r in board if r["team"] == team)
    times = sorted(decision_times)
    return {
        "strategy": name, "seed": seed, "team": team, "engine": "in-process-no-faults",
        "history_lag": history_lag, "market_dropout": market_dropout, "bot_history": bot_history,
        "config": asdict(config), "profile": node.profile(),
        "decision_ms": {"mean": statistics.mean(times), "p99": times[min(len(times)-1, int(len(times)*0.99))], "max": max(times)},
        "metrics": {**row, "lsw_total": sum(t["lsw"] for t in trace),
                    "swarm_score": sum(r["score"] for r in board),
                    "neighbors_score": sum(r["score"] for r in board if r["team"] != team),
                    "mean_battery": statistics.mean(t["battery"] for t in trace),
                    "beat_all_bots": all(row["score"] > r["score"] for r in board if r["team"] != team)},
        "leaderboard": board, "trace": trace,
    }


def summarize(records: list[dict]) -> dict:
    names = {r["strategy"] for r in records}
    reference = "template" if "template" in names else "guarded-kelly" if "guarded-kelly" in names else records[0]["strategy"]
    baseline = {r["seed"]: r["metrics"] for r in records if r["strategy"] == reference}
    summary = {}
    for name in dict.fromkeys(r["strategy"] for r in records):
        rows = [r for r in records if r["strategy"] == name]
        metrics = [r["metrics"] for r in rows]
        item = {"reference_strategy": reference, "seeds": len(rows), "mean_score": statistics.mean(m["score"] for m in metrics),
                "mean_idle": statistics.mean(m["rounds_idle"] for m in metrics),
                "mean_floor_violations": statistics.mean(m["floor_violations"] for m in metrics),
                "mean_lsw_total": statistics.mean(m["lsw_total"] for m in metrics),
                "beat_all_bots": sum(m["beat_all_bots"] for m in metrics),
                "missed_rounds": sum(m["rounds_missed"] for m in metrics)}
        paired = [(r["metrics"], baseline[r["seed"]]) for r in rows if r["seed"] in baseline]
        if paired:
            gains = [100 * (m["score"] / b["score"] - 1) for m, b in paired]
            margin = 1.96 * statistics.stdev(gains) / math.sqrt(len(gains)) if len(gains) > 1 else 0
            item.update({"mean_paired_gain_percent": statistics.mean(gains),
                         "paired_gain_approx_95ci": [statistics.mean(gains) - margin, statistics.mean(gains) + margin],
                         "improved_seeds": sum(m["score"] > b["score"] for m, b in paired),
                         "mean_neighbors_delta": statistics.mean(m["neighbors_score"] - b["neighbors_score"] for m, b in paired),
                         "mean_lsw_delta": statistics.mean(m["lsw_total"] - b["lsw_total"] for m, b in paired)})
        summary[name] = item
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategies", nargs="+", default=["lookahead", "cycle-aware"])
    parser.add_argument("--seeds", nargs="+", type=int)
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--seed-count", type=int, default=30)
    parser.add_argument("--scenario", choices=("practice", "graded"), default="graded")
    parser.add_argument("--team", default=os.environ.get("TEAM_NAME", "CardanoEdge"))
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--history-lag", type=int, default=1,
                        help="team feedback delay; 1 matches collection after bidding")
    parser.add_argument("--market-dropout", type=float, default=0.0,
                        help="fraction of decisions with unavailable public snapshots")
    parser.add_argument("--bot-history", choices=("empty", "full"), default="empty",
                        help="full is a robustness test against history-aware opponents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--traces", action="store_true", help="save complete per-round traces too")
    parser.add_argument("--workers", type=int, default=1, help="independent simulation processes; timing under load")
    args = parser.parse_args()
    available = POLICIES
    if any(name not in available for name in args.strategies):
        parser.error(f"strategies must be among {list(available)}")
    if args.workers < 1 or args.seed_count < 1 or args.history_lag < 0 or not 0 <= args.market_dropout <= 1 or len(set(args.strategies)) != len(args.strategies):
        parser.error("use positive seed count, nonnegative history lag, and distinct strategies")
    seeds = args.seeds or list(range(args.seed_start, args.seed_start + args.seed_count))
    if len(set(seeds)) != len(seeds):
        parser.error("seed list must not contain duplicates")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"strategies": args.strategies, "seeds": seeds, "scenario": args.scenario,
                "team": args.team, "rounds": args.rounds, "history_lag": args.history_lag,
                "market_dropout": args.market_dropout, "bot_history": args.bot_history,
                "workers": args.workers,
                "strategy_sha256": hashlib.sha256(Path(strategy.__file__).read_bytes()).hexdigest(),
                "planner_sha256": hashlib.sha256((ROOT / "agent-template/planner.py").read_bytes()).hexdigest(),
                "arena_sha256": hashlib.sha256((ROOT / 'arena/state.py').read_bytes()).hexdigest(),
                "python": sys.version, "engine": "in-process-no-faults"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    records = []
    with ProcessPoolExecutor(max_workers=args.workers) if args.workers > 1 else nullcontext(None) as pool:
        for name in args.strategies:
            arguments = [(name, seed, args.team, args.scenario, args.rounds, args.history_lag,
                          args.market_dropout, args.bot_history) for seed in seeds]
            results = pool.map(simulate_arguments, arguments) if pool else map(simulate_arguments, arguments)
            for record in results:
                if not args.traces:
                    record.pop("trace")
                (args.output / f"{name}-seed-{record['seed']}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
                records.append(record)
            summary = summarize(records)[name]
            gain = summary.get("mean_paired_gain_percent", float("nan"))
            print(f"{name:16} score={summary['mean_score']:.4f} gain={gain:+.2f}% "
                  f"idle={summary['mean_idle']:.2f} floors={summary['mean_floor_violations']:.2f} "
                  f"beat_all={summary['beat_all_bots']}/{len(seeds)}", flush=True)
    summary = summarize(records)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    fields = ["strategy", "seed", "score", "rounds_participated", "rounds_missed", "rounds_idle",
              "floor_violations", "battery", "mean_battery", "beat_all_bots", "neighbors_score", "swarm_score", "lsw_total"]
    with (args.output / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({"strategy": record["strategy"], "seed": record["seed"], **record["metrics"]})
    print(f"Saved experiment to {args.output}", flush=True)
    return 0


def simulate_arguments(arguments):
    return simulate(*arguments)


if __name__ == "__main__":
    raise SystemExit(main())

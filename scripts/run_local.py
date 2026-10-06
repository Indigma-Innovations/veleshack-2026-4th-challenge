"""Run the supplied arena, three bots and team agent as local Python processes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env() -> None:
    """Read the simple KEY=value settings in .env; existing env vars win."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def get_json(url: str) -> dict:
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def handle_stop(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("practice", "graded"),
                        default=os.environ.get("ARENA_SCENARIO") or os.environ.get("SCENARIO", "practice"))
    parser.add_argument("--team", default=os.environ.get("TEAM_NAME", "CardanoEdge"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARENA_PORT", "8080")))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--round-seconds", type=float)
    parser.add_argument("--keep-open", action="store_true",
                        help="keep the final leaderboard available until Ctrl+C")
    args = parser.parse_args()
    if not args.team.strip() or len(args.team) > 64 or args.team in (
        "change-me", "bot-naive-max", "bot-even-split", "bot-proportional"
    ):
        parser.error("choose a nonempty team name (up to 64 characters), distinct from the bots")

    os.environ["ARENA_SCENARIO"] = args.scenario
    for key, value in (("ARENA_SEED", args.seed), ("ARENA_TOTAL_ROUNDS", args.rounds),
                       ("ARENA_ROUND_SECONDS", args.round_seconds)):
        if value is not None:
            os.environ[key] = str(value)
    # Start a fresh run with a registration window even if the caller ran other experiments.
    os.environ["ARENA_AUTOSTART"] = "true"
    os.environ["ARENA_START_DELAY"] = "10"
    from arena.config import ScenarioConfig
    config = ScenarioConfig.load(args.scenario)

    selected = "Cycle-Aware Planner"

    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"port {args.port} is occupied; stop that run or pass --port 8090")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "runs" / f"{stamp}-{args.scenario}-seed{config.seed}"
    output.mkdir(parents=True)
    base = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "ARENA_URL": base, "PYTHONUNBUFFERED": "1",
           "PYTHONUTF8": "1", "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO")}
    processes: list[tuple[str, subprocess.Popen]] = []
    handles = []

    def start(name: str, command: list[str], settings: dict | None = None) -> None:
        handle = (output / f"{name}.log").open("w", encoding="utf-8")
        handles.append(handle)
        processes.append((name, subprocess.Popen(
            [sys.executable, *command], cwd=ROOT, env={**env, **(settings or {})},
            stdout=handle, stderr=subprocess.STDOUT,
        )))

    def check_arena() -> None:
        if processes[0][1].poll() is not None:
            raise RuntimeError(f"arena exited; see {output / 'arena.log'}")

    metadata = {
        "team": args.team, "strategy": selected, "scenario": args.scenario, "seed": config.seed,
        "rounds": config.total_rounds, "round_seconds": config.round_seconds,
        "python": sys.version, "started_at_utc": stamp,
        "strategy_sha256": hashlib.sha256((ROOT / "agent-template/strategy.py").read_bytes()).hexdigest(),
        "planner_sha256": hashlib.sha256((ROOT / "agent-template/planner.py").read_bytes()).hexdigest(),
    }
    print(f"Team: {args.team}\nLeaderboard: {base}/\nAPI: {base}/docs\nLogs and results: {output}", flush=True)
    signal.signal(signal.SIGTERM, handle_stop)
    try:
        start("arena", ["-m", "arena", "--host", "127.0.0.1", "--port", str(args.port),
                        "--log-level", "warning"])
        deadline = time.monotonic() + 30
        while True:
            check_arena()
            try:
                if get_json(f"{base}/healthz").get("ok"):
                    break
            except (URLError, TimeoutError, OSError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("arena did not become healthy within 30 seconds")
            time.sleep(0.2)

        for bot in ("naive-max", "even-split", "proportional"):
            start(f"bot-{bot}", ["baselines/bot.py"],
                  {"BOT": bot, "TEAM_NAME": f"bot-{bot}", "LOG_LEVEL": "WARNING"})
        start("agent", ["agent-template/agent.py"], {"TEAM_NAME": args.team})

        snapshots = []
        last_settled = -1
        deadline = time.monotonic() + config.start_delay_seconds + config.total_rounds * config.round_seconds + 30
        while True:
            check_arena()
            if time.monotonic() > deadline:
                raise RuntimeError("run exceeded its expected duration")
            status = get_json(f"{base}/v1/status")
            if status["round"] and status["nodes_registered"] != 4:
                raise RuntimeError("expected the team and all three bots to register before round 1")
            for name, process in processes[1:]:
                code = process.poll()
                if code is not None and (code != 0 or status["round"] < config.total_rounds):
                    raise RuntimeError(f"{name} exited early with code {code}; see its log")
            settled = len(status["lsw_series"])
            if settled != last_settled:
                board = get_json(f"{base}/v1/leaderboard")["leaderboard"]
                snapshots.append({"settled_rounds": settled, "status": status, "leaderboard": board})
                last_settled = settled
                if settled:
                    row = next(row for row in board if row["team"] == args.team)
                    print(f"Round {settled}/{config.total_rounds}: score={row['score']:.4f} "
                          f"bids={row['rounds_participated']} idle={row['rounds_idle']} "
                          f"missed={row['rounds_missed']}", flush=True)
            if status["finished"]:
                result = {"metadata": metadata, "status": status, "leaderboard": board,
                          "swarm": get_json(f"{base}/v1/swarm"), "snapshots": snapshots}
                (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                print("\nFinal standings:", flush=True)
                for row in board:
                    print(f"  #{row['rank']} {row['team']:<24} {row['score']:.4f} "
                          f"bids={row['rounds_participated']} idle={row['rounds_idle']} "
                          f"missed={row['rounds_missed']} floors={row['floor_violations']}", flush=True)
                print(f"Saved {output / 'results.json'}", flush=True)
                break
            time.sleep(0.2)
        if args.keep_open:
            # The game has ended. Keep the arena/UI, but stop completed clients
            # that may otherwise poll forever after an inadmissible last round.
            for _, process in processes[1:]:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            print("Leaderboard remains available. Ctrl+C stops the local processes.", flush=True)
            while True:
                check_arena()
                time.sleep(1)
        return 0
    except KeyboardInterrupt:
        print("\nStopping the local run.", flush=True)
        return 0
    except Exception as exc:
        print(f"Local run failed: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        for _, process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())

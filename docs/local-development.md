# Native local setup and verification

Use Python 3.12 or newer from the release checkout. These commands use PowerShell and require no Docker:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r arena\requirements.txt -r agent-template\requirements.txt
.\.venv\Scripts\python.exe tests\test_cycle_aware.py
.\.venv\Scripts\python.exe -m tests.conformance --team CardanoEdge --seed 22301 --workdir runs\conformance --json runs\conformance.json
.\.venv\Scripts\python.exe scripts\run_local.py --scenario graded --team CardanoEdge --seed 16001 --port 8080 --keep-open
```

The launcher starts the unchanged arena, all three original bots and the final agent as separate processes. It exports settled-round snapshots, source hashes, status, swarm state and final leaderboard to `runs/<timestamp>/results.json`.
`--keep-open` keeps the final leaderboard until Ctrl+C, then stops its own processes. Choose another port if 8080 is occupied.

To reproduce the final simulator comparison on the published seeds:

```powershell
.\.venv\Scripts\python.exe experiments\run.py --strategies lookahead cycle-aware --scenario graded --team CardanoEdge --seed-start 20001 --seed-count 1000 --workers 8 --output runs\reproduction
```

This does not inject HTTP faults or impose wall-clock bidding deadlines. Use one worker for less-contended decision timing. Reusing published seeds after a policy change is reproduction, not a fresh evaluation.

The production agent can be run from `agent-template` in a separate virtual  environment installed with **only** its requirements; the arena's libraries are needed by the local driver and experiments, not by the submitted image.

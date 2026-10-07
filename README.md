# CardanoEdge - Cycle-Aware Planner

**TEAM_NAME:** `CardanoEdge`.
**Members:** Vasilis Perifanis, Nikolaos Pavlidis.

The agent runs **Cycle-Aware  Planner** with a guarded market-estimation fallback. The organizer arena, baselines, HTTP client, conformance suite and Dockerfile are retained unchanged. The measured **Lookahead Planner** comparator lives in `experiments/`, outside the agent image. Development variants and fitted models are excluded.

## Evidence and verification

Read [evaluation](docs/evaluation.md) for seed cohorts, selected experiments, score/win tables, HTTP faults, lease recovery and latency. Read [methodology](docs/methodology.md) for exact inputs, equations, candidate search, continuation and fallback. The [comparison figure](evidence/figures/paired-scores.png) and [paired CSV](evidence/paired-scores.csv) retain all 1,000 final comparisons. 

See [local development](docs/local-development.md) for native setup, a fresh graded run and verification commands.

See the online arena with all of our evaluated agents [here](https://cardanoedge-arena.fastapicloud.dev/).

You can find the prepared presentation [here](docs/CardanoEdge_Pitch_Final.pdf).


## Run with Docker

### Ubuntu

To run the agent:

```bash
git clone https://github.com/Indigma-Innovations/veleshack-2026-4th-challenge
cd veleshack-2026-4th-challenge
cp .env.example .env
grep TEAM_NAME .env 
make up
make agent
```

### Windows (PowerShell)

Start Docker Desktop and use Linux containers. After cloning the repository,
open PowerShell in the project folder and run:

```powershell
$env:TEAM_NAME = "CardanoEdge"
$env:SCENARIO = "graded"
$env:ARENA_SEED = "16001"
$env:ARENA_PORT = "8080"

docker compose --profile agent down --remove-orphans
docker compose --profile agent build
docker compose --profile agent up -d
docker compose logs -f agent
```

These environment settings apply to the current PowerShell session and take
precedence over `.env`. No `make`, `cp` or `grep` is required. The first command
removes this project's previous containers for a fresh run; building all images
before starting keeps image-build time outside the arena's registration window.
The `agent` profile starts Cycle-Aware Planner alongside the arena and all three
baseline bots.


Open the leaderboard at **[http://localhost:8080/](http://localhost:8080/)**.
The graded run has 60 four-second rounds and takes about four minutes.
Seed `16001` is a local rehearsal seed; organizers supply the grading seed.

Press **Ctrl+C** to stop following logs. Containers continue running, keeping the
leaderboard available. To stop the containers:

```powershell
docker compose --profile agent down
```

To replay from round one, rerun the Windows command block above. Change
`ARENA_PORT` if port 8080 is occupied, then open the matching localhost port.

## Organizer handoff

- [x] Team name: `CardanoEdge`.
- [x] Dockerfile: [`agent-template/Dockerfile`](agent-template/Dockerfile).
  Build from the repository root:
  `docker build -t cardanoedge:release agent-template`.
- [x] README strategy write-up and both team members, below.
- [x] Live pitch: [Presentation](docs/CardanoEdge_Pitch_Final.pdf)
- [x] Optional best retained normal graded run (fresh release verification):
  [`results.json`](evidence/best-run/results.json) and
  [leaderboard screenshot](evidence/figures/leaderboard.png), a labeled replay
  of its final-settlement state.

## Strategy write-up

We treated energy as battery expenditure and not as a resource to maximize.  The Cycle-Aware Planner predicts the published baseline bots from public swarm  profiles, evaluates a six-round horizon over three independent hypothetical  futures, and optimizes compute/security bids around service floors. It adds  energy bids near our own and opponents' next-round battery cutoffs, then  re-evaluates the three strongest current bids with a causal two-round  continuation. This strategy exploits when nodes must rest without reading future arena  draws, submitted opponent bids, or the grading seed. Nothing is trained.

A separate observer caches current-round public state. Missing or stale state uses a guarded Kelly response estimated from our own awarded shares. Heartbeats, sanitized bids and the organizer's retry/backoff client keep the operational  path independent of planning.

Battery tapering raised mean score from 13.51 to 16.83 on 100 paired seeds.  Guarded Kelly scored 16.82, i.e. more immediate optimization did not materially beat simple battery control. Lookahead then beat Guarded Kelly on 98/100 separate paired seeds. On 1.000 paired seeds, Cycle-Aware averaged 19.063 versus Lookahead's 18.849, won 620/1.000 comparisons, and beat every bot in 999/1.000 runs. Its mean per-seed gain was 1.390% compared to LookAhead, with a bootstrap 95% interval of 0.980-1.811%.

The main cost is latency. Serial mean decisions were 99 versus 29 ms. Both passed normal HTTP core checks in 4/4 trials and recovered real lease expiry in 2/2. Higher stress exposed more missed bids for Cycle-Aware. Relative to Lookahead, neighbors' score and log social welfare declined slightly. We therefore report an own-score improvement, operational limits and swarm costs. A same-seed bot-only counterfactual also showed that adding our bidder reduced neighbors' combined score from 59.876 to 37.969.

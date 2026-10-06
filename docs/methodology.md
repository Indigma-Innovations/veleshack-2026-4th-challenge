# Cycle-Aware Planner: method and inputs

## Information boundary

`strategy.decide_bid(W, prices, capacities, profile, history)` returns a
nonnegative bid for compute, energy and security with total at most wallet W. The production policy is fixed. No coefficients, trees, neural weights or terminal-value model are fitted or loaded. Constants below were chosen during development and frozen before the final 1000-seed comparison.

| Input | Source | Used for |
|---|---|---|
| Wallet W, capacities C_k | Current `GET /v1/round` | Feasible bids and shares |
| Prices p_k | Current round's published previous clearing prices | Forecast proportional baseline bids |
| Weights, compute/security floors, mobility | Registration profile | Utility, penalties and battery drain |
| Own current battery B | Round response updates the client profile | Admission and charge trajectory |
| Other node names, active/ejected flags, weights, floors, mobility, batteries | Public `GET /v1/swarm` | Forecast the three published bots |
| Current and total rounds | Public status/registration | Shorten planning near the real end |
| Own settled bids, shares, capacities and spend | Own result endpoint | Fallback market estimate |

The observer uses its own HTTP client, a 0.5-second timeout and a 0.2-second poll interval. It brackets the swarm read with two status reads and caches only matching rounds. The decision accepts a matching snapshot at most one second old. Otherwise it falls back. In normal agent execution, result t-1 is collected after bidding at t; decision t therefore ordinarily has history through t-2. The in-process benchmark reproduces this delay. Submitted rival bids, private state, future capacities and the arena seed are never inputs.

## Auction and device model

For resource k and total rival demand S_k, the predicted share is

$$x_k(b)=C_k\frac{b_k}{b_k+S_k},\qquad b_k\ge0,\quad\sum_k b_k=W.$$

With normalized nonnegative weights w, immediate utility is

$$
u(b) =
\left(\sum_k w_k\sqrt{x_k(b)}\right)^2
\cdot
2^{-\mathbf{1}\!\left[x_C \lt q_{\min}\right]
   -\mathbf{1}\!\left[x_S \lt s_{\min}\right]}.
$$

Numerical denominators are protected at 10^-9 Forecast active battery dynamics use the published mobility m and physics:

$$B^+=\operatorname{round}_4\!\left(\max(0,B-0.30(1+m)x_E-0.004)\right).$$

At B <= 0.05 the rollout rests, earns zero and forecasts`round_4(min(1,B+0.22))`. Rival batteries follow the same simultaneous allocation and rounding; next prices are `round_6(max(0.01,total_bid/C))`. The live arena remains authoritative. Forecasts approximate its public rounded state; no arena module is imported into the agent.

`predict_bot` mirrors the supplied policies under their observed empty-history runner: weight-proportional, equal split, and the published proportional-price policy with its floor top-ups. Inactive/ejected or battery-inadmissible nodes bid zero. An unrecognized active team invalidates this market forecast and uses the fallback, so this result is specific to the supplied three-bot field.

## Search over bids

For each energy amount e, optimize compute c and security W-e-c. When affordable, reserve predicted floors with a 1.002 margin:

$$
\ell_k=S_k\frac{t_k}{C_k-t_k},\qquad
t_C=\min(0.98C_C,1.002q_{\min}),\quad
t_S=\min(0.98C_S,1.002s_{\min}).
$$

Search c in `[ell_C, W-e-ell_S]` with 14 golden-section iterations and explicit endpoints. If the two floors are unaffordable, evaluate both floor boundaries, zero/all compute, and eleven interior points of a twelve-part grid. The floor penalties make that region non-concave; the fallback grid does not claim an exact global optimum. Normalize the selected bundle to W.

The coarse energy set includes 0, W*10^-5, the battery taper amount and bids for target shares `{.015,.03,.055,.085,.125,.18,.25,.36,.5,.7}`, capped at 0.95*C_E and W. For positive rival energy demand D, share x requires`e=D*x/(C_E-x)`.

Cycle-Aware augments this set with reachable next batteries z in `{0.0498,0.0502}`, two battery quanta around the cutoff:

$$
x_E=\frac{B-0.004-z}{0.30(1+m)},\qquad e=\frac{D x_E}{C_E-x_E}
$$

for our own crossing, and

$$
e=\frac{0.30(1+m_j)C_E b_{j,E}}{B_j-0.004-z}-D
$$

for each forecast rival crossing. Keep only finite amounts in `[0,W]`, with positive relevant drain/demand; round amounts to eight decimals and remove duplicates. All coarse candidates remain; at most eight extra crossing amounts arise with three rivals.

## Bounded lookahead and causal refinement

Depth H is `min(6, remaining_rounds)`. For every current candidate, forecast its immediate outcome and H-1 subsequent rounds on three equally weighted hypothetical paths. Each future capacity is drawn independently from U(0.7,1.3); the shared wallet is drawn from U(0.75,1.25). An internal fixed pseudorandom generator (61983) provides identical paths for every candidate and repeatable decisions. This generator is independent of the arena seed.

The cheap continuation fixes energy to `W*w_E*max(0,(B-.05)/.95)` and optimizes the other two bids. Each path's value is

$$
Q(b)=\frac13\sum_{s=1}^3\left[\sum_{h=0}^{H-1}u_{s,h}
 +\mathbf1[\text{rounds remain after H}]
 \left(1.2B_{s,H}-0.15\sum_jB_{j,s,H}\right)\right].
$$

Lookahead Planner selects the best coarse-grid bid using this value. Cycle-Aware ranks its augmented candidates using the same cheap continuation, keeps three, and revalues them with an improved decision at the first future  step. This decision searches the augmented candidates at that future state and maximizes current utility plus mean next utility over three **independent** one-step hypothetical draws (generator 61984). Its next action is the cheap taper continuation; the same battery terminal heuristic applies only if more than two real rounds remain. It cannot peek at the next draw on the outer path. Subsequent outer steps use the cheap continuation. With only one real round left there is no refinement or terminal reward. Stable Python ordering  resolves equal values. These are scenario heuristics, not a globally optimal dynamic program or an expectation computed under the true future sequence.

## Fallback and operational behavior

When public prediction is missing, infer rival demand from own realized share:

$$
\widehat S_k(t)=W_t\operatorname{mean}_{r\in\text{last 4 usable results}}
 \frac{b_{r,k}(C_{r,k}/x_{r,k}-1)}{\operatorname{spend}_r}.
$$

Discard zero bid/share/spend samples and protect the estimate at 10^-4 before wallet scaling. Without samples use `3*W*w_k`. Published prices precede the result capacities, so multiplying those two fields would be misaligned. The guarded Kelly fallback reserves floors with margin 1.10, caps energy at the battery-taper allowance, and numerically maximizes one-round utility (nested golden-section searches: 16 split iterations, 12 energy iterations). Its unused generalized drain-price parameter is zero: it is not a learned value model or exact closed-form Kelly solution.

The original client retries transient 503/429 and transport failures with bounded exponential backoff, jitter and Retry-After handling. A separate heartbeat thread runs every `max(1,lease_seconds/3)` seconds. The agent handles  closed/duplicate/wrong rounds and battery rests, collects feedback after the bid, clears history on a restarted round counter, sanitizes outgoing bids and handles termination signals.

## Sustainable duty-cycle insight

For roughly stationary active drain `d=.30(1+m)*x_E+.004` and resting recharge rho=.22, inventory balance suggests active fraction

$$
f d\approx(1-f)\rho,\qquad f\approx\frac{\rho}{\rho+d}.
$$

This is a steady-state approximation; finite initial charge, caps, threshold rounding and variable rival demand matter. Cycle-Aware's final simulator participation was 49.742/60=82.903%, versus Lookahead's 83.208%. It improved  score with slightly more rests and fewer floor misses, not by maximizing participation. The crossing candidates optimize the timing of those rests.

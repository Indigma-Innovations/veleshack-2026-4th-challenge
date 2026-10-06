"""Measured, non-learned reference policies. Never copied into the agent image."""
import random
import strategy as base
from planner import market, immediate_bid, energy_candidates, continuation, transition
RESOURCES = base.RESOURCES

def lookahead(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    prediction = market(profile, budget, prices, capacities)
    if prediction is None:
        return base.guarded_kelly_bid(budget, prices, capacities, profile, history)
    nodes, _, others = prediction
    observation = profile["_market"]
    remaining = int(observation.get("total_rounds", 0)) - int(observation.get("round", 0)) + 1
    if remaining < 1:
        return immediate_bid(budget, prices, capacities, profile, history)
    depth = min(6, remaining)
    candidates = energy_candidates(budget, capacities, profile, others)
    # Independent possible futures, shared by every candidate. No arena seed
    # or future arena draw is used to generate these hypothetical scenarios.
    rng = random.Random(61983)
    scenarios = [[({k: rng.uniform(0.7, 1.3) for k in RESOURCES}, rng.uniform(0.75, 1.25))
                  for _ in range(depth - 1)] for _ in range(3)]

    def value(bid: dict, improved: bool = False) -> float:
        reward, initial_profile, initial_nodes, initial_prices = transition(profile, nodes, budget, prices, capacities, bid)
        total = 0.0
        for scenario in scenarios:
            current_profile, current_nodes, current_prices = initial_profile, initial_nodes, initial_prices
            score = reward
            for step, (caps, wallet) in enumerate(scenario):
                action = continuation(current_profile, current_nodes, wallet, current_prices, caps)
                utility, current_profile, current_nodes, current_prices = transition(
                    current_profile, current_nodes, wallet, current_prices, caps, action)
                score += utility
            future_rounds = remaining - depth
            if future_rounds:
                score += 1.2 * base._battery(current_profile) - 0.15 * sum(n["features"]["battery"] for n in current_nodes)
            total += score
        return total / len(scenarios)
    return max(candidates, key=value)


def _reserve(bid: dict, budget: float, lower: dict) -> dict:
    """Reserve both floors simultaneously, avoiding sequential top-up theft."""
    required = sum(lower.values())
    if required >= budget:
        return base._normalize(lower, budget)
    surplus = {k: max(0, bid[k] - lower[k]) for k in base.RESOURCES}
    total = sum(surplus.values())
    if not total:
        surplus = {k: 1.0 for k in base.RESOURCES}
        total = 3.0
    return {k: lower[k] + (budget - required) * surplus[k] / total for k in base.RESOURCES}

def market_floors(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    """Price/market allocation alone, intentionally without battery control."""
    weights = base._weights(profile)
    others = base.estimate_others(budget, profile, history)
    bid = base._normalize({k: weights[k] ** 2 * max(0, capacities.get(k, 1)) / max(0.05, others[k])
                      for k in base.RESOURCES}, budget)
    return _reserve(bid, budget, base._floor_needs(capacities, profile, others))

POLICIES = {
    "market-floors": market_floors,
    "template": base.template_bid,
    "battery-taper": base.battery_taper_bid,
    "guarded-kelly": base.guarded_kelly_bid,
    "lookahead": lookahead,
    "cycle-aware": base.decide_bid,
}
PUBLIC_MARKET_POLICIES = {"lookahead", "cycle-aware"}

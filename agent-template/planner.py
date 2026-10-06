"""Planning from public swarm observations and the published baseline policies.

The agent never imports the arena, reads its seed, or sees submitted bids.
Forecasts are hypotheses from the public API and can use the guarded fallback.
"""
from __future__ import annotations

from copy import deepcopy
import math
import random

import strategy as base

RESOURCES = base.RESOURCES


def predict_bot(node: dict, budget: float, prices: dict, capacities: dict) -> dict | None:
    """Mirror the supplied policies under their observed empty-history runner."""
    features = node.get("features", {})
    if (node.get("ejected") or not node.get("active", True) or
            float(features.get("battery", 0)) <= 0.05 or
            float(node.get("q_min", 0)) + float(node.get("s_min", 0)) >= 0.99):
        return {k: 0.0 for k in RESOURCES}
    name = node.get("team", "")
    weights = node.get("weights", {})
    if name == "bot-naive-max":
        return {k: budget * float(weights.get(k, 1 / 3)) for k in RESOURCES}
    if name == "bot-even-split":
        return {k: budget / 3 for k in RESOURCES}
    if name != "bot-proportional":
        return None
    scores = {k: float(weights.get(k, 1 / 3)) ** 2 * float(capacities.get(k, 1)) /
              max(0.05, float(prices.get(k, 1))) for k in RESOURCES}
    bid = base._normalize(scores, budget)
    for k, key in (("compute", "q_min"), ("security", "s_min")):
        cap = float(capacities.get(k, 1))
        target = min(0.95 * cap, 1.2 * float(node.get(key, 0)))
        if target <= 0 or target >= cap:
            continue
        needed = target / (cap - target)  # supplied empty-history estimate is 1
        deficit = needed - bid[k]
        if deficit <= 0:
            continue
        donors = [r for r in RESOURCES if r != k and bid[r] > 0]
        available = sum(bid[r] for r in donors)
        headroom = max(0, budget - sum(bid.values()))
        take = min(deficit, headroom + available)
        from_donors = take - min(take, headroom)
        if available > 1e-9:
            for r in donors:
                bid[r] -= from_donors * bid[r] / available
        bid[k] += take
    return base._normalize(bid, budget)


def market(profile: dict, budget: float, prices: dict, capacities: dict) -> tuple[list, list, dict] | None:
    observation = profile.get("_market", {})
    nodes = observation.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    bids = [predict_bot(node, budget, prices, capacities) for node in nodes]
    if any(bid is None for bid in bids):
        return None
    others = {k: sum(bid[k] for bid in bids) for k in RESOURCES}
    return nodes, bids, others


def split_energy(energy: float, budget: float, capacities: dict, profile: dict, others: dict) -> dict:
    """Optimize the free compute/security split at a fixed energy bid."""
    energy = min(max(0, energy), budget)
    available = budget - energy
    lower = base._floor_needs(capacities, profile, others, margin=1.002)
    low, high = lower["compute"], available - lower["security"]
    def value(compute: float) -> float:
        return base._utility({"compute": compute, "energy": energy,
                              "security": available - compute}, capacities, profile, others)
    if low <= high:
        compute = base._maximize(value, low, high, 14)
    else:
        # Enumerate floor boundaries too: penalties make an unconstrained
        # search non-concave when both floors cannot be afforded together.
        candidates = [0, available, min(available, low), max(0, high)]
        candidates += [available * i / 12 for i in range(1, 12)]
        compute = max(candidates, key=value)
    bid = {"compute": max(0, compute), "energy": energy,
           "security": max(0, available - compute)}
    return base._normalize(bid, budget)


def immediate_bid(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    prediction = market(profile, budget, prices, capacities)
    if prediction is None:
        return base.guarded_kelly_bid(budget, prices, capacities, profile, history)
    others = prediction[2]
    limit = base.battery_taper_bid(budget, prices, capacities, profile, history)["energy"]
    energy = base._maximize(lambda e: base._utility(split_energy(e, budget, capacities, profile, others),
                                                   capacities, profile, others), 0, limit, 12)
    return split_energy(energy, budget, capacities, profile, others)


def transition(profile: dict, nodes: list, budget: float, prices: dict,
               capacities: dict, bid: dict) -> tuple[float, dict, list, dict]:
    """Forecast one simultaneous auction from current public observations."""
    bids = [predict_bot(node, budget, prices, capacities) for node in nodes]
    own_active = base._battery(profile) > 0.05
    totals = {k: (bid[k] if own_active else 0) + sum(b[k] for b in bids) for k in RESOURCES}
    allocation = {k: capacities[k] * bid[k] / max(1e-9, totals[k]) if own_active else 0
                  for k in RESOURCES}
    others = {k: totals[k] - (bid[k] if own_active else 0) for k in RESOURCES}
    utility = base._utility(bid, capacities, profile, others) if own_active else 0
    next_profile = deepcopy(profile)
    next_profile.pop("_market", None)
    coefficient = 0.30 * (1 + float(profile.get("features", {}).get("mobility", 0)))
    battery = base._battery(profile)
    next_profile["features"]["battery"] = round(max(0, battery - coefficient * allocation["energy"] - 0.004)
                                                if own_active else min(1, battery + 0.22), 4)
    next_nodes = deepcopy(nodes)
    for node, prediction in zip(next_nodes, bids):
        if node.get("ejected"):
            continue
        battery = float(node["features"]["battery"])
        if sum(prediction.values()) > 0:
            share = capacities["energy"] * prediction["energy"] / max(1e-9, totals["energy"])
            coefficient = 0.30 * (1 + float(node["features"].get("mobility", 0)))
            battery = max(0, battery - coefficient * share - 0.004)
        else:
            battery = min(1, battery + 0.22)
        node["features"]["battery"] = round(battery, 4)
    next_prices = {k: round(max(0.01, totals[k] / max(1e-9, capacities[k])), 6) for k in RESOURCES}
    return utility, next_profile, next_nodes, next_prices


def energy_candidates(budget: float, capacities: dict, profile: dict, others: dict) -> list[dict]:
    cap = float(capacities["energy"])
    amounts = [0.0, budget * 1e-5]
    for share in (0.015, 0.03, 0.055, 0.085, 0.125, 0.18, 0.25, 0.36, 0.5, 0.7):
        target = min(cap * 0.95, share)
        amounts.append(min(budget, others["energy"] * target / max(1e-9, cap - target)))
    amounts.append(base._taper({k: budget * base._weights(profile)[k] for k in RESOURCES},
                                budget, profile)["energy"])
    return [split_energy(e, budget, capacities, profile, others) for e in sorted(set(round(e, 8) for e in amounts))]


def threshold_candidates(budget: float, capacities: dict, profile: dict,
                         nodes: list, prices: dict, others: dict) -> list[dict]:
    """Retain coarse-grid bids and add reachable admission crossings for all batteries."""
    candidates = energy_candidates(budget, capacities, profile, others)
    amounts = {bid["energy"] for bid in candidates}
    cap, demand = float(capacities["energy"]), others["energy"]
    coefficient = 0.30 * (1 + float(profile.get("features", {}).get("mobility", 0)))
    # Two battery quanta away from the cutoff avoids round-to-even ambiguity.
    for target_battery in (0.0498, 0.0502):
        share = (base._battery(profile) - 0.004 - target_battery) / coefficient
        if demand > 0 and 0 < share < cap:
            amounts.add(demand * share / (cap - share))
        for node in nodes:
            bid = predict_bot(node, budget, prices, capacities)
            if bid["energy"] <= 0:
                continue
            drain = float(node["features"]["battery"]) - 0.004 - target_battery
            if drain <= 0:
                continue
            coefficient_j = 0.30 * (1 + float(node["features"].get("mobility", 0)))
            amounts.add(coefficient_j * cap * bid["energy"] / drain - demand)
    known = {round(bid["energy"], 8) for bid in candidates}
    for amount in sorted({round(e, 8) for e in amounts if math.isfinite(e) and 0 <= e <= budget} - known):
        candidates.append(split_energy(amount, budget, capacities, profile, others))
    return candidates


def continuation(profile: dict, nodes: list, budget: float, prices: dict, capacities: dict) -> dict:
    if base._battery(profile) <= 0.05:
        return {k: 0.0 for k in RESOURCES}
    bids = [predict_bot(node, budget, prices, capacities) for node in nodes]
    others = {k: sum(bid[k] for bid in bids) for k in RESOURCES}
    # A cheap continuation policy keeps each rollout bounded. The real current
    # decision searches several energy allocations, rather than this taper.
    energy = budget * base._weights(profile)["energy"] * max(0, (base._battery(profile) - 0.05) / 0.95)
    return split_energy(energy, budget, capacities, profile, others)


def two_step_continuation(profile: dict, nodes: list, budget: float, prices: dict,
                          capacities: dict, remaining: int) -> dict:
    """A causal two-round policy: independent next-round samples, no path peek."""
    if base._battery(profile) <= 0.05:
        return {k: 0.0 for k in RESOURCES}
    others = {k: sum(predict_bot(n, budget, prices, capacities)[k] for n in nodes) for k in RESOURCES}
    candidates = threshold_candidates(budget, capacities, profile, nodes, prices, others)
    rng = random.Random(61984)
    futures = [({k: rng.uniform(0.7, 1.3) for k in RESOURCES}, rng.uniform(0.75, 1.25))
               for _ in range(3)] if remaining > 1 else []
    def value(bid):
        reward, after, competitors, next_prices = transition(profile, nodes, budget, prices, capacities, bid)
        total = 0.0
        for caps, wallet in futures:
            action = continuation(after, competitors, wallet, next_prices, caps)
            utility, last, others_last, _ = transition(after, competitors, wallet, next_prices, caps, action)
            terminal = (1.2 * base._battery(last) - 0.15 * sum(n["features"]["battery"] for n in others_last)) if remaining > 2 else 0
            total += utility + terminal
        return reward + total / len(futures) if futures else reward
    return max(candidates, key=value)


def plan(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    prediction = market(profile, budget, prices, capacities)
    if prediction is None:
        return base.guarded_kelly_bid(budget, prices, capacities, profile, history)
    nodes, _, others = prediction
    observation = profile["_market"]
    remaining = int(observation.get("total_rounds", 0)) - int(observation.get("round", 0)) + 1
    if remaining < 1:
        return immediate_bid(budget, prices, capacities, profile, history)
    depth = min(6, remaining)
    candidates = threshold_candidates(budget, capacities, profile, nodes, prices, others)
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
                if improved and step == 0:
                    action = two_step_continuation(current_profile, current_nodes, wallet, current_prices,
                                                   caps, remaining - 1)
                else:
                    action = continuation(current_profile, current_nodes, wallet, current_prices, caps)
                utility, current_profile, current_nodes, current_prices = transition(
                    current_profile, current_nodes, wallet, current_prices, caps, action)
                score += utility
            future_rounds = remaining - depth
            if future_rounds:
                # Preserve charge beyond the finite planning horizon.
                score += 1.2 * base._battery(current_profile) - 0.15 * sum(n["features"]["battery"] for n in current_nodes)
            total += score
        return total / len(scenarios)
    if depth > 1:
        # Bound branching: shortlist with the unchanged rollout, then revalue
        # every shortlisted action under the same improved continuation.
        shortlist = sorted(candidates, key=value, reverse=True)[:3]
        return max(shortlist, key=lambda bid: value(bid, improved=True))
    return max(candidates, key=value)

"""Cycle-Aware Planner and its guarded market-estimation fallback.

Uses only public inputs and the agent's own settled results.
Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations
import math
from typing import Callable

RESOURCES = ("compute", "energy", "security")

def _weights(profile: dict) -> dict:
    weights = {k: max(0.0, float(profile.get("weights", {}).get(k, 1 / 3))) for k in RESOURCES}
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


def _normalize(bid: dict, budget: float) -> dict:
    clean = {k: max(0.0, float(bid.get(k, 0))) for k in RESOURCES}
    total = sum(clean.values())
    if total <= 0:
        return {k: max(0, budget) / 3 for k in RESOURCES}
    return {k: v * max(0, budget) / total for k, v in clean.items()}


def _battery(profile: dict) -> float:
    return min(1.0, max(0.0, float(profile.get("features", {}).get("battery", 1))))


def _taper(bid: dict, budget: float, profile: dict, power: float = 1.0) -> dict:
    keep = max(0.0, (_battery(profile) - 0.05) / 0.95) ** power
    bid = dict(bid)
    freed = bid["energy"] * (1 - keep)
    bid["energy"] *= keep
    weights = _weights(profile)
    nonenergy = weights["compute"] + weights["security"] or 1
    for k in ("compute", "security"):
        bid[k] += freed * weights[k] / nonenergy
    return _normalize(bid, budget)


def estimate_others(budget: float, profile: dict, history: list, window: int = 4) -> dict:
    """Invert awarded shares: S = b * (C / x - 1).

    A result's `prices` precede its capacities in this arena, so price*capacity
    is not its round's total bid. Allocation, own bid and capacity DO align.
    Normalize by the past wallet to adapt estimates to this round's wallet.
    """
    weights = _weights(profile)
    estimates = {}
    for k in RESOURCES:
        samples = []
        for entry in history[-window:]:
            own = float(entry.get("bid", {}).get(k, 0))
            share = float(entry.get("allocation", {}).get(k, 0))
            capacity = float(entry.get("capacities", {}).get(k, 0))
            spend = float(entry.get("spend", 0))
            if own > 1e-8 and share > 1e-8 and capacity > 0 and spend > 0:
                samples.append(max(1e-4, own * (capacity / share - 1)) / spend)
        estimates[k] = budget * (sum(samples) / len(samples) if samples else 3 * weights[k])
    return estimates


def _floor_needs(capacities: dict, profile: dict, others: dict, margin: float = 1.10) -> dict:
    lower = {k: 0.0 for k in RESOURCES}
    for k, key in (("compute", "q_min"), ("security", "s_min")):
        cap = max(1e-8, float(capacities.get(k, 1)))
        target = min(0.98 * cap, max(0, float(profile.get(key, 0))) * margin)
        lower[k] = max(0.0, others[k]) * target / max(1e-8, cap - target)
    return lower


def template_bid(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    """The supplied weight-proportional policy, unchanged as a comparator."""
    return {k: budget * float(profile.get("weights", {}).get(k, 1 / 3)) for k in RESOURCES}


def battery_taper_bid(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    return _taper(template_bid(budget, prices, capacities, profile, history), budget, profile)


def _maximize(fn: Callable[[float], float], low: float, high: float, steps: int = 18) -> float:
    """Bounded golden-section search; also evaluate both boundary solutions."""
    left, right = low, max(low, high)
    ratio = (math.sqrt(5) - 1) / 2
    a = right - ratio * (right - left)
    b = left + ratio * (right - left)
    fa, fb = fn(a), fn(b)
    for _ in range(steps):
        if fa > fb:
            right, b, fb = b, a, fa
            a = right - ratio * (right - left)
            fa = fn(a)
        else:
            left, a, fa = a, b, fb
            b = left + ratio * (right - left)
            fb = fn(b)
    return max((low, high, a, b), key=fn)


def _utility(bid: dict, capacities: dict, profile: dict, others: dict) -> float:
    weights = _weights(profile)
    allocation = {k: max(0, capacities.get(k, 1)) * max(0, bid[k]) / max(1e-9, max(0, bid[k]) + others[k])
                  for k in RESOURCES}
    score = sum(weights[k] * math.sqrt(allocation[k]) for k in RESOURCES) ** 2
    if allocation["compute"] + 1e-9 < float(profile.get("q_min", 0)):
        score *= 0.5
    if allocation["security"] + 1e-9 < float(profile.get("s_min", 0)):
        score *= 0.5
    return score


def _kelly(budget: float, capacities: dict, profile: dict, others: dict,
           energy_limit: float, energy_cost: float = 0.0, coefficient: float = 0.3,
           recharge: float = 0.22) -> dict:
    lower = _floor_needs(capacities, profile, others)
    if sum(lower.values()) >= budget:
        # Floors can be mutually unaffordable under a noisy estimate. Do not
        # pretend scaling reservations down still guarantees either floor.
        candidates = [_normalize(lower, budget)]
        for k in ("compute", "security"):
            candidate = {r: 0.0 for r in RESOURCES}
            candidate[k] = min(budget, lower[k])
            other = "security" if k == "compute" else "compute"
            candidate[other] = budget - candidate[k]
            candidates.append(candidate)
        return max(candidates, key=lambda bid: _utility(bid, capacities, profile, others))
    limit = max(0, min(energy_limit, budget - lower["compute"] - lower["security"]))

    def split(energy: float) -> dict:
        available = budget - energy
        def value(compute: float) -> float:
            bid = {"compute": compute, "energy": energy, "security": available - compute}
            return _utility(bid, capacities, profile, others)
        compute = _maximize(value, lower["compute"], available - lower["security"], 16)
        return {"compute": compute, "energy": energy, "security": available - compute}

    def value(energy: float) -> float:
        bid = split(energy)
        share = max(0, capacities.get("energy", 1)) * energy / max(1e-9, energy + others["energy"])
        # Effective reward per work+recharge cycle, with a tunable drain price.
        return _utility(bid, capacities, profile, others) / (1 + energy_cost * (coefficient * share + 0.004) / recharge)

    energy = _maximize(value, 0.0, limit, 12)
    return _normalize(split(energy), budget)


def guarded_kelly_bid(budget: float, prices: dict, capacities: dict, profile: dict, history: list) -> dict:
    """Kelly best response under the battery-taper energy allowance and reserved floors."""
    others = estimate_others(budget, profile, history)
    limit = battery_taper_bid(budget, prices, capacities, profile, history)["energy"]
    return _kelly(budget, capacities, profile, others, limit)


def decide_bid(budget: float, prices: dict, capacities: dict, profile: dict,
               history: list) -> dict:
    """The single production policy; no experiment selector or model files."""
    from planner import plan
    return plan(budget, prices, capacities, profile, history)

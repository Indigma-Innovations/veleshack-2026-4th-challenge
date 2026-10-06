"""Cache round-aligned public swarm snapshots without blocking bids."""
from copy import deepcopy
import logging
import threading
import time

import httpx

LOG = logging.getLogger("agent.observations")


class PublicObserver:
    def __init__(self, arena_url: str, team: str, stop: threading.Event):
        self.url = arena_url.rstrip("/")
        self.team = team
        self.stop = stop
        self.lock = threading.Lock()
        self.cached = {}

    def run(self):
        # A separate client keeps observer timeouts and requests independent of
        # registration, heartbeat, result collection and bid retry behavior.
        with httpx.Client(timeout=0.5) as client:
            while not self.stop.is_set():
                try:
                    before = client.get(f"{self.url}/v1/status")
                    before.raise_for_status()
                    swarm = client.get(f"{self.url}/v1/swarm")
                    swarm.raise_for_status()
                    after = client.get(f"{self.url}/v1/status")
                    after.raise_for_status()
                    first, last = before.json(), after.json()
                    if last["finished"]:
                        self.stop.set()
                        break
                    if (first["round"] == last["round"] and not last["finished"]
                            and last.get("seconds_remaining", 0) > 0.05):
                        observation = {"round": last["round"], "observed_at": time.monotonic(),
                            "nodes": [node for node in swarm.json()["nodes"] if node["team"] != self.team]}
                        with self.lock:
                            self.cached = observation
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    LOG.debug("public snapshot unavailable: %s", exc)
                self.stop.wait(0.2)

    def snapshot(self, round_index: int, total_rounds: int) -> dict:
        with self.lock:
            cached = deepcopy(self.cached)
        if cached.get("round") != round_index or time.monotonic() - cached.get("observed_at", 0) > 1:
            cached = {"round": round_index, "nodes": []}
        cached["total_rounds"] = total_rounds
        return cached

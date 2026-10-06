"""Release behavior checks, including frozen bids from the measured winner."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent-template'))
import strategy
import planner
from observations import PublicObserver
from agent import _sanitise


class CycleAwareTests(unittest.TestCase):
    def test_frozen_measured_decisions(self):
        fixtures = json.loads((ROOT / 'tests/cycle-aware-fixtures.json').read_text(encoding='utf-8'))
        self.assertTrue(fixtures)
        for fixture in fixtures:
            with self.subTest(round=fixture['round']):
                self.assertEqual(strategy.decide_bid(**deepcopy(fixture['inputs'])), fixture['expected_bid'])

    def test_unrecognized_opponent_uses_fallback(self):
        fixture = json.loads((ROOT / 'tests/cycle-aware-fixtures.json').read_text(encoding='utf-8'))[0]
        inputs = deepcopy(fixture['inputs'])
        inputs['profile']['_market']['nodes'][0]['team'] = 'unknown-opponent'
        self.assertEqual(strategy.decide_bid(**inputs), strategy.guarded_kelly_bid(**inputs))

    def test_observer_rejects_old_or_different_round(self):
        observer = PublicObserver('http://localhost:1', 'CardanoEdge', threading.Event())
        observer.cached = {'round': 4, 'observed_at': time.monotonic(), 'nodes': [{'team': 'bot-even-split'}]}
        self.assertTrue(observer.snapshot(4, 60)['nodes'])
        self.assertEqual(observer.snapshot(5, 60)['nodes'], [])
        observer.cached['observed_at'] -= 2
        self.assertEqual(observer.snapshot(4, 60)['nodes'], [])

    def test_battery_cutoff_rests_and_recharges(self):
        fixture = json.loads((ROOT / 'tests/cycle-aware-fixtures.json').read_text(encoding='utf-8'))[0]
        inputs = deepcopy(fixture['inputs'])
        profile = inputs['profile']; profile['features']['battery'] = .05
        bid = planner.continuation(profile, profile['_market']['nodes'], inputs['budget'], inputs['prices'], inputs['capacities'])
        self.assertEqual(sum(bid.values()), 0)
        reward, after, _, _ = planner.transition(profile, profile['_market']['nodes'], inputs['budget'], inputs['prices'], inputs['capacities'], bid)
        self.assertEqual(reward, 0)
        self.assertEqual(after['features']['battery'], .27)

    def test_sanitise_nonfinite_and_overbudget(self):
        clean = _sanitise({'compute': float('nan'), 'energy': -1, 'security': 10}, 1)
        self.assertEqual(clean, {'compute': 0, 'energy': 0, 'security': 1})
        self.assertTrue(all(math.isfinite(v) and v >= 0 for v in clean.values()))


if __name__ == '__main__':
    unittest.main()

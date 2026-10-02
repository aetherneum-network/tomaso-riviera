"""The validator chain: one veto refuses, approval needs a quorum (claim A2)."""
from __future__ import annotations

import itertools
import random
import unittest

from corpus import reference_gate
from harness import validators

from tests import _util as U


class Votes(unittest.TestCase):
    def setUp(self):
        self.cfg = U.load_rules().validators
        self.market = {"market_id": "M0", "resolves_at": 100}

    def vote(self, name, **over):
        tally = validators.evaluate(U.cand("C", "M0", 10, **over), self.market, self.cfg)
        return tally.votes[name].split(":")[0]

    def test_price_feed(self):
        self.assertEqual(self.vote("price_feed", price_as_of=7), "APPROVE")    # 3 steps old: the limit
        self.assertEqual(self.vote("price_feed", price_as_of=6), "VETO")       # 4 steps old
        self.assertEqual(self.vote("price_feed", price_as_of=11), "VETO")      # dated after the candidate
        self.assertEqual(self.vote("price_feed", price_as_of=None), "VETO")    # no date: never an approval

    def test_cross_market(self):
        self.assertEqual(self.vote("cross_market", complement_price_ppm=650_000), "APPROVE")   # gap 50000
        self.assertEqual(self.vote("cross_market", complement_price_ppm=650_001), "VETO")
        self.assertEqual(self.vote("cross_market", complement_price_ppm=549_999), "VETO")
        self.assertEqual(self.vote("cross_market", complement_price_ppm=None), "ABSTAIN")

    def test_calendar(self):
        def at(t, market=self.market):
            tally = validators.evaluate(U.cand("C", "M0", t), market, self.cfg)
            return tally.votes["calendar"].split(":")[0]
        self.assertEqual(at(97), "APPROVE")
        self.assertEqual(at(98), "VETO")
        self.assertEqual(at(100), "VETO")
        self.assertEqual(at(10, None), "VETO")     # unknown market: never an approval

    def test_confirmation(self):
        self.assertEqual(self.vote("confirmation", confirm_ppm=410_000), "APPROVE")
        self.assertEqual(self.vote("confirmation", confirm_ppm=409_999), "ABSTAIN")
        self.assertEqual(self.vote("confirmation", confirm_ppm=340_001), "ABSTAIN")
        self.assertEqual(self.vote("confirmation", confirm_ppm=340_000), "VETO")
        self.assertEqual(self.vote("confirmation", confirm_ppm=None), "ABSTAIN")

    def test_every_vote_names_its_rule(self):
        tally = validators.evaluate(U.cand("C", "M0", 10), self.market, self.cfg)
        self.assertEqual(tally.votes, {"price_feed": "APPROVE:V10", "cross_market": "APPROVE:V12",
                                       "calendar": "APPROVE:V13", "confirmation": "APPROVE:V15"})
        self.assertTrue(tally.quorum_met)
        self.assertEqual(tally.vetoes, [])


class Asymmetry(unittest.TestCase):
    """All 3 x 3 x 2 x 3 x ... combinations of the four validators, through the engine."""

    OPTIONS = {
        "price_feed": {"APPROVE": {}, "VETO": {"price_as_of": 1}},
        "cross_market": {"APPROVE": {}, "VETO": {"complement_price_ppm": 700_000}, "ABSTAIN": {"complement_price_ppm": None}},
        "calendar": {"APPROVE": {}, "VETO": {"_near_resolution": True}},
        "confirmation": {"APPROVE": {}, "VETO": {"confirm_ppm": 300_000}, "ABSTAIN": {"confirm_ppm": None}},
    }

    def test_every_combination(self):
        names = list(self.OPTIONS)
        combos = list(itertools.product(*(self.OPTIONS[n].items() for n in names)))
        self.assertEqual(len(combos), 2 * 3 * 2 * 3)
        b = U.Bench(self, n_markets=len(combos) + 1)
        b.markets["NEAR"] = {"market_id": "NEAR", "opens_at": 0, "resolves_at": 12}
        executed = 0
        for i, combo in enumerate(combos):
            over = {}
            for _vote, change in combo:
                over.update(change)
            near = over.pop("_near_resolution", False)
            c = U.cand(f"C{i}", "NEAR" if near else f"M{i}", 10, **over)
            if near and b.state.positions.get("NEAR"):
                continue
            body = b.decide(c)
            want = dict(zip(names, (vote for vote, _change in combo)))
            got = {n: v.split(":")[0] for n, v in body["votes"].items()}
            self.assertEqual(got, want)
            vetoes = sum(1 for v in want.values() if v == "VETO")
            approvals = sum(1 for v in want.values() if v == "APPROVE")
            if vetoes:
                self.assertEqual((body["decision"], body["reason"]), ("REJECTED", "VALIDATOR_VETO"), want)
            elif approvals < 3:
                self.assertEqual((body["decision"], body["reason"]), ("REJECTED", "NO_QUORUM"), want)
            else:
                self.assertIn(body["reason"], ("EDGE_ABOVE_THRESHOLD", "RISK_PORTFOLIO_EXPOSURE"), want)
                executed += body["decision"] == "EXECUTED"
        self.assertGreater(executed, 0)

    def test_no_single_validator_can_approve_alone(self):
        cfg = U.load_rules().validators
        self.assertGreaterEqual(cfg["quorum"]["min_approvals"], 2)
        only_one = U.cand("C", "M0", 10, complement_price_ppm=None, confirm_ppm=None, price_as_of=None)
        tally = validators.evaluate(only_one, {"resolves_at": 100}, cfg)
        self.assertEqual(tally.approvals, 1)
        self.assertFalse(tally.quorum_met)


class AgainstTheReference(unittest.TestCase):
    def test_two_thousand_random_candidates_vote_as_the_reference_says(self):
        rng = random.Random(20260930)
        cfg = U.load_rules().validators

        def maybe(value):
            return None if rng.randrange(8) == 0 else value
        for i in range(2_000):
            t = rng.randrange(5, 60)
            price = rng.randrange(1, 1_000_000)
            c = U.cand(f"C{i}", "M0", t, price=price,
                       price_as_of=maybe(t - rng.randrange(-2, 7)),
                       complement_price_ppm=maybe(1_000_000 - price + rng.randrange(-80_000, 80_001)),
                       confirm_ppm=maybe(price + rng.randrange(-90_000, 90_001)))
            market = maybe({"market_id": "M0", "resolves_at": t + rng.randrange(0, 6)})
            got = {n: v.split(":")[0] for n, v in validators.evaluate(c, market, cfg).votes.items()}
            self.assertEqual(got, reference_gate.votes(c, market), c)


if __name__ == "__main__":
    unittest.main()

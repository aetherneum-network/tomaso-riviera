"""Position size: a quarter of Kelly, capped at 5%, never a function of conviction (claim A3)."""
from __future__ import annotations

import inspect
import random
import unittest
from fractions import Fraction

from corpus import reference_kelly
from harness import risk, sizing
from harness.exact import PPM

from tests import _util as U


class ClosedFormula(unittest.TestCase):
    def setUp(self):
        self.cfg = U.load_rules().risk

    def test_ten_thousand_seeded_cases_against_the_independent_reference(self):
        rng = random.Random(20260930)
        shrink = Fraction(3, 4)
        worst = 0
        for _ in range(10_000):
            q = rng.randrange(1, PPM)
            p = rng.randrange(0, PPM + 1)
            equity = rng.randrange(1, 10**9)
            throttle = shrink ** rng.randrange(0, 5)
            got = sizing.size(p, q, equity, throttle, self.cfg)
            want = reference_kelly.stake(p, q, equity, throttle)
            worst = max(worst, abs(got.stake_units - want))
            self.assertEqual(got.limit_units, reference_kelly.position_limit(p, q, equity))
            self.assertEqual(got.payout_if_win_units, reference_kelly.payout_if_win(got.stake_units, q))
            # the never-event, as an inequality: never above min(quarter Kelly, 5%) of equity
            self.assertLessEqual(got.stake_units, got.limit_units)
            self.assertLessEqual(got.stake_units * 20, equity)
            self.assertGreaterEqual(got.stake_units, 0)
            if p > q:
                self.assertLessEqual(Fraction(got.stake_units, equity), Fraction(p - q, PPM - q) / 4)
            else:
                self.assertEqual(got.stake_units, 0)
        self.assertEqual(worst, 0)   # tolerance declared in the plan: 0 units (exact arithmetic)

    def test_known_values(self):
        self.assertEqual(sizing.size(500_000, 410_000, 1_000_000, Fraction(1), self.cfg).stake_units, 38_135)
        self.assertEqual(sizing.size(800_000, 410_000, 1_000_000, Fraction(1), self.cfg).stake_units, 50_000)
        self.assertEqual(sizing.size(430_001, 410_000, 1_000_000, Fraction(1), self.cfg).stake_units, 8_475)
        self.assertEqual(sizing.kelly_fraction(500_000, 410_000), Fraction(9, 59))

    def test_cap_binds_exactly_where_a_quarter_of_kelly_reaches_five_percent(self):
        # f*/4 = 5%  <=>  f* = 20%  <=>  p = q + 0.2 (1 - q); at q = 500000 that is p = 600000
        at = sizing.size(600_000, 500_000, 1_000_000, Fraction(1), self.cfg)
        below = sizing.size(599_999, 500_000, 1_000_000, Fraction(1), self.cfg)
        above = sizing.size(999_999, 500_000, 1_000_000, Fraction(1), self.cfg)
        self.assertEqual((at.stake_units, above.stake_units), (50_000, 50_000))
        self.assertEqual(below.stake_units, 49_999)

    def test_the_throttle_can_only_reduce(self):
        full = sizing.size(800_000, 410_000, 1_000_000, Fraction(1), self.cfg).stake_units
        previous = full
        for level in range(1, 5):
            stake = sizing.size(800_000, 410_000, 1_000_000, Fraction(3, 4) ** level, self.cfg).stake_units
            self.assertLess(stake, previous)
            previous = stake
        for bad in (Fraction(4, 3), Fraction(0), Fraction(-1, 2)):
            with self.assertRaises(ValueError):
                sizing.size(800_000, 410_000, 1_000_000, bad, self.cfg)

    def test_an_effective_price_of_zero_or_one_is_refused(self):
        for q in (0, PPM, -5, PPM + 1):
            with self.assertRaises(ValueError):
                sizing.kelly_fraction(500_000, q)


class NeverWithConviction(unittest.TestCase):
    def test_the_sizing_function_has_no_way_to_receive_a_conviction(self):
        self.assertEqual(list(inspect.signature(sizing.size).parameters),
                         ["p_hat_ppm", "q_eff_ppm", "equity_units", "throttle_multiplier", "cfg"])
        source = inspect.getsource(sizing)
        body = source.split('"""', 2)[2]                 # the code, without the module docstring
        self.assertNotIn("conviction", body)
        self.assertNotIn("confidence", body)

    def test_same_figures_different_conviction_same_size(self):
        b = U.Bench(self)
        stakes = {}
        for i, label in enumerate(("low", "medium", "high", "extreme", "ALL IN", None, 10**9)):
            body = b.decide(U.cand(f"C{i}", f"M{i}", 1, signal_ppm=600_000, confidence=500_000, conviction=label))
            stakes[str(label)] = body["sizing"]["stake_units"]
        self.assertEqual(set(stakes.values()), {38_135}, stakes)

    def test_confidence_moves_the_estimate_not_the_size_directly(self):
        b = U.Bench(self)
        a = b.decide(U.cand("A", "M0", 1, signal_ppm=600_000, confidence=500_000))
        c = b.decide(U.cand("C", "M1", 1, signal_ppm=500_000, confidence=1_000_000))
        self.assertEqual(a["decomposition"]["p_hat_ppm"], c["decomposition"]["p_hat_ppm"])
        self.assertEqual(a["sizing"]["stake_units"], c["sizing"]["stake_units"])


class RiskVeto(unittest.TestCase):
    """The risk engine can only refuse (claim: portfolio limits override signal strength)."""

    def test_one_position_per_market(self):
        b = U.Bench(self)
        self.assertEqual(b.decide(U.cand("C1", "M0", 1))["decision"], "EXECUTED")
        again = b.decide(U.cand("C2", "M0", 2))
        self.assertEqual((again["decision"], again["reason"]), ("REJECTED", "RISK_POSITION_ALREADY_OPEN"))
        self.assertEqual(len(b.state.positions["M0"]), 1)

    def test_portfolio_exposure_stops_the_seventh_position(self):
        b = U.Bench(self)
        for i in range(6):
            self.assertEqual(b.decide(U.cand(f"C{i}", f"M{i}", 1))["decision"], "EXECUTED", i)
        self.assertEqual(b.state.open_stake_units, 300_000)      # exactly 30%: allowed
        seventh = b.decide(U.cand("C6", "M6", 2))
        self.assertEqual((seventh["decision"], seventh["reason"]), ("REJECTED", "RISK_PORTFOLIO_EXPOSURE"))

    def test_exposure_tightens_after_a_ten_percent_drawdown(self):
        b = U.Bench(self)
        b.lose(1, 2)                                             # equity 900000: drawdown exactly 10%
        self.assertEqual(b.state.drawdown_ppm, 100_000)
        self.assertEqual(risk.exposure_limit_ppm(b.state.drawdown_ppm, b.rules.risk), 150_000)
        opened = 0
        for i in range(10, 20):
            body = b.decide(U.cand(f"C{i}", f"M{i}", 50))
            if body["decision"] == "EXECUTED":
                opened += 1
            else:
                self.assertEqual(body["reason"], "RISK_PORTFOLIO_EXPOSURE")
        self.assertLessEqual(b.state.open_stake_units, 135_000)  # 15% of 900000
        self.assertLess(opened, 10)

    def test_the_risk_engine_never_enlarges(self):
        source = inspect.getsource(risk.veto)
        self.assertNotIn("return stake", source)
        b = U.Bench(self)
        self.assertIsNone(risk.veto(50_000, "M0", b.state, b.rules.risk))
        self.assertEqual(risk.veto(50_001, "M0", b.state, b.rules.risk), "RISK_MARKET_CAP")


if __name__ == "__main__":
    unittest.main()

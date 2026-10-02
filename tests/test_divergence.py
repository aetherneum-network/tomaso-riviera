"""Divergence of the replayed stream from the backtest, and the look-ahead (leak) test (claim A7).

Nothing here is live: the "stream" is the replay of a synthetic world.
"""
from __future__ import annotations

import json
import unittest
from fractions import Fraction

from harness import backtest, divergence, run
from harness.engine import Engine
from harness.ledger import Ledger, verify
from harness.stream import load_world

from tests import _util as U

CFG = {"n_sigma": 4, "window_trades": 50, "min_baseline_trades": 50}


def base(mean: int, variance: int, n: int = 100) -> dict:
    return {"n": n, "mean_ppm": f"{mean}/1", "variance_ppm2": f"{variance}/1"}


class Baseline(unittest.TestCase):
    def test_mean_and_sample_variance_are_exact(self):
        self.assertEqual(divergence.baseline_from([]), {"n": 0, "mean_ppm": "0/1", "variance_ppm2": "0/1"})
        self.assertEqual(divergence.baseline_from([5]), {"n": 1, "mean_ppm": "5/1", "variance_ppm2": "0/1"})
        self.assertEqual(divergence.baseline_from([100, 200, 300]),
                         {"n": 3, "mean_ppm": "200/1", "variance_ppm2": "10000/1"})
        self.assertEqual(divergence.baseline_from([1, 2]), {"n": 2, "mean_ppm": "3/2", "variance_ppm2": "1/2"})

    def test_a_detector_without_a_usable_baseline_is_not_armed_and_says_why(self):
        for baseline, reason in ((None, "NO_BASELINE"), (base(0, 100, n=49), "BASELINE_TOO_SMALL"),
                                 (base(0, 0), "BASELINE_WITHOUT_VARIANCE")):
            det = divergence.Detector(baseline, CFG)
            self.assertEqual((det.armed, det.reason_unarmed), (False, reason))
            self.assertTrue(all(det.observe(10**6) is None for _ in range(200)))
        self.assertTrue(divergence.Detector(base(0, 100, n=50), CFG).armed)


class Boundary(unittest.TestCase):
    def verdict(self, baseline: dict, value: int) -> dict:
        det = divergence.Detector(baseline, CFG)
        out = [det.observe(value) for _ in range(50)]
        self.assertTrue(all(v is None for v in out[:-1]))
        return out[-1]

    def test_the_rule_by_hand(self):
        # allowed = 4^2 x 7500 x (1/50 + 1/100) = 3600 = 60^2: exactly 60 from the mean is NOT beyond 4 sigma
        b = base(1_000, 7_500)
        at = self.verdict(b, 1_060)
        self.assertEqual((at["allowed_sq"], at["deviation_sq"], at["diverged"]), ("3600/1", "3600/1", False))
        self.assertTrue(self.verdict(b, 1_061)["diverged"])
        self.assertFalse(self.verdict(b, 940)["diverged"])
        self.assertTrue(self.verdict(b, 939)["diverged"])                # two-sided: better than backtest pauses too

    def test_a_smaller_baseline_widens_the_band(self):
        small, large = base(0, 7_500, n=50), base(0, 7_500, n=5_000)
        self.assertEqual(Fraction(self.verdict(small, 0)["allowed_sq"].split("/")[0]), 4_800)
        self.assertFalse(self.verdict(small, 69)["diverged"])
        self.assertTrue(self.verdict(large, 69)["diverged"])

    def test_windows_do_not_overlap(self):
        det = divergence.Detector(base(0, 7_500), CFG)
        verdicts = [(k, det.observe(0)) for k in range(1, 121)]
        done = [(k, v["window_index"]) for k, v in verdicts if v is not None]
        self.assertEqual(done, [(50, 0), (100, 1)])

    def test_no_float_in_a_verdict(self):
        v = self.verdict(base(1, 3), 2)
        self.assertFalse(any(isinstance(x, float) for x in v.values()))
        json.dumps(v)


class InTheEngine(unittest.TestCase):
    def replay(self, baseline: dict, batches: int = 11):
        rules = U.load_rules()
        market_map = U.markets(80)
        path = U.scratch("divergence") / "ledger.jsonl"
        ledger = Ledger.create(path, U.genesis(rules))
        self.addCleanup(ledger.close)
        engine = Engine(rules, ledger, market_map, baseline=baseline, start_t=0)
        decisions = []
        for batch in range(batches):
            t = 1 + 2 * batch
            ids = [f"M{5 * batch + k}" for k in range(5)]
            for m in ids:
                decisions.append(engine.on_candidate(U.cand(f"C-{m}", m, t))["body"])
            for m in ids:
                engine.on_settlement(t + 1, m, "YES")                    # every trade wins: x = +590000
        ledger.close()
        rows = U.read_rows(path)
        return decisions, rows, U.audit(path, market_map), path

    def test_a_stream_like_the_backtest_is_checked_and_not_paused(self):
        decisions, rows, audit, _path = self.replay(base(590_000, 1))
        self.assertEqual([r["kind"] for r in rows[:2]], ["GENESIS", "BASELINE"])
        checks = [r for r in rows if r["kind"] == "DIVERGENCE_CHECK"]
        self.assertEqual([(c["body"]["window_index"], c["body"]["diverged"]) for c in checks], [(0, False)])
        self.assertEqual(sum(1 for r in rows if r["kind"] == "PAUSE"), 0)
        self.assertTrue(all(d["decision"] == "EXECUTED" for d in decisions))
        self.assertEqual((audit["mismatches"], audit["violations"]), ([], []))

    def test_a_stream_far_from_the_backtest_is_paused_at_the_end_of_the_window(self):
        decisions, rows, audit, path = self.replay(base(0, 1))
        pauses = [r for r in rows if r["kind"] == "PAUSE"]
        self.assertEqual(len(pauses), 1)
        settlements_before = sum(1 for r in rows[:pauses[0]["seq"]] if r["kind"] == "SETTLEMENT")
        self.assertEqual(settlements_before, 50)                         # exactly at the fiftieth settled trade
        self.assertEqual(rows[pauses[0]["seq"] - 1]["kind"], "DIVERGENCE_CHECK")
        self.assertEqual([d["decision"] for d in decisions[:50]], ["EXECUTED"] * 50)
        self.assertEqual({(d["decision"], d["reason"], d["rule_id"]) for d in decisions[50:]},
                         {("REJECTED", "SIGNAL_PAUSED", "G03")})        # the pause is final for the run
        self.assertEqual((audit["mismatches"], audit["violations"]), ([], []))
        self.assertTrue(verify(path)["ok"])

    def test_the_declared_operating_point_holds_on_a_seeded_study(self):
        score = U.score_module()
        a = score.detector_study(U.DEV_SEED, runs=200)
        self.assertEqual(a, score.detector_study(U.DEV_SEED, runs=200))   # seeded: same numbers twice
        declared = score.DECLARED
        for key in ("false_pause_runs_planted_like", "false_pause_runs_favourites"):
            self.assertLessEqual(a[key] * 1_000_000, declared["false_pause_per_run_max_ppm"] * 200, key)
        self.assertGreaterEqual(a["regime_runs_paused_within_declared_K"] * 1_000_000,
                                declared["pause_within_K_min_ppm"] * 200)
        self.assertEqual(a["rule"], U.load_rules().risk["divergence"])


def small_world(baseline_until_t: int = 0):
    market_map = {f"M{i}": {"market_id": f"M{i}", "opens_at": 0, "resolves_at": 20 + 5 * i} for i in range(12)}
    outcomes = {f"M{i}": "YES" if i % 3 else "NO" for i in range(12)}
    candidates = []
    for i in range(12):
        for k in range(2):
            t = 3 + 5 * i + 4 * k
            candidates.append(U.cand(f"C{i}-{k}", f"M{i}", t, price=380_000 + 20_000 * k))
    return U.make_world(candidates, market_map, outcomes, baseline_until_t=baseline_until_t)


def honest_mean(cand: dict, view) -> int:
    """Uses only the quotes dated up to the candidate's own time, whatever the view would show."""
    quotes = [price for t, _side, price in view.quotes(cand["market_id"]) if t <= cand["t"]]
    return sum(quotes) // len(quotes) + 100_000


def careless_mean(cand: dict, view) -> int:
    """The same mean without the date filter: it reads whatever the view shows, later quotes included."""
    quotes = [price for _t, _side, price in view.quotes(cand["market_id"])]
    return sum(quotes) // len(quotes) + 100_000


def peeks_at_the_outcome(cand: dict, view) -> int:
    outcome = view.outcome(cand["market_id"])
    return cand["price_ppm"] if outcome is None else (990_000 if outcome == cand["side"] else 10_000)


def peeks_at_later_quotes(cand: dict, view) -> int:
    return view.quotes(cand["market_id"])[-1][2]


class LeakTest(unittest.TestCase):
    def setUp(self):
        self.world = load_world(small_world())
        self.rules = U.load_rules()

    def test_a_view_shows_nothing_beyond_its_time(self):
        view = backtest.StreamView(self.world, 22)
        self.assertEqual(view.outcome("M0"), "NO")                       # resolved at 20
        self.assertIsNone(view.outcome("M1"))                            # resolves at 25
        self.assertEqual(backtest.StreamView(self.world, 25).outcome("M1"), "YES")
        self.assertEqual(backtest.StreamView(self.world, None).outcome("M11"), "YES")
        self.assertEqual([q[0] for q in backtest.StreamView(self.world, 8).quotes("M1")], [8])
        self.assertEqual([q[0] for q in backtest.StreamView(self.world, 7).quotes("M1")], [])
        self.assertEqual([q[0] for q in backtest.StreamView(self.world, None).quotes("M1")], [8, 12])

    def test_a_signal_that_reads_the_outcome_is_caught_on_the_first_candidate(self):
        verdict = backtest.leak_test(peeks_at_the_outcome, self.world, self.world.candidates)
        self.assertEqual((verdict["status"], verdict["reason"], verdict["candidate_id"], verdict["checked"]),
                         ("FAILED", "LOOKAHEAD", "C0-0", 1))
        self.assertEqual((verdict["with_future"], verdict["without_future"]), (10_000, 380_000))

    def test_a_signal_that_reads_a_later_quote_is_caught(self):
        verdict = backtest.leak_test(peeks_at_later_quotes, self.world, self.world.candidates)
        self.assertEqual((verdict["status"], verdict["candidate_id"]), ("FAILED", "C0-0"))

    def test_a_mean_without_a_date_filter_is_caught(self):
        verdict = backtest.leak_test(careless_mean, self.world, self.world.candidates)
        self.assertEqual((verdict["status"], verdict["candidate_id"], verdict["with_future"],
                          verdict["without_future"]), ("FAILED", "C0-0", 490_000, 480_000))

    def test_an_honest_signal_passes(self):
        verdict = backtest.leak_test(honest_mean, self.world, self.world.candidates)
        self.assertEqual(verdict, {"status": "OK", "reason": None, "checked": 24})

    def test_a_failed_backtest_produces_no_ledger_no_baseline_no_trade(self):
        out = U.scratch("backtest")
        result = backtest.run_backtest(self.world, self.rules, out, signal_fn=peeks_at_the_outcome, signal_name="leaky")
        self.assertEqual((result["status"], result["reason"], result["baseline"], result["trades_settled"],
                          result["ledger_rows"]), ("FAILED", "LOOKAHEAD", None, 0, 0))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["backtest.json"])
        self.assertEqual(json.loads((out / "backtest.json").read_text(encoding="utf-8"))["status"], "FAILED")

    def test_an_honest_backtest_writes_a_paper_ledger_and_a_baseline(self):
        out = U.scratch("backtest")
        result = backtest.run_backtest(self.world, self.rules, out, signal_fn=honest_mean, signal_name="honest_mean")
        self.assertEqual((result["status"], result["leak_test"]["status"]), ("OK", "OK"))
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["backtest.json", "backtest_anchors.json", "backtest_ledger.jsonl"])
        check = verify(out / "backtest_ledger.jsonl", expect_head=result["ledger_head"])
        self.assertTrue(check["ok"])
        rows = U.read_rows(out / "backtest_ledger.jsonl")
        self.assertEqual(rows[0]["body"]["phase"], "BACKTEST")
        self.assertEqual({r["body"]["signal_source"] for r in rows if r["kind"] == "CANDIDATE"}, {"honest_mean"})
        self.assertEqual(result["baseline"]["n"], result["trades_settled"])
        audit = U.audit(out / "backtest_ledger.jsonl", self.world.markets)
        self.assertEqual((audit["mismatches"], audit["violations"]), ([], []))

    def test_a_backtest_never_overwrites(self):
        out = U.scratch("backtest")
        backtest.run_backtest(self.world, self.rules, out)
        with self.assertRaises(Exception):
            backtest.run_backtest(self.world, self.rules, out)


class BacktestThenReplay(unittest.TestCase):
    def test_the_replay_starts_where_the_backtest_stops_and_carries_its_baseline(self):
        world_dir = small_world(baseline_until_t=40)
        out = U.scratch("run")
        summary = run.run_world(world_dir, U.RULES_DIR, out)
        self.assertEqual((summary["backtest_status"], summary["divergence_armed"],
                          summary["divergence_unarmed_reason"]), ("OK", False, "BASELINE_TOO_SMALL"))
        back = U.read_rows(out / "backtest_ledger.jsonl")
        replay = U.read_rows(out / "ledger.jsonl")
        self.assertLess(max(r["t"] for r in back), 40)
        self.assertGreaterEqual(min(r["t"] for r in replay[1:]), 40)
        self.assertEqual(replay[1]["kind"], "BASELINE")
        bt = json.loads((out / "backtest.json").read_text(encoding="utf-8"))
        self.assertEqual({k: replay[1]["body"][k] for k in ("n", "mean_ppm", "variance_ppm2")}, bt["baseline"])
        ids_back = {r["body"]["candidate_id"] for r in back if r["kind"] == "CANDIDATE"}
        ids_replay = {r["body"]["candidate_id"] for r in replay if r["kind"] in ("CANDIDATE", "REFUSED_INPUT")}
        self.assertEqual(len(ids_back | ids_replay), 24)
        self.assertEqual(ids_back & ids_replay, set())


if __name__ == "__main__":
    unittest.main()

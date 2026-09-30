"""The drawdown circuit breaker halts by itself and is re-armed only by a human consent record (claim A4).

And the throttle: size shrinks after each loss; it re-expands one step at a time, only after a cooldown
AND on an edge above a higher bar (claim A5).
"""
from __future__ import annotations

import json
import unittest
from fractions import Fraction

from harness import breaker
from harness.ledger import verify
from harness.throttle import Throttle

from tests import _util as U

STATEMENT = "I have read the halt and I re-arm the breaker."


def halted_bench(test) -> tuple[U.Bench, int]:
    b = U.Bench(test)
    t = b.lose(1, 4)                     # four full-size losses: 1,000,000 -> 800,000, exactly 20%
    assert b.state.halted
    return b, t


def consent_for(b: U.Bench, t: int, **over) -> dict:
    halt = b.state.halt_row
    consent = {"kind": "HUMAN_REARM_CONSENT", "operator": "Fixture Operator (test record)", "operator_is_human": True,
               "halt_hash": halt["hash"], "acknowledged_drawdown_ppm": halt["body"]["drawdown_ppm"],
               "statement": STATEMENT, "signed_at_t": t}
    consent.update(over)
    return consent


class Halt(unittest.TestCase):
    def test_the_comparison_is_exact(self):
        self.assertTrue(breaker.tripped(800_000, 1_000_000, 200_000))
        self.assertFalse(breaker.tripped(800_001, 1_000_000, 200_000))
        self.assertFalse(breaker.tripped(800_000, 999_999, 200_000))     # 19.99998%: below, however close
        self.assertTrue(breaker.tripped(799_999, 999_999, 200_000))
        self.assertEqual(breaker.drawdown_ppm(800_001, 1_000_000), 199_999)
        self.assertEqual(breaker.drawdown_ppm(1_100_000, 1_000_000), 0)
        self.assertEqual(breaker.drawdown_ppm(5, 0), 1_000_000)

    def test_the_ledger_halts_at_the_settlement_that_reaches_the_limit(self):
        b = U.Bench(self)
        for i in range(5):
            b.decide(U.cand(f"C{i}", f"M{i}", 1))
        for i in range(3):
            b.settle(10 + i, f"M{i}", "NO")
        self.assertFalse(b.state.halted)                                 # 15%: not yet
        self.assertEqual(b.state.drawdown_ppm, 150_000)
        b.settle(13, "M3", "NO")
        rows = b.rows()
        self.assertEqual([r["kind"] for r in rows[-2:]], ["SETTLEMENT", "HALT"])
        halt = rows[-1]
        self.assertEqual((halt["t"], halt["body"]["equity_units"], halt["body"]["peak_units"],
                          halt["body"]["drawdown_ppm"], halt["body"]["trigger_seq"]),
                         (13, 800_000, 1_000_000, 200_000, rows[-2]["seq"]))
        b.settle(14, "M4", "NO")                                         # a further loss while halted
        self.assertEqual(sum(1 for r in b.rows() if r["kind"] == "HALT"), 1)
        self.assertTrue(b.state.halted)

    def test_while_halted_nothing_is_executed_however_strong_the_signal(self):
        b, t = halted_bench(self)
        for i in range(10, 20):
            body = b.decide(U.cand(f"C{i}", f"M{i}", t + i, signal_ppm=1_000_000, confidence=1_000_000,
                                   conviction="extreme"))
            self.assertEqual((body["decision"], body["reason"]), ("REJECTED", "BREAKER_HALTED"))
        self.assertEqual(b.state.open_stake_units, 0)


class Rearm(unittest.TestCase):
    def refused(self, b: U.Bench, t: int, consent, expect: str) -> None:
        rows = b.ledger.rows
        with self.assertRaises(breaker.RearmRefused, msg=f"{expect}: {consent!r}") as ctx:
            b.ledger.rearm(t, consent)
        self.assertEqual(ctx.exception.reason, expect)
        last = b.rows()[-1]
        self.assertEqual((b.ledger.rows, last["kind"], last["body"]["reason"]), (rows + 1, "REARM_REFUSED", expect))
        self.assertTrue(b.state.halted)

    def test_refused_without_a_consent_record(self):
        b, t = halted_bench(self)
        self.refused(b, t, None, "NO_CONSENT_RECORD")
        self.refused(b, t, "yes, go ahead", "NO_CONSENT_RECORD")
        self.refused(b, t, [], "NO_CONSENT_RECORD")

    def test_refused_for_every_defect_of_the_record(self):
        b, t = halted_bench(self)
        defects = (
            ({"kind": "REARM"}, "CONSENT_WRONG_KIND"),
            ({"operator": ""}, "CONSENT_NO_OPERATOR"),
            ({"operator": None}, "CONSENT_NO_OPERATOR"),
            ({"operator_is_human": False}, "CONSENT_NOT_HUMAN"),
            ({"operator_is_human": "true"}, "CONSENT_NOT_HUMAN"),
            ({"operator_is_human": 1}, "CONSENT_NOT_HUMAN"),
            ({"operator": "agent-7"}, "CONSENT_NOT_HUMAN"),
            ({"operator": "Bot"}, "CONSENT_NOT_HUMAN"),
            ({"operator": "harness_autopilot"}, "CONSENT_NOT_HUMAN"),
            ({"operator": "AI supervisor"}, "CONSENT_NOT_HUMAN"),
            ({"halt_hash": "0" * 64}, "CONSENT_FOR_ANOTHER_HALT"),
            ({"halt_hash": None}, "CONSENT_FOR_ANOTHER_HALT"),
            ({"acknowledged_drawdown_ppm": 199_999}, "CONSENT_DRAWDOWN_NOT_ACKNOWLEDGED"),
            ({"statement": "ok"}, "CONSENT_NO_STATEMENT"),
            ({"statement": None}, "CONSENT_NO_STATEMENT"),
            ({"signed_at_t": t - 2}, "CONSENT_TIME_INVALID"),           # signed before the halt (at t - 1) existed
            ({"signed_at_t": t + 50}, "CONSENT_TIME_INVALID"),          # dated in the future
            ({"signed_at_t": "now"}, "CONSENT_TIME_INVALID"),
        )
        for over, reason in defects:
            self.refused(b, t + 1, consent_for(b, t, **over), reason)
        body = b.decide(U.cand("C10", "M10", t + 2))
        self.assertEqual(body["reason"], "BREAKER_HALTED")               # eighteen refusals later: still halted
        self.assertEqual(b.forge(U.cand("C11", "M11", t + 2)), "EXECUTION_WHILE_HALTED")
        self.assertTrue(verify(b.path)["ok"])

    def test_refused_when_the_consent_file_is_missing_or_unreadable(self):
        b, t = halted_bench(self)
        (b.dir / "broken.json").write_text("{not json", encoding="utf-8")
        (b.dir / "list.json").write_text("[1, 2]", encoding="utf-8")
        (b.dir / "float.json").write_text(json.dumps(consent_for(b, t, note=0.5)), encoding="utf-8")
        requests = ((None, "NO_CONSENT_RECORD"), ("missing.json", "CONSENT_FILE_NOT_FOUND"),
                    ("broken.json", "CONSENT_UNREADABLE"), ("list.json", "CONSENT_UNREADABLE"),
                    ("float.json", "CONSENT_UNREADABLE"))
        for name, reason in requests:
            request = {"kind": "REARM_REQUEST", "t": t + 1}
            if name is not None:
                request["consent_file"] = name
            b.engine.on_request(request)                                 # the engine swallows the refusal ...
            last = b.rows()[-1]
            self.assertEqual((last["kind"], last["body"]["reason"]), ("REARM_REFUSED", reason))   # ... the ledger records it
            self.assertTrue(b.state.halted)

    def test_a_request_when_nothing_is_halted_is_refused_and_recorded(self):
        b = U.Bench(self)
        with self.assertRaises(breaker.RearmRefused) as ctx:
            b.ledger.rearm(1, {"kind": "HUMAN_REARM_CONSENT"})
        self.assertEqual(ctx.exception.reason, "NOT_HALTED")
        self.assertEqual(b.rows()[-1]["body"], {"reason": "NOT_HALTED", "halt_hash": None})

    def test_a_valid_human_consent_rearms_and_is_recorded_in_full(self):
        b, t = halted_bench(self)
        consent = consent_for(b, t)
        halt_hash = b.state.halt_row["hash"]
        (b.dir / "consent.json").write_text(json.dumps(consent), encoding="utf-8")
        b.engine.on_request({"kind": "REARM_REQUEST", "t": t + 1, "consent_file": "consent.json"})
        row = b.rows()[-1]
        self.assertEqual(row["kind"], "REARM")
        self.assertEqual(row["body"]["consent"], consent)
        self.assertEqual((row["body"]["halt_hash"], row["body"]["peak_reset_to_units"]), (halt_hash, 800_000))
        self.assertEqual(len(row["body"]["consent_sha256"]), 64)
        self.assertFalse(b.state.halted)
        self.assertEqual((b.state.peak_units, b.state.drawdown_ppm), (800_000, 0))
        body = b.decide(U.cand("C10", "M10", t + 2))
        self.assertEqual(body["decision"], "EXECUTED")
        # four losses: throttle at level 4 (no cooldown elapsed), so 5% x (3/4)^4 of 800000
        self.assertEqual((body["sizing"]["throttle_level"], body["sizing"]["stake_units"]), (4, 12_656))
        audit = U.audit(b.path, b.markets)
        self.assertEqual((audit["violations"], audit["mismatches"]), ([], []))

    def test_a_consent_cannot_be_reused_for_a_later_halt(self):
        b, t = halted_bench(self)
        first = consent_for(b, t)
        b.ledger.rearm(t, first)
        for i in range(10, 14):
            self.assertIsNone(b.forge(U.cand(f"C{i}", f"M{i}", t + 1)))  # 4 x 40000 at full size
        for i in range(10, 14):
            b.settle(t + 2 + i, f"M{i}", "NO")
        self.assertTrue(b.state.halted)                                  # 800000 -> 640000: 20% again
        self.assertEqual(sum(1 for r in b.rows() if r["kind"] == "HALT"), 2)
        now = t + 20
        with self.assertRaises(breaker.RearmRefused) as ctx:
            b.ledger.rearm(now, dict(first, signed_at_t=now))
        self.assertEqual(ctx.exception.reason, "CONSENT_FOR_ANOTHER_HALT")
        b.ledger.rearm(now, consent_for(b, now))
        self.assertFalse(b.state.halted)
        self.assertEqual(U.audit(b.path, b.markets)["violations"], [])

    def test_there_is_no_other_way_to_clear_the_halt(self):
        b, t = halted_bench(self)
        b.ledger.close()
        from harness.ledger import Ledger
        again = Ledger.load(b.path)                                      # re-opening the file does not re-arm
        self.addCleanup(again.close)
        self.assertTrue(again.state.halted)
        self.assertEqual(again.state.halt_row["kind"], "HALT")
        source = (U.ROOT / "harness" / "ledger.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("st.halted, st.halt_row = False, None"), 1)   # only in the REARM row
        self.assertEqual(source.count('_commit("REARM"'), 1)


class ThrottleRule(unittest.TestCase):
    def setUp(self):
        self.cfg = U.load_rules().throttle

    def test_each_loss_shrinks_by_a_quarter_down_to_the_floor(self):
        th = Throttle(self.cfg)
        seen = []
        for t in range(1, 8):
            th.on_loss(t)
            seen.append((th.level, th.multiplier()))
        self.assertEqual(seen[:4], [(1, Fraction(3, 4)), (2, Fraction(9, 16)), (3, Fraction(27, 64)),
                                    (4, Fraction(81, 256))])
        self.assertEqual(seen[4:], [(4, Fraction(81, 256))] * 3)
        self.assertEqual(th.last_change_t, 7)                            # a loss restarts the clock even at the floor

    def test_reexpansion_needs_time_and_a_higher_bar_both(self):
        th = Throttle(self.cfg)
        th.on_loss(100)
        th.on_loss(100)
        self.assertEqual(th.preview(119, 90_000)[0], 2)                  # strong edge, too early
        self.assertEqual(th.preview(120, 40_000)[0], 2)                  # time elapsed, edge only equal to the bar
        self.assertEqual(th.preview(500, 30_000)[0], 2)                  # edge above the execute threshold: not enough
        self.assertEqual(th.preview(120, 40_001), (1, Fraction(3, 4)))   # both: one step, not two
        self.assertEqual(th.level, 2)                                    # a preview changes nothing
        th.commit(1, 120)
        self.assertEqual((th.level, th.last_change_t), (1, 120))
        self.assertEqual(th.preview(139, 90_000)[0], 1)                  # the clock restarted at the step
        self.assertEqual(th.preview(140, 90_000)[0], 0)
        th.commit(0, 140)
        self.assertEqual(th.preview(10_000, 900_000), (0, Fraction(1)))  # never above full size

    def test_the_bar_is_higher_than_the_execute_threshold(self):
        self.assertGreater(self.cfg["reexpansion_edge_bar_ppm"], U.load_rules().threshold_ppm)


class ThrottleInTheEngine(unittest.TestCase):
    def test_size_shrinks_after_each_loss(self):
        b = U.Bench(self)
        t = b.lose(1, 1)                                                 # equity 950000, level 1
        body = b.decide(U.cand("C1", "M1", t))
        self.assertEqual((body["sizing"]["throttle_level"], body["sizing"]["throttle_multiplier"],
                          body["sizing"]["stake_units"], body["sizing"]["limit_units"]), (1, "3/4", 35_625, 47_500))
        b.settle(t + 1, "M1", "NO")                                      # equity 914375, level 2
        body = b.decide(U.cand("C2", "M2", t + 2))
        self.assertEqual((body["sizing"]["throttle_level"], body["sizing"]["stake_units"]), (2, 25_716))

    def test_a_win_does_not_restore_the_size(self):
        b = U.Bench(self)
        t = b.lose(1, 2)
        b.decide(U.cand("W", "M5", t))
        b.settle(t + 1, "M5", "YES")
        self.assertEqual(b.engine.throttle.level, 2)
        body = b.decide(U.cand("N", "M6", t + 2))
        self.assertEqual(body["sizing"]["throttle_level"], 2)

    def test_a_refused_candidate_does_not_step_the_throttle_down(self):
        b = U.Bench(self)
        t = b.lose(1, 1)                                                 # the loss is settled at t - 1
        later = t - 1 + 20
        stale = b.decide(U.cand("S", "M5", later, price_as_of=later - 9))
        self.assertEqual(stale["decision"], "REJECTED")
        self.assertEqual(b.engine.throttle.level, 1)
        weak = b.decide(U.cand("K", "M6", later, signal_ppm=470_000, confidence=1_000_000))   # net edge 60000 > bar
        self.assertEqual((weak["decision"], weak["sizing"]["throttle_level"]), ("EXECUTED", 0))
        self.assertEqual(b.engine.throttle.level, 0)

    def test_an_edge_between_threshold_and_bar_executes_at_the_reduced_size(self):
        b = U.Bench(self)
        t = b.lose(1, 1)
        later = t + 40
        body = b.decide(U.cand("C", "M5", later, signal_ppm=440_000, confidence=1_000_000))   # net edge 30000
        self.assertEqual((body["decision"], body["sizing"]["throttle_level"], body["sizing"]["throttle_multiplier"]),
                         ("EXECUTED", 1, "3/4"))
        self.assertEqual(U.audit(b.path, b.markets)["mismatches"], [])


if __name__ == "__main__":
    unittest.main()

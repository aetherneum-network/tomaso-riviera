"""The ledger: append-only hash chain, and the last line of defence against the never-event (claim A6).

The never-event of this pack is a ledger row that violates a limit (position size, circuit breaker,
threshold). These tests try to write one in every way the author could think of - through the public
API, through a sabotaged engine, through random and hostile input - and check that the ledger refuses,
that a refusal leaves no trace, and that the independent audit finds no violating row afterwards.
"""
from __future__ import annotations

import dataclasses
import json
import random
import unittest
from fractions import Fraction
from unittest import mock

from corpus import reference_drawdown
from harness import ledger as ledger_mod, signal, sizing, validators
from harness.exact import PPM, canonical
from harness.ledger import ChainError, Ledger, LedgerError, LimitViolation, row_hash, verify

from tests import _util as U

EDGE_AT_THRESHOLD = {"signal_ppm": 460_000, "confidence": 500_000}      # net edge exactly 20000
KELLY_BOUND = {"signal_ppm": 600_000, "confidence": 500_000}            # limit 38135, below the 5% cap


def busy(test) -> U.Bench:
    """Eleven rows: three candidates decided, two markets settled, a fourth candidate decided."""
    b = U.Bench(test)
    b.decide(U.cand("C0", "M0", 1))
    b.decide(U.cand("C1", "M1", 2, **EDGE_AT_THRESHOLD))
    b.decide(U.cand("C2", "M2", 3))
    b.settle(5, "M0", "YES")
    b.settle(6, "M2", "NO")
    b.decide(U.cand("C3", "M3", 7))
    return b


def rechain(rows: list[dict]) -> list[dict]:
    """What a forger would do: renumber the rows and recompute every hash."""
    prev = ledger_mod.GENESIS_PREV
    for seq, row in enumerate(rows):
        row["seq"], row["prev"] = seq, prev
        row["hash"] = row_hash(row)
        prev = row["hash"]
    return rows


def write_rows(rows: list[dict], name: str = "tampered"):
    path = U.scratch(name) / "ledger.jsonl"
    path.write_bytes("".join(canonical(r) + "\n" for r in rows).encode("ascii"))
    return path


class Chain(unittest.TestCase):
    def test_an_honest_ledger_verifies_against_its_published_head_and_anchors(self):
        b = busy(self)
        published = b.ledger.anchors()
        before = b.path.read_bytes()
        result = verify(b.path, expect_head=published["head"], anchors=published["anchors"])
        self.assertEqual((result["ok"], result["rows"], result["head"]), (True, 11, b.ledger.head))
        self.assertEqual(b.path.read_bytes(), before)                   # verification never writes
        rows = b.rows()
        self.assertEqual([r["seq"] for r in rows], list(range(11)))
        self.assertEqual({r["mode"] for r in rows}, {"PAPER"})
        self.assertEqual(rows[0]["prev"], "0" * 64)
        for previous, row in zip(rows, rows[1:]):
            self.assertEqual(row["prev"], previous["hash"])
        self.assertTrue(before.endswith(b"\n") and b"\r" not in before)

    def test_the_file_is_only_ever_appended_to(self):
        b = busy(self)
        self.assertEqual(b.ledger._fh.mode, "ab")
        snapshot = b.path.read_bytes()
        b.decide(U.cand("C4", "M4", 8))
        self.assertTrue(b.path.read_bytes().startswith(snapshot))
        public = sorted(n for n in dir(Ledger) if not n.startswith("_"))
        self.assertEqual(public, ["anchors", "baseline", "candidate", "close", "create", "decision",
                                  "divergence_check", "load", "pause", "rearm", "refused_input", "settle",
                                  "write_anchors"])                     # nothing that edits or deletes

    def test_an_existing_ledger_is_never_overwritten(self):
        b = busy(self)
        before = b.path.read_bytes()
        with self.assertRaises(LedgerError):
            Ledger.create(b.path, U.genesis(b.rules))
        self.assertEqual(b.path.read_bytes(), before)

    def test_a_genesis_without_limits_is_refused(self):
        rules = U.load_rules()
        g = U.genesis(rules)
        del g["limits"]["cap_ppm"]
        with self.assertRaises(LedgerError):
            Ledger.create(U.scratch("genesis") / "ledger.jsonl", g)
        for key in ("limits", "costs", "min_approvals"):
            g = U.genesis(rules)
            del g[key]
            with self.assertRaises(LedgerError):
                Ledger.create(U.scratch("genesis") / "ledger.jsonl", g)

    def test_rows_cannot_be_backdated_or_written_after_close(self):
        b = busy(self)
        with self.assertRaises(LedgerError) as ctx:
            b.ledger.candidate(6, U.cand("LATE", "M9", 6))
        self.assertIn("BACKDATED_ROW", str(ctx.exception))
        with self.assertRaises(LedgerError):
            b.ledger._commit("WITHDRAWAL", 9, {})
        rows = b.ledger.rows
        b.ledger.close()
        with self.assertRaises(LedgerError):
            b.ledger.candidate(9, U.cand("C9", "M9", 9))
        self.assertEqual(len(b.rows()), rows)

    def test_a_float_fails_the_run_and_writes_nothing(self):
        b = busy(self)
        size = b.path.stat().st_size
        with self.assertRaises(TypeError):
            b.decide(U.cand("F", "M9", 8, price_ppm=400000.5))
        self.assertEqual(b.path.stat().st_size, size)
        self.assertTrue(verify(b.path)["ok"])

    def test_a_ledger_can_be_reopened_and_continued(self):
        b = busy(self)
        state = b.state
        b.ledger.close()
        again = Ledger.load(b.path)
        self.addCleanup(again.close)
        for name in ("cash_units", "open_stake_units", "peak_units", "halted", "paused", "settled", "decided"):
            self.assertEqual(getattr(again.state, name), getattr(state, name), name)
        self.assertEqual((again.rows, again.head), (11, b.ledger.head))
        again.candidate(9, U.cand("C9", "M9", 9))
        again.close()
        self.assertEqual((verify(b.path)["ok"], verify(b.path)["rows"]), (True, 12))

    def test_the_account_is_derived_from_the_rows(self):
        b = busy(self)
        audit = U.audit(b.path, b.markets)
        self.assertEqual(audit["final_equity_units"], b.state.equity_units)
        self.assertEqual((audit["mismatches"], audit["violations"]), ([], []))
        # C0 won: 50000 staked at 0.41 pays 121951; C2 lost 50000; C3 is open
        self.assertEqual(b.state.equity_units, 1_000_000 - 50_000 + 121_951 - 50_000)


class Tampering(unittest.TestCase):
    def setUp(self):
        self.bench = busy(self)
        self.published = self.bench.ledger.anchors()
        self.bench.ledger.close()
        self.rows = self.bench.rows()

    def first(self, rows, **kwargs):
        result = verify(write_rows(rows), **kwargs)
        return result["ok"], result["first_bad_seq"], result["reason"]

    def test_a_changed_amount(self):
        self.rows[2]["body"]["sizing"]["stake_units"] = 1
        path = write_rows(self.rows)
        result = verify(path)
        self.assertEqual((result["ok"], result["first_bad_seq"], result["reason"]), (False, 2, "ROW_HASH_MISMATCH"))
        _rows, chain = reference_drawdown.read_rows(path)               # the independent reader agrees
        self.assertEqual((chain["ok"], chain["first_bad_seq"], chain["reason"]), (False, 2, "HASH"))
        with self.assertRaises(ChainError):
            Ledger.load(path)

    def test_a_changed_row_with_its_own_hash_recomputed(self):
        self.rows[2]["body"]["sizing"]["stake_units"] = 1
        self.rows[2]["hash"] = row_hash(self.rows[2])
        self.assertEqual(self.first(self.rows), (False, 3, "PREV_HASH_MISMATCH"))

    def test_a_deleted_row(self):
        del self.rows[4]
        self.assertEqual(self.first(self.rows), (False, 4, "SEQ_OUT_OF_ORDER"))

    def test_reordered_rows(self):
        self.rows[3], self.rows[4] = self.rows[4], self.rows[3]
        self.assertEqual(self.first(self.rows), (False, 3, "SEQ_OUT_OF_ORDER"))

    def test_an_inserted_row(self):
        forged = dict(self.rows[3], seq=4)
        self.rows.insert(4, forged)
        ok, seq, _reason = self.first(self.rows)
        self.assertEqual((ok, seq), (False, 4))

    def test_a_full_rewrite_is_caught_only_by_the_published_head(self):
        self.rows[2]["body"]["sizing"]["stake_units"] = 1
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (True, None, None))     # the limit of a hash chain, stated
        self.assertEqual(self.first(self.rows, expect_head=self.published["head"]),
                         (False, 10, "HEAD_DIFFERS_FROM_PUBLISHED_HEAD"))

    def test_a_rewritten_genesis_is_caught_by_the_anchors(self):
        self.rows[0]["body"]["limits"]["cap_ppm"] = 900_000
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (True, None, None))
        self.assertEqual(self.first(self.rows, anchors=self.published["anchors"]), (False, 0, "ANCHOR_MISMATCH"))

    def test_a_row_not_labelled_paper(self):
        self.rows[2]["mode"] = "LIVE"
        rechain(self.rows)
        path = write_rows(self.rows)
        self.assertEqual(verify(path)["reason"], "NOT_LABELLED_PAPER")
        self.assertEqual(reference_drawdown.read_rows(path)[1]["reason"], "NOT_PAPER")

    def test_an_unknown_kind_of_row(self):
        self.rows[7]["kind"] = "WITHDRAWAL"
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (False, 7, "UNKNOWN_KIND"))

    def test_time_going_backwards(self):
        self.rows[9]["t"] = self.rows[10]["t"] = 1
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (False, 9, "TIME_GOES_BACKWARDS"))

    def test_a_candidate_added_after_the_outcome(self):
        late = {"seq": 0, "t": 8, "kind": "CANDIDATE", "mode": "PAPER", "body": U.cand("LATE", "M0", 8), "prev": ""}
        self.rows.append(late)
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (False, 11, "CANDIDATE_AFTER_OUTCOME"))

    def test_a_decision_without_its_candidate(self):
        del self.rows[9]
        rechain(self.rows)
        self.assertEqual(self.first(self.rows), (False, 9, "DECISION_WITHOUT_CANDIDATE"))

    def test_an_execution_placed_after_the_outcome(self):
        order = [0, 1, 2, 3, 4, 5, 8, 6, 7, 9, 10]                      # settlement of M2 before the decision on C2
        rows = [self.rows[i] for i in order]
        rows[7]["t"] = rows[8]["t"] = 6
        rechain(rows)
        self.assertEqual(self.first(rows), (False, 7, "EXECUTION_AFTER_OUTCOME"))

    def test_a_line_that_is_not_canonical(self):
        path = U.scratch("tampered") / "ledger.jsonl"
        lines = [canonical(r) for r in self.rows]
        lines[5] = json.dumps(self.rows[5], sort_keys=True)             # same content, other spacing
        path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
        result = verify(path)
        self.assertEqual((result["first_bad_seq"], result["reason"]), (5, "NOT_CANONICAL_JSON"))
        lines[5] = "not json at all"
        path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
        self.assertEqual(verify(path)["reason"], "NOT_CANONICAL_JSON")

    def test_a_truncated_file(self):
        raw = self.bench.path.read_bytes()
        path = U.scratch("tampered") / "ledger.jsonl"
        path.write_bytes(raw[:-1])                                       # last newline missing
        result = verify(path)
        self.assertEqual((result["ok"], result["first_bad_seq"], result["reason"]), (False, 10, "LAST_LINE_NOT_TERMINATED"))
        path.write_bytes(raw[:-40])                                      # last row cut in the middle
        result = verify(path)
        self.assertEqual((result["ok"], result["first_bad_seq"]), (False, 10))
        with self.assertRaises(ChainError):
            Ledger.load(path)


class NeverEvent(unittest.TestCase):
    """Every way to write an EXECUTED row that violates a limit, through the ledger's own API."""

    def test_the_forging_helper_can_write_an_honest_row(self):
        b = U.Bench(self)
        self.assertIsNone(b.forge(U.cand("C0", "M0", 1)))               # so the refusals below are not vacuous
        self.assertEqual(b.state.open_stake_units, 50_000)

    # ----------------------------------------------------------------- threshold
    def test_edge_equal_to_the_threshold(self):
        b = U.Bench(self)
        self.assertEqual(b.forge(U.cand("C0", "M0", 1, **EDGE_AT_THRESHOLD)), "EDGE_NOT_ABOVE_THRESHOLD")
        self.assertEqual(b.forge(U.cand("C1", "M1", 1, signal_ppm=300_000)), "EDGE_NOT_ABOVE_THRESHOLD")

    def test_an_incomplete_candidate(self):
        b = U.Bench(self)
        for i, over in enumerate(({"confidence_ppm": None}, {"cost": None}, {"price_ppm": None}, {"side": "MAYBE"},
                                  {"prior_ppm": "0.4"}, {"cost": {"fee_ppm": 0, "slippage_ppm": 0, "latency_steps": 7}})):
            def invent(body):
                body["decomposition"] = {"p_hat_ppm": 900_000, "price_ppm": 400_000, "cost_ppm": 0,
                                         "q_eff_ppm": 400_000, "net_edge_ppm": 500_000}
                body["sizing"] = {"stake_units": 50_000, "equity_units": 1_000_000, "throttle_multiplier": "1/1",
                                  "payout_if_win_units": 125_000}
            self.assertEqual(b.forge(U.cand(f"C{i}", f"M{i}", 1, **over), invent), "EXECUTION_OF_INCOMPLETE_CANDIDATE", over)

    def test_an_embellished_decomposition(self):
        b = U.Bench(self)
        for i, key in enumerate(("p_hat_ppm", "price_ppm", "cost_ppm", "q_eff_ppm", "net_edge_ppm")):
            def embellish(body, key=key):
                body["decomposition"][key] += 1
            self.assertEqual(b.forge(U.cand(f"C{i}", f"M{i}", 1), embellish), "DECOMPOSITION_MISMATCH", key)

        def rosy(body):                                                 # a weak candidate dressed as a strong one
            body["decomposition"].update(p_hat_ppm=800_000, net_edge_ppm=390_000)
        self.assertEqual(b.forge(U.cand("W", "M9", 1, **EDGE_AT_THRESHOLD), rosy), "DECOMPOSITION_MISMATCH")

    # ----------------------------------------------------------------- validators
    def test_a_veto_or_a_missing_quorum(self):
        b = U.Bench(self)
        self.assertEqual(b.forge(U.cand("C0", "M0", 10, price_as_of=6)), "EXECUTION_WITH_VETO")
        self.assertEqual(b.forge(U.cand("C1", "M1", 10, complement_price_ppm=None, confirm_ppm=None)),
                         "EXECUTION_WITHOUT_QUORUM")

        def no_ballot(body):
            body["votes"] = {}
        self.assertEqual(b.forge(U.cand("C2", "M2", 10), no_ballot), "EXECUTION_WITHOUT_QUORUM")

    # ----------------------------------------------------------------- position size
    def test_one_unit_above_the_limit(self):
        b = U.Bench(self)

        def plus(units):
            def change(body):
                s = body["sizing"]
                s["stake_units"] += units
                s["payout_if_win_units"] = s["stake_units"] * PPM // body["decomposition"]["q_eff_ppm"]
            return change
        self.assertEqual(b.forge(U.cand("C0", "M0", 1), plus(1)), "SIZE_OVER_LIMIT")            # cap-bound: 50001
        self.assertEqual(b.forge(U.cand("C1", "M1", 1, **KELLY_BOUND), plus(1)), "SIZE_OVER_LIMIT")   # 38136
        self.assertEqual(b.forge(U.cand("C2", "M2", 1), plus(950_000)), "SIZE_OVER_LIMIT")      # the whole bankroll
        self.assertIsNone(b.forge(U.cand("C3", "M3", 1, **KELLY_BOUND), plus(0)))               # exactly the limit
        self.assertEqual(b.rows()[-1]["body"]["sizing"]["stake_units"], 38_135)

    def test_a_size_computed_on_an_equity_the_ledger_does_not_have(self):
        b = U.Bench(self)

        def inflate(body):
            body["sizing"].update(equity_units=10_000_000, stake_units=500_000, limit_units=500_000)
        self.assertEqual(b.forge(U.cand("C0", "M0", 1), inflate), "STALE_EQUITY")

    def test_a_stake_that_is_not_a_positive_integer(self):
        b = U.Bench(self)
        for i, value in enumerate((0, -1, None, "50000", True, 49_999.5, [50_000])):
            def change(body, value=value):
                body["sizing"]["stake_units"] = value
            self.assertEqual(b.forge(U.cand(f"C{i}", f"M{i}", 1), change), "STAKE_NOT_POSITIVE", value)

        def no_sizing(body):
            body["sizing"] = None
        self.assertEqual(b.forge(U.cand("C9", "M9", 1), no_sizing), "STAKE_NOT_POSITIVE")

    def test_a_throttle_multiplier_that_enlarges(self):
        b = U.Bench(self)
        for i, value in enumerate(("4/3", "0/1", "-1/2", "1/0", "x", None, 1)):
            def change(body, value=value):
                body["sizing"]["throttle_multiplier"] = value
            self.assertEqual(b.forge(U.cand(f"C{i}", f"M{i}", 1), change), "THROTTLE_MULTIPLIER_INVALID", value)

    def test_a_payout_that_does_not_follow_from_the_stake(self):
        b = U.Bench(self)

        def generous(body):
            body["sizing"]["payout_if_win_units"] += 1
        self.assertEqual(b.forge(U.cand("C0", "M0", 1), generous), "PAYOUT_MISMATCH")

    def test_a_second_position_in_the_same_market(self):
        b = U.Bench(self)
        self.assertIsNone(b.forge(U.cand("C0", "M0", 1)))
        self.assertEqual(b.forge(U.cand("C1", "M0", 2)), "POSITION_ALREADY_OPEN")

    def test_the_market_cap_holds_even_if_two_positions_were_allowed(self):
        b = U.Bench(self, max_open_positions_per_market=2)
        self.assertIsNone(b.forge(U.cand("C0", "M0", 1)))
        self.assertEqual(b.forge(U.cand("C1", "M0", 2)), "MARKET_CAP_EXCEEDED")

    def test_portfolio_exposure(self):
        b = U.Bench(self)
        for i in range(6):
            self.assertIsNone(b.forge(U.cand(f"C{i}", f"M{i}", 1)), i)
        self.assertEqual(b.forge(U.cand("C6", "M6", 2)), "PORTFOLIO_EXPOSURE_EXCEEDED")

    def test_portfolio_exposure_after_a_ten_percent_drawdown(self):
        b = U.Bench(self)
        t = b.lose(1, 2)
        self.assertEqual((b.state.equity_units, b.state.drawdown_ppm), (900_000, 100_000))
        for i in range(10, 13):
            self.assertIsNone(b.forge(U.cand(f"C{i}", f"M{i}", t)), i)  # 3 x 45000 = 15% of 900000
        self.assertEqual(b.forge(U.cand("C13", "M13", t)), "PORTFOLIO_EXPOSURE_EXCEEDED")

    def test_cash_is_a_last_guard_behind_the_exposure_limit(self):
        # Unreachable with the real limits (exposure <= 30%): shown with the exposure limit lifted to 200%.
        b = U.Bench(self, max_portfolio_exposure_ppm=2_000_000)
        for i in range(20):
            self.assertIsNone(b.forge(U.cand(f"C{i}", f"M{i}", 1)), i)
        self.assertEqual(b.state.cash_units, 0)
        self.assertEqual(b.forge(U.cand("C20", "M20", 2)), "INSUFFICIENT_CASH")

    # ----------------------------------------------------------------- circuit breaker and pause
    def test_no_execution_while_the_breaker_is_halted(self):
        b = U.Bench(self)
        t = b.lose(1, 4)
        self.assertTrue(b.state.halted)
        self.assertEqual(b.state.drawdown_ppm, 200_000)
        self.assertEqual(b.forge(U.cand("C9", "M9", t)), "EXECUTION_WHILE_HALTED")
        body = b.decide(U.cand("C10", "M10", t))                         # and the honest engine refuses too
        self.assertEqual((body["decision"], body["reason"], body["rule_id"]), ("REJECTED", "BREAKER_HALTED", "G02"))

    def test_no_execution_while_the_signal_is_paused(self):
        b = U.Bench(self)
        b.ledger.pause(1, {"reason": "test"})
        self.assertEqual(b.forge(U.cand("C0", "M0", 1)), "EXECUTION_WHILE_PAUSED")
        body = b.decide(U.cand("C1", "M1", 1))
        self.assertEqual((body["decision"], body["reason"]), ("REJECTED", "SIGNAL_PAUSED"))

    # ----------------------------------------------------------------- order of events
    def test_no_execution_once_the_outcome_is_known(self):
        b = U.Bench(self)
        c = U.cand("C0", "M0", 1)
        b.ledger.candidate(1, c)
        b.settle(2, "M0", "YES")
        self.assertEqual(b.forge(c, log_candidate=False), "EXECUTION_AFTER_OUTCOME")
        with self.assertRaises(LimitViolation) as ctx:
            b.ledger.candidate(3, U.cand("C1", "M0", 3))
        self.assertEqual(ctx.exception.code, "CANDIDATE_AFTER_OUTCOME")

    def test_a_decision_needs_its_candidate_and_only_one_decision(self):
        b = U.Bench(self)
        self.assertEqual(b.forge(U.cand("GHOST", "M0", 1), log_candidate=False), "DECISION_WITHOUT_CANDIDATE")
        c = U.cand("C0", "M0", 1)
        self.assertIsNone(b.forge(c))
        self.assertEqual(b.forge(c, log_candidate=False), "ALREADY_DECIDED")
        with self.assertRaises(LedgerError) as ctx:
            b.ledger.candidate(1, c)
        self.assertIn("DUPLICATE_CANDIDATE", str(ctx.exception))

    def test_a_decision_must_be_about_its_own_candidate(self):
        b = U.Bench(self)

        def other_market(body):
            body["market_id"] = "M7"

        def other_side(body):
            body["side"] = "NO"
        self.assertEqual(b.forge(U.cand("C0", "M0", 1), other_market), "DECISION_DOES_NOT_MATCH_CANDIDATE")
        self.assertEqual(b.forge(U.cand("C1", "M1", 1), other_side), "DECISION_DOES_NOT_MATCH_CANDIDATE")

    def test_every_refusal_code_of_the_guard_is_exercised_here(self):
        source = (U.ROOT / "harness" / "ledger.py").read_text(encoding="utf-8")
        import re
        codes = set(re.findall(r'LimitViolation\("([A-Z_]+)"', source))
        mine = (U.ROOT / "tests" / "test_ledger.py").read_text(encoding="utf-8")
        self.assertEqual(sorted(c for c in codes if f'"{c}"' not in mine), [])
        self.assertGreaterEqual(len(codes), 20)


class SabotagedEngine(unittest.TestCase):
    """The ledger does not trust the engine: a bug upstream cannot produce a violating row."""

    def assert_refused(self, b: U.Bench, c: dict, code: str) -> None:
        rows = b.ledger.rows
        open_before = b.state.open_stake_units
        with self.assertRaises(LimitViolation) as ctx:
            b.decide(c)
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(b.ledger.rows, rows + 1)                        # the candidate row, and no decision
        self.assertEqual(b.rows()[-1]["kind"], "CANDIDATE")
        self.assertEqual(b.state.open_stake_units, open_before)
        self.assertTrue(verify(b.path)["ok"])

    def test_a_sizing_bug_of_one_unit(self):
        b = U.Bench(self)
        real = sizing.size

        def greedy(p, q, equity, mult, cfg):
            s = real(p, q, equity, mult, cfg)
            return dataclasses.replace(s, stake_units=s.stake_units + 1,
                                       payout_if_win_units=sizing.payout_if_win(s.stake_units + 1, q))
        with mock.patch("harness.engine.sizing.size", greedy):
            self.assert_refused(b, U.cand("C0", "M0", 1, **KELLY_BOUND), "SIZE_OVER_LIMIT")

    def test_a_large_sizing_bug_is_stopped_twice(self):
        real = sizing.size

        def triple(p, q, equity, mult, cfg):
            s = real(p, q, equity, mult, cfg)
            return dataclasses.replace(s, stake_units=s.stake_units * 3,
                                       payout_if_win_units=sizing.payout_if_win(s.stake_units * 3, q))
        b = U.Bench(self)
        with mock.patch("harness.engine.sizing.size", triple):
            body = b.decide(U.cand("C0", "M0", 1))                       # first by the risk engine ...
            self.assertEqual((body["decision"], body["reason"]), ("REJECTED", "RISK_MARKET_CAP"))
            with mock.patch("harness.engine.risk.veto", lambda *a, **k: None):
                self.assert_refused(b, U.cand("C1", "M1", 1), "SIZE_OVER_LIMIT")   # ... then by the ledger

    def test_a_risk_engine_that_never_vetoes(self):
        b = U.Bench(self)
        with mock.patch("harness.engine.risk.veto", lambda *a, **k: None):
            for i in range(6):
                self.assertEqual(b.decide(U.cand(f"C{i}", f"M{i}", 1))["decision"], "EXECUTED")
            self.assert_refused(b, U.cand("C6", "M0", 2), "POSITION_ALREADY_OPEN")
            self.assert_refused(b, U.cand("C7", "M7", 2), "PORTFOLIO_EXPOSURE_EXCEEDED")

    def test_a_signal_that_flatters_itself(self):
        b = U.Bench(self)
        real = signal.decompose

        def rosy(c, rules):
            d = real(c, rules)
            return dataclasses.replace(d, p_hat_ppm=d.p_hat_ppm + 200_000, edge_ppm=d.edge_ppm + 200_000,
                                       net_edge_ppm=d.net_edge_ppm + 200_000, gate_pass=True)
        with mock.patch("harness.engine.signal.decompose", rosy):
            self.assert_refused(b, U.cand("C0", "M0", 1, **EDGE_AT_THRESHOLD), "DECOMPOSITION_MISMATCH")

    def test_a_gate_that_executes_everything(self):
        b = U.Bench(self)
        b.rules.gate["decision_rules"][:] = [{"id": "X", "when": [], "decision": "EXECUTED", "reason": "X"}]
        self.assert_refused(b, U.cand("C0", "M0", 1, confidence_ppm=None), "EXECUTION_OF_INCOMPLETE_CANDIDATE")
        self.assert_refused(b, U.cand("C1", "M1", 1, **EDGE_AT_THRESHOLD), "EDGE_NOT_ABOVE_THRESHOLD")
        self.assert_refused(b, U.cand("C2", "M2", 10, price_as_of=6), "EXECUTION_WITH_VETO")
        self.assert_refused(b, U.cand("C3", "M3", 10, complement_price_ppm=None, confirm_ppm=None),
                            "EXECUTION_WITHOUT_QUORUM")
        t = b.lose(11, 4, start=10)
        self.assertTrue(b.state.halted)
        self.assert_refused(b, U.cand("C4", "M4", t), "EXECUTION_WHILE_HALTED")

    def test_a_gate_that_ignores_the_pause(self):
        b = U.Bench(self)
        b.rules.gate["decision_rules"][:] = [{"id": "X", "when": [], "decision": "EXECUTED", "reason": "X"}]
        b.ledger.pause(1, {"reason": "test"})
        self.assert_refused(b, U.cand("C0", "M0", 1), "EXECUTION_WHILE_PAUSED")

    def test_a_forged_ballot_passes_the_ledger_and_is_caught_by_the_independent_audit(self):
        # Known limit, pinned here so that it cannot change silently: the ledger re-derives the numbers of
        # a decision from the candidate row, but it reads the validators' votes as recorded.
        b = U.Bench(self)
        approve_all = validators.Tally(votes={n: "APPROVE:FORGED" for n in b.rules.validators["quorum"]["validators"]},
                                       vetoes=[], approvals=4, quorum_met=True)
        with mock.patch("harness.engine.validators.evaluate", lambda *a, **k: approve_all):
            body = b.decide(U.cand("C0", "M0", 10, price_as_of=1))       # a stale price: must be vetoed
        self.assertEqual(body["decision"], "EXECUTED")
        audit = U.audit(b.path, b.markets)
        self.assertEqual([(m["what"], m["got"], m["want"]) for m in audit["mismatches"]],
                         [("decision", "EXECUTED", "VALIDATOR_VETO")])


class Fuzz(unittest.TestCase):
    GARBAGE = (None, "x", -1, True, 10**12, [], {}, "")
    FIELDS = ("side", "price_ppm", "price_as_of", "complement_price_ppm", "prior_ppm", "signal_ppm",
              "confidence_ppm", "confirm_ppm", "cost", "conviction")

    def candidate(self, rng: random.Random, cid: str, market: str, t: int) -> dict:
        price = rng.randrange(1, PPM) if rng.randrange(4) == 0 else rng.randrange(200_000, 800_000)
        c = U.cand(cid, market, t, price=price, side=rng.choice(("YES", "YES", "NO")),
                   signal_ppm=rng.randrange(0, PPM + 1), confidence=rng.randrange(0, PPM + 1),
                   conviction=rng.choice(("low", "high", "ALL IN", None)))
        c["prior_ppm"] = min(PPM, max(0, price + rng.randrange(-30_000, 30_001)))
        c["price_as_of"] = t - rng.randrange(0, 5)
        c["complement_price_ppm"] = PPM - price + rng.randrange(-60_000, 60_001)
        c["confirm_ppm"] = price + rng.randrange(-70_000, 70_001)
        c["cost"] = {"fee_ppm": rng.randrange(0, 20_000), "slippage_ppm": rng.randrange(0, 20_000),
                     "latency_steps": rng.randrange(0, 8)}
        if rng.randrange(5) == 0:
            c[rng.choice(self.FIELDS)] = rng.choice(self.GARBAGE)
        if rng.randrange(40) == 0:
            c["cost"] = {"fee_ppm": rng.choice(self.GARBAGE), "slippage_ppm": 0, "latency_steps": 0}
        return c

    def world(self, seed: int, n_markets: int = 120):
        rng = random.Random(seed)
        b = U.Bench(self, n_markets=n_markets)
        for i, market in enumerate(b.markets.values()):
            market["resolves_at"] = 30 + 7 * i
        by_time = {m["resolves_at"]: m["market_id"] for m in b.markets.values()}
        return rng, b, by_time

    @staticmethod
    def pick_market(rng: random.Random, t: int, n_markets: int = 120) -> str:
        """Mostly a market that is still open; now and then any market, settled ones included."""
        first_open = max(0, (t - 30) // 7 + 1)
        if first_open < n_markets and rng.randrange(10):
            return f"M{rng.randrange(first_open, n_markets)}"
        return f"M{rng.randrange(n_markets)}"

    def test_random_and_malformed_candidates_through_the_engine(self):
        rng, b, by_time = self.world(20260930)
        executed = 0
        for t in range(1, 30 + 7 * 120 + 2):
            if t in by_time:
                b.settle(t, by_time[t], "YES" if rng.randrange(10) < 7 else "NO")
            for k in range(rng.randrange(0, 4)):
                market = self.pick_market(rng, t)
                cid = f"F{t}-{k}" if rng.randrange(25) else "F1-0"       # now and then a duplicate id
                row = b.engine.on_candidate(self.candidate(rng, cid, market, t))
                executed += row["kind"] == "DECISION" and row["body"]["decision"] == "EXECUTED"
        audit = U.audit(b.path, b.markets)
        self.assertGreater(audit["candidates"], 1_000)
        self.assertGreater(executed, 20)
        self.assertEqual(audit["violations"], [])
        self.assertEqual(audit["mismatches"], [])
        self.assertEqual(audit["max_stake_deviation_units"], 0)
        self.assertTrue(audit["chain"]["ok"] and verify(b.path)["ok"])

    def test_hostile_rows_never_get_past_the_ledger(self):
        rng, b, by_time = self.world(20260929)

        def scaled(factor):
            def change(body):
                s = body["sizing"]
                if s is not None:
                    s["stake_units"] = s["stake_units"] * factor.numerator // factor.denominator
                    s["payout_if_win_units"] = s["stake_units"] * PPM // body["decomposition"]["q_eff_ppm"]
            return change

        def on_inflated_equity(body):
            s = body["sizing"]
            if s is not None:
                s.update(equity_units=s["equity_units"] * 10, stake_units=s["stake_units"] * 10)

        def forged_ballot(body):
            body["votes"] = {name: "APPROVE:FORGED" for name in body["votes"]}
            body["approvals"], body["vetoes"] = 4, []

        def invented_numbers(body):
            body["decomposition"] = {"prior_ppm": 400_000, "signal_ppm": 900_000, "confidence_ppm": 800_000,
                                     "p_hat_ppm": 800_000, "price_ppm": 400_000, "cost_ppm": 10_000,
                                     "q_eff_ppm": 410_000, "edge_ppm": 400_000, "net_edge_ppm": 390_000,
                                     "threshold_ppm": 20_000, "gate_pass": True}
            body["sizing"] = {"kelly": "39/59", "kelly_multiplier": "1/4", "cap_ppm": 50_000, "fraction_used": "1/20",
                              "throttle_level": 0, "throttle_multiplier": "1/1",
                              "equity_units": b.state.equity_units, "limit_units": 50_000, "stake_units": 50_000,
                              "payout_if_win_units": 121_951}

        def enlarging_throttle(body):
            if body["sizing"] is not None:
                body["sizing"]["throttle_multiplier"] = "4/3"

        hostile = (None, scaled(Fraction(1, 2)), scaled(Fraction(2)), scaled(Fraction(21, 20)), on_inflated_equity,
                   forged_ballot, invented_numbers, enlarging_throttle)
        refused: dict[str, int] = {}
        accepted = 0
        for t in range(1, 30 + 7 * 120 + 2):
            if t in by_time:
                b.settle(t, by_time[t], "YES" if rng.randrange(10) < 8 else "NO")
            for k in range(rng.randrange(0, 3)):
                c = self.candidate(rng, f"H{t}-{k}", self.pick_market(rng, t), t)
                try:
                    code = b.forge(c, rng.choice(hostile))
                except LimitViolation as exc:                            # the candidate itself was refused
                    code = exc.code
                if code is None:
                    accepted += 1
                else:
                    refused[code] = refused.get(code, 0) + 1
        audit = U.audit(b.path, b.markets)
        self.assertEqual(audit["violations"], [])                        # the never-event did not happen
        self.assertTrue(audit["chain"]["ok"] and verify(b.path)["ok"])
        self.assertGreater(accepted, 10)
        self.assertGreater(sum(refused.values()), 500)
        for code in ("SIZE_OVER_LIMIT", "EDGE_NOT_ABOVE_THRESHOLD", "EXECUTION_WITH_VETO", "STALE_EQUITY",
                     "DECOMPOSITION_MISMATCH", "THROTTLE_MULTIPLIER_INVALID", "EXECUTION_OF_INCOMPLETE_CANDIDATE"):
            self.assertIn(code, refused)
        # every accepted row respects the hard limit, recomputed here from the rows alone
        equity_check = [r for r in audit["executed_rows"]]
        self.assertEqual(len(equity_check), accepted)
        for e in equity_check:
            self.assertLessEqual(e["stake_units"] * 20, e["equity_units"])


if __name__ == "__main__":
    unittest.main()

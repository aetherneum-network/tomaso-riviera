"""The execute condition and the ordered rule files (claim A1)."""
from __future__ import annotations

import json
import unittest

from harness import costs, rules as rules_mod, signal
from harness.rules import RuleError, first_match, holds

from tests import _util as U


def facts(**over) -> dict:
    base = {"missing": None, "halted": False, "paused": False, "veto_count": 0, "quorum_met": True,
            "net_edge_minus_threshold_ppm": 1, "stake_units": 100, "risk_veto": None}
    base.update(over)
    return base


class Conditions(unittest.TestCase):
    def test_an_unknown_value_never_satisfies_a_comparison(self):
        for op in (">", ">=", "<", "<=", "==", "!="):
            self.assertFalse(holds(["x", op, 0], {"x": None}), op)
        self.assertTrue(holds(["x", "is_null"], {"x": None}))
        self.assertFalse(holds(["x", "not_null"], {"x": None}))

    def test_comparisons(self):
        f = {"x": 5}
        self.assertTrue(holds(["x", ">", 4], f) and holds(["x", ">=", 5], f) and holds(["x", "<=", 5], f))
        self.assertFalse(holds(["x", ">", 5], f) or holds(["x", "<", 5], f) or holds(["x", "!=", 5], f))

    def test_malformed_conditions_stop_the_run(self):
        for cond, f in ((["x", "~", 1], {"x": 1}), (["y", ">", 1], {"x": 1}), (["x", ">"], {"x": 1}),
                        (["x"], {"x": 1}), (["x", ">", True], {"x": 1}), ("x > 1", {"x": 1})):
            with self.assertRaises(RuleError, msg=str(cond)):
                holds(cond, f)

    def test_a_list_without_default_is_an_error_not_a_silent_pass(self):
        with self.assertRaises(RuleError):
            first_match([{"id": "A", "when": [["x", ">", 1]]}], {"x": 0})


class GateRules(unittest.TestCase):
    def setUp(self):
        self.rules = U.load_rules()
        self.gate = self.rules.gate["decision_rules"]

    def decide(self, **over):
        rule = first_match(self.gate, facts(**over))
        return rule["decision"], rule["id"]

    def test_everything_in_order_executes(self):
        self.assertEqual(self.decide(), ("EXECUTED", "G90"))

    def test_edge_equal_to_threshold_is_refused(self):
        self.assertEqual(self.decide(net_edge_minus_threshold_ppm=0), ("REJECTED", "G06"))
        self.assertEqual(self.decide(net_edge_minus_threshold_ppm=-1), ("REJECTED", "G06"))
        self.assertEqual(self.decide(net_edge_minus_threshold_ppm=1), ("EXECUTED", "G90"))

    def test_each_refusal(self):
        for over, rule_id in (({"missing": "MISSING_COST"}, "G01"), ({"halted": True}, "G02"), ({"paused": True}, "G03"),
                              ({"veto_count": 1}, "G04"), ({"quorum_met": False}, "G05"),
                              ({"stake_units": 0}, "G07"), ({"risk_veto": "RISK_MARKET_CAP"}, "G08")):
            self.assertEqual(self.decide(**over), ("REJECTED", rule_id), over)

    def test_first_match_wins_refusals_on_top(self):
        everything_wrong = {"missing": "MISSING_COST", "halted": True, "paused": True, "veto_count": 2,
                            "quorum_met": False, "net_edge_minus_threshold_ppm": None, "stake_units": None}
        self.assertEqual(self.decide(**everything_wrong)[1], "G01")
        self.assertEqual(self.decide(halted=True, paused=True, veto_count=1)[1], "G02")
        self.assertEqual(self.decide(paused=True, veto_count=1, quorum_met=False)[1], "G03")
        self.assertEqual(self.decide(veto_count=1, quorum_met=False)[1], "G04")
        ids = [r["id"] for r in self.gate]
        executed_at = ids.index("G90")
        self.assertTrue(all(r["decision"] == "REJECTED" for r in self.gate[:executed_at]))

    def test_anything_unforeseen_is_refused_by_the_default(self):
        self.assertEqual(self.decide(stake_units=None), ("REJECTED", "G99"))
        self.assertEqual(self.decide(net_edge_minus_threshold_ppm=None), ("REJECTED", "G99"))
        self.assertEqual(self.gate[-1]["when"], [])
        self.assertEqual(self.gate[-1]["decision"], "REJECTED")

    def test_only_one_rule_can_execute(self):
        self.assertEqual([r["id"] for r in self.gate if r["decision"] == "EXECUTED"], ["G90"])

    def test_threshold_and_comparison_as_claimed(self):
        self.assertEqual(self.rules.gate["threshold_ppm"], 20_000)
        self.assertEqual(self.rules.gate["comparison"], "strictly_greater")


class RuleFiles(unittest.TestCase):
    def test_bad_rule_files_are_refused(self):
        bad = (
            {"gate.json": {"comparison": "greater_or_equal"}},
            {"gate.json": {"threshold_ppm": -1}},
            {"gate.json": {"decision_rules": [{"id": "X", "when": [], "decision": "EXECUTED", "reason": "X"}]}},
            {"gate.json": {"decision_rules": [{"id": "X", "when": [["missing", "is_null"]], "decision": "REJECTED",
                                               "reason": "X"}]}},
            {"risk_limits.json": {"kelly_multiplier": "3/2"}},
            {"risk_limits.json": {"cap_ppm": 0}},
            {"risk_limits.json": {"cap_ppm": 50000.0}},
            {"risk_limits.json": {"max_open_positions_per_market": 0}},
            {"risk_limits.json": {"divergence": {"n_sigma": 0, "window_trades": 50, "min_baseline_trades": 50}}},
            {"throttle.json": {"shrink": "1/1"}},
            {"throttle.json": {"reexpansion_edge_bar_ppm": 20_000}},
            {"validators.json": {"quorum": {"min_approvals": 1, "validators": ["price_feed", "cross_market", "calendar",
                                                                            "confirmation"]}}},
            {"costs.json": {"max_component_ppm": 0}},
        )
        for change in bad:
            with self.assertRaises((RuleError, ValueError, ZeroDivisionError), msg=str(change)):
                U.load_rules(U.rules_copy(**change))

    def test_a_missing_rule_file_is_refused(self):
        folder = U.rules_copy()
        (folder / "costs.json").unlink()
        with self.assertRaises(RuleError):
            U.load_rules(folder)

    def test_the_digest_follows_the_content(self):
        a = U.load_rules()
        self.assertEqual(a.digest, U.load_rules(U.rules_copy()).digest)   # a reformatted copy: same content
        self.assertNotEqual(a.digest, U.load_rules(U.rules_copy(**{"risk_limits.json": {"cap_ppm": 40_000}})).digest)

    def test_rule_files_hold_no_float(self):
        def walk(obj, where):
            self.assertNotIsInstance(obj, float, where)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{where}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    walk(v, f"{where}[{i}]")
        for name in rules_mod.RULE_FILES:
            walk(json.loads((U.RULES_DIR / name).read_text(encoding="utf-8")), name)


class Decomposition(unittest.TestCase):
    def setUp(self):
        self.rules = U.load_rules()

    def test_the_numbers(self):
        d = signal.decompose(U.cand("C", "M0", 1, signal_ppm=600_000, confidence=500_000), self.rules)
        self.assertEqual((d.p_hat_ppm, d.cost_ppm, d.q_eff_ppm, d.edge_ppm, d.net_edge_ppm, d.gate_pass),
                         (500_000, 10_000, 410_000, 100_000, 90_000, True))

    def test_the_floor_never_rounds_the_estimate_up(self):
        up = signal.decompose(U.cand("C", "M0", 1, signal_ppm=460_001, confidence=500_000), self.rules)
        down = signal.decompose(U.cand("C", "M0", 1, signal_ppm=339_999, confidence=500_000), self.rules)
        self.assertEqual(up.p_hat_ppm, 430_000)       # 30000.5 -> 30000
        self.assertEqual(down.p_hat_ppm, 369_999)     # -30000.5 -> -30001
        self.assertFalse(up.gate_pass)

    def test_strictly_greater(self):
        at = signal.decompose(U.cand("C", "M0", 1, signal_ppm=460_000, confidence=500_000), self.rules)
        above = signal.decompose(U.cand("C", "M0", 1, signal_ppm=460_002, confidence=500_000), self.rules)
        self.assertEqual((at.net_edge_ppm, at.gate_pass), (20_000, False))
        self.assertEqual((above.net_edge_ppm, above.gate_pass), (20_001, True))

    def test_every_missing_figure_has_its_reason(self):
        cases = (({"side": "MAYBE"}, "MISSING_SIDE"), ({"side": None}, "MISSING_SIDE"),
                 ({"price_ppm": None}, "MISSING_PRICE"), ({"price_ppm": 0}, "MISSING_PRICE"),
                 ({"price_ppm": 1_000_000}, "MISSING_PRICE"), ({"price_ppm": True}, "MISSING_PRICE"),
                 ({"price_ppm": 400000.0}, "MISSING_PRICE"), ({"prior_ppm": None}, "MISSING_PRIOR"),
                 ({"signal_ppm": "0.9"}, "MISSING_SIGNAL"), ({"signal_ppm": 1_000_001}, "MISSING_SIGNAL"),
                 ({"confidence_ppm": None}, "MISSING_CONFIDENCE"), ({"confidence_ppm": "high"}, "MISSING_CONFIDENCE"),
                 ({"confidence_ppm": -1}, "MISSING_CONFIDENCE"), ({"cost": None}, "MISSING_COST"),
                 ({"cost": 10_000}, "MISSING_COST"), ({"cost": {"fee_ppm": 5_000, "latency_steps": 0}}, "MISSING_COST"),
                 ({"cost": {"fee_ppm": -1, "slippage_ppm": 0, "latency_steps": 0}}, "MISSING_COST"),
                 ({"cost": {"fee_ppm": 0, "slippage_ppm": 0, "latency_steps": 7}}, "COST_OUT_OF_MODEL"),
                 ({"cost": {"fee_ppm": 100_001, "slippage_ppm": 0, "latency_steps": 0}}, "COST_OUT_OF_MODEL"),
                 ({"price_ppm": 995_000, "prior_ppm": 995_000}, "COST_OUT_OF_MODEL"))
        for over, reason in cases:
            d = signal.decompose(U.cand("C", "M0", 1, **over), self.rules)
            self.assertEqual(d.missing, reason, over)
            self.assertIsNone(d.as_body())
            self.assertIsNone(d.gate_pass)

    def test_conviction_is_never_read(self):
        a = signal.decompose(U.cand("C", "M0", 1, conviction="low"), self.rules)
        b = signal.decompose(U.cand("C", "M0", 1, conviction="extreme"), self.rules)
        self.assertEqual(a, b)

    def test_cost_is_part_of_the_edge(self):
        cfg = self.rules.costs
        self.assertEqual(costs.total_cost_ppm({"fee_ppm": 5_000, "slippage_ppm": 3_000, "latency_steps": 2}, cfg),
                         (11_000, None))
        self.assertEqual(costs.total_cost_ppm({"fee_ppm": 0, "slippage_ppm": 0, "latency_steps": 6}, cfg), (9_000, None))
        d = signal.decompose(U.cand("C", "M0", 1, signal_ppm=500_000, confidence=500_000,
                                    cost={"fee_ppm": 30_000, "slippage_ppm": 25_000, "latency_steps": 0}), self.rules)
        self.assertEqual((d.edge_ppm, d.net_edge_ppm, d.gate_pass), (50_000, -5_000, False))


class EngineGate(unittest.TestCase):
    def test_incomplete_candidates_are_logged_and_refused(self):
        b = U.Bench(self)
        body = b.decide(U.cand("C1", "M0", 1, confidence_ppm=None))
        self.assertEqual((body["decision"], body["reason"], body["rule_id"]), ("REJECTED", "MISSING_CONFIDENCE", "G01"))
        rows = b.rows()
        self.assertEqual([r["kind"] for r in rows], ["GENESIS", "CANDIDATE", "DECISION"])
        self.assertEqual(b.state.open_stake_units, 0)

    def test_the_candidate_row_always_precedes_its_decision(self):
        b = U.Bench(self)
        for i in range(6):
            b.decide(U.cand(f"C{i}", f"M{i}", i + 1, signal_ppm=400_000 + 40_000 * i))
        rows = b.rows()[1:]
        for cand_row, dec_row in zip(rows[0::2], rows[1::2]):
            self.assertEqual((cand_row["kind"], dec_row["kind"]), ("CANDIDATE", "DECISION"))
            self.assertEqual(cand_row["body"]["candidate_id"], dec_row["body"]["candidate_id"])
            self.assertLess(cand_row["seq"], dec_row["seq"])

    def test_duplicates_and_late_candidates_are_recorded_but_not_admitted(self):
        b = U.Bench(self)
        b.decide(U.cand("C1", "M0", 1))
        again = b.engine.on_candidate(U.cand("C1", "M1", 2))
        self.assertEqual((again["kind"], again["body"]["reason"]), ("REFUSED_INPUT", "DUPLICATE_CANDIDATE"))
        b.settle(5, "M0", "YES")
        late = b.engine.on_candidate(U.cand("C2", "M0", 6))
        self.assertEqual((late["kind"], late["body"]["reason"]), ("REFUSED_INPUT", "CANDIDATE_AFTER_OUTCOME"))
        self.assertEqual(sum(1 for r in b.rows() if r["kind"] == "CANDIDATE"), 1)


if __name__ == "__main__":
    unittest.main()

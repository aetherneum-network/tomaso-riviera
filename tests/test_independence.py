"""Two hands: the references share no code with the harness, and the harness never sees the truth."""
from __future__ import annotations

import ast
import shutil
import unittest

from harness import run

from tests import _util as U
from tests.test_no_network import imported_modules

REFERENCES = ("reference_kelly.py", "reference_gate.py", "reference_drawdown.py")


def string_constants(source: str) -> list[str]:
    """Every string literal of a module except the docstrings."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


class TheReferencesStandAlone(unittest.TestCase):
    def test_no_reference_imports_the_harness(self):
        for name in REFERENCES + ("generate.py", "worlds.py"):
            names = imported_modules((U.ROOT / "corpus" / name).read_text(encoding="utf-8"))
            self.assertNotIn("harness", names, name)
            self.assertEqual(names & {"eval", "tests", "tools", "scenarios"}, set(), name)

    def test_the_references_do_not_read_the_rule_files(self):
        # They carry their own copy of the limits: a wrong rule file cannot make both sides wrong together.
        for name in REFERENCES:
            source = (U.ROOT / "corpus" / name).read_text(encoding="utf-8")
            self.assertEqual([s for s in string_constants(source) if s.endswith(".json") or "rules" in s.split("/")],
                             [], name)

    def test_the_reference_limits_are_the_ones_in_the_rule_files_today(self):
        from fractions import Fraction

        from corpus import reference_drawdown
        rules, params = U.load_rules(), reference_drawdown.PARAMS
        self.assertEqual(params["threshold_ppm"], rules.gate["threshold_ppm"])
        self.assertEqual(params["min_approvals"], rules.validators["quorum"]["min_approvals"])
        self.assertEqual(params["cap"], Fraction(rules.risk["cap_ppm"], U.PPM))
        self.assertEqual(params["kelly_multiplier"], Fraction(rules.risk["kelly_multiplier"]))
        self.assertEqual(params["exposure_ppm"], rules.risk["max_portfolio_exposure_ppm"])
        self.assertEqual(params["drawdown_halt_ppm"], rules.risk["drawdown_halt_ppm"])
        self.assertEqual(params["initial_bankroll_units"], rules.risk["initial_bankroll_units"])
        self.assertEqual(params["throttle_shrink"], Fraction(rules.throttle["shrink"]))
        self.assertEqual(params["divergence_n_sigma"], rules.risk["divergence"]["n_sigma"])

    def test_the_harness_does_not_import_the_corpus_or_the_scorer(self):
        for path in sorted((U.ROOT / "harness").glob("*.py")):
            names = imported_modules(path.read_text(encoding="utf-8"))
            self.assertEqual(names & {"corpus", "eval", "tests", "tools", "scenarios"}, set(), path.name)


class TheHarnessNeverSeesTheTruth(unittest.TestCase):
    def test_no_harness_module_names_the_gold_folder_or_a_true_probability(self):
        for path in sorted((U.ROOT / "harness").glob("*.py")):
            for value in string_constants(path.read_text(encoding="utf-8")):
                self.assertNotIn("gold", value, path.name)
                self.assertNotIn("truth", value, path.name)
                self.assertNotIn("p_true", value, path.name)
                self.assertNotIn("true_edge", value, path.name)

    def test_a_world_without_its_gold_folder_gives_the_same_ledger(self):
        dev = U.dev_suite()
        copy = U.scratch("no-gold") / "regime_change"
        shutil.copytree(dev["corpus"] / "regime_change", copy, ignore=shutil.ignore_patterns("gold"))
        self.assertFalse((copy / "gold").exists())
        out = U.scratch("no-gold-run")
        summary = run.run_world(copy, U.RULES_DIR, out / "regime_change")
        self.assertEqual((out / "regime_change" / "ledger.jsonl").read_bytes(),
                         (dev["run"] / "regime_change" / "ledger.jsonl").read_bytes())
        self.assertEqual((out / "regime_change" / "backtest_ledger.jsonl").read_bytes(),
                         (dev["run"] / "regime_change" / "backtest_ledger.jsonl").read_bytes())
        self.assertEqual(summary["ledger_rows"], 7492)


if __name__ == "__main__":
    unittest.main()

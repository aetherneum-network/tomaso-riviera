"""The ten scenarios: layout, run the way the council's executor runs them, and proof that they can fail.

A scenario that cannot fail proves nothing: each claim's scenario is re-run here against a harness with
one rule or one guard deliberately broken, and must then report FAIL.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from harness import breaker, ledger as ledger_mod, rules as rules_mod, signal
from harness.ledger import Ledger

from tests import _util as U

SCENARIOS = U.ROOT / "scenarios"
IDS = [f"S{i:02d}" for i in range(1, 11)]
CLAIM_OF = {"S01": "A1", "S02": "A1", "S03": "A2", "S04": "A3", "S05": "A4", "S06": "A5", "S07": "A6", "S08": "A7",
            "S09": "A7", "S10": "A3"}
EXECUTOR_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "PYTHONIOENCODING")

_RESULTS: dict | None = None


def tree_digest() -> dict:
    out = {}
    for path in sorted(SCENARIOS.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(SCENARIOS).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def executor_results() -> dict:
    """Every scenario run once as the executor seat runs it: the interpreter, the script, the scenario folder
    as working directory, a minimal environment, the scenario's own timeout."""
    global _RESULTS
    if _RESULTS is None:
        before = tree_digest()
        env = {k: os.environ[k] for k in EXECUTOR_ENV if k in os.environ}
        started = []
        for sid in IDS:
            spec = json.loads((SCENARIOS / sid / "scenario.json").read_text(encoding="utf-8"))
            argv = [sys.executable if a in ("python", "python3", "py") else a for a in spec["run"]]
            started.append((sid, spec, subprocess.Popen(argv, cwd=str(SCENARIOS / sid), env=env, text=True,
                                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
        results = {}
        for sid, spec, process in started:
            try:
                stdout, stderr = process.communicate(timeout=spec["timeout_s"])
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                stderr += "\nTIMEOUT"
            results[sid] = {"exit": process.returncode, "expect": spec["expect_exit"], "stdout": stdout.strip(),
                            "stderr": stderr.strip()}
        results["_tree_unchanged"] = before == tree_digest()
        _RESULTS = results
    return _RESULTS


class Layout(unittest.TestCase):
    def test_ten_scenarios_each_with_the_five_parts(self):
        found = sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir() and p.name.startswith("S"))
        self.assertEqual(found, IDS)
        for sid in IDS:
            folder = SCENARIOS / sid
            for part in ("scenario.json", "check.py", "run.md", "expected/expected.json"):
                self.assertTrue((folder / part).is_file(), f"{sid}/{part}")
            self.assertTrue(any((folder / "input").iterdir()), f"{sid}/input is empty")

    def test_scenario_json_follows_the_executor_convention(self):
        for sid in IDS:
            spec = json.loads((SCENARIOS / sid / "scenario.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["id"], sid)
            self.assertEqual(spec["run"], ["python", "check.py"])          # a script inside the scenario folder
            self.assertEqual(spec["expect_exit"], 0)
            self.assertTrue(isinstance(spec["timeout_s"], int) and 1 <= spec["timeout_s"] <= 300)
            self.assertEqual(spec["claim"], CLAIM_OF[sid])
            self.assertTrue(spec["title"].strip())

    def test_every_claim_has_a_scenario_and_three_are_negative_or_boundary(self):
        self.assertEqual(sorted(set(CLAIM_OF.values())), [f"A{i}" for i in range(1, 8)])
        titles = {sid: json.loads((SCENARIOS / sid / "scenario.json").read_text(encoding="utf-8"))["title"] for sid in IDS}
        # three scenarios are about something that must NOT happen: a trade refused, a boundary, a backtest failed
        self.assertTrue(titles["S01"].startswith("Negative"), titles["S01"])
        self.assertTrue(titles["S02"].startswith("Boundary"), titles["S02"])
        self.assertIn("fails the backtest", titles["S09"])

    def test_run_md_is_a_title_a_claim_line_one_paragraph_and_the_command(self):
        for sid in IDS:
            text = (SCENARIOS / sid / "run.md").read_text(encoding="utf-8")
            blocks = [b for b in text.split("\n\n") if b.strip()]
            self.assertTrue(blocks[0].startswith(f"# {sid} - "), sid)
            self.assertIn(f"Claim {CLAIM_OF[sid]}", blocks[1], sid)
            self.assertEqual(len(blocks), 5, f"{sid}: title, claim line, ONE paragraph, command, inputs line")
            self.assertIn(f"python scenarios/{sid}/check.py", blocks[3], sid)

    def test_expected_files_say_how_they_were_derived(self):
        for sid in IDS:
            doc = json.loads((SCENARIOS / sid / "expected" / "expected.json").read_text(encoding="utf-8"))
            self.assertTrue(any(k in doc for k in ("how_derived", "why")), sid)

    def test_inputs_on_disk_are_what_the_input_writer_writes(self):
        make_inputs = importlib.import_module("scenarios.make_inputs")
        self.assertEqual(make_inputs.main(["--check"]), 0)

    def test_scenario_inputs_are_synthetic_and_dated_by_as_of(self):
        for sid in IDS:
            folder = SCENARIOS / sid / "input"
            if (folder / "meta.json").exists():
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                self.assertIs(meta["synthetic"], True, sid)
            else:                                        # S08 regenerates the development corpus from a seed
                meta = json.loads((folder / "params.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["seed"], U.DEV_SEED, sid)
            self.assertEqual(meta["as_of"], U.AS_OF, sid)
            spec = json.loads((SCENARIOS / sid / "scenario.json").read_text(encoding="utf-8"))
            self.assertIn("synthetic", spec["data"])
            self.assertIn("paper trading only", spec["data"])


class AsTheExecutorRunsThem(unittest.TestCase):
    def test_all_ten_pass_alone_with_a_minimal_environment(self):
        results = executor_results()
        for sid in IDS:
            r = results[sid]
            self.assertEqual(r["exit"], r["expect"], f"{sid}: {r['stdout'][-300:]} {r['stderr'][-300:]}")
            self.assertTrue(r["stdout"].splitlines()[-1].startswith(f"{sid} PASS - "), r["stdout"])
            self.assertEqual(r["stderr"], "", sid)

    def test_a_run_leaves_the_scenario_folders_as_they_were(self):
        self.assertTrue(executor_results()["_tree_unchanged"])

    def test_the_committed_report_says_what_the_scenarios_say_now(self):
        report = json.loads((U.ROOT / "reports" / "scenarios.json").read_text(encoding="utf-8"))
        results = executor_results()
        self.assertEqual((report["passed"], report["total"], report["mode"], report["data"]), (10, 10, "PAPER", "synthetic"))
        self.assertEqual(report["rules_sha256"], U.load_rules().digest)
        self.assertEqual(report["as_of"], U.AS_OF)
        for entry in report["scenarios"]:
            self.assertEqual(entry["line"], results[entry["id"]]["stdout"].splitlines()[-1])
            self.assertEqual(entry["claim"], CLAIM_OF[entry["id"]])
        self.assertNotIn("seconds", json.dumps(report))                   # no timing, no clock: the file is stable


def broken_rules(change):
    """``harness.rules.load`` with one value changed after loading: a rule file edited the wrong way."""
    real = rules_mod.load

    def load(rules_dir):
        rules = real(rules_dir)
        change(rules)
        return rules
    return mock.patch("harness.rules.load", load)


class TheScenariosCanFail(unittest.TestCase):
    def verdict(self, sid: str) -> tuple[bool, str]:
        run_all = importlib.import_module("scenarios.run_all")
        result = run_all.run_all([sid], echo=False)[0]
        return result["passed"], result["line"]

    def assert_fails(self, sid: str) -> None:
        passed, line = self.verdict(sid)
        self.assertFalse(passed, f"{sid} still passes with the harness broken: {line}")
        self.assertTrue(line.startswith(f"{sid} FAIL - "), line)

    def test_s01_fails_if_a_missing_confidence_is_defaulted(self):
        real = signal.decompose

        def forgiving(candidate, rules):
            patched = dict(candidate)
            if patched.get("confidence_ppm") is None:
                patched["confidence_ppm"] = 1_000_000
            return real(patched, rules)
        with mock.patch("harness.engine.signal.decompose", forgiving):
            self.assert_fails("S01")

    def test_s02_fails_if_the_threshold_is_one_ppm_lower(self):
        with broken_rules(lambda r: r.gate.__setitem__("threshold_ppm", 19_999)):
            self.assert_fails("S02")

    def test_s03_fails_if_two_approvals_are_enough(self):
        with broken_rules(lambda r: r.validators["quorum"].__setitem__("min_approvals", 2)):
            self.assert_fails("S03")

    def test_s04_and_s10_fail_if_the_cap_is_six_percent(self):
        with broken_rules(lambda r: r.risk.__setitem__("cap_ppm", 60_000)):
            self.assert_fails("S04")
            self.assert_fails("S10")

    def test_s05_fails_if_the_breaker_trips_later(self):
        with broken_rules(lambda r: r.risk.__setitem__("drawdown_halt_ppm", 250_000)):
            self.assert_fails("S05")

    def test_s05_fails_if_any_consent_record_is_accepted(self):
        real = breaker.validate_consent

        def lax(consent, halt_row, t):                   # while halted, any record at all lifts the halt
            return None if isinstance(consent, dict) and halt_row is not None else real(consent, halt_row, t)
        with mock.patch("harness.breaker.validate_consent", lax):
            passed, line = self.verdict("S05")
        self.assertFalse(passed, line)
        self.assertNotIn("Error", line)                  # a verdict of the checker (the halt was lifted), not a crash

    def test_s06_fails_if_the_throttle_shrinks_by_another_factor(self):
        with broken_rules(lambda r: r.throttle.__setitem__("shrink", "1/2")):
            self.assert_fails("S06")

    def test_s06_fails_if_reexpansion_ignores_the_edge_bar(self):
        with broken_rules(lambda r: r.throttle.__setitem__("reexpansion_edge_bar_ppm", 0)):
            self.assert_fails("S06")

    def test_s07_fails_if_the_ledger_accepts_a_candidate_after_the_outcome(self):
        with mock.patch.object(Ledger, "candidate", lambda self, t, candidate: self._commit("CANDIDATE", t, candidate)):
            self.assert_fails("S07")

    def test_s07_fails_if_verification_ignores_the_published_head(self):
        real = ledger_mod.verify
        with mock.patch("harness.ledger.verify", lambda path, expect_head=None, anchors=None: real(path)):
            self.assert_fails("S07")

    def test_s09_fails_if_the_leak_test_lets_everything_through(self):
        with mock.patch("harness.backtest.leak_test", lambda fn, world, cands: {"status": "OK", "reason": None,
                                                                              "checked": len(cands)}):
            self.assert_fails("S09")

    def test_the_same_scenarios_pass_with_the_harness_intact(self):
        for sid in ("S01", "S02", "S03", "S04", "S06", "S07", "S09", "S10"):
            passed, line = self.verdict(sid)
            self.assertTrue(passed, line)


if __name__ == "__main__":
    unittest.main()

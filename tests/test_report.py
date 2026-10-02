"""The report and the command line: what is shown, in which order, and what a failed run says."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

from harness import report, run

from tests import _util as U


def small_world():
    market_map = U.markets(3, resolves_at=50)
    cands = [U.cand("C0", "M0", 5), U.cand("C1", "M1", 6, signal_ppm=460_000, confidence=500_000),
             U.cand("C2", "M2", 7, confidence=None)]
    return U.make_world(cands, market_map, {"M0": "YES", "M1": "NO", "M2": "YES"})


class Report(unittest.TestCase):
    def setUp(self):
        self.run_dir = U.dev_suite()["run"]
        self.md = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.doc = json.loads((self.run_dir / "report.json").read_text(encoding="utf-8"))

    def test_it_opens_with_the_disclaimer_and_the_date_of_the_data(self):
        lines = self.md.splitlines()
        self.assertEqual(lines[0], "# Paper-trading replay on synthetic worlds")
        self.assertIn("Simulated only", lines[2])
        self.assertIn("Not investment advice", lines[2])
        self.assertIn("not a performance claim", lines[2])
        self.assertIn(U.AS_OF, lines[4])
        self.assertEqual((self.doc["mode"], self.doc["as_of"]), ("PAPER", [U.AS_OF]))

    def test_limits_come_first_and_the_simulated_result_last_under_its_caption(self):
        limits = self.md.index("## Limits and ledger")
        refusals = self.md.index("## Refusals by reason")
        result = self.md.index("## Simulated result per world")
        self.assertTrue(limits < refusals < result)
        self.assertEqual(self.md.count(report.CAPTION), 1)
        self.assertGreater(self.md.index(report.CAPTION), result)
        self.assertNotIn("delta", self.md[:result])
        self.assertNotIn("final equity", self.md[:result])
        for word in ("profit", "return", "performance of", "gain", "yield", "%/", "annual"):
            self.assertNotIn(word, self.md.lower().replace("not a performance", ""), word)

    def test_the_null_world_is_always_in_both_tables(self):
        rows = [line for line in self.md.splitlines() if line.startswith("| null |")]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1], "| null | 1000000 | 846247 | -153753 |")      # the world without an edge loses
        self.assertEqual([w["world"] for w in self.doc["worlds"]], ["null", "planted_edge", "regime_change", "high_cost"])

    def test_the_report_counts_what_the_ledger_holds(self):
        for world in self.doc["worlds"]:
            rows = U.read_rows(self.run_dir / world["world"] / "ledger.jsonl")
            replay = world["replay"]
            self.assertEqual(replay["rows"], len(rows))
            self.assertEqual(replay["candidates"], sum(1 for r in rows if r["kind"] == "CANDIDATE"))
            self.assertEqual(replay["executed"], sum(1 for r in rows if r["kind"] == "DECISION"
                                                     and r["body"]["decision"] == "EXECUTED"))
            self.assertEqual(replay["executed"] + replay["rejected_total"], replay["candidates"])
            self.assertEqual(replay["head"], rows[-1]["hash"])
            self.assertIs(replay["chain_ok"], True)
            self.assertEqual(replay["simulated_result"]["caption"], report.CAPTION)
            self.assertEqual(world["seed"], U.DEV_SEED)

    def test_the_pause_and_the_refusals_are_the_measured_ones(self):
        regime = self.doc["worlds"][2]["replay"]
        self.assertEqual((regime["pause"]["t"], regime["halt"], regime["rejected"]["SIGNAL_PAUSED"]), (3069, None, 2300))
        self.assertIn("| regime_change | 20260930 | 3598 | 104 | 3494 | - | 3069 | 11.9755% | 7492 | OK |", self.md)


class CommandLine(unittest.TestCase):
    def call(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run.main(argv)
        return code, out.getvalue()

    def test_a_run_says_paper_and_synthetic_and_writes_the_report(self):
        world, out = small_world(), U.scratch("cli") / "hand"
        code, text = self.call(["--world", str(world), "--out", str(out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines()[-1], "RUN OK (PAPER, synthetic data)")
        self.assertIn(f"as_of {U.AS_OF}", text)
        decisions = [(r["body"]["decision"], r["body"]["reason"]) for r in U.read_rows(out / "ledger.jsonl")
                     if r["kind"] == "DECISION"]
        self.assertEqual(decisions, [("EXECUTED", "EDGE_ABOVE_THRESHOLD"), ("REJECTED", "EDGE_NOT_ABOVE_THRESHOLD"),
                                     ("REJECTED", "MISSING_CONFIDENCE")])
        self.assertTrue((out.parent / "report.md").is_file())

    def test_a_second_run_into_the_same_folder_is_a_failed_run_said_first(self):
        world, out = small_world(), U.scratch("cli") / "hand"
        self.assertEqual(self.call(["--world", str(world), "--out", str(out)])[0], 0)
        before = (out / "ledger.jsonl").read_bytes()
        code, text = self.call(["--world", str(world), "--out", str(out)])
        self.assertEqual(code, 3)
        self.assertTrue(text.startswith("RUN FAILED - "), text)
        self.assertNotIn("RUN OK", text)
        self.assertEqual((out / "ledger.jsonl").read_bytes(), before)       # nothing was overwritten

    def test_a_missing_world_is_a_failed_run_not_a_traceback(self):
        code, text = self.call(["--world", str(U.scratch("cli") / "absent"), "--out", str(U.scratch("cli") / "o")])
        self.assertEqual(code, 3)
        self.assertTrue(text.startswith("RUN FAILED - "), text)

    def test_a_corpus_without_worlds_is_a_failed_run(self):
        code, text = self.call(["--corpus", str(U.scratch("cli")), "--out", str(U.scratch("cli") / "o")])
        self.assertEqual(code, 3)
        self.assertIn("no world found", text)


if __name__ == "__main__":
    unittest.main()

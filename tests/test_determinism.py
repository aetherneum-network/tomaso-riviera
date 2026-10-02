"""Same seed, same bytes: no clock, no float, no folder name and no set order reaches an output."""
from __future__ import annotations

import json
import re
import unittest

from harness import run

from tests import _util as U

_SECOND = None


def second_run():
    """The cached development corpus replayed a second time, into another folder of this process."""
    global _SECOND
    if _SECOND is None:
        out = U.scratch("second-run")
        run.run_corpus(U.dev_suite()["corpus"], U.RULES_DIR, out / "run")
        _SECOND = out / "run"
    return _SECOND


def files_under(folder) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class TwoRunsOneResult(unittest.TestCase):
    def test_a_second_replay_writes_the_same_bytes(self):
        first, second = files_under(U.dev_suite()["run"]), files_under(second_run())
        self.assertEqual(sorted(first), sorted(second))
        self.assertEqual([rel for rel in first if first[rel] != second[rel]], [])
        self.assertEqual(len(first), 4 * 6 + 2)            # six files per world, plus the report in two forms

    def test_the_ledger_heads_are_the_recorded_ones(self):
        results = json.loads((U.ROOT / "eval" / "results.json").read_text(encoding="utf-8"))
        recorded = {w["world"]: w["ledger"]["head"] for w in results["suites"]["dev"]["worlds"]}
        for world, head in recorded.items():
            summary = json.loads((second_run() / world / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["ledger_head"], head, world)


class NothingOfTheMachineInTheOutputs(unittest.TestCase):
    def outputs(self) -> dict:
        return files_under(U.dev_suite()["dir"])

    def test_no_float_in_any_output(self):
        def no_float(text):
            raise AssertionError(f"a float was written: {text}")
        for rel, raw in self.outputs().items():
            if rel.endswith(".jsonl"):
                for line in raw.decode("utf-8").splitlines():
                    json.loads(line, parse_float=no_float)
            elif rel.endswith(".json"):
                json.loads(raw.decode("utf-8"), parse_float=no_float)

    def test_no_path_no_folder_name_and_no_clock_in_any_output(self):
        folder = U.dev_suite()["dir"]
        names = {folder.parent.name, folder.parent.parent.name}       # the scratch folder and the temporary root
        stamp = re.compile(r"20\d\d-\d\d-\d\dT\d\d[:]\d\d")
        for rel, raw in self.outputs().items():
            text = raw.decode("utf-8")
            for name in names:
                self.assertFalse(name in text, f"{rel} names the folder it was written in")
            self.assertFalse(":\\" in text or ":/" in text.replace("://", ""), f"{rel} holds a path")
            self.assertEqual({m.group(0) for m in stamp.finditer(text)} - {U.AS_OF[:16]}, set(), rel)

    def test_every_output_is_lf_utf8_and_ends_with_a_newline(self):
        for rel, raw in self.outputs().items():
            self.assertNotIn(b"\r", raw, rel)
            self.assertTrue(raw.endswith(b"\n"), rel)
            raw.decode("ascii")

    def test_every_ledger_row_is_labelled_paper_and_canonical(self):
        for world in ("null", "planted_edge", "regime_change", "high_cost"):
            for name in ("ledger.jsonl", "backtest_ledger.jsonl"):
                for line in (U.dev_suite()["run"] / world / name).read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    self.assertEqual(row["mode"], "PAPER")
                    self.assertEqual(line, json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


if __name__ == "__main__":
    unittest.main()

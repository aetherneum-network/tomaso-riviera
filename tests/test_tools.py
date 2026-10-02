"""The rebuild and the manifest: two rebuilds in two folders and two processes give the same bytes."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import unittest

from tests import _util as U

rebuild = importlib.import_module("tools.rebuild")
manifest = importlib.import_module("tools.manifest")
results_tool = importlib.import_module("tools.results")

_DOUBLE = None


def double_rebuild() -> dict:
    """``tools/rebuild.py --double`` once per test process: two interpreters, two folders, two hash seeds."""
    global _DOUBLE
    if _DOUBLE is None:
        base = U.scratch("double")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rebuild.double(base)
        _DOUBLE = {"code": code, "stdout": out.getvalue(), "a": base / "rebuild-001-a", "b": base / "rebuild-001-b"}
    return _DOUBLE


class DoubleRebuild(unittest.TestCase):
    def test_two_rebuilds_are_byte_identical(self):
        d = double_rebuild()
        self.assertEqual(d["code"], 0, d["stdout"])
        self.assertIn("DOUBLE REBUILD OK - byte-identical", d["stdout"])
        self.assertIn("scenarios 10/10", d["stdout"])
        first, second = (d["a"] / rebuild.BUNDLE).read_bytes(), (d["b"] / rebuild.BUNDLE).read_bytes()
        self.assertEqual(first, second)
        self.assertGreater(len(first.splitlines()), 50)

    def test_the_bundle_lists_every_file_and_the_files_do_match(self):
        d = double_rebuild()
        for folder in (d["a"], d["b"]):
            self.assertEqual(rebuild.bundle_lines(folder), (folder / rebuild.BUNDLE).read_text(encoding="utf-8"))
        names = [line.split("  ", 1)[1] for line in (d["a"] / rebuild.BUNDLE).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(names, sorted(names))
        for expected in ("scenarios.json", "suite/score.json", "suite/run/report.md", "suite/run/null/ledger.jsonl",
                         "suite/corpus/regime_change/candidates.jsonl"):
            self.assertIn(expected, names)

    def test_a_third_build_inside_this_process_gives_the_same_suite(self):
        d = double_rebuild()
        mine = rebuild.bundle_lines(U.dev_suite()["dir"]).splitlines()
        theirs = [line.replace("  suite/", "  ", 1) for line in
                  (d["a"] / rebuild.BUNDLE).read_text(encoding="utf-8").splitlines() if "  suite/" in line]
        self.assertEqual(mine, theirs)

    def test_the_readme_records_the_hash_of_this_bundle(self):
        d = double_rebuild()
        digest = rebuild.bundle_hash((d["a"] / rebuild.BUNDLE).read_text(encoding="utf-8"))
        self.assertIn(digest, d["stdout"])
        self.assertIn(digest, (U.ROOT / "README.md").read_text(encoding="utf-8"))

    def test_a_rebuild_never_writes_into_a_folder_that_holds_something(self):
        with self.assertRaisesRegex(SystemExit, "nothing is overwritten"):
            rebuild.rebuild(double_rebuild()["a"])


class Bundle(unittest.TestCase):
    def test_a_changed_byte_changes_the_hash_and_the_folder_name_does_not(self):
        one, two = U.scratch("bundle-one"), U.scratch("bundle-two")
        for folder in (one, two):
            (folder / "sub").mkdir()
            (folder / "sub" / "b.txt").write_bytes(b"beta\n")
            (folder / "a.txt").write_bytes(b"alpha\n")
        self.assertEqual(rebuild.bundle_lines(one), rebuild.bundle_lines(two))
        self.assertEqual(rebuild.bundle_lines(one).splitlines()[1].split("  ")[1], "sub/b.txt")
        before = rebuild.bundle_hash(rebuild.bundle_lines(one))
        (one / rebuild.BUNDLE).write_bytes(b"ignored\n")            # the list does not list itself
        self.assertEqual(rebuild.bundle_hash(rebuild.bundle_lines(one)), before)
        (one / "a.txt").write_bytes(b"alphA\n")
        self.assertNotEqual(rebuild.bundle_hash(rebuild.bundle_lines(one)), before)

    def test_the_next_pair_of_folders_is_always_new(self):
        base = U.scratch("pairs")
        first = rebuild._next_pair(base)
        self.assertEqual([p.name for p in first], ["rebuild-001-a", "rebuild-001-b"])
        first[1].mkdir()
        self.assertEqual([p.name for p in rebuild._next_pair(base)], ["rebuild-002-a", "rebuild-002-b"])


class Manifest(unittest.TestCase):
    def tree(self):
        root = U.scratch("manifest")
        for rel, data in {"harness/x.py": b"x = 1\n", "rules/r.json": b"{}\n", "README.md": b"# r\n",
                          "build/skipped.txt": b"no\n", "corpus/out/skipped.jsonl": b"no\n",
                          "harness/__pycache__/x.pyc": b"no"}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        return root

    def test_it_lists_the_tree_without_build_products_and_checks_clean(self):
        root = self.tree()
        text = manifest.render("abc1234", root)
        self.assertEqual(manifest.tree_files(root), ["README.md", "harness/x.py", "rules/r.json"])
        self.assertTrue(text.startswith("# commit abc1234\n# files 3\n"))
        (root / "MANIFEST.sha256").write_text(text, encoding="utf-8", newline="\n")
        result = manifest.check(root, root / "MANIFEST.sha256")
        self.assertEqual((result["commit"], result["listed"], result["missing"], result["differing"],
                          result["added_later"], result["frozen_changed"]), ("abc1234", 3, [], [], [], []))

    def test_it_tells_a_changed_file_from_a_missing_one_and_from_one_added_later(self):
        root = self.tree()
        (root / "MANIFEST.sha256").write_text(manifest.render("abc1234", root), encoding="utf-8", newline="\n")
        (root / "harness" / "x.py").write_bytes(b"x = 2\n")
        (root / "rules" / "r.json").unlink()
        (root / "eval").mkdir()
        (root / "eval" / "history.json").write_bytes(b"{}\n")
        result = manifest.check(root, root / "MANIFEST.sha256")
        self.assertEqual(result["differing"], ["harness/x.py"])
        self.assertEqual(result["missing"], ["rules/r.json"])
        self.assertEqual(result["added_later"], ["eval/history.json"])
        self.assertEqual(result["frozen_changed"], ["rules/r.json", "harness/x.py"])

    def test_the_order_of_the_list_does_not_depend_on_the_operating_system(self):
        root = self.tree()
        (root / "Zeta.md").write_bytes(b"z\n")
        (root / "harness.md").write_bytes(b"h\n")
        self.assertEqual(manifest.tree_files(root), ["README.md", "Zeta.md", "harness.md", "harness/x.py", "rules/r.json"])
        lines = rebuild.bundle_lines(root).splitlines()
        self.assertEqual([line.split("  ", 1)[1] for line in lines][:4], ["README.md", "Zeta.md", "build/skipped.txt",
                                                                        "corpus/out/skipped.jsonl"])

    def test_writing_needs_the_commit_it_describes(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            manifest.main(["--write"])


class Results(unittest.TestCase):
    def test_the_results_file_is_a_copy_of_the_score_files_never_typed_by_hand(self):
        folder = U.scratch("results")
        (folder / "dev").mkdir()
        (folder / "dev" / "score.json").write_text(json.dumps(U.dev_suite()["result"]), encoding="utf-8")
        doc = results_tool.build({"dev": folder / "dev"}, None, "2026-09-30")
        self.assertEqual(list(doc["suites"]), ["dev"])
        self.assertEqual(doc["suites"]["dev"]["headline"], U.dev_suite()["result"]["headline"])
        self.assertEqual(doc["measured_on"], "2026-09-30")
        with self.assertRaisesRegex(SystemExit, "holds the suite dev"):
            results_tool.build({"holdout": folder / "dev"}, None, "2026-09-30")


if __name__ == "__main__":
    unittest.main()

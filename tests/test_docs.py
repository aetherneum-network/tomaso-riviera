"""The documents: the banner, the profile text left as it was, and numbers that are the measured ones."""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import unittest

from tests import _util as U

# The text changed in PR #1 (week-1 review, merge commit 4be79b0 on main), not in the pack: hash of the reviewed text.
ORIGINAL_README_SHA256 = "adda415eb4332f3fb19c3ef0725d6694df109bcf1764c841070cd25cf40d84c0"   # the profile before this pack
SECTION = "\n## Proof pack v2.0\n"


def read(rel: str) -> str:
    return (U.ROOT / rel).read_text(encoding="utf-8")


def readme_parts() -> tuple[str, str, str]:
    text = read("README.md")
    start, end = text.index("# Tomaso Riviera\n"), text.index(SECTION)
    return text[:start], text[start:end], text[end:]


class Banner(unittest.TestCase):
    def test_the_readme_opens_with_the_banner(self):
        banner, _, _ = readme_parts()
        self.assertTrue(banner.startswith("> **SYNTHETIC - Tomaso Riviera is a synthetic alumnus (an AI agent)"))
        for phrase in ("not a person, not a financial adviser", "PAPER TRADING ONLY, on synthetic data",
                       "connects to no exchange, broker, wallet or account", "has never sent an order and has never moved money",
                       "Nothing here is investment advice, a solicitation or a performance claim",
                       "[TO CONFIRM with legal]"):
            self.assertIn(phrase, banner)

    def test_the_disclaimer_says_the_same_and_is_marked_for_legal_review(self):
        text = read("DISCLAIMER.md")
        for phrase in ("[TO CONFIRM with legal]", "Paper trading only", "No profitability evidence",
                       "Not investment advice", "synthetic alumnus (an AI agent)"):
            self.assertIn(phrase, text)


class TheProfileIsLeftAsItWas(unittest.TestCase):
    def test_the_original_text_is_byte_for_byte_the_one_before_this_pack(self):
        _, original, _ = readme_parts()
        self.assertEqual(hashlib.sha256(original.encode("utf-8")).hexdigest(), ORIGINAL_README_SHA256)

    def test_the_sentence_awaiting_legal_review_is_neither_edited_nor_removed(self):
        _, original, _ = readme_parts()
        self.assertIn("Tomaso is the platform's Probabilistic Trading Engineer.", original)
        claims = read("CLAIMS.md")
        self.assertIn("\"Tomaso is the platform's Probabilistic Trading Engineer.\"", claims)
        self.assertIn("awaiting legal review - not touched", claims)

    def test_the_section_lists_the_statements_it_does_not_support(self):
        section = " ".join(readme_parts()[2].split())
        self.assertIn("Statements above that this pack does not support", section)
        self.assertIn("is awaiting legal review and has not been touched", section)


class ProofPackSection(unittest.TestCase):
    def setUp(self):
        self.section = readme_parts()[2]
        self.results = json.loads(read("eval/results.json"))

    def test_it_is_the_last_section_and_has_the_four_parts(self):
        titles = re.findall(r"^### (.+)$", self.section, flags=re.M)
        self.assertEqual(titles[:4], ["What is demonstrated", "How to re-run", "Numbers", "What is NOT demonstrated"])
        self.assertEqual(re.findall(r"^## (.+)$", self.section, flags=re.M), ["Proof pack v2.0"])

    def test_rerun_takes_fewer_than_five_commands(self):
        block = self.section.split("### How to re-run", 1)[1].split("```", 2)[1]
        commands = [line for line in block.splitlines() if line.startswith("python ")]
        self.assertTrue(1 <= len(commands) < 5, commands)
        for command in commands:
            script = command.split()[1]
            self.assertTrue(script == "-m" or (U.ROOT / script).is_file(), command)

    def test_the_numbers_are_the_recorded_ones_with_seed_and_date(self):
        self.assertIn("30 September 2026", self.section)
        self.assertEqual(self.results["measured_on"], "2026-09-30")
        self.assertIn(U.AS_OF, self.section)
        for name in ("dev", "holdout", "stress"):
            suite = self.results["suites"][name]
            h = suite["headline"]
            row = next(line for line in self.section.splitlines() if line.startswith(f"| {name} |"))
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertEqual(cells[1:], [str(suite["seed"]), str(h["limit_violations"]),
                                         f"{h['decisions_agree']:,} / {h['decisions_total']:,}",
                                         f"{h['max_size_deviation_units']} units",
                                         f"intact ({h['ledger_rows']:,} rows)"], name)

    def test_the_per_world_table_is_the_development_suite_with_the_null_world_first(self):
        rows = [line for line in self.section.splitlines() if re.match(r"^\| `(null|planted_edge|regime_change|high_cost)` \|", line)]
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[0].startswith("| `null` |"))
        for row, world in zip(rows, self.results["suites"]["dev"]["worlds"]):
            cells = [c.strip() for c in row.strip("|").split("|")]
            replay, cal = world["replay"], world["calibration"]
            self.assertEqual(cells[0], f"`{world['world']}`")
            self.assertEqual(cells[1], f"{replay['executed']} / {replay['candidates']:,}")
            self.assertEqual(cells[2], str(replay["halts_t"][0]) if replay["halts_t"] else "-")
            self.assertEqual(cells[3], f"t = {replay['pauses_t'][0]}" if replay["pauses_t"] else "-")
            dd = replay["max_drawdown_ppm"]
            self.assertEqual(cells[4], f"{dd // 10000}.{dd % 10000 // 100:02d}%")
            self.assertEqual(cells[5], f"{cal['wins']} of {cal['settled_trades']}")
            self.assertEqual(cells[6], f"{cal['expected_wins_milli'] // 1000}.{cal['expected_wins_milli'] % 1000:03d}")
            self.assertEqual(cells[7], str(cal["trades_with_true_net_edge_above_threshold"]))

    def test_no_simulated_result_is_printed_in_the_readme(self):
        for suite in self.results["suites"].values():
            for world in suite["worlds"]:
                for key in ("delta_units", "final_equity_units"):
                    value = abs(world["simulated_result"][key])
                    if value not in (0, 1_000_000):          # as a number of its own, not as digits inside a hash
                        found = re.search(rf"(?<![0-9a-f]){value}(?![0-9a-f])", self.section.replace(",", ""))
                        self.assertIsNone(found, (suite["suite"], world["world"], key))
        for word in ("equity curve", "return of", "profit of", "outperform"):
            self.assertNotIn(word, self.section.lower())
        self.assertIn("not a performance", self.section)

    def test_the_number_of_tests_is_the_number_of_tests(self):
        suite = unittest.TestLoader().discover(str(U.ROOT / "tests"), top_level_dir=str(U.ROOT))
        self.assertIn(f"{suite.countTestCases()} tests", self.section)

    def test_the_detector_numbers_are_the_recorded_ones(self):
        study = self.results["detector_study"]
        runs = study["runs_per_kind"]
        self.assertIn(f"{study['false_pause_runs_planted_like']} and {study['false_pause_runs_favourites']} of {runs:,}", self.section)
        self.assertIn(f"{study['regime_runs_paused_within_declared_K']:,} of {runs:,}", self.section)

    def test_it_says_what_is_not_demonstrated_and_that_only_the_standard_library_is_used(self):
        part = self.section.split("### What is NOT demonstrated", 1)[1]
        self.assertIn("Any result on real markets", part)
        self.assertIn("Any profitability", part)
        self.assertIn("standard library only", self.section)
        self.assertIn("v2.0.0-freeze", self.section)
        self.assertIn("PENDING", self.section)


class RequiredFiles(unittest.TestCase):
    def test_they_exist_with_these_exact_names(self):
        for rel in ("SYNTHETIC.md", "CLAIMS.md", "MODEL.md", "CHANGELOG.md", "DISCLAIMER.md", "LICENSE", "README.md",
                    "requirements.txt", ".github/workflows/ci.yml", "corpus/MANIFEST.sha256", "eval/results.json",
                    "reports/scenarios.json", "eval/blind/fifth_world.template.json",
                    "eval/blind/hand_candidates.template.jsonl"):
            self.assertTrue((U.ROOT / rel).is_file(), rel)

    def test_claims_gives_a_state_to_every_claim_and_points_at_things_that_exist(self):
        claims = read("CLAIMS.md")
        for state in ("**demonstrated**", "**not demonstrated: out of v2.0**", "**awaiting legal review - not touched**"):
            self.assertIn(state, claims)
        self.assertIn("No evidence of profitability", claims)
        self.assertIn("## Known limits", claims)
        by_claim: dict = {}
        for sid in sorted(p.name for p in (U.ROOT / "scenarios").iterdir() if p.is_dir() and p.name.startswith("S")):
            spec = json.loads(read(f"scenarios/{sid}/scenario.json"))
            by_claim.setdefault(spec["claim"], []).append(sid)
        for number in range(1, 8):
            row = next(line for line in claims.splitlines() if line.startswith(f"| A{number} |"))
            self.assertEqual(re.findall(r"scenarios/(S\d\d)", row), by_claim[f"A{number}"], row[:40])
            self.assertTrue(re.findall(r"tests/test_\w+\.py", row), row[:40])

    def test_every_file_a_document_points_at_exists(self):
        pattern = re.compile(r"`((?:harness|corpus|rules|scenarios|tests|tools|eval|reports)/[A-Za-z0-9_./-]+)`")
        later = {"eval/BLIND_PROTOCOL.md", "eval/history.json", "reports/scan.json", "eval/blind/result.json"}
        for doc in ("README.md", "CLAIMS.md", "SYNTHETIC.md", "MODEL.md", "CHANGELOG.md", "DISCLAIMER.md"):
            for rel in pattern.findall(read(doc)):
                rel = rel.rstrip("/.")
                if rel in later or "*" in rel:
                    continue
                self.assertTrue((U.ROOT / rel).exists(), f"{doc} points at {rel}")

    def test_model_md_says_who_wrote_it_and_that_no_model_runs(self):
        text = read("MODEL.md")
        for phrase in ("synthetic AI agent", "Claude Opus 5.5", "`claude-opus-5-5`", "`claude-fable-5-1`",
                       "No model at runtime", "disabled by default", "standard library only"):
            self.assertIn(phrase, text)
        mentioned = set(re.findall(r"claude-[a-z]+-\d[\d-]*", text))
        self.assertEqual(mentioned, {"claude-opus-5-5", "claude-fable-5-1"})

    def test_synthetic_md_names_the_four_worlds_and_what_is_absent(self):
        text = read("SYNTHETIC.md")
        for phrase in ("`null`", "`planted_edge`", "`regime_change`", "`high_cost`", "No account, no address, no key",
                       "generated by a seeded program", U.AS_OF, "[TO CONFIRM]"):
            self.assertIn(phrase, text)

    def test_the_ci_workflow_is_offline(self):
        text = read(".github/workflows/ci.yml")
        commands = [line.strip() for line in text.splitlines() if line.strip().startswith("run:")]
        self.assertEqual(commands, ["run: python -m unittest discover -s tests -t .", "run: python scenarios/run_all.py",
                                    "run: python corpus/generate.py --check", "run: python tools/rebuild.py --double",
                                    "run: python tools/manifest.py --check"])
        self.assertNotIn("secrets.", text)
        self.assertIn("contents: read", text)
        self.assertIn("ubuntu-latest, windows-latest", text)
        self.assertIn('python-version: "3.12"', text)
        self.assertIn("[TO CONFIRM]", text)

    def test_the_changelog_records_the_corrections(self):
        text = read("CHANGELOG.md")
        for phrase in ("Run 001", "Run 003", "false pause", "did not contain the banner", "CI has never run"):
            self.assertIn(phrase, text)


class AfterTheFreeze(unittest.TestCase):
    """Files written after the tag; checked when they are there."""

    def test_the_tree_matches_the_manifest_when_there_is_one(self):
        manifest = importlib.import_module("tools.manifest")
        if not manifest.MANIFEST.is_file():
            self.skipTest("MANIFEST.sha256 is written by the last commit before the tag")
        result = manifest.check()
        self.assertEqual((result["missing"], result["differing"], result["frozen_changed"]), ([], [], []))
        self.assertRegex(result["commit"], r"^[0-9a-f]{7,40}$")
        self.assertLessEqual(set(result["added_later"]), {"eval/BLIND_PROTOCOL.md", "eval/history.json",
                                                         "eval/blind/result.json"})

    def test_the_blind_protocol_gives_the_exact_command(self):
        path = U.ROOT / "eval" / "BLIND_PROTOCOL.md"
        if not path.is_file():
            self.skipTest("written after the tag v2.0.0-freeze")
        text = path.read_text(encoding="utf-8")
        self.assertIn("python eval/score.py --blind --seed", text)
        for phrase in ("v2.0.0-freeze", "--fifth-world", "--hand-candidates", "--runner", "once", "twenty"):
            self.assertIn(phrase, text)

    def test_the_history_keeps_the_bad_first_runs(self):
        path = U.ROOT / "eval" / "history.json"
        if not path.is_file():
            self.skipTest("written after the tag v2.0.0-freeze")
        doc = json.loads(path.read_text(encoding="utf-8"), parse_float=self.fail)
        runs = doc["development_runs"]
        self.assertEqual([r["run"] for r in runs], ["001", "002", "003", "004", "005", "006"])
        self.assertTrue(all(r["seed"] == U.DEV_SEED and r["measured_on"] and "led_to" in r for r in runs))
        self.assertEqual(doc["blind"]["status"], "PENDING")
        self.assertEqual(doc["suites"], json.loads(read("eval/results.json"))["suites"])


if __name__ == "__main__":
    unittest.main()

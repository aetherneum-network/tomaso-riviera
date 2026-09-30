"""The synthetic corpus: generated from a seed, fictitious, reproducible, with a known truth kept apart."""
from __future__ import annotations

import hashlib
import json
import re
import unittest

from corpus import generate, worlds as W

from tests import _util as U

_FILES: dict | None = None


def dev_files() -> dict:
    global _FILES
    if _FILES is None:
        _FILES = generate.generate(U.DEV_SEED)
    return _FILES


def jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line]


class Reproducible(unittest.TestCase):
    def test_the_manifest_lists_what_the_seed_generates(self):
        manifest = (U.ROOT / "corpus" / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertEqual(manifest, generate.digest_lines(dev_files()))
        self.assertEqual(len(manifest.splitlines()), 28)                  # 4 worlds x 7 files

    def test_the_same_seed_gives_the_same_bytes_and_another_seed_does_not(self):
        again = generate.generate(U.DEV_SEED)
        self.assertEqual(again, dev_files())
        other = generate.generate(U.DEV_SEED - 1_000)
        self.assertEqual(sorted(other), sorted(dev_files()))
        self.assertNotEqual(other["null/candidates.jsonl"], dev_files()["null/candidates.jsonl"])

    def test_the_worlds_do_not_share_a_random_stream(self):
        digests = {hashlib.sha256(dev_files()[f"{w}/outcomes.jsonl"].encode()).hexdigest() for w in W.WORLD_ORDER}
        self.assertEqual(len(digests), 4)

    def test_the_cached_suite_was_written_from_the_same_files(self):
        corpus = U.dev_suite()["corpus"]
        for rel, text in dev_files().items():
            self.assertEqual((corpus / rel).read_bytes(), text.encode("utf-8"), rel)

    def test_the_cli_check_agrees(self):
        self.assertEqual(generate.main(["--check"]), 0)

    def test_files_are_lf_ascii_and_without_floats(self):
        for rel, text in dev_files().items():
            self.assertNotIn("\r", text, rel)
            text.encode("ascii")
            self.assertTrue(text.endswith("\n"), rel)
            loads = (lambda s: json.loads(s, parse_float=self.fail))
            if rel.endswith(".jsonl"):
                for line in text.splitlines():
                    loads(line)
            else:
                loads(text)


class Synthetic(unittest.TestCase):
    def test_every_world_says_it_is_synthetic_and_carries_the_fixed_as_of(self):
        for world in W.WORLD_ORDER:
            meta = json.loads(dev_files()[f"{world}/meta.json"])
            self.assertIs(meta["synthetic"], True)
            self.assertEqual((meta["as_of"], meta["seed"], meta["world"]), (U.AS_OF, U.DEV_SEED, world))
            self.assertIn("Not real data", meta["note"])
            self.assertEqual((meta["steps"], meta["markets"], meta["baseline_until_t"]), (6000, 400, 1500))

    def test_the_plan_s_size(self):
        counts = {w: json.loads(dev_files()[f"{w}/meta.json"])["candidates"] for w in W.WORLD_ORDER}
        for world, count in counts.items():
            self.assertEqual(count, len(dev_files()[f"{world}/candidates.jsonl"].splitlines()))
            self.assertTrue(4000 <= count <= 6000, (world, count))
        self.assertTrue(19_000 <= sum(counts.values()) <= 21_000, counts)   # "about 20,000 candidates"

    def test_market_names_are_a_fictitious_place_an_event_and_a_number(self):
        for world in W.WORLD_ORDER:
            spec = W.WORLDS[world]
            pattern = re.compile(re.escape(f"{spec['place']} - {spec['event']} ") + r"\d{3}$")
            for market in jsonl(dev_files()[f"{world}/markets.jsonl"]):
                self.assertRegex(market["name"], pattern)
                self.assertTrue(market["market_id"].startswith(spec["code"] + "-M"))
                self.assertLess(market["opens_at"], market["resolves_at"])

    def test_candidates_carry_integers_only_and_lie_inside_their_market_s_life(self):
        for world in W.WORLD_ORDER:
            markets = {m["market_id"]: m for m in jsonl(dev_files()[f"{world}/markets.jsonl"])}
            per_market: dict = {}
            for cand in jsonl(dev_files()[f"{world}/candidates.jsonl"]):
                market = markets[cand["market_id"]]
                self.assertTrue(market["opens_at"] <= cand["t"] < market["resolves_at"], cand["candidate_id"])
                self.assertIn(cand["conviction"], W.CONVICTIONS)
                self.assertIn(cand["side"], ("YES", "NO"))
                self.assertTrue(0 < cand["price_ppm"] < U.PPM)
                per_market.setdefault(cand["market_id"], set()).add(cand["side"])
            self.assertTrue(all(len(sides) == 1 for sides in per_market.values()))   # one side per market
            sizes = [sum(1 for c in jsonl(dev_files()[f"{world}/candidates.jsonl"]) if c["market_id"] == "%s-M0000" % W.WORLDS[world]["code"])]
            self.assertTrue(10 <= sizes[0] <= 15, sizes)

    def test_the_planted_defects_are_there_in_about_the_declared_share(self):
        for world in W.WORLD_ORDER:
            cands = jsonl(dev_files()[f"{world}/candidates.jsonl"])
            n = len(cands)
            missing_conf = sum(1 for c in cands if c.get("confidence_ppm") is None)
            missing_cost = sum(1 for c in cands if c.get("cost") is None)
            off_book = sum(1 for c in cands if c["price_ppm"] + c["complement_price_ppm"] != U.PPM)
            self.assertTrue(0 < missing_conf < n * 25 // 1000, (world, missing_conf))   # planted at 1%
            self.assertTrue(0 < missing_cost < n * 25 // 1000, (world, missing_cost))   # planted at 1%
            self.assertGreater(off_book, n // 100, world)                               # inconsistent books, 3%


class KnownTruth(unittest.TestCase):
    def test_the_truth_is_kept_under_gold_and_nowhere_else(self):
        for world in W.WORLD_ORDER:
            for rel in ("candidates.jsonl", "markets.jsonl", "outcomes.jsonl", "meta.json"):
                self.assertNotIn("true", dev_files()[f"{world}/{rel}"].replace('"synthetic": true', ""), (world, rel))
            for rel in ("gold/truth.jsonl", "gold/markets_truth.jsonl", "gold/world_truth.json"):
                self.assertIn(f"{world}/{rel}", dev_files())

    def test_the_price_is_the_true_probability_minus_the_planted_edge(self):
        for world in W.WORLD_ORDER:
            truth = {r["candidate_id"]: r for r in jsonl(dev_files()[f"{world}/gold/truth.jsonl"])}
            regimes = W.WORLDS[world]["regimes"]
            for cand in jsonl(dev_files()[f"{world}/candidates.jsonl"]):
                t = truth[cand["candidate_id"]]
                self.assertIn(t["true_edge_ppm"], regimes[t["regime_index"]]["true_edge_ppm"])
                self.assertEqual(cand["prior_ppm"], t["p_side_true_ppm"] - t["true_edge_ppm"], cand["candidate_id"])

    def test_the_null_world_has_no_edge_and_the_regime_world_loses_it_at_a_known_instant(self):
        null = jsonl(dev_files()["null/gold/truth.jsonl"])
        self.assertEqual({r["true_edge_ppm"] for r in null}, {0})
        change_t = W.WORLDS["regime_change"]["regimes"][1]["from_t"]
        self.assertEqual(change_t, 2400)
        markets = {m["market_id"]: m for m in jsonl(dev_files()["regime_change/markets.jsonl"])}
        for row in jsonl(dev_files()["regime_change/gold/markets_truth.jsonl"]):
            before = markets[row["market_id"]]["opens_at"] < change_t
            self.assertEqual(row["regime_index"], 0 if before else 1, row["market_id"])   # a regime per market

    def test_outcomes_are_drawn_near_the_known_probabilities(self):
        for world in W.WORLD_ORDER:
            truths = jsonl(dev_files()[f"{world}/gold/markets_truth.jsonl"])
            expected = sum(r["p_true_ppm"] for r in truths)                 # expected YES outcomes, in ppm
            variance = sum(r["p_true_ppm"] * (U.PPM - r["p_true_ppm"]) for r in truths)
            yes = sum(1 for r in truths if r["outcome"] == "YES")
            # |observed - expected| <= 4 sigma, in exact integers (both sides squared, scaled by PPM^2)
            self.assertLessEqual((yes * U.PPM - expected) ** 2, 16 * variance, world)


class FifthWorldSpec(unittest.TestCase):
    def spec(self, **over) -> dict:
        doc = json.loads((U.ROOT / "eval" / "blind" / "fifth_world.template.json").read_text(encoding="utf-8"))
        doc = {k: v for k, v in doc.items() if not k.startswith("_")}
        doc.update(over)
        return doc

    def test_the_template_is_a_valid_specification(self):
        W.validate_spec(self.spec())

    def test_bad_specifications_are_refused_with_a_reason(self):
        bad = {
            "a float": {"noise_ppm": 0.06},
            "a probability outside the range": {"p_side_ppm": [50_000, 700_000]},
            "an inverted range": {"confidence_ppm": [900_000, 300_000]},
            "no regime at t 0": {"regimes": [{"from_t": 5, "true_edge_ppm": [0], "signal_bias_ppm": 0}]},
            "regimes out of order": {"regimes": [{"from_t": 0, "true_edge_ppm": [0], "signal_bias_ppm": 0},
                                                 {"from_t": 0, "true_edge_ppm": [0], "signal_bias_ppm": 0}]},
            "a regime after the end": {"regimes": [{"from_t": 0, "true_edge_ppm": [0], "signal_bias_ppm": 0},
                                                   {"from_t": 6000, "true_edge_ppm": [0], "signal_bias_ppm": 0}]},
            "an edge too large": {"regimes": [{"from_t": 0, "true_edge_ppm": [400_000], "signal_bias_ppm": 0}]},
            "a boolean fee": {"fee_ppm": True},
            "latency beyond the range": {"latency_steps": [0, 51]},
        }
        for label, change in bad.items():
            with self.assertRaises(ValueError, msg=label):
                W.validate_spec(self.spec(**change))
        incomplete = self.spec()
        del incomplete["fee_ppm"]
        with self.assertRaisesRegex(ValueError, "fee_ppm"):
            W.validate_spec(incomplete)

    def test_a_fifth_world_is_generated_next_to_the_four_without_changing_them(self):
        files = generate.generate(U.DEV_SEED, fifth=self.spec())
        self.assertEqual({k: v for k, v in files.items() if not k.startswith("fifth/")}, dev_files())
        meta = json.loads(files["fifth/meta.json"])
        self.assertEqual((meta["world"], meta["code"], meta["synthetic"]), ("fifth", "W5", True))
        self.assertTrue(json.loads(files["fifth/markets.jsonl"].splitlines()[0])["name"].startswith("Fifth world - event "))

    def test_the_stress_corpus_is_another_corpus(self):
        stress = generate.generate(U.DEV_SEED, stress=True)
        self.assertNotEqual(stress["null/candidates.jsonl"], dev_files()["null/candidates.jsonl"])
        self.assertIs(json.loads(stress["null/meta.json"])["stress"], True)
        latencies = {c["cost"]["latency_steps"] for c in jsonl(stress["null/candidates.jsonl"]) if c.get("cost")}
        self.assertGreater(max(latencies), 2)                               # beyond the development range


if __name__ == "__main__":
    unittest.main()

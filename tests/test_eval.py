"""The scorer, the recorded measurements and the blind protocol's refusals.

No blind seed is generated here: every refusal below is raised before anything is generated or run.
"""
from __future__ import annotations

import importlib
import json
import unittest
from unittest import mock

from harness import run

from tests import _util as U

score = U.score_module()
results_tool = importlib.import_module("tools.results")


def recorded() -> dict:
    return json.loads((U.ROOT / "eval" / "results.json").read_text(encoding="utf-8"))


class RecordedMeasurements(unittest.TestCase):
    def test_the_development_numbers_on_file_are_the_ones_measured_now(self):
        now = results_tool.compact(U.dev_suite()["result"])
        self.assertEqual(recorded()["suites"]["dev"], json.loads(json.dumps(now)))

    def test_the_headline_is_limits_agreement_and_integrity_never_a_result(self):
        for name, suite in recorded()["suites"].items():
            h = suite["headline"]
            self.assertEqual(sorted(h), ["decisions_agree", "decisions_total", "ledger_rows", "ledgers_chain_ok",
                                         "limit_violations", "max_size_deviation_units", "other_mismatches"], name)
            self.assertEqual((h["limit_violations"], h["max_size_deviation_units"], h["other_mismatches"]), (0, 0, 0), name)
            self.assertEqual(h["decisions_agree"], h["decisions_total"], name)
            self.assertIs(h["ledgers_chain_ok"], True, name)

    def test_every_recorded_suite_carries_its_seed_its_date_and_the_digests(self):
        doc = recorded()
        self.assertRegex(doc["measured_on"], r"^2026-\d\d-\d\d$")
        self.assertEqual({k: v["seed"] for k, v in doc["suites"].items()},
                         {"dev": 20260930, "holdout": 20261001, "stress": 20261002, "rehearsal": 20260929})
        for name, suite in doc["suites"].items():
            self.assertEqual(suite["as_of"], U.AS_OF, name)
            self.assertEqual(suite["rules_sha256"], U.load_rules().digest, name)
            self.assertEqual(suite["code_sha256"], score.code_digest(), name)   # measured with the code of today
        self.assertEqual(doc["blind"]["status"], "PENDING")
        self.assertIn("NOT blind", doc["who_ran_it"])

    def test_the_null_world_is_in_every_suite_and_a_result_never_travels_without_its_caption(self):
        for name, suite in recorded()["suites"].items():
            worlds = [w["world"] for w in suite["worlds"]]
            self.assertEqual(worlds[:4], ["null", "planted_edge", "regime_change", "high_cost"], name)
            for world in suite["worlds"]:
                self.assertEqual(world["simulated_result"]["caption"], score.CAPTION)
                self.assertEqual(world["calibration"]["trades_with_true_net_edge_above_threshold"] == 0,
                                 world["world"] in ("null", "high_cost"), (name, world["world"]))

    def test_the_regime_world_of_the_development_suite(self):
        world = recorded()["suites"]["dev"]["worlds"][2]
        self.assertEqual(world["regime"], {"change_t": 2400, "pause_t": 3069, "delay_steps": 669, "halt_t": None,
                                           "post_change_trades_settled_until_pause": 42, "within_declared_K": True})
        self.assertEqual(world["replay"]["rejected"]["SIGNAL_PAUSED"], 2300)

    def test_the_detector_study_on_file_is_within_what_the_pack_declares(self):
        study = recorded()["detector_study"]
        self.assertEqual(study["declared"], score.DECLARED)
        runs = study["runs_per_kind"]
        for key in ("false_pause_runs_planted_like", "false_pause_runs_favourites"):
            self.assertLessEqual(study[key] * U.PPM, score.DECLARED["false_pause_per_run_max_ppm"] * runs)
        self.assertGreaterEqual(study["regime_runs_paused_within_declared_K"] * U.PPM,
                                score.DECLARED["pause_within_K_min_ppm"] * runs)

    def test_the_render_puts_the_result_last_and_under_its_caption(self):
        lines = score.render(U.dev_suite()["result"]).splitlines()
        self.assertTrue(lines[1].strip().startswith("limit violations: 0"))
        self.assertIn(score.CAPTION, lines[-1])
        self.assertEqual([i for i, line in enumerate(lines) if "delta" in line or score.CAPTION in line], [len(lines) - 1])
        self.assertIn("null ", lines[-1])

    def test_the_code_digest_moves_with_the_rules(self):
        self.assertRegex(score.code_digest(), r"^[0-9a-f]{64}$")
        self.assertEqual(score.code_digest(), score.code_digest())


def hand_rows(n: int = 20) -> list[dict]:
    rows = []
    for i in range(n):
        market = {"market_id": f"B-M{i:02d}", "opens_at": 0, "resolves_at": 200}
        cand = U.cand(f"B-{i:02d}", market["market_id"], 10 + i)
        rows.append({"candidate": cand, "market": market, "outcome": "YES", "expected": {"decision": "EXECUTED"}})
    return rows


class BlindProtocolRefusals(unittest.TestCase):
    def setUp(self):
        self.dir = U.scratch("blind")
        spec = json.loads(score.TEMPLATE_FIFTH.read_text(encoding="utf-8"))
        spec = {k: v for k, v in spec.items() if not k.startswith("_")}
        spec["fee_ppm"] = 9_000                                   # not the template any more
        self.fifth = self.dir / "fifth.json"
        self.fifth.write_text(json.dumps(spec), encoding="utf-8")
        self.hand = self.dir / "hand.jsonl"
        self.write_hand(hand_rows())
        absent = mock.patch.object(score, "BLIND_RESULT", self.dir / "result.json")   # never the repository's file
        absent.start()
        self.addCleanup(absent.stop)
        spy = mock.patch.object(score, "run_suite", side_effect=AssertionError("a refused run must not start"))
        self.run_suite = spy.start()
        self.addCleanup(spy.stop)

    def write_hand(self, rows) -> None:
        self.hand.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    def refused(self, pattern: str, *, seed=777, fifth=None, hand=None, runner="a different hand"):
        with self.assertRaisesRegex(SystemExit, pattern):
            score.run_blind(seed, fifth or self.fifth, hand or self.hand, runner, self.dir / "out")
        self.run_suite.assert_not_called()
        self.assertFalse((self.dir / "result.json").exists())
        self.assertFalse((self.dir / "out").exists())

    def test_the_seeds_the_author_has_seen_are_refused(self):
        for seed in (20260930, 20261001, 20261002, 20260929):
            self.refused("a blind seed must be new", seed=seed)

    def test_the_author_cannot_be_the_runner_and_the_runner_must_be_named(self):
        self.refused("must not be the author", runner="Tomaso Riviera")
        self.refused("must not be the author", runner="  ")

    def test_a_blind_run_is_run_once(self):
        (self.dir / "result.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "it is run once"):
            score.run_blind(777, self.fifth, self.hand, "a different hand", self.dir / "out")
        self.run_suite.assert_not_called()

    def test_the_author_s_templates_are_refused_by_path_and_by_content(self):
        self.refused("written by the author", fifth=score.TEMPLATE_FIFTH)
        self.refused("written by the author", hand=score.TEMPLATE_HAND)
        copy = self.dir / "copy.json"
        copy.write_text(score.TEMPLATE_FIFTH.read_text(encoding="utf-8").replace("TEMPLATE", "mine"), encoding="utf-8")
        self.refused("written by the author", fifth=copy)
        copy_hand = self.dir / "copy.jsonl"
        copy_hand.write_bytes(score.TEMPLATE_HAND.read_bytes())
        self.refused("written by the author", hand=copy_hand)

    def test_fewer_than_twenty_hand_candidates_are_refused(self):
        self.write_hand(hand_rows(19))
        self.refused("at least 20")

    def test_a_fifth_world_the_generator_cannot_honour_is_refused_before_anything_runs(self):
        bad = json.loads(self.fifth.read_text(encoding="utf-8"))
        bad["noise_ppm"] = 0.06
        self.fifth.write_text(json.dumps(bad), encoding="utf-8")
        self.refused("REFUSED: fifth world: noise_ppm")

    def test_a_decimal_number_in_a_hand_candidate_is_refused_before_anything_runs(self):
        rows = hand_rows()
        rows[7]["candidate"]["price_ppm"] = 0.4
        self.write_hand(rows)
        self.refused("hand candidate 8: a decimal number")

    def test_with_valid_inputs_the_run_would_start(self):
        self.run_suite.side_effect = RuntimeError("started")
        with self.assertRaisesRegex(RuntimeError, "started"):
            score.run_blind(777, self.fifth, self.hand, "a different hand", self.dir / "out")
        self.assertFalse((self.dir / "result.json").exists())      # a run that did not finish records nothing


class HandCandidates(unittest.TestCase):
    def check(self, mutate, pattern: str) -> None:
        rows = hand_rows(3)
        mutate(rows)
        with self.assertRaisesRegex(SystemExit, pattern):
            score.check_hand_rows(rows)

    def test_well_formed_rows_pass(self):
        score.check_hand_rows(hand_rows(3))

    def test_each_malformed_row_is_named_with_its_line(self):
        self.check(lambda r: r.__setitem__(1, "text"), "hand candidate 2")
        self.check(lambda r: r[0].pop("expected"), "hand candidate 1")
        self.check(lambda r: r[2]["candidate"].__setitem__("candidate_id", 7), "hand candidate 3")
        self.check(lambda r: r[1]["candidate"].__setitem__("market_id", "elsewhere"), "hand candidate 2")
        self.check(lambda r: r[1]["candidate"].__setitem__("t", -1), "hand candidate 2")
        self.check(lambda r: r[0]["market"].pop("resolves_at"), "hand candidate 1")
        self.check(lambda r: r[0].__setitem__("outcome", "MAYBE"), "hand candidate 1")
        self.check(lambda r: r[0]["expected"].__setitem__("decision", "BOUGHT"), "hand candidate 1")
        self.check(lambda r: r[2]["candidate"].__setitem__("candidate_id", "B-00"), "hand candidate 3")
        self.check(lambda r: r[0]["candidate"]["cost"].__setitem__("fee_ppm", 5000.0), "hand candidate 1: a decimal")
        with self.assertRaises(SystemExit):
            score.check_hand_rows([])

    def test_one_market_cannot_be_described_twice_differently_or_end_twice(self):
        def second_description(rows):
            rows[1]["candidate"]["market_id"] = rows[1]["market"]["market_id"] = "B-M00"
            rows[1]["market"]["resolves_at"] = 300
        self.check(second_description, "hand candidate 2")

        def second_outcome(rows):
            rows[1]["candidate"]["market_id"] = rows[1]["market"]["market_id"] = "B-M00"
            rows[1]["outcome"] = "NO"
        self.check(second_outcome, "hand candidate 2")

    def test_the_author_s_templates_are_well_formed_and_decided_as_written(self):
        rows = score._jsonl(score.TEMPLATE_HAND)
        score.check_hand_rows(rows)
        self.assertLess(len(rows), score.HAND_CANDIDATES_MIN)      # a template, not a blind input
        out = U.scratch("hand-template")
        expected = score._hand_world(rows, 0, out / "world")
        run.run_world(out / "world", U.RULES_DIR, out / "run")
        decided = {r["body"]["candidate_id"]: r["body"] for r in U.read_rows(out / "run" / "ledger.jsonl")
                   if r["kind"] == "DECISION"}
        for cid, want in expected.items():
            self.assertEqual(decided[cid]["decision"], want["decision"], cid)
            if "reason" in want:
                self.assertEqual(decided[cid]["reason"], want["reason"], cid)
            if "stake_units" in want:
                self.assertEqual(decided[cid]["sizing"]["stake_units"], want["stake_units"], cid)


class ScoreCli(unittest.TestCase):
    def test_a_suite_is_never_written_over_an_existing_one(self):
        with self.assertRaisesRegex(SystemExit, "nothing is overwritten"):
            score.run_suite("dev", U.DEV_SEED, False, U.dev_suite()["dir"])

    def test_the_command_line_asks_for_what_it_needs(self):
        import contextlib
        import io
        for argv in ([], ["--suite", "dev"], ["--rehearsal"], ["--blind", "--seed", "5"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                score.main(argv)
            self.assertEqual(ctx.exception.code, 2, argv)


if __name__ == "__main__":
    unittest.main()

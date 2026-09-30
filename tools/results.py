"""Compact the score files of the measured suites into ``eval/results.json``.

    python tools/results.py --dev build/eval-dev-002 --holdout build/eval-holdout-001 \
        --stress build/eval-stress-001 --rehearsal build/eval-rehearsal-002 \
        --detector build/eval-detector-001 --measured-on 2026-09-30

Nothing is measured here and nothing is typed by hand: every number is copied from a ``score.json`` written
by ``eval/score.py`` (or from ``detector_study.json``). The date of the measurement is an argument, because
no clock is read anywhere in this pack. The simulated result of each world is kept only with its caption.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "eval" / "results.json"
ORDER = ("dev", "holdout", "stress", "rehearsal")


def compact_world(world: dict) -> dict:
    replay, cal = world["replay"], world["calibration"]
    return {
        "world": world["world"],
        "limit_violations": world["limit_violations"],
        "decisions": {k: world["decisions"][k] for k in ("total", "agree", "other_mismatches", "max_size_deviation_units")},
        "replay": {"candidates": replay["candidates"], "executed": replay["executed"],
                   "rejected": replay["rejected"], "halts_t": replay["halts"], "pauses_t": replay["pauses"],
                   "max_drawdown_ppm": replay["max_drawdown_ppm"]},
        "calibration": {k: cal[k] for k in ("settled_trades", "wins", "expected_wins_milli", "z_milli",
                                            "trades_with_true_net_edge_above_threshold")},
        "signal_vs_truth": world["signal_vs_truth"],
        "detector": world["detector"],
        "regime": world.get("regime"),
        "ledger": {"rows": world["ledger"]["rows"], "backtest_rows": world["ledger"]["backtest_rows"],
                   "chain_ok": world["ledger"]["chain_ok"], "head": world["ledger"]["head"]},
        "simulated_result": world["simulated_result"],
    }


def compact(result: dict) -> dict:
    out = {"suite": result["suite"], "seed": result["seed"], "stress": result["stress"], "as_of": result["as_of"],
           "rules_sha256": result["rules_sha256"], "code_sha256": result["code_sha256"],
           "harness_version": result["harness_version"], "generator_version": result["generator_version"],
           "headline": result["headline"], "worlds": [compact_world(w) for w in result["worlds"]]}
    if "hand_candidates" in result:
        hc = result["hand_candidates"]
        out["hand_candidates"] = {k: hc[k] for k in ("count", "agree_with_the_hand", "reference_mismatches",
                                                     "limit_violations", "ledger_head")}
        out["runner"] = result["runner"]
    return out


def build(folders: dict, detector: Path | None, measured_on: str) -> dict:
    suites = {}
    for name in ORDER:
        if folders.get(name):
            result = json.loads((Path(folders[name]) / "score.json").read_text(encoding="utf-8"))
            if result["suite"] != name:
                raise SystemExit(f"REFUSED: the folder given for {name} holds the suite {result['suite']}")
            suites[name] = compact(result)
    doc = {
        "title": "Measurements of the proof pack v2.0 - paper trading on synthetic data",
        "measured_on": measured_on,
        "who_ran_it": "the author of the pack (dev, holdout, stress and rehearsal are NOT blind runs)",
        "note": "Headline numbers are limits, agreement with the independent references and ledger integrity. "
                "The simulated result of a world is not a metric and not a performance.",
        "suites": suites,
        "detector_study": None,
        "blind": {"status": "PENDING", "note": "run once by a different hand at the tag v2.0.0-freeze; "
                                               "see eval/BLIND_PROTOCOL.md"},
    }
    if detector:
        doc["detector_study"] = json.loads((Path(detector) / "detector_study.json").read_text(encoding="utf-8"))
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write eval/results.json from the score files of the suites.")
    for name in ORDER:
        ap.add_argument(f"--{name}", help=f"folder written by eval/score.py for the {name} run")
    ap.add_argument("--detector", help="folder written by eval/score.py --detector-study --out")
    ap.add_argument("--measured-on", required=True, help="date of the measurement, YYYY-MM-DD")
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args(argv)
    doc = build({name: getattr(args, name) for name in ORDER}, args.detector, args.measured_on)
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"results written: suites {', '.join(doc['suites']) or 'none'}; measured on {args.measured_on}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

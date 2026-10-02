"""Runner: backtest the first segment, replay the rest against that baseline, rebuild the report.

    python -m harness.run --corpus corpus/out --out build/run
    python -m harness.run --world corpus/out/planted_edge --out build/run/planted_edge

Exit codes: 0 OK, 3 FAILED. "RUN OK" is printed only on success. ``as_of`` and the seed are read from
the world's meta.json: there is no wall clock and no environment variable in any output.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import backtest, report, rules as rules_mod  # noqa: E402
from harness.engine import Engine  # noqa: E402
from harness.exact import write_json  # noqa: E402
from harness.ledger import Ledger  # noqa: E402
from harness.stream import events, load_world  # noqa: E402

WORLD_ORDER = ("null", "planted_edge", "regime_change", "high_cost")


def run_world(world_dir: Path, rules_dir: Path, out_dir: Path) -> dict:
    """One world: backtest on t < baseline_until_t (if any), then replay of the rest."""
    world = load_world(world_dir)
    rules = rules_mod.load(rules_dir)
    out_dir = Path(out_dir)
    split = world.meta.get("baseline_until_t", 0)
    bt = backtest.run_backtest(world, rules, out_dir, until_t=split) if split > 0 else None
    baseline = bt["baseline"] if bt is not None and bt["status"] == "OK" else None
    with Ledger.create(out_dir / "ledger.jsonl", backtest.genesis(world, rules, "REPLAY")) as ledger:
        engine = Engine(rules, ledger, world.markets, baseline=baseline, consent_dir=world.dir, start_t=split)
        engine.run(events(world, from_t=split))
        ledger.write_anchors(out_dir / "anchors.json")
        summary = {"world": world.meta["world"], "seed": world.meta.get("seed"), "as_of": world.meta["as_of"],
                   "rules_sha256": rules.digest, "baseline_until_t": split,
                   "backtest_status": None if bt is None else bt["status"],
                   "divergence_armed": engine.detector.armed,
                   "divergence_unarmed_reason": engine.detector.reason_unarmed,
                   "ledger_rows": ledger.rows, "ledger_head": ledger.head}
    write_json(out_dir / "summary.json", summary)
    return summary


def run_corpus(corpus_dir: Path, rules_dir: Path, out_dir: Path) -> list[dict]:
    corpus_dir, out_dir = Path(corpus_dir), Path(out_dir)
    names = [n for n in WORLD_ORDER if (corpus_dir / n / "meta.json").is_file()]
    names += sorted(p.name for p in corpus_dir.iterdir()
                    if p.is_dir() and p.name not in names and (p / "meta.json").is_file())
    if not names:
        raise FileNotFoundError("no world found in the corpus directory")
    summaries = [run_world(corpus_dir / name, rules_dir, out_dir / name) for name in names]
    report.build(out_dir, names)
    return summaries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Paper-trading replay on synthetic worlds (no network, no account).")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--corpus", help="directory holding one sub-directory per world")
    group.add_argument("--world", help="directory of a single world")
    ap.add_argument("--rules", default=str(ROOT / "rules"))
    ap.add_argument("--out", required=True, help="output directory (must not already hold a ledger)")
    args = ap.parse_args(argv)
    try:
        if args.corpus:
            summaries = run_corpus(Path(args.corpus), Path(args.rules), Path(args.out))
        else:
            summaries = [run_world(Path(args.world), Path(args.rules), Path(args.out))]
            report.build(Path(args.out).parent, [Path(args.out).name])
    except Exception as exc:  # a failed write or a refused row is a failed run, said first
        print(f"RUN FAILED - {type(exc).__name__}: {exc}")
        return 3
    for s in summaries:
        print(f"{s['world']}: {s['ledger_rows']} ledger rows, head {s['ledger_head'][:16]}..., as_of {s['as_of']}")
    print("RUN OK (PAPER, synthetic data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

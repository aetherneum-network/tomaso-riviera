"""Run the ten scenario checks; one PASS/FAIL line each and a total. Exit 1 if any fails.

    python scenarios/run_all.py                                # all ten
    python scenarios/run_all.py S05 S07                        # some
    python scenarios/run_all.py --json reports/scenarios.json  # also write the result (no timings, no clock)

Offline, standard library only. Paper trading on synthetic data: no market, no account, no money.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IDS = [f"S{i:02d}" for i in range(1, 11)]


def load_check(sid: str):
    spec = importlib.util.spec_from_file_location(f"scenario_{sid}", HERE / sid / "check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check


def run_all(ids: list[str] | None = None, echo: bool = True) -> list[dict]:
    results = []
    for sid in ids or IDS:
        try:
            ok, line, _details = load_check(sid)()
        except Exception as exc:  # a crashing check is a failing check
            ok, line = False, f"{sid} FAIL - {type(exc).__name__}: {exc}"
        meta = json.loads((HERE / sid / "scenario.json").read_text(encoding="utf-8"))
        results.append({"id": sid, "title": meta["title"], "claim": meta["claim"], "passed": ok, "line": line})
        if echo:
            print(line, flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = None
    if "--json" in args:
        at = args.index("--json")
        if at + 1 >= len(args):
            print("--json needs a file name")
            return 2
        out = Path(args[at + 1])
        del args[at:at + 2]
    unknown = [a for a in args if a not in IDS]
    if unknown:
        print(f"unknown scenario: {unknown}")
        return 2
    results = run_all(args or IDS)
    passed = sum(1 for r in results if r["passed"])
    print(f"\nScenarios: {passed}/{len(results)} PASS (paper trading, synthetic data)")
    if out is not None:
        from harness import VERSION
        from harness import rules as rules_mod
        from harness.exact import write_text_lf
        doc = {"pack": "tomaso-riviera", "harness_version": VERSION,
               "rules_sha256": rules_mod.load(ROOT / "rules").digest,
               "as_of": json.loads((HERE / "S01" / "input" / "meta.json").read_text(encoding="utf-8"))["as_of"],
               "mode": "PAPER", "data": "synthetic", "passed": passed, "total": len(results), "scenarios": results}
        out.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(out, json.dumps(doc, indent=2) + "\n")  # a failed write raises: never reported as written
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

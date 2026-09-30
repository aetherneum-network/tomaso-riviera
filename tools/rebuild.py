"""Rebuild everything from the seed, and prove that two independent rebuilds are byte-identical.

    python tools/rebuild.py --out build/rebuild-001      # one rebuild into a new folder
    python tools/rebuild.py --double                     # two rebuilds, two new folders, two processes; compare

One rebuild = the development corpus generated from its seed, the backtest and the replay of the four
worlds on paper, the report, the score against the independent references, and the ten scenarios.
``BUNDLE.sha256`` lists the SHA-256 of every file produced; the bundle hash is the SHA-256 of that list.

The two rebuilds of ``--double`` run in two separate interpreter processes started with different hash
seeds, in two different folders: nothing may depend on the folder, on the process or on the order of a
set. Exit 0 only if the two lists are byte-identical. Offline, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE = "BUNDLE.sha256"


def bundle_lines(folder: Path) -> str:
    folder = Path(folder)
    # ordered by the relative path as text: the order of Path objects depends on the operating system
    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.name != BUNDLE),
                   key=lambda p: p.relative_to(folder).as_posix())
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(folder).as_posix()}\n" for p in files)


def bundle_hash(lines: str) -> str:
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def rebuild(out: Path) -> dict:
    """One rebuild into ``out`` (which must be new or empty). Returns the summary written next to the bundle."""
    import importlib

    from harness.exact import write_text_lf
    from scenarios import run_all as scenarios_run

    score = importlib.import_module("eval.score")
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"REFUSED: {out.name} is not empty; use a new directory (nothing is overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    suite = score.SUITES["dev"]
    result = score.run_suite("dev", suite["seed"], suite["stress"], out / "suite")
    h = result["headline"]
    clean = (h["limit_violations"] == 0 and h["ledgers_chain_ok"] and h["decisions_agree"] == h["decisions_total"]
             and h["other_mismatches"] == 0)
    scen = scenarios_run.run_all(echo=False)
    write_text_lf(out / "scenarios.json", json.dumps(scen, indent=2) + "\n")
    lines = bundle_lines(out)
    write_text_lf(out / BUNDLE, lines)
    return {"files": len(lines.splitlines()), "bundle_sha256": bundle_hash(lines), "score_clean": clean,
            "scenarios_passed": sum(1 for s in scen if s["passed"]), "scenarios_total": len(scen),
            "seed": suite["seed"], "as_of": result["as_of"]}


def _next_pair(base: Path) -> tuple[Path, Path]:
    number = 1
    while (base / f"rebuild-{number:03d}-a").exists() or (base / f"rebuild-{number:03d}-b").exists():
        number += 1
    return base / f"rebuild-{number:03d}-a", base / f"rebuild-{number:03d}-b"


def double(base: Path) -> int:
    first, second = _next_pair(Path(base))
    env_keys = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE")
    summaries, started = [], []
    for folder, hash_seed in ((first, "1"), (second, "2")):
        env = {k: os.environ[k] for k in env_keys if k in os.environ}
        env["PYTHONHASHSEED"] = hash_seed
        started.append((folder, subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--out", str(folder), "--quiet"],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
    failed = False
    for folder, process in started:                      # the two rebuilds run side by side, never sharing a folder
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            print(f"REBUILD FAILED in {folder.name}: {(stdout + stderr).strip()[-400:]}")
            failed = True
        else:
            summaries.append(json.loads(stdout.strip().splitlines()[-1]))
    if failed:
        return 1
    a, b = (first / BUNDLE).read_bytes(), (second / BUNDLE).read_bytes()
    print(f"{first.name}: {summaries[0]['files']} files, bundle sha256 {summaries[0]['bundle_sha256']}")
    print(f"{second.name}: {summaries[1]['files']} files, bundle sha256 {summaries[1]['bundle_sha256']}")
    ok = a == b and all(s["score_clean"] and s["scenarios_passed"] == s["scenarios_total"] for s in summaries)
    if a != b:
        differing = sorted(set(a.decode().splitlines()) ^ set(b.decode().splitlines()))
        print(f"DOUBLE REBUILD FAILED - {len(differing)} differing lines, first: {differing[:2]}")
    elif not ok:
        print("DOUBLE REBUILD FAILED - identical, but the score or a scenario is not clean")
    else:
        print(f"DOUBLE REBUILD OK - byte-identical (seed {summaries[0]['seed']}, as_of {summaries[0]['as_of']}, "
              f"scenarios {summaries[0]['scenarios_passed']}/{summaries[0]['scenarios_total']})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild the pack's outputs from the seed; compare two rebuilds.")
    ap.add_argument("--out", help="folder of a single rebuild (new or empty)")
    ap.add_argument("--double", action="store_true", help="two rebuilds in two new folders under --base")
    ap.add_argument("--base", default=str(ROOT / "build"))
    ap.add_argument("--quiet", action="store_true", help="print only the summary, as one JSON line")
    args = ap.parse_args(argv)
    if args.double:
        return double(Path(args.base))
    if not args.out:
        ap.error("choose --out DIR or --double")
    summary = rebuild(Path(args.out))
    if args.quiet:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"rebuild: {summary['files']} files, bundle sha256 {summary['bundle_sha256']}, scenarios "
              f"{summary['scenarios_passed']}/{summary['scenarios_total']}, score "
              f"{'clean' if summary['score_clean'] else 'NOT CLEAN'}")
    return 0 if summary["score_clean"] and summary["scenarios_passed"] == summary["scenarios_total"] else 1


if __name__ == "__main__":
    sys.exit(main())

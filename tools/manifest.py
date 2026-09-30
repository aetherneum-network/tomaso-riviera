"""MANIFEST.sha256: the SHA-256 of every file of the pack, and the commit it describes.

    python tools/manifest.py --write --commit <sha>   # write MANIFEST.sha256 for the tree as it is
    python tools/manifest.py --check                  # compare the tree with MANIFEST.sha256

The manifest is committed right after the commit it names, so it cannot list itself. Build artefacts
(``build/``, ``corpus/out/``, caches) are not part of the pack and are not listed.

``--check`` exits 1 if a listed file is missing or differs. Files added after the manifest are reported
and do not fail the check: nothing in this repository is deleted or rewritten, files are added.
Offline, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "MANIFEST.sha256"
SKIP_DIRS = {".git", "build", "__pycache__"}
SKIP_PREFIXES = ("corpus/out/",)
FROZEN_PREFIXES = ("harness/", "rules/", "corpus/", "scenarios/", "tests/", "tools/", "eval/score.py",
                   "eval/__init__.py")


def tree_files(root: Path = ROOT) -> list[str]:
    out = []
    # ordered by the relative path as text: the order of Path objects depends on the operating system
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if rel.startswith(SKIP_PREFIXES) or rel.endswith(".pyc") or rel == MANIFEST.name:
            continue
        out.append(rel)
    return out


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(commit: str, root: Path = ROOT) -> str:
    files = tree_files(root)
    head = [f"# commit {commit}", f"# files {len(files)}",
            "# sha256 of the bytes of each file (LF line endings); this file does not list itself"]
    return "\n".join(head + [f"{sha256_of(root / rel)}  {rel}" for rel in files]) + "\n"


def parse(text: str) -> tuple[str | None, dict[str, str]]:
    commit, entries = None, {}
    for line in text.splitlines():
        if line.startswith("# commit "):
            commit = line[len("# commit "):].strip()
        elif line and not line.startswith("#"):
            digest, rel = line.split("  ", 1)
            entries[rel] = digest
    return commit, entries


def check(root: Path = ROOT, manifest: Path = MANIFEST) -> dict:
    commit, entries = parse(Path(manifest).read_text(encoding="utf-8"))
    missing = sorted(rel for rel in entries if not (root / rel).is_file())
    differing = sorted(rel for rel, digest in entries.items() if (root / rel).is_file() and sha256_of(root / rel) != digest)
    added = sorted(set(tree_files(root)) - set(entries))
    return {"commit": commit, "listed": len(entries), "missing": missing, "differing": differing, "added_later": added,
            "frozen_changed": [rel for rel in missing + differing if rel.startswith(FROZEN_PREFIXES)]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write or check MANIFEST.sha256.")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--commit", help="the commit the manifest describes (required with --write)")
    args = ap.parse_args(argv)
    if args.write:
        if not args.commit:
            ap.error("--write needs --commit <sha>: the manifest names the commit it describes")
        text = render(args.commit)
        with open(MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"MANIFEST.sha256 written: {len(text.splitlines()) - 3} files, commit {args.commit}")
        return 0
    if args.check:
        if not MANIFEST.is_file():
            print("MANIFEST CHECK FAILED - MANIFEST.sha256 not found")
            return 1
        result = check()
        ok = not result["missing"] and not result["differing"]
        print(f"{'MANIFEST OK' if ok else 'MANIFEST CHECK FAILED'} - commit {result['commit']}; listed "
              f"{result['listed']}; missing {len(result['missing'])}; differing {len(result['differing'])}; "
              f"added later {len(result['added_later'])}")
        for rel in (result["missing"] + result["differing"])[:20]:
            print(f"  changed: {rel}")
        for rel in result["added_later"][:20]:
            print(f"  added later: {rel}")
        return 0 if ok else 1
    ap.error("choose --write or --check")
    return 2


if __name__ == "__main__":
    sys.exit(main())

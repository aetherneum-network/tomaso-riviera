"""Exact arithmetic and byte-stable serialisation helpers.

No float ever reaches a hashed artefact: ``canonical`` refuses them. Files are written as UTF-8 with
LF line endings on every operating system.
"""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

PPM = 1_000_000


def _refuse_floats(obj: Any, where: str = "$") -> None:
    if isinstance(obj, float):
        raise TypeError(f"float in a hashed artefact at {where}: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"non-string key at {where}: {k!r}")
            _refuse_floats(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _refuse_floats(v, f"{where}[{i}]")
    elif not (obj is None or isinstance(obj, (str, int, bool))):
        raise TypeError(f"unsupported type at {where}: {type(obj).__name__}")


def canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, ASCII only, no floats."""
    _refuse_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fstr(value: Fraction | int) -> str:
    fr = Fraction(value)
    return f"{fr.numerator}/{fr.denominator}"


def fparse(text: str) -> Fraction:
    if not isinstance(text, str) or "/" not in text:
        raise ValueError(f"not an exact fraction string: {text!r}")
    num, den = text.split("/", 1)
    return Fraction(int(num), int(den))


def floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def write_text_lf(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path: Path, obj: Any) -> None:
    _refuse_floats(obj)
    write_text_lf(path, json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    write_text_lf(path, "".join(canonical(r) + "\n" for r in rows))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8", newline="\n") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                yield json.loads(line)

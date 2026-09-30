"""Shared helpers for the checkers of scenarios S01-S10 (paper trading on synthetic data; offline).

A checker runs the harness on the files under ``input/`` inside a temporary folder, compares what the
ledger says with ``expected/expected.json`` (derived on paper, not copied from an output), cross-checks
with the independent references, prints one line and exits 0 (PASS) or 1 (FAIL).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from corpus import reference_drawdown  # noqa: E402
from harness import backtest, rules as rules_mod  # noqa: E402
from harness.engine import Engine  # noqa: E402
from harness.ledger import Ledger, verify  # noqa: E402
from harness.stream import events, load_world  # noqa: E402

RULES = ROOT / "rules"
_OPEN: list = []   # ledgers opened by the helpers below, closed when the work folder is left


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def expected(scenario_dir: Path) -> dict:
    return load_json(Path(scenario_dir) / "expected" / "expected.json")


@contextmanager
def workdir(sid: str):
    with tempfile.TemporaryDirectory(prefix=f"tomaso-{sid}-") as tmp:
        try:
            yield Path(tmp)
        finally:                      # an open file cannot be removed on Windows: close before leaving
            while _OPEN:
                _OPEN.pop().close()


def copy_world(scenario_dir: Path, work: Path) -> Path:
    target = Path(work) / "world"
    shutil.copytree(Path(scenario_dir) / "input", target)
    return target


def replay(world_dir: Path, out_dir: Path, rules_dir: Path = RULES, until_t: int | None = None):
    """Open a fresh paper ledger and run the world's events (up to ``until_t``). Returns (engine, ledger, world)."""
    world = load_world(world_dir)
    rules = rules_mod.load(rules_dir)
    ledger = Ledger.create(Path(out_dir) / "ledger.jsonl", backtest.genesis(world, rules, "REPLAY"))
    _OPEN.append(ledger)
    engine = Engine(rules, ledger, world.markets, consent_dir=world.dir)
    engine.run(events(world, until_t=until_t))
    return engine, ledger, world


def rows_of(ledger_path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(ledger_path).read_text(encoding="utf-8").splitlines() if line]


def decisions(rows: list[dict]) -> dict:
    return {r["body"]["candidate_id"]: r["body"] for r in rows if r["kind"] == "DECISION"}


def compare_decisions(rows: list[dict], want: dict, problems: list[str]) -> None:
    """``want``: candidate_id -> {"decision", "reason", optional "stake_units", "payout_if_win_units", "throttle_level"}."""
    got = decisions(rows)
    if sorted(got) != sorted(want):
        problems.append(f"decided candidates {sorted(got)} differ from the expected {sorted(want)}")
    for cid, w in want.items():
        g = got.get(cid)
        if g is None:
            continue
        if (g["decision"], g["reason"]) != (w["decision"], w["reason"]):
            problems.append(f"{cid}: {g['decision']}/{g['reason']}, expected {w['decision']}/{w['reason']}")
        sizing = g.get("sizing") or {}
        for key in ("stake_units", "payout_if_win_units", "throttle_level"):
            if key in w and sizing.get(key) != w[key]:
                problems.append(f"{cid}: {key} {sizing.get(key)}, expected {w[key]}")
        if w["decision"] == "REJECTED" and g["decision"] == "REJECTED" and sizing.get("stake_units") and "stake_units" not in w:
            pass  # a size may have been computed before a risk veto; it is never a position
    # structural: every decision is immediately preceded by its own candidate row
    for prev, row in zip(rows, rows[1:]):
        if row["kind"] == "DECISION" and not (prev["kind"] == "CANDIDATE"
                                              and prev["body"]["candidate_id"] == row["body"]["candidate_id"]):
            problems.append(f"seq {row['seq']}: decision not preceded by its candidate row")
    for row in rows:
        if row.get("mode") != "PAPER":
            problems.append(f"seq {row['seq']}: row not labelled PAPER")


def reference_audit(ledger_path: Path, world_dir: Path, problems: list[str], params: dict | None = None) -> dict:
    markets = {}
    for line in (Path(world_dir) / "markets.jsonl").read_text(encoding="utf-8").splitlines():
        m = json.loads(line)
        markets[m["market_id"]] = m
    audit = reference_drawdown.audit(ledger_path, markets, params)
    if not audit["chain"]["ok"]:
        problems.append(f"reference: chain broken at seq {audit['chain']['first_bad_seq']}")
    if audit["mismatches"]:
        problems.append(f"reference disagrees with the ledger: {audit['mismatches'][:2]}")
    if audit["violations"]:
        problems.append(f"reference found limit violations: {audit['violations'][:2]}")
    return audit


def check_chain(ledger_path: Path, problems: list[str]) -> dict:
    result = verify(ledger_path)
    if not result["ok"]:
        problems.append(f"chain does not verify: {result['reason']} at seq {result['first_bad_seq']}")
    return result


def finish(sid: str, problems: list[str], summary: str, details: dict | None = None) -> tuple[bool, str, dict]:
    ok = not problems
    line = f"{sid} PASS - {summary}" if ok else f"{sid} FAIL - {problems[0]}" + (
        f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
    return ok, line, {"problems": problems, **(details or {})}


def main(check) -> None:
    ok, line, _details = check()
    print(line)
    sys.exit(0 if ok else 1)


def scratch_ledger(world_dir: Path, out_dir: Path, rules_dir: Path = RULES):
    """A fresh paper ledger for the world, with no event replayed: used to try forged rows."""
    world = load_world(world_dir)
    rules = rules_mod.load(rules_dir)
    ledger = Ledger.create(Path(out_dir) / "ledger.jsonl", backtest.genesis(world, rules, "REPLAY"))
    _OPEN.append(ledger)
    return ledger, rules, world


def forged_execution(ledger, rules, markets: dict, cand: dict, mutate=None) -> str | None:
    """Log the candidate, then try to write an EXECUTED decision for it, as a faulty engine would.

    Returns the code of the refusal, or None if the ledger accepted the row. A refusal must leave the
    file untouched: that is asserted here.
    """
    from fractions import Fraction

    from harness import signal, sizing, validators
    from harness.ledger import LimitViolation

    ledger.candidate(cand["t"], cand)
    st = ledger.state
    dec = signal.decompose(cand, rules)
    tally = validators.evaluate(cand, markets.get(cand["market_id"]), rules.validators)
    if dec.missing is None:
        size = sizing.size(dec.p_hat_ppm, dec.q_eff_ppm, st.equity_units, Fraction(1), rules.risk)
        sizing_body = size.as_body(equity_units=st.equity_units, level=0, multiplier=Fraction(1), cfg=rules.risk)
    else:
        sizing_body = {"stake_units": 1_000, "equity_units": st.equity_units, "throttle_level": 0,
                       "throttle_multiplier": "1/1", "payout_if_win_units": 0}
    body = {"candidate_id": cand["candidate_id"], "market_id": cand["market_id"], "side": cand.get("side"),
            "decision": "EXECUTED", "rule_id": "FORGED", "reason": "FORGED", "also": [],
            "decomposition": dec.as_body(), "votes": tally.votes, "approvals": tally.approvals,
            "vetoes": tally.vetoes, "sizing": sizing_body}
    if mutate is not None:
        mutate(body)
    rows_before, bytes_before = ledger.rows, ledger.path.stat().st_size
    try:
        ledger.decision(cand["t"], body)
    except LimitViolation as exc:
        if ledger.rows != rows_before or ledger.path.stat().st_size != bytes_before:
            raise AssertionError("a refused row left a trace in the ledger file") from exc
        return exc.code
    return None

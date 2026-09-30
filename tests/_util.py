"""Shared helpers of the test suite (offline, standard library only)."""
from __future__ import annotations

import atexit
import importlib
import json
import shutil
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import rules as rules_mod, signal, sizing, validators  # noqa: E402
from harness.engine import Engine  # noqa: E402
from harness.ledger import Ledger, LimitViolation  # noqa: E402

RULES_DIR = ROOT / "rules"
PPM = 1_000_000
AS_OF = "2026-10-01T09:00:00+02:00"
DEV_SEED = 20260930
DEFAULT_COST = {"fee_ppm": 5_000, "slippage_ppm": 5_000, "latency_steps": 0}

_TMP_ROOT: Path | None = None
_DEV: dict | None = None


def scratch(name: str) -> Path:
    """A new empty folder under one temporary root, removed when the test process ends."""
    global _TMP_ROOT
    if _TMP_ROOT is None:
        _TMP_ROOT = Path(tempfile.mkdtemp(prefix="tomaso-tests-"))
        atexit.register(shutil.rmtree, _TMP_ROOT, True)
    number = 0
    while (_TMP_ROOT / f"{name}-{number:03d}").exists():
        number += 1
    path = _TMP_ROOT / f"{name}-{number:03d}"
    path.mkdir(parents=True)
    return path


def score_module():
    return importlib.import_module("eval.score")


def dev_suite() -> dict:
    """The development suite (seed 20260930), generated, replayed and scored once per test process."""
    global _DEV
    if _DEV is None:
        out = scratch("dev-suite")
        result = score_module().run_suite("dev", DEV_SEED, False, out / "suite")
        _DEV = {"result": result, "dir": out / "suite", "corpus": out / "suite" / "corpus", "run": out / "suite" / "run"}
    return _DEV


def load_rules(rules_dir: Path = RULES_DIR):
    return rules_mod.load(rules_dir)


def rules_copy(**changes) -> Path:
    """A copy of the rule files with some values replaced: {"risk_limits.json": {"cap_ppm": 40000}, ...}."""
    target = scratch("rules")
    for path in RULES_DIR.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update(changes.get(path.name, {}))
        (target / path.name).write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


def cand(cid: str, market: str, t: int, *, side="YES", price=400_000, signal_ppm=900_000, confidence=800_000,
         conviction="medium", **over) -> dict:
    """A complete candidate that passes all four validators; defaults: estimate 0.80 at price 0.40."""
    c = {"candidate_id": cid, "market_id": market, "t": t, "side": side, "price_ppm": price, "price_as_of": t,
         "complement_price_ppm": PPM - price, "prior_ppm": price, "signal_ppm": signal_ppm,
         "confidence_ppm": confidence, "conviction": conviction, "confirm_ppm": price + 50_000,
         "cost": dict(DEFAULT_COST)}
    c.update(over)
    return c


def markets(n: int = 40, resolves_at: int = 1_000) -> dict:
    return {f"M{i}": {"market_id": f"M{i}", "opens_at": 0, "resolves_at": resolves_at} for i in range(n)}


def genesis(rules, **limit_overrides) -> dict:
    limits = dict(rules.limits_snapshot())
    limits.update(limit_overrides)
    return {"world": "test", "seed": None, "as_of": AS_OF, "phase": "TEST", "harness_version": "test",
            "rules_sha256": rules.digest, "limits": limits, "costs": rules.costs,
            "min_approvals": rules.validators["quorum"]["min_approvals"], "note": "PAPER, synthetic test data"}


class Bench:
    """A fresh paper ledger and an engine on hand-made markets, closed and removed at the end of the test."""

    def __init__(self, test, *, n_markets: int = 40, rules_dir: Path = RULES_DIR, **limit_overrides):
        self.rules = load_rules(rules_dir)
        self.markets = markets(n_markets)
        self.dir = scratch("bench")
        self.path = self.dir / "ledger.jsonl"
        self.ledger = Ledger.create(self.path, genesis(self.rules, **limit_overrides))
        self.engine = Engine(self.rules, self.ledger, self.markets, consent_dir=self.dir)
        test.addCleanup(self.ledger.close)

    @property
    def state(self):
        return self.ledger.state

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def decide(self, c: dict) -> dict:
        return self.engine.on_candidate(c)["body"]

    def settle(self, t: int, market_id: str, outcome: str) -> None:
        self.engine.on_settlement(t, market_id, outcome)

    def lose(self, first_t: int, count: int, start: int = 0) -> int:
        """Open ``count`` strong positions on fresh markets and settle them all as losses. Returns the next t."""
        t = first_t
        for i in range(start, start + count):
            self.decide(cand(f"L{i}", f"M{i}", t))
            t += 1
        for i in range(start, start + count):
            self.settle(t, f"M{i}", "NO")
            t += 1
        return t

    def body_for(self, c: dict, decision: str = "EXECUTED", mutate=None) -> dict:
        """The decision body an honest engine would write for ``c`` at full size (then optionally altered)."""
        st = self.state
        dec = signal.decompose(c, self.rules)
        tally = validators.evaluate(c, self.markets.get(c["market_id"]), self.rules.validators)
        sizing_body = None
        if dec.missing is None:
            size = sizing.size(dec.p_hat_ppm, dec.q_eff_ppm, st.equity_units, Fraction(1), self.rules.risk)
            sizing_body = size.as_body(equity_units=st.equity_units, level=0, multiplier=Fraction(1), cfg=self.rules.risk)
        body = {"candidate_id": c["candidate_id"], "market_id": c["market_id"], "side": c.get("side"),
                "decision": decision, "rule_id": "TEST", "reason": "TEST", "also": [],
                "decomposition": dec.as_body(), "votes": tally.votes, "approvals": tally.approvals,
                "vetoes": tally.vetoes, "sizing": sizing_body}
        if mutate is not None:
            mutate(body)
        return body

    def forge(self, c: dict, mutate=None, *, log_candidate: bool = True) -> str | None:
        """Try to write an EXECUTED row for ``c``. Returns the refusal code, or None if it was accepted.

        A refusal must leave no trace: the number of rows and the bytes of the file are compared.
        """
        if log_candidate:
            self.ledger.candidate(c["t"], c)
        body = self.body_for(c, mutate=mutate)
        rows, size = self.ledger.rows, self.path.stat().st_size
        try:
            self.ledger.decision(c["t"], body)
        except LimitViolation as exc:
            assert self.ledger.rows == rows and self.path.stat().st_size == size, "a refused row left a trace"
            return exc.code
        return None


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def audit(path: Path, market_map: dict, params: dict | None = None) -> dict:
    from corpus import reference_drawdown
    return reference_drawdown.audit(path, market_map, params)


def make_world(candidates: list[dict], market_map: dict, outcomes: dict, *, baseline_until_t: int = 0,
               requests: list[dict] | None = None, name: str = "hand") -> Path:
    """Write a small hand-made world to a scratch folder, in the layout the harness reads."""
    out = scratch("world")
    steps = max(m["resolves_at"] for m in market_map.values()) + 1
    meta = {"world": name, "code": "T", "seed": None, "as_of": AS_OF, "steps": steps,
            "baseline_until_t": baseline_until_t, "markets": len(market_map), "candidates": len(candidates),
            "generator_version": "hand-written", "stress": False, "synthetic": True,
            "note": "Hand-written test world. Not real data."}

    def jsonl(rows):
        return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)

    files = {"meta.json": json.dumps(meta, sort_keys=True, indent=2) + "\n",
             "markets.jsonl": jsonl(market_map.values()), "candidates.jsonl": jsonl(candidates),
             "outcomes.jsonl": jsonl({"market_id": k, "t": market_map[k]["resolves_at"], "outcome": v}
                                     for k, v in outcomes.items())}
    if requests:
        files["requests.jsonl"] = jsonl(requests)
    for rel, text in files.items():
        (out / rel).write_text(text, encoding="utf-8", newline="\n")
    return out

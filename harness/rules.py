"""Ordered rule files: the rule decides, the code only extracts the facts.

Every rule list is evaluated top to bottom and the FIRST rule whose conditions all hold wins; vetoes
and exceptions sit on top. To change a decision you change a rule file, never an output.

A condition is ``[fact, op]`` or ``[fact, op, value]``. A fact that is ``None`` (unknown) satisfies
only ``is_null``: an unknown value never satisfies a comparison, so an unknown never approves.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from harness.exact import canonical, fparse, is_int, read_json, sha256_text

RULE_FILES = ("gate.json", "validators.json", "risk_limits.json", "throttle.json", "costs.json")
_OPS = ("is_null", "not_null", "==", "!=", ">", ">=", "<", "<=")


class RuleError(ValueError):
    """A rule file is malformed. The run stops: a wrong rule is never silently skipped."""


def holds(cond: list, facts: dict[str, Any]) -> bool:
    if not isinstance(cond, list) or len(cond) not in (2, 3):
        raise RuleError(f"malformed condition: {cond!r}")
    name, op = cond[0], cond[1]
    if op not in _OPS:
        raise RuleError(f"unknown operator {op!r} in {cond!r}")
    if name not in facts:
        raise RuleError(f"unknown fact {name!r} in {cond!r}")
    value = facts[name]
    if op == "is_null":
        return value is None
    if op == "not_null":
        return value is not None
    if len(cond) != 3:
        raise RuleError(f"operator {op!r} needs a value: {cond!r}")
    if value is None:
        return False
    ref = cond[2]
    if op == "==":
        return value == ref
    if op == "!=":
        return value != ref
    if isinstance(value, bool) or isinstance(ref, bool):
        raise RuleError(f"ordering comparison on a boolean: {cond!r}")
    if op == ">":
        return value > ref
    if op == ">=":
        return value >= ref
    if op == "<":
        return value < ref
    return value <= ref


def first_match(rules: list[dict], facts: dict[str, Any]) -> dict:
    for rule in rules:
        if all(holds(c, facts) for c in rule.get("when", [])):
            return rule
    raise RuleError("no rule matched and the rule list has no default")


def all_matches(rules: list[dict], facts: dict[str, Any]) -> list[dict]:
    return [r for r in rules if all(holds(c, facts) for c in r.get("when", []))]


@dataclass(frozen=True)
class Rules:
    gate: dict
    validators: dict
    risk: dict
    throttle: dict
    costs: dict
    digest: str  # sha256 over the canonical content of the five files

    @property
    def threshold_ppm(self) -> int:
        return self.gate["threshold_ppm"]

    @property
    def kelly_multiplier(self) -> Fraction:
        return fparse(self.risk["kelly_multiplier"])

    def limits_snapshot(self) -> dict:
        """The limits written into the ledger's first row: the ledger refuses what violates them."""
        r = self.risk
        return {
            "threshold_ppm": self.gate["threshold_ppm"],
            "kelly_multiplier": r["kelly_multiplier"],
            "cap_ppm": r["cap_ppm"],
            "max_open_positions_per_market": r["max_open_positions_per_market"],
            "max_portfolio_exposure_ppm": r["max_portfolio_exposure_ppm"],
            "drawdown_warn_ppm": r["drawdown_warn_ppm"],
            "exposure_when_warned_ppm": r["exposure_when_warned_ppm"],
            "drawdown_halt_ppm": r["drawdown_halt_ppm"],
            "initial_bankroll_units": r["initial_bankroll_units"],
        }


def _need_int(obj: dict, key: str, lo: int, hi: int, where: str) -> None:
    if not is_int(obj.get(key)) or not lo <= obj[key] <= hi:
        raise RuleError(f"{where}: {key!r} must be an integer in [{lo}, {hi}], got {obj.get(key)!r}")


def _validate(gate: dict, validators: dict, risk: dict, throttle: dict, costs: dict) -> None:
    _need_int(gate, "threshold_ppm", 0, 1_000_000, "gate.json")
    if gate.get("comparison") != "strictly_greater":
        raise RuleError("gate.json: 'comparison' must be 'strictly_greater' (edge - cost > threshold)")
    rules = gate.get("decision_rules")
    if not isinstance(rules, list) or not rules or rules[-1].get("when") != []:
        raise RuleError("gate.json: 'decision_rules' must end with a default rule (empty 'when')")
    if rules[-1].get("decision") != "REJECTED":
        raise RuleError("gate.json: the default decision must be REJECTED (fail closed)")
    for r in rules:
        if r.get("decision") not in ("EXECUTED", "REJECTED") or not isinstance(r.get("id"), str):
            raise RuleError(f"gate.json: malformed decision rule {r!r}")

    names = validators.get("quorum", {}).get("validators")
    if not isinstance(names, list) or len(names) < 2:
        raise RuleError("validators.json: quorum.validators must list at least two validators")
    _need_int(validators["quorum"], "min_approvals", 2, len(names), "validators.json quorum")
    seen_default = set()
    for r in validators.get("rules", []):
        if r.get("validator") not in names or r.get("vote") not in ("VETO", "APPROVE", "ABSTAIN"):
            raise RuleError(f"validators.json: malformed rule {r!r}")
        if r.get("when") == []:
            seen_default.add(r["validator"])
    if seen_default != set(names):
        raise RuleError("validators.json: every validator needs a default rule (empty 'when')")

    for key in ("cap_ppm", "max_portfolio_exposure_ppm", "drawdown_warn_ppm", "exposure_when_warned_ppm",
                "drawdown_halt_ppm"):
        _need_int(risk, key, 1, 1_000_000, "risk_limits.json")
    _need_int(risk, "initial_bankroll_units", 1, 10**15, "risk_limits.json")
    _need_int(risk, "max_open_positions_per_market", 1, 1000, "risk_limits.json")
    mult = fparse(risk["kelly_multiplier"])
    if not 0 < mult <= 1:
        raise RuleError("risk_limits.json: kelly_multiplier must be in (0, 1]")
    div = risk.get("divergence", {})
    for key, lo, hi in (("n_sigma", 1, 10), ("window_trades", 2, 10**6), ("min_baseline_trades", 2, 10**6)):
        _need_int(div, key, lo, hi, "risk_limits.json divergence")

    shrink = fparse(throttle["shrink"])
    if not 0 < shrink < 1:
        raise RuleError("throttle.json: shrink must be in (0, 1)")
    _need_int(throttle, "max_level", 1, 64, "throttle.json")
    _need_int(throttle, "cooldown_steps", 0, 10**9, "throttle.json")
    _need_int(throttle, "reexpansion_edge_bar_ppm", 0, 1_000_000, "throttle.json")
    if throttle["reexpansion_edge_bar_ppm"] <= gate["threshold_ppm"]:
        raise RuleError("throttle.json: the re-expansion edge bar must be higher than the execute threshold")

    _need_int(costs, "latency_ppm_per_step", 0, 1_000_000, "costs.json")
    _need_int(costs, "max_latency_steps", 0, 10**6, "costs.json")
    _need_int(costs, "max_component_ppm", 1, 1_000_000, "costs.json")


def load(rules_dir: Path) -> Rules:
    rules_dir = Path(rules_dir)
    docs = {}
    for name in RULE_FILES:
        path = rules_dir / name
        if not path.is_file():
            raise RuleError(f"missing rule file: {name}")
        docs[name] = read_json(path)
    _validate(docs["gate.json"], docs["validators.json"], docs["risk_limits.json"], docs["throttle.json"],
              docs["costs.json"])
    digest = sha256_text("".join(f"{n}\n{canonical(docs[n])}\n" for n in RULE_FILES))
    return Rules(gate=docs["gate.json"], validators=docs["validators.json"], risk=docs["risk_limits.json"],
                 throttle=docs["throttle.json"], costs=docs["costs.json"], digest=digest)

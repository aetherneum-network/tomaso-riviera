"""Drawdown circuit breaker. It halts by itself; it re-arms only on a human consent record.

Halt: drawdown from the running peak >= drawdown_halt_ppm, checked at every settlement.
Re-arm: refused unless a consent record is presented that (a) declares a human operator, (b) names the
exact halt it lifts by the hash of its ledger row, (c) acknowledges the drawdown that caused it.

What this does NOT prove: that a human really wrote the record. The harness checks the form of the
record and its binding to one specific halt; who holds the pen is an organisational control.
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.exact import PPM, is_int

CONSENT_KIND = "HUMAN_REARM_CONSENT"
_NOT_HUMAN = ("agent", "auto", "bot", "harness", "system", "model", "ai")


class RearmRefused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def drawdown_ppm(equity_units: int, peak_units: int) -> int:
    if peak_units <= 0:
        return PPM
    return max(0, ((peak_units - equity_units) * PPM) // peak_units)


def tripped(equity_units: int, peak_units: int, halt_ppm: int) -> bool:
    """Exact comparison, no rounding: (peak - equity) / peak >= halt."""
    return (peak_units - equity_units) * PPM >= halt_ppm * peak_units


def load_consent(path: str | Path | None) -> tuple[dict | None, str | None]:
    if path is None:
        return None, "NO_CONSENT_RECORD"
    p = Path(path)
    if not p.is_file():
        return None, "CONSENT_FILE_NOT_FOUND"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "CONSENT_UNREADABLE"
    if not isinstance(data, dict):
        return None, "CONSENT_UNREADABLE"
    return data, None


def validate_consent(consent: object, halt_row: dict | None, t: int) -> str | None:
    """Return None if the consent lifts this halt, else the reason of the refusal."""
    if halt_row is None:
        return "NOT_HALTED"
    if not isinstance(consent, dict):
        return "NO_CONSENT_RECORD"
    if consent.get("kind") != CONSENT_KIND:
        return "CONSENT_WRONG_KIND"
    operator = consent.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        return "CONSENT_NO_OPERATOR"
    if consent.get("operator_is_human") is not True:
        return "CONSENT_NOT_HUMAN"
    lowered = operator.strip().lower()
    if any(lowered == w or lowered.startswith((w + "-", w + "_", w + " ")) for w in _NOT_HUMAN):
        return "CONSENT_NOT_HUMAN"
    if consent.get("halt_hash") != halt_row["hash"]:
        return "CONSENT_FOR_ANOTHER_HALT"
    if consent.get("acknowledged_drawdown_ppm") != halt_row["body"]["drawdown_ppm"]:
        return "CONSENT_DRAWDOWN_NOT_ACKNOWLEDGED"
    statement = consent.get("statement")
    if not isinstance(statement, str) or len(statement.strip()) < 20:
        return "CONSENT_NO_STATEMENT"
    signed = consent.get("signed_at_t")
    if not is_int(signed) or signed < halt_row["t"] or signed > t:
        return "CONSENT_TIME_INVALID"
    return None


def request_rearm(ledger, consent_path: str | Path | None, t: int) -> dict:
    """Ask the ledger to re-arm. Every request is recorded, accepted or not. Raises RearmRefused."""
    consent, reason = load_consent(consent_path)
    return ledger.rearm(t, consent, load_error=reason)

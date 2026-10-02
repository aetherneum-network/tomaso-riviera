"""Validator chain: asymmetric on purpose. One veto refuses; approval needs a quorum.

The code extracts four facts from the candidate; ``rules/validators.json`` decides each vote.
"""
from __future__ import annotations

from dataclasses import dataclass

from harness.exact import PPM, is_int
from harness.rules import first_match


@dataclass(frozen=True)
class Tally:
    votes: dict           # validator -> "VOTE:rule_id"
    vetoes: list          # validators that voted VETO, in quorum order
    approvals: int
    quorum_met: bool

    @property
    def veto_count(self) -> int:
        return len(self.vetoes)


def extract_facts(candidate: dict, market: dict | None) -> dict:
    t = candidate.get("t")
    price = candidate.get("price_ppm")
    as_of = candidate.get("price_as_of")
    complement = candidate.get("complement_price_ppm")
    confirm = candidate.get("confirm_ppm")
    resolves_at = (market or {}).get("resolves_at")
    return {
        "price_age_steps": t - as_of if is_int(t) and is_int(as_of) else None,
        "book_gap_ppm": abs(price + complement - PPM) if is_int(price) and is_int(complement) else None,
        "steps_to_resolution": resolves_at - t if is_int(t) and is_int(resolves_at) else None,
        "confirm_delta_ppm": confirm - price if is_int(price) and is_int(confirm) else None,
    }


def evaluate(candidate: dict, market: dict | None, cfg: dict) -> Tally:
    facts = extract_facts(candidate, market)
    names = cfg["quorum"]["validators"]
    votes, vetoes, approvals = {}, [], 0
    for name in names:
        rule = first_match([r for r in cfg["rules"] if r["validator"] == name], facts)
        votes[name] = f"{rule['vote']}:{rule['id']}"
        if rule["vote"] == "VETO":
            vetoes.append(name)
        elif rule["vote"] == "APPROVE":
            approvals += 1
    return Tally(votes=votes, vetoes=vetoes, approvals=approvals,
                 quorum_met=approvals >= cfg["quorum"]["min_approvals"])

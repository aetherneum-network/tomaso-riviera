"""Decomposition of a candidate: prior, signal, confidence, edge after cost.

    p_hat    = prior + confidence x (signal - prior)      (floored to ppm: never rounds the edge up)
    edge     = p_hat - price
    net edge = edge - cost
    execute condition (gate): net edge > threshold, strictly

The label ``conviction`` is carried by candidates and is deliberately never read here or in sizing.
"""
from __future__ import annotations

from dataclasses import dataclass

from harness import costs
from harness.exact import PPM, is_int
from harness.rules import Rules


@dataclass(frozen=True)
class Decomposition:
    missing: str | None
    prior_ppm: int | None = None
    signal_ppm: int | None = None
    confidence_ppm: int | None = None
    p_hat_ppm: int | None = None
    price_ppm: int | None = None
    cost_ppm: int | None = None
    q_eff_ppm: int | None = None
    edge_ppm: int | None = None
    net_edge_ppm: int | None = None
    threshold_ppm: int | None = None
    gate_pass: bool | None = None

    def as_body(self) -> dict | None:
        if self.missing is not None:
            return None
        return {"prior_ppm": self.prior_ppm, "signal_ppm": self.signal_ppm, "confidence_ppm": self.confidence_ppm,
                "p_hat_ppm": self.p_hat_ppm, "price_ppm": self.price_ppm, "cost_ppm": self.cost_ppm,
                "q_eff_ppm": self.q_eff_ppm, "edge_ppm": self.edge_ppm, "net_edge_ppm": self.net_edge_ppm,
                "threshold_ppm": self.threshold_ppm, "gate_pass": self.gate_pass}


def _prob(value: object, lo: int = 0, hi: int = PPM) -> bool:
    return is_int(value) and lo <= value <= hi


def decompose(candidate: dict, rules: Rules) -> Decomposition:
    """Every candidate gets a number, or it does not trade: anything missing is a refusal with its reason."""
    if candidate.get("side") not in ("YES", "NO"):
        return Decomposition(missing="MISSING_SIDE")
    price = candidate.get("price_ppm")
    if not _prob(price, 1, PPM - 1):
        return Decomposition(missing="MISSING_PRICE")
    prior, signal = candidate.get("prior_ppm"), candidate.get("signal_ppm")
    if not _prob(prior):
        return Decomposition(missing="MISSING_PRIOR")
    if not _prob(signal):
        return Decomposition(missing="MISSING_SIGNAL")
    confidence = candidate.get("confidence_ppm")
    if not _prob(confidence):
        return Decomposition(missing="MISSING_CONFIDENCE")
    cost_ppm, reason = costs.total_cost_ppm(candidate.get("cost"), rules.costs)
    if reason is not None:
        return Decomposition(missing=reason)
    q_eff = price + cost_ppm
    if q_eff >= PPM:
        return Decomposition(missing="COST_OUT_OF_MODEL")
    p_hat = prior + (confidence * (signal - prior)) // PPM  # floor division: rounds toward the smaller estimate
    edge = p_hat - price
    net = edge - cost_ppm
    threshold = rules.threshold_ppm
    return Decomposition(missing=None, prior_ppm=prior, signal_ppm=signal, confidence_ppm=confidence,
                         p_hat_ppm=p_hat, price_ppm=price, cost_ppm=cost_ppm, q_eff_ppm=q_eff, edge_ppm=edge,
                         net_edge_ppm=net, threshold_ppm=threshold, gate_pass=net > threshold)

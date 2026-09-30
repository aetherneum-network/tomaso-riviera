"""Simulated cost model: fee + slippage + latency. The cost is part of the edge, not bolted on after.

This is a model of cost on a synthetic world. It is not a model of any real venue.
"""
from __future__ import annotations

from harness.exact import is_int


def total_cost_ppm(cost: object, cfg: dict) -> tuple[int | None, str | None]:
    """Return (cost_ppm, None) or (None, reason). A missing or out-of-model cost is never defaulted."""
    if not isinstance(cost, dict):
        return None, "MISSING_COST"
    parts = []
    for key in ("fee_ppm", "slippage_ppm", "latency_steps"):
        value = cost.get(key)
        if not is_int(value) or value < 0:
            return None, "MISSING_COST"
        parts.append(value)
    fee, slippage, latency = parts
    if fee > cfg["max_component_ppm"] or slippage > cfg["max_component_ppm"] or latency > cfg["max_latency_steps"]:
        return None, "COST_OUT_OF_MODEL"
    return fee + slippage + latency * cfg["latency_ppm_per_step"], None

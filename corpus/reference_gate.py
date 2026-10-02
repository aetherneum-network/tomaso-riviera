"""Independent reference: the execute condition, the validators, and the condition on the TRUE probability.

This file does not import the code under test and does not read the rule files: the constants below are
the pack's claims written out by hand. If a rule file drifts from them, the comparison in the tests and
in ``eval/score.py`` shows it.

Two different questions are answered here and must not be confused:

  * ``stateless(candidate, market)``: what the harness must decide from what it is allowed to see
    (the ESTIMATED edge). This is the expected decision with its reason.
  * ``true_gate(candidate, p_side_true_ppm)``: whether the execute condition would hold on the TRUE
    probability, known only to the generator. This measures the signal, not the harness: in the null
    world every execution is, by construction, a false positive.
"""
from __future__ import annotations

ONE = 1_000_000

PARAMS = {
    "threshold_ppm": 20_000,             # net edge must be strictly greater
    "latency_ppm_per_step": 1_500,
    "max_latency_steps": 6,
    "max_component_ppm": 100_000,        # fee or slippage above this is outside the cost model
    "max_price_age_steps": 3,            # older than this: veto
    "max_book_gap_ppm": 50_000,          # |price + complement - 1| above this: veto
    "min_steps_to_resolution": 3,        # fewer than this: veto
    "confirm_veto_delta_ppm": -60_000,   # confirmation at or below price + this: veto
    "confirm_approve_delta_ppm": 10_000,  # confirmation at or above price + this: approve
    "min_approvals": 3,                  # of four validators; one veto refuses
}


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def estimate(cand: dict, params: dict = PARAMS):
    """Return the estimate as a dict, or the refusal reason as a string. Nothing missing is defaulted."""
    if cand.get("side") not in ("YES", "NO"):
        return "MISSING_SIDE"
    price = cand.get("price_ppm")
    if not _int(price) or not 0 < price < ONE:
        return "MISSING_PRICE"
    prior = cand.get("prior_ppm")
    if not _int(prior) or not 0 <= prior <= ONE:
        return "MISSING_PRIOR"
    signal = cand.get("signal_ppm")
    if not _int(signal) or not 0 <= signal <= ONE:
        return "MISSING_SIGNAL"
    confidence = cand.get("confidence_ppm")
    if not _int(confidence) or not 0 <= confidence <= ONE:
        return "MISSING_CONFIDENCE"
    cost = cand.get("cost")
    if not isinstance(cost, dict):
        return "MISSING_COST"
    fee, slippage, latency = cost.get("fee_ppm"), cost.get("slippage_ppm"), cost.get("latency_steps")
    if not all(_int(v) and v >= 0 for v in (fee, slippage, latency)):
        return "MISSING_COST"
    if max(fee, slippage) > params["max_component_ppm"] or latency > params["max_latency_steps"]:
        return "COST_OUT_OF_MODEL"
    cost_ppm = fee + slippage + latency * params["latency_ppm_per_step"]
    if price + cost_ppm >= ONE:
        return "COST_OUT_OF_MODEL"
    # The estimate moves from the prior toward the signal by the confidence; the floor never rounds it up.
    p_hat = prior + (confidence * (signal - prior)) // ONE
    return {"p_hat_ppm": p_hat, "price_ppm": price, "cost_ppm": cost_ppm, "q_eff_ppm": price + cost_ppm,
            "net_edge_ppm": p_hat - price - cost_ppm}


def votes(cand: dict, market: dict | None, params: dict = PARAMS) -> dict:
    """The four validators. Each returns VETO, APPROVE or ABSTAIN; a missing check never approves."""
    t, price = cand.get("t"), cand.get("price_ppm")
    out = {}

    as_of = cand.get("price_as_of")
    if not (_int(t) and _int(as_of)):
        out["price_feed"] = "VETO"
    else:
        age = t - as_of
        out["price_feed"] = "VETO" if age > params["max_price_age_steps"] or age < 0 else "APPROVE"

    complement = cand.get("complement_price_ppm")
    if not (_int(price) and _int(complement)):
        out["cross_market"] = "ABSTAIN"
    else:
        out["cross_market"] = "VETO" if abs(price + complement - ONE) > params["max_book_gap_ppm"] else "APPROVE"

    resolves_at = (market or {}).get("resolves_at")
    if not (_int(t) and _int(resolves_at)):
        out["calendar"] = "VETO"
    else:
        out["calendar"] = "VETO" if resolves_at - t < params["min_steps_to_resolution"] else "APPROVE"

    confirm = cand.get("confirm_ppm")
    if not (_int(price) and _int(confirm)):
        out["confirmation"] = "ABSTAIN"
    elif confirm - price <= params["confirm_veto_delta_ppm"]:
        out["confirmation"] = "VETO"
    elif confirm - price >= params["confirm_approve_delta_ppm"]:
        out["confirmation"] = "APPROVE"
    else:
        out["confirmation"] = "ABSTAIN"
    return out


def stateless(cand: dict, market: dict | None, params: dict = PARAMS) -> tuple[str | None, dict | None, dict]:
    """(refusal reason or None, estimate or None, votes) from the candidate alone, in the order of the gate.

    Halt and pause are states of the account, not of the candidate: the caller places them between the
    "missing" refusals and the validator refusals (see ``reference_drawdown``).
    """
    est = estimate(cand, params)
    ballot = votes(cand, market, params)
    if isinstance(est, str):
        return est, None, ballot
    if "VETO" in ballot.values():
        return "VALIDATOR_VETO", est, ballot
    if sum(1 for v in ballot.values() if v == "APPROVE") < params["min_approvals"]:
        return "NO_QUORUM", est, ballot
    if not est["net_edge_ppm"] > params["threshold_ppm"]:
        return "EDGE_NOT_ABOVE_THRESHOLD", est, ballot
    return None, est, ballot


def true_gate(cand: dict, p_side_true_ppm: int, params: dict = PARAMS) -> dict | None:
    """The execute condition evaluated on the true probability of the proposed side (generator's truth)."""
    est = estimate(cand, params)
    if isinstance(est, str):
        return None
    true_net = p_side_true_ppm - est["price_ppm"] - est["cost_ppm"]
    return {"true_net_edge_ppm": true_net, "true_gate": true_net > params["threshold_ppm"],
            "estimated_net_edge_ppm": est["net_edge_ppm"]}

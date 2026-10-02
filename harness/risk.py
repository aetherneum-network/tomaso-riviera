"""Risk engine: a veto layer downstream of the signal. It can only refuse, never enlarge.

Portfolio limits override any signal strength:
  * per market: at most ``max_open_positions_per_market`` open positions (one), and open stake in the
    market + new stake <= cap x equity: the cap cannot be stacked by repeating a signal;
  * portfolio: open stake + new stake <= exposure limit x equity; the limit tightens once the
    drawdown reaches the warning threshold;
  * cash: a stake larger than the simulated cash is refused.
"""
from __future__ import annotations

from harness.exact import PPM


def exposure_limit_ppm(drawdown_ppm: int, cfg: dict) -> int:
    if drawdown_ppm >= cfg["drawdown_warn_ppm"]:
        return cfg["exposure_when_warned_ppm"]
    return cfg["max_portfolio_exposure_ppm"]


def veto(stake_units: int, market_id: str, state, cfg: dict) -> str | None:
    """``state`` is the ledger's own account state, re-read at every decision, never remembered."""
    equity = state.equity_units
    if len(state.positions.get(market_id, [])) >= cfg["max_open_positions_per_market"]:
        return "RISK_POSITION_ALREADY_OPEN"
    if state.open_in_market(market_id) + stake_units > (cfg["cap_ppm"] * equity) // PPM:
        return "RISK_MARKET_CAP"
    if state.open_stake_units + stake_units > (exposure_limit_ppm(state.drawdown_ppm, cfg) * equity) // PPM:
        return "RISK_PORTFOLIO_EXPOSURE"
    if stake_units > state.cash_units:
        return "RISK_INSUFFICIENT_CASH"
    return None

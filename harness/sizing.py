"""Position sizing: a fraction of Kelly, hard-capped, never a function of conviction.

For a binary contract bought at effective price q (price + cost) that pays 1 if it wins:

    Kelly  f* = (p - q) / (1 - q)
    used      = min(kelly_multiplier x f*, cap)
    limit     = floor(used x equity)                  (the hard limit of the position)
    stake     = floor(used x throttle x equity)       (throttle <= 1, so stake <= limit)

``size`` takes probabilities, equity and the throttle multiplier - nothing else. There is no parameter
through which a conviction label or a confidence figure could reach the size.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from harness.exact import PPM, floor_fraction, fparse, fstr


@dataclass(frozen=True)
class Sizing:
    kelly: Fraction
    fraction_used: Fraction
    limit_units: int
    stake_units: int
    payout_if_win_units: int

    def as_body(self, *, equity_units: int, level: int, multiplier: Fraction, cfg: dict) -> dict:
        return {"kelly": fstr(self.kelly), "kelly_multiplier": cfg["kelly_multiplier"], "cap_ppm": cfg["cap_ppm"],
                "fraction_used": fstr(self.fraction_used), "throttle_level": level,
                "throttle_multiplier": fstr(multiplier), "equity_units": equity_units,
                "limit_units": self.limit_units, "stake_units": self.stake_units,
                "payout_if_win_units": self.payout_if_win_units}


def kelly_fraction(p_hat_ppm: int, q_eff_ppm: int) -> Fraction:
    if not 0 < q_eff_ppm < PPM:
        raise ValueError("effective price must be strictly between 0 and 1")
    return Fraction(p_hat_ppm - q_eff_ppm, PPM - q_eff_ppm)


def payout_if_win(stake_units: int, q_eff_ppm: int) -> int:
    return (stake_units * PPM) // q_eff_ppm


def size(p_hat_ppm: int, q_eff_ppm: int, equity_units: int, throttle_multiplier: Fraction, cfg: dict) -> Sizing:
    if not 0 < throttle_multiplier <= 1:
        raise ValueError("throttle multiplier must be in (0, 1]")
    kelly = kelly_fraction(p_hat_ppm, q_eff_ppm)
    used = min(kelly * fparse(cfg["kelly_multiplier"]), Fraction(cfg["cap_ppm"], PPM))
    used = max(used, Fraction(0))
    limit = floor_fraction(used * equity_units)
    stake = floor_fraction(used * throttle_multiplier * equity_units)
    return Sizing(kelly=kelly, fraction_used=used, limit_units=limit, stake_units=stake,
                  payout_if_win_units=payout_if_win(stake, q_eff_ppm))

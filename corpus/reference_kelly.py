"""Independent reference: Kelly fraction and position size for a binary contract, exact fractions.

This file does not import the code under test. It is the closed formula, written from the definition.

A contract costs q (effective price: price + cost, as a fraction of 1) and pays 1 if it wins, 0 if not.
Staking a fraction f of the bankroll buys f/q contracts. With win probability p the expected log growth is

    g(f) = p * log(1 - f + f/q) + (1 - p) * log(1 - f)

Net odds are b = (1 - q) / q, so g(f) = p * log(1 + b f) + (1 - p) * log(1 - f), which is maximal at

    f* = p - (1 - p) / b

The pack's claim: the fraction actually used is min(1/4 * f*, 5%), never negative, and the size is that
fraction of the simulated equity, multiplied by a throttle <= 1 and floored to whole units.

    python corpus/reference_kelly.py 500000 410000 1000000      # p (ppm), q (ppm), equity (units)
"""
from __future__ import annotations

import math
import sys
from fractions import Fraction

ONE = 1_000_000
QUARTER = Fraction(1, 4)
FIVE_PERCENT = Fraction(1, 20)


def kelly(p_ppm: int, q_ppm: int) -> Fraction:
    """Full Kelly fraction f* for win probability p and effective price q, both in ppm."""
    p, q = Fraction(p_ppm, ONE), Fraction(q_ppm, ONE)
    if not 0 < q < 1:
        raise ValueError("the effective price must be strictly between 0 and 1")
    odds = (1 - q) / q
    return p - (1 - p) / odds


def fraction_used(p_ppm: int, q_ppm: int, multiplier: Fraction = QUARTER, cap: Fraction = FIVE_PERCENT) -> Fraction:
    return max(Fraction(0), min(multiplier * kelly(p_ppm, q_ppm), cap))


def position_limit(p_ppm: int, q_ppm: int, equity_units: int, multiplier: Fraction = QUARTER,
                   cap: Fraction = FIVE_PERCENT) -> int:
    """The largest stake the claim allows on this candidate at this equity."""
    return math.floor(fraction_used(p_ppm, q_ppm, multiplier, cap) * equity_units)


def stake(p_ppm: int, q_ppm: int, equity_units: int, throttle: Fraction = Fraction(1),
          multiplier: Fraction = QUARTER, cap: Fraction = FIVE_PERCENT) -> int:
    if not 0 < throttle <= 1:
        raise ValueError("the throttle must be in (0, 1]")
    return math.floor(fraction_used(p_ppm, q_ppm, multiplier, cap) * throttle * equity_units)


def payout_if_win(stake_units: int, q_ppm: int) -> int:
    """Contracts bought = stake / q, each pays 1: floored to whole units."""
    return math.floor(Fraction(stake_units * ONE, q_ppm))


if __name__ == "__main__":
    p_arg, q_arg, equity_arg = (int(a) for a in sys.argv[1:4])
    f = kelly(p_arg, q_arg)
    print(f"kelly {f.numerator}/{f.denominator}; used {fraction_used(p_arg, q_arg)}; "
          f"limit {position_limit(p_arg, q_arg, equity_arg)} units; stake {stake(p_arg, q_arg, equity_arg)} units")

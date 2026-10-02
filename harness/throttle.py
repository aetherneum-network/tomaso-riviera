"""Drawdown-aware throttle: size shrinks after each loss; it re-expands slowly and only on two conditions.

  * each losing settlement raises the level by one (multiplier = shrink ^ level), up to max_level;
  * the level comes down by ONE only when both hold: at least ``cooldown_steps`` since the last change,
    and the candidate's net edge is above ``reexpansion_edge_bar_ppm`` (higher than the execute threshold).

The step down is previewed at decision time and committed only if the candidate is executed.
"""
from __future__ import annotations

from fractions import Fraction

from harness.exact import fparse


class Throttle:
    def __init__(self, cfg: dict):
        self.shrink: Fraction = fparse(cfg["shrink"])
        self.max_level: int = cfg["max_level"]
        self.cooldown: int = cfg["cooldown_steps"]
        self.bar_ppm: int = cfg["reexpansion_edge_bar_ppm"]
        self.level = 0
        self.last_change_t: int | None = None

    def multiplier(self, level: int | None = None) -> Fraction:
        return self.shrink ** (self.level if level is None else level)

    def on_loss(self, t: int) -> None:
        self.level = min(self.level + 1, self.max_level)
        self.last_change_t = t  # a loss restarts the clock even at the maximum level

    def preview(self, t: int, net_edge_ppm: int) -> tuple[int, Fraction]:
        level = self.level
        if (level > 0 and self.last_change_t is not None and t - self.last_change_t >= self.cooldown
                and net_edge_ppm > self.bar_ppm):
            level -= 1
        return level, self.multiplier(level)

    def commit(self, level: int, t: int) -> None:
        if level != self.level:
            self.level = level
            self.last_change_t = t

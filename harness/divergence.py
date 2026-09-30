"""Divergence of the replayed stream from the backtested baseline, in exact arithmetic.

Per settled paper trade the realised edge is  x = payoff - effective price  (ppm per contract; payoff is
1,000,000 or 0). The backtest gives the baseline mean mu and variance s2 over n_b trades. The replay is
cut into consecutive windows of n_w settled trades with mean m. The signal is PAUSED when

    (m - mu)^2  >  n_sigma^2 x s2 x (1/n_w + 1/n_b)

i.e. when the window mean is more than n_sigma standard errors from the baseline, on either side. The
comparison is between exact fractions: no square root, no float.

Nothing here is "live": the stream is a replay of a synthetic world. A pause is final for the run;
lifting it needs a new baseline and is out of scope in v2.0.
"""
from __future__ import annotations

from fractions import Fraction

from harness.exact import fparse, fstr


def baseline_from(xs: list[int]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean_ppm": "0/1", "variance_ppm2": "0/1"}
    mean = Fraction(sum(xs), n)
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else Fraction(0)
    return {"n": n, "mean_ppm": fstr(mean), "variance_ppm2": fstr(var)}


class Detector:
    def __init__(self, baseline: dict | None, cfg: dict):
        self.n_sigma: int = cfg["n_sigma"]
        self.window_size: int = cfg["window_trades"]
        self.baseline = baseline
        self.reason_unarmed: str | None = None
        if baseline is None:
            self.reason_unarmed = "NO_BASELINE"
        elif baseline["n"] < cfg["min_baseline_trades"]:
            self.reason_unarmed = "BASELINE_TOO_SMALL"
        elif fparse(baseline["variance_ppm2"]) <= 0:
            self.reason_unarmed = "BASELINE_WITHOUT_VARIANCE"
        self.armed = self.reason_unarmed is None
        self._window: list[int] = []
        self._index = 0

    def observe(self, x_ppm: int) -> dict | None:
        """Feed one settled trade. Returns the verdict of a window when it completes, else None."""
        if not self.armed:
            return None
        self._window.append(x_ppm)
        if len(self._window) < self.window_size:
            return None
        n_w, n_b = self.window_size, self.baseline["n"]
        mean = Fraction(sum(self._window), n_w)
        mu, var = fparse(self.baseline["mean_ppm"]), fparse(self.baseline["variance_ppm2"])
        deviation_sq = (mean - mu) ** 2
        allowed_sq = self.n_sigma ** 2 * var * (Fraction(1, n_w) + Fraction(1, n_b))
        verdict = {"window_index": self._index, "window_trades": n_w, "window_mean_ppm": fstr(mean),
                   "baseline_mean_ppm": fstr(mu), "baseline_trades": n_b, "n_sigma": self.n_sigma,
                   "deviation_sq": fstr(deviation_sq), "allowed_sq": fstr(allowed_sq),
                   "diverged": deviation_sq > allowed_sq}
        self._window = []
        self._index += 1
        return verdict

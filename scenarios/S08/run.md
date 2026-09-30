# S08 - The replay is paused when it diverges from the backtested baseline by more than N sigma

*Claim A7 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Three parts. First, a boundary computed by hand: with a baseline of 100 trades and the rule of `rules/risk_limits.json` (4 sigma, windows of 50), a window mean of 69,631 ppm is inside and 69,632 is outside, in exact fractions. Second, the development corpus is regenerated from its seed and replayed: in the world whose edge disappears at a known instant the signal is paused after that instant, within the declared 150 settled trades and before any halt, and later candidates are refused with `SIGNAL_PAUSED`; the stationary world is not paused. Third, the operating point on seeded synthetic sequences: at most 2% of stationary runs paused, at least 85% of runs with a change paused within 150 trades. The replay is a replay of generated data: nothing here is a live stream, and the size of the planted shift is large by design.

```
python scenarios/S08/check.py     # one line: S08 PASS / S08 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

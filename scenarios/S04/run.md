# S04 - The size is min(quarter Kelly, 5%) and never grows with conviction

*Claim A3 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Pairs of candidates identical in every figure and labelled `low` and `extreme` conviction must get the same size: 38,135 units where a quarter of Kelly is 3.81%, 50,000 where the 5% cap binds. A candidate with full confidence on a weaker signal that yields the same estimate gets the same size; a candidate with `extreme` conviction and a net edge of 10,000 ppm is not a trade. Every size is compared with the closed formula in `corpus/reference_kelly.py`, written independently of the harness, and the checker verifies that the sizing function has no parameter through which a conviction could enter. Forged rows one unit above the cap, or at the cap where a quarter of Kelly is lower, are refused (`SIZE_OVER_LIMIT`).

```
python scenarios/S04/check.py     # one line: S04 PASS / S04 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

# S01 - Negative: a candidate without a confidence or without a cost is never traded

*Claim A1 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Six hand-written candidates on four fictitious markets: one with no confidence, one with the word "high" in its place, one with no cost, one with a cost missing its slippage, one with a latency outside the cost model, and one complete candidate as the control. The harness must log all six, refuse the five incomplete ones as `REJECTED` with the reason (`MISSING_CONFIDENCE`, `MISSING_COST`, `COST_OUT_OF_MODEL`) and open exactly one paper position, of 38,135 units, for the control. The checker also plays a faulty engine: it tries to write an `EXECUTED` row for an incomplete candidate and the ledger must refuse it (`EXECUTION_OF_INCOMPLETE_CANDIDATE`) leaving the file untouched.

```
python scenarios/S01/check.py     # one line: S01 PASS / S01 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

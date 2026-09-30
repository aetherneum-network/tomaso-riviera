# S02 - Boundary: a net edge equal to the threshold does not trade

*Claim A1 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Five candidates whose net edge (estimate minus price minus cost) is 20,000 ppm exactly, 20,001, 19,999, 20,000 after a rounding that goes down, and -5,000 because the cost is larger than the edge. The threshold in `rules/gate.json` is 20,000 ppm and the condition is strictly greater: only the candidate at 20,001 is executed, for 8,475 units. The checker then tries to force an `EXECUTED` row for the candidate at the threshold, first honestly (`EDGE_NOT_ABOVE_THRESHOLD`), then with a false decomposition claiming 20,001 (`DECOMPOSITION_MISMATCH`): the ledger recomputes the edge from the candidate row and refuses both.

```
python scenarios/S02/check.py     # one line: S02 PASS / S02 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

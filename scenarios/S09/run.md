# S09 - Leak test: a signal that uses the future fails the backtest

*Claim A7 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Three signal functions are backtested on a small null world. One reads the outcome of the candidate's own market; one, subtler, reads the last quote of the market in the whole stream; one uses only quotes dated up to the candidate. The harness calls each function twice per candidate, with the whole stream and with the stream cut at the candidate's time: the first two fail (`LOOKAHEAD`, at the first and at the second candidate), and a failed backtest writes no ledger and no baseline. The third passes and, having no information, trades nothing. Limit: the test sees only what goes through the view it hands to the function.

```
python scenarios/S09/check.py     # one line: S09 PASS / S09 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

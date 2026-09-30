# S05 - The breaker halts at a 20% drawdown; re-arming needs a human consent record

*Claim A4 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Six paper positions of 5% lose one after the other. At the fourth loss the equity is 800,000 from a peak of 1,000,000: the ledger writes a `HALT` row right after that settlement, at that instant. Strong signals that follow are `REJECTED` with `BREAKER_HALTED`. Four re-arm requests are refused and recorded (`REARM_REFUSED`): no consent record, a record that declares an agent, a human record that names another halt, a file that does not exist. Only a consent that declares a human operator, names this halt by the hash of its row and acknowledges its drawdown re-arms the breaker; the next candidate is then executed at the throttled size of 11,074 units, not at full size. A forged `EXECUTED` row written while halted is refused (`EXECUTION_WHILE_HALTED`). What this does not prove: that a human really wrote the record.

```
python scenarios/S05/check.py     # one line: S05 PASS / S05 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

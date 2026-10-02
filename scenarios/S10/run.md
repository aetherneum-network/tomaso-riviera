# S10 - Rule change: the cap goes from 5% to 4% by editing the rule file

*Claim A3 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Five candidates sized at 2%, 3.81%, 4.5%, the cap, and exactly 4%. The same input is replayed twice: with the rule file as it is, and with a copy in which `cap_ppm` is 40,000. Only the two sizes above 4% change (45,000 and 50,000 become 40,000); the other three do not move by a unit. Each ledger records in its first row the limits and the digest of the rules it was written under. The old ledger, audited under the new cap, is flagged on exactly those two rows. No code and no output is edited: the decision changes because the rule changed.

```
python scenarios/S10/check.py     # one line: S10 PASS / S10 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

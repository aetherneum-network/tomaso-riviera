# S07 - The ledger is append-only: a candidate inserted after the outcome is detected

*Claim A6 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

A ten-row honest ledger, then five attacks by someone who already knows that a market resolved NO. (a) A correctly hashed candidate and executed decision are inserted at their false date: verification reports the first altered row, seq 7. (b) The forger also re-hashes every later row: the file verifies on its own - this limit is stated - and is caught by the published head hash and by the independent audit (a second position on the same market). (c) The forged candidate is appended with its old date: time goes backwards. (d) A stake is edited in place: the row hash no longer matches. (e) Through the ledger's own interface the candidate, the raw backdated row and the decision without a candidate are all refused, and the file is byte-identical afterwards. Nothing is deleted or repaired: verification only reads.

```
python scenarios/S07/check.py     # one line: S07 PASS / S07 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

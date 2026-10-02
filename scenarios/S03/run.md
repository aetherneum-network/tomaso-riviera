# S03 - Validators are asymmetric: one veto refuses, approval needs a quorum of three

*Claim A2 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Eight strong candidates (estimate 0.80 at price 0.40), so that only the validators can stop them. Three approvals and one veto: refused. Two approvals and two abstentions: refused, below quorum. A quote five steps old, a quote dated after the candidate, a market two steps from resolution: refused, each by its veto. Three approvals and one abstention, a quote exactly three steps old, a book gap exactly at the limit: executed at the 5% cap. Forged `EXECUTED` rows carrying a veto or fewer than three approvals are refused by the ledger. A stated limit is shown, not hidden: a row whose ballot itself is forged passes the ledger guard and is caught by the independent audit, which re-derives every vote.

```
python scenarios/S03/check.py     # one line: S03 PASS / S03 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

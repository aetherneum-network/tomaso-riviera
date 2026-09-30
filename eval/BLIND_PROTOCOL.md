# Blind protocol

*Paper trading on synthetic data only. Written by the author after the tag `v2.0.0-freeze`; to be executed
once, by a different hand.*

The author has generated and looked at four seeds only: 20260930 (dev), 20261001 (holdout), 20261002 (stress)
and 20260929 (rehearsal of this command on his own templates). He has never generated or seen a blind seed,
a fifth world or a hand-written candidate other than his own four templates. The scorer refuses those four
seeds, the author as runner, the author's templates (or copies of them) and a second run.

## Who runs it

A hand that is not the author: another session on the other model of the fleet (`claude-fable-5-1`, since the
code was written on `claude-opus-5-5`) or the Rector. The name given to `--runner` is recorded. Do not show
the seed, the fifth world or the candidates to the author before the run.

## 0. Check the freeze (nothing to install, offline, Python 3.12)

```
git diff --name-only v2.0.0-freeze HEAD
python tools/manifest.py --check
python -m unittest discover -s tests -t .
```

The first command must list only `eval/BLIND_PROTOCOL.md` and `eval/history.json`. The second must print
`MANIFEST OK` with `missing 0; differing 0`, and its `added later` lines may name only those two files (the
manifest names the commit just before its own, since a file cannot hold the hash of the commit that adds it).
The third must end with `OK`. If any of the three fails, stop: do not run the blind command, and write down
what failed.

## 1. Choose a seed

Any positive integer `N` other than 20260929, 20260930, 20261001 and 20261002. Do not try several and keep
one: the first seed is the seed.

## 2. Write a fifth world

Create the folder `build/blind/` (it is not tracked) and write `build/blind/fifth_world.json` with your own
parameters. `eval/blind/fifth_world.template.json` shows the format; the template itself, or a copy with the
same values, is refused. All values are integers.

| Key | Meaning | Allowed |
|---|---|---|
| `p_side_ppm` | `[low, high]` range of the true probability of the side offered | inside [100000, 950000] |
| `confidence_ppm` | `[low, high]` range of the confidence attached to a signal | inside [0, 1000000] |
| `regimes` | list of `{"from_t", "true_edge_ppm": [...], "signal_bias_ppm"}`; the first starts at `from_t` 0; `from_t` strictly increasing and below 6000; a regime may carry its own `p_side_ppm` | edges and bias inside [-300000, 300000] |
| `noise_ppm`, `confirm_noise_ppm` | noise of the signal and of the confirmation | 0 to 300000 |
| `fee_ppm` | fee | 0 to 200000 |
| `slippage_ppm` | `[low, high]` | inside [0, 200000] |
| `latency_steps` | `[low, high]` | inside [0, 50] |

In the generated world: price = true probability - true edge; signal = true probability + bias + noise.
Choose what you think will be hard: a small edge, a small or late change of regime, a biased signal, costs
near the threshold.

## 3. Write twenty candidates by hand

Write `build/blind/hand_candidates.jsonl`: at least twenty lines, one JSON object per line, integers only
(parts per million, units or steps; a decimal number is refused). Each line:

```
{"candidate": {"candidate_id": "X-01", "market_id": "X-M01", "t": 10, "side": "YES", "price_ppm": 400000, "price_as_of": 10, "complement_price_ppm": 600000, "prior_ppm": 400000, "signal_ppm": 900000, "confidence_ppm": 800000, "conviction": "low", "confirm_ppm": 450000, "cost": {"fee_ppm": 5000, "slippage_ppm": 5000, "latency_steps": 0}}, "market": {"market_id": "X-M01", "opens_at": 0, "resolves_at": 100}, "outcome": "YES", "expected": {"decision": "EXECUTED", "stake_units": 50000}}
```

`expected.decision` is `EXECUTED` or `REJECTED`; `expected.reason` and `expected.stake_units` are optional
and are checked when present. **Compute the expected values by hand from the rules below, not by running the
harness.** Candidate identifiers are unique; a market is described the same way on every line that uses it.

The rules, as shipped in `rules/` at the tag:

- estimate = prior + floor(confidence x (signal - prior) / 1,000,000)
- cost = fee + slippage + 1,500 x latency steps; a fee or slippage above 100,000 or a latency above 6 steps is
  `COST_OUT_OF_MODEL`; a missing cost is `MISSING_COST`; a missing confidence is `MISSING_CONFIDENCE`
- net edge = estimate - price - cost; it must be **strictly greater** than 20,000, else
  `EDGE_NOT_ABOVE_THRESHOLD`
- one veto refuses (`VALIDATOR_VETO`): a quote more than 3 steps old or dated after the candidate; the two
  prices more than 50,000 away from adding up to 1,000,000; 2 steps or fewer to the resolution; a
  confirmation at or below price - 60,000
- approval needs three of four validators (`NO_QUORUM` otherwise): the confirmation approves only at or above
  price + 10,000, and abstains in between or when absent; an absent complement price makes the book
  validator abstain
- size = floor(min(Kelly / 4, 5%) x equity), with Kelly = (estimate - price - cost) / (1,000,000 - price - cost)
  and equity 1,000,000 units until a market settles; after each losing settlement the size is multiplied by
  3/4 (at most four times)
- one open position per market (`RISK_POSITION_ALREADY_OPEN`); open stakes plus the new stake at most 30% of
  equity (`RISK_PORTFOLIO_EXPOSURE`), 15% once the drawdown has reached 10%
- a candidate that arrives after the outcome of its market, or with an identifier already seen, is refused
  (`CANDIDATE_AFTER_OUTCOME`, `DUPLICATE_CANDIDATE`)
- the order of the checks is the order of `rules/gate.json`: first match wins
- the conviction label changes nothing

Boundaries worth probing: a net edge of exactly 20,000 and of 20,001; a size bound by Kelly and one bound by
the cap; the same numbers under two conviction labels; a quote 3 and 4 steps old; 2 and 3 steps to the
resolution; two approvals and three; a seventh position at the cap; a loss followed by a new candidate.

## 4. Run it, once

```
python eval/score.py --blind --seed N --fifth-world build/blind/fifth_world.json --hand-candidates build/blind/hand_candidates.jsonl --runner "NAME OF THE HAND" --out build/eval-blind-001
```

Replace `N` and the name. The command generates the four worlds and your fifth world from seed `N`, replays
them on paper, audits every ledger with the independent references, then decides your hand-written
candidates and compares each with what you expected. It prints the headline numbers and a last line,
`SCORE OK` or `SCORE FAILED - ...`, and writes `eval/blind/result.json`. A second run is refused.

If the command stops with `REFUSED: ...` before running, nothing was measured and nothing was recorded: fix
the input it names and run again. If it ran, that is the blind run, whatever it says.

## 5. Record it

Append to `eval/history.json` an entry `blind_run` next to the entry `blind` (which records the state at the
freeze and is not edited): the runner, the date, the seed, the exact command, every printed line verbatim,
the text of `fifth_world.json` and the lines of `hand_candidates.jsonl` as strings (their SHA-256 are in
`eval/blind/result.json`). Commit `eval/blind/result.json` and `eval/history.json`, nothing else.

**Numbers are reported as they are.** A limit violation, a disagreement with the reference, a broken chain
or a candidate decided differently from what the hand expected is a finding: it is written down, not re-run.
A world that is not paused after a change of regime is also a finding, and is not a failure of the score.
The simulated result of a world is not a metric and is not a performance.

`README.md` keeps saying that the blind run is pending until a release `v2.0.0` is cut from the recorded
outcome; that step (a new README line, a new manifest, a new tag) is a new version and is not part of this
protocol.

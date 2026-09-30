# Claims

Each statement of the profile (`README.md`, text above the proof-pack section) and what stands behind it in
this repository. Three possible states:

- **demonstrated** - a scenario and tests fail if the statement stops being true of this code;
- **not demonstrated: out of v2.0** - nothing here supports it;
- **awaiting legal review - not touched** - the sentence is left exactly as it was, neither edited nor removed.

Everything demonstrated is demonstrated **on paper, on synthetic worlds with known probabilities**. Nothing is
demonstrated about any real market.

## Demonstrated

| # | Statement in the profile | Scenario | Tests | What exactly is shown |
|---|---|---|---|---|
| A1 | "`edge - cost > threshold` is the only execute condition" | `scenarios/S01`, `scenarios/S02` | `tests/test_gate.py`, `tests/test_ledger.py` (`NeverEvent`) | A candidate without a confidence or a cost is never traded and never defaulted; a net edge equal to the threshold (20,000 ppm) does not trade; the ledger itself refuses an execution whose recorded edge is not above the threshold |
| A2 | "asymmetric thresholds (quorum to approve, single veto)" | `scenarios/S03` | `tests/test_validators.py` | One veto refuses; approval needs three of four; all 36 vote combinations checked; 2000 random candidates agree with the independent reference |
| A3 | "quarter-Kelly default, hard-capped at 5% bankroll, never grows with conviction" | `scenarios/S04`, `scenarios/S10` | `tests/test_sizing.py`, `tests/test_ledger.py` | Size = floor(min(Kelly/4, 5%) x throttle x equity), equal to the closed formula of `corpus/reference_kelly.py` on 10,000 seeded cases with deviation 0; the sizing function does not take the conviction label; the cap lives in a rule file and changing it changes only the sizes above the new cap |
| A3b | "Risk engine as downstream veto layer - portfolio exposure and drawdown thresholds override signal strength" | `scenarios/S05` | `tests/test_sizing.py` (`RiskVeto`), `tests/test_ledger.py` | One open position per market, 30% portfolio exposure (15% after a 10% drawdown), refusals recorded with their reason whatever the signal says |
| A4 | "drawdown circuit breaker that halts trading automatically and requires a human to re-arm" | `scenarios/S05` | `tests/test_breaker.py` | HALT row right after the settlement that brings the drawdown to 20%; nothing executes while halted; re-arm refused without a consent record that names that halt, its drawdown and a human operator; a consent cannot be reused |
| A5 | "size shrinks after each loss; re-expansion gated by both elapsed time and a higher edge bar" | `scenarios/S06` | `tests/test_breaker.py` (`ThrottleRule`, `ThrottleInTheEngine`) | x3/4 per losing settlement down to four levels; one level back only after 20 steps without a loss and on a candidate whose net edge is above 40,000 ppm; a win does not restore the size |
| A6 | "every candidate signal logged *before* execution, blocking post-hoc cherry-picking structurally" | `scenarios/S07` | `tests/test_ledger.py` | Append-only hash chain; a candidate after the outcome of its market is refused; a decision without a logged candidate is refused; an edited, removed, inserted or re-ordered row is found by `verify`, which names the first bad row |
| A7 | "live divergence beyond N sigma auto-pauses" - **reworded**: on a *replayed* stream of generated data | `scenarios/S08`, `scenarios/S09` | `tests/test_divergence.py` | Pause when a window of 50 settled trades is more than 4 sigma from the backtested baseline; boundary computed by hand in exact fractions; a signal that reads the future fails the backtest and leaves no baseline |
| - | "cost included in the edge calculation, not bolted on after" | `scenarios/S02` | `tests/test_gate.py` | Fee, slippage and a latency charge are subtracted before the comparison with the threshold; a cost outside the model refuses the candidate |

Re-run: `python scenarios/run_all.py` and `python -m unittest discover -s tests -t .`. Numbers with their seed
and date: `eval/results.json`, summarised in `README.md`.

## Not demonstrated: out of v2.0

| Statement in the profile | Why it is not demonstrated | Proposed wording, for review |
|---|---|---|
| Any result on a real market; any profitability | Only synthetic worlds exist here. **No number in this repository is evidence of profitability** | Do not claim |
| "live divergence", "live-vs-backtest divergence detector", "the live edge drifted three sigma" | Nothing is live: the stream is a replay of generated data | "divergence of a replayed stream from its backtested baseline" |
| "Primary Placement: signal systems, validators, and risk engines for systematic event-market trading" | Suggests real activity | "research harness: paper trading on synthetic data" |
| "with an explicit confidence band" (thesis, biography, skills) | The size uses a point estimate; no band is computed | Remove, or build and demonstrate it |
| "sentiment confirmation" | The fourth validator reads a generated confirmation number; there is no sentiment source | "a fourth, independent confirmation" |
| "event-market mechanics, AMM and order-book execution paths" | Only a simulated cost model (fee, slippage, latency charge); no execution path of any kind | "simulated cost model" |
| "the dominant cause of ruin in retail systematic trading is sizing growing with conviction" / "the most common ruin mode" | Empirical statement without a source | Remove, or cite a source |
| "He has built the layer that catches the system before the human notices" | Past work that cannot be shown; what exists is this pack | "This pack contains a breaker and a divergence rule, shown on synthetic data" |
| "Council Defense PASS - quorum 3/3 (...)", "Faculty Advisor", the diploma block | Not produced here; the review artifacts are outside this repository | Out of scope of this pack |
| "Each invocation is recorded in the git history of the placement repository" | Not shown here | Out of scope of this pack |
| "14 alumni - 22 subagents - 330+ skills" | A catalogue count, not checked here | Out of scope of this pack |
| "commits authored as Tomaso Riviera" | True of the commits of this pack (see `MODEL.md`); not checked for anything else | - |

## Awaiting legal review - not touched

| Statement in the profile | State |
|---|---|
| "Tomaso is the platform's Probabilistic Trading Engineer." | **awaiting legal review - not touched.** The sentence is in `README.md` exactly as it was before this pack (`tests/test_docs.py` fails if the original text changes). This pack has no relation to any platform: it connects to nothing |
| Any sentence about money having been moved | None is present in this README. The banner states the opposite ("this code has never sent an order and has never moved money"). **not demonstrated**, and not claimed |
| The banner and `DISCLAIMER.md` | Text marked `[TO CONFIRM with legal]`: not yet reviewed by counsel |

## Known limits

1. **No evidence of profitability.** The pack shows that the machine respects its own limits when the
   probabilities are known. It does not show that an edge exists anywhere.
2. **The divergence rule sees only large shifts.** In the development and holdout suites the regime world is
   paused 669 and 1153 steps after the change. In the **stress** suite it is **not paused**: the breaker halts
   it at step 4066 instead. In the author's rehearsal a fifth world with a smaller shift is never paused. The
   planted shift is large by design.
3. **The null world still trades.** With no edge at all, 232 of 3718 candidates are executed in the
   development replay: a noisy signal clears the threshold by chance. The limits hold; the estimate is wrong.
4. **The drawdown can exceed 20%.** The breaker stops new executions when the drawdown reaches 20%; positions
   already open still settle (23.9% in the stress suite).
5. **The ledger trusts the recorded ballot.** A row with a forged validator ballot is accepted by the ledger
   guard and caught only by the independent audit (`tests/test_ledger.py`, `scenarios/S03`).
6. **A full rewrite verifies alone.** Someone who rewrites the whole file and re-computes every hash gets a
   self-consistent chain; only a head or an anchor published elsewhere exposes it (`scenarios/S07`). Loading a
   ledger does not re-apply the limits to its old rows; the audit does.
7. **A consent record cannot prove a human wrote it.** The check is on the record: kind, operator, the halt it
   names, the drawdown it acknowledges, a statement, a time. Operator names that start like an automated agent
   are refused; this may refuse a real name.
8. **The leak test sees only what goes through the view** it controls. A signal that reaches the future by
   another road is not caught.
9. **Both sides have the same author** (see `MODEL.md`).
10. **Calibration under stress.** With correlated markets the z-score (which assumes independence) exceeds 2 in
    two worlds of the stress suite. Reported as measured.
11. **A malformed number fails the run.** A decimal number, a non-string identifier or a non-integer time in a
    candidate stops the run loudly and writes nothing; it is not recorded as a refusal.
12. **One guard is unreachable with the shipped limits** (`INSUFFICIENT_CASH`): it is exercised only with the
    exposure limit lifted in a test.
13. **The throttle sits low.** In the generated worlds the size multiplier spends most of its time at levels 3
    and 4: re-expansion is slow by design and rarely observed over a whole run.
14. **CI has never run.** The workflow file exists; nothing was pushed. Action versions and the image digest
    are `[TO CONFIRM]`.

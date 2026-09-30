# Changelog

Nothing is deleted or rewritten in this history: a wrong step is followed by a step that corrects it.

## 2.0.0 - proof pack (30 September 2026)

First proof pack: a paper-trading harness on four synthetic worlds with known probabilities, ten scenarios,
an offline test suite, two independent rebuilds that agree byte for byte. Standard library only.

### Added

- `harness/`: signal decomposition, validators, sizing, risk veto, breaker, throttle, append-only hash-chain
  ledger, replayed stream, engine, divergence rule, backtest with leak test, runner, report.
- `rules/`: five ordered rule files (first match wins, vetoes on top).
- `corpus/`: seeded generator of four worlds, the digests of the development corpus, three references that
  share no code with the harness.
- `scenarios/S01`-`S10`, each with `scenario.json`, `input/`, `expected/`, `check.py`, `run.md`.
- `tests/`: unit tests, never-event tests, mutation tests of the scenarios, import scan, socket block.
- `eval/`: scorer, recorded measurements, templates for the blind run.
- `tools/`: double rebuild, tree manifest, results compactor.
- `SYNTHETIC.md`, `CLAIMS.md`, `MODEL.md`, `DISCLAIMER.md`, this file, an offline CI workflow.

### What changed while building, and why

These are the development runs on seed 20260930. The first four were wrong in ways worth keeping; the numbers
are in `eval/history.json`.

1. **Run 001** - positions could be stacked on one market and the regime world was *halted* by the breaker
   without ever being *paused* by the divergence rule. Led to: one open position per market, one side per
   market, the regime decided per market.
2. **Run 002** - the regime world was still halted first (median stake 2.8% of equity on a world whose edge
   had vanished). Led to: smaller stakes in that world, and a try with more, shorter markets.
3. **Run 003** - 1000 markets of 3 to 7 candidates (off the plan): a **false pause** on the stationary world
   and a pause on the regime world that was luck (a window straddling the change). Led to: 4 sigma instead
   of 3.
4. **Run 004** - 4 sigma worked on the off-plan corpus. Led to: back to the plan's 400 markets, with a regime
   world of under-priced favourites whose edge is deliberately large.
5. **Run 005** - the design that ships. **Run 006** - same ledgers after the change below; audit clean.

Other changes made during construction:

- **Refused inputs are recorded.** A duplicate candidate, or a candidate arriving after the outcome of its
  market, is written as a `REFUSED_INPUT` row instead of stopping the run. Beyond the plan's wording.
- **One open position per market** is a risk limit in `rules/risk_limits.json`: the 5% cap cannot be stacked
  by repeating a signal. Beyond the plan's wording.
- **Helper modules beyond the plan's list**: `exact.py`, `rules.py`, `stream.py`, `engine.py`.
- **Blind inputs are validated before anything runs**: a fifth-world file the generator cannot honour, a
  decimal number, a duplicated identifier, a copy of the author's templates or fewer than twenty hand-written
  candidates stop the blind command with a reason.
- **The double rebuild runs its two processes side by side.**

### Corrections

- The first commit of this branch says "banner" in its message but did not contain the banner; the next
  commit adds it and says so. The first message was left as it is.
- A test string shaped like an address was replaced, before any commit, by a string that only has the shape
  (`"T" + "a" * 33`).

### Not in this version

- No real market data, no connector, no account, no order routing: by construction, not by omission.
- No confidence band on the size. No composition with other packs (`reports/scan.json` belongs to a later
  composition). No tag `v2.0.0`: only `v2.0.0-freeze`, waiting for the blind run.
- CI has never run: nothing is pushed.

### After the freeze

`eval/BLIND_PROTOCOL.md` and `eval/history.json` are added after the tag `v2.0.0-freeze`. No code, rule,
scenario or test changes after the tag.

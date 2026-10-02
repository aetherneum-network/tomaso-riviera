# S06 - After losses the size shrinks and re-expands slowly, never to recover

*Claim A5 in `CLAIMS.md`. Paper trading on synthetic data; no market, no account, no money.*

Two losses, one win, then a quiet stretch. The size goes 50,000, 35,625, 25,716 (three quarters per loss, on a smaller equity). After the win it does not step up: a win is not a reason. At t=30, twenty steps after the last change, a candidate whose net edge equals the re-expansion bar of 40,000 ppm still trades at level 2. A strong candidate at t=31 brings the level down by one; the next one, a step later, stays there; twenty steps later the level returns to zero. The checker verifies every size, that the level never comes down by more than one at a time, and that a row claiming a multiplier above 1 is refused (`THROTTLE_MULTIPLIER_INVALID`).

```
python scenarios/S06/check.py     # one line: S06 PASS / S06 FAIL
```

Inputs: `input/` (written by `scenarios/make_inputs.py`). Expected: `expected/expected.json`, derived on paper.

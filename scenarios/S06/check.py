"""S06 - after losses the size shrinks; it comes back one step at a time, only with time AND a higher bar."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S06") as work:
        world_dir = C.copy_world(HERE, work)
        _engine, ledger, _world = C.replay(world_dir, work / "run")
        ledger.close()
        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        equities = [r["body"]["equity_units"] for r in rows if r["kind"] == "SETTLEMENT"][:3]
        if equities != exp["equity_after_first_three_settlements"]:
            problems.append(f"equity after the first settlements {equities}, "
                            f"expected {exp['equity_after_first_three_settlements']}")
        got = C.decisions(rows)
        levels = [got[cid]["sizing"]["throttle_level"] for cid in exp["level_sequence"]["candidates"]]
        if levels != exp["level_sequence"]["levels"]:
            problems.append(f"throttle levels {levels}, expected {exp['level_sequence']['levels']}")
        steps_down = [a - b for a, b in zip(levels, levels[1:]) if b < a]
        if any(step != 1 for step in steps_down):
            problems.append(f"the throttle came back by more than one level at a time: {levels}")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")

        # a row that claims a multiplier above 1 (a size that grew "to recover") is refused
        forge, rules, world = C.scratch_ledger(world_dir, work / "forge")

        def grow(body):
            body["sizing"]["throttle_multiplier"] = "4/3"
        code = C.forged_execution(forge, rules, world.markets, dict(world.candidates[0], candidate_id="S06-FORGED"), grow)
        forge.close()
        if code != exp["forged_multiplier_above_one"]:
            problems.append(f"forged row with a throttle multiplier above 1: {code}")
    return C.finish("S06", problems, "sizes 50000 -> 35625 -> 25716 after two losses; no step up on a win, on time "
                    "alone or on an edge equal to the bar; back to full size one level at a time (t=31, t=51)")


if __name__ == "__main__":
    C.main(check)

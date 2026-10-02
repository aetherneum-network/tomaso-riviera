"""S05 - the breaker halts at the instant the drawdown reaches 20%; re-arming needs a human consent record."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from harness.stream import events  # noqa: E402

SPLIT_T = 36   # the valid consent is written by the fixture operator just before this step


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S05") as work:
        world_dir = C.copy_world(HERE, work)
        engine, ledger, world = C.replay(world_dir, work / "run", until_t=SPLIT_T)
        halt_row = ledger.state.halt_row
        if halt_row is None:
            ledger.close()
            return C.finish("S05", ["the breaker did not halt"], "")
        # The fixture operator reads the halt and signs a consent that names this halt by its hash.
        consent = C.load_json(world_dir / "consent_valid.template.json")
        consent["halt_hash"] = halt_row["hash"]
        (world_dir / "consent_valid.json").write_text(json.dumps(consent, sort_keys=True, indent=2) + "\n",
                                                      encoding="utf-8", newline="\n")
        engine.run(events(world, from_t=SPLIT_T))
        ledger.close()

        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        halts = [r for r in rows if r["kind"] == "HALT"]
        want = exp["halt"]
        if len(halts) != 1:
            problems.append(f"{len(halts)} HALT rows, expected 1")
        else:
            h, before = halts[0], rows[halts[0]["seq"] - 1]
            got = {"t": h["t"], "equity_units": h["body"]["equity_units"], "peak_units": h["body"]["peak_units"],
                   "drawdown_ppm": h["body"]["drawdown_ppm"]}
            if got != want:
                problems.append(f"halt {got}, expected {want}")
            if before["kind"] != "SETTLEMENT" or before["t"] != h["t"]:
                problems.append("the HALT row does not immediately follow the settlement that caused it")
        equities = [r["body"]["equity_units"] for r in rows if r["kind"] == "SETTLEMENT"][:6]
        if equities != exp["equity_after_each_loss"]:
            problems.append(f"equity after the losses {equities}, expected {exp['equity_after_each_loss']}")
        refused = [r["body"]["reason"] for r in rows if r["kind"] == "REARM_REFUSED"]
        if refused != exp["rearm_refused"]:
            problems.append(f"re-arm refusals {refused}, expected {exp['rearm_refused']}")
        rearms = [{"t": r["t"], "peak_reset_to_units": r["body"]["peak_reset_to_units"]} for r in rows if r["kind"] == "REARM"]
        if rearms != [exp["rearm"]]:
            problems.append(f"re-arm rows {rearms}, expected {[exp['rearm']]}")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")

        # the never-event, attempted: an EXECUTED row while the breaker is halted
        _e2, halted, world2 = C.replay(world_dir, work / "run2", until_t=31)
        strong = dict(world2.candidates[6], candidate_id="S05-FORGED", market_id="P-M8", t=31)
        code = C.forged_execution(halted, engine.rules, world2.markets, strong)
        halted.close()
        if code != exp["forged_execution_while_halted"]:
            problems.append(f"forged execution while halted: {code}")
    return C.finish("S05", problems, "HALT at t=23 with equity 800000 (drawdown exactly 20%); strong signals refused "
                    "while halted; 4 re-arm requests without a valid human consent refused; re-armed only by a "
                    "consent naming the halt")


if __name__ == "__main__":
    C.main(check)

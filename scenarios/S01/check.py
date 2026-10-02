"""S01 (negative) - a candidate without a confidence or without a cost is never traded: REJECTED, with the reason."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S01") as work:
        world_dir = C.copy_world(HERE, work)
        _engine, ledger, _world = C.replay(world_dir, work / "run")
        ledger.close()
        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        opened = [r["body"]["candidate_id"] for r in rows
                  if r["kind"] == "DECISION" and r["body"]["decision"] == "EXECUTED"]
        if opened != exp["positions_opened"]:
            problems.append(f"positions opened {opened}, expected {exp['positions_opened']}")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")
        # an incomplete candidate cannot be executed even by a faulty engine
        forge, rules, world = C.scratch_ledger(world_dir, work / "forge")
        incomplete = dict(world.candidates[0], candidate_id="S01-FORGED")
        code = C.forged_execution(forge, rules, world.markets, incomplete)
        forge.close()
        if code != exp["forged_execution_refused_with"]:
            problems.append(f"forged execution of an incomplete candidate: {code}")
    return C.finish("S01", problems, "5 incomplete candidates REJECTED with their reason, 1 complete control EXECUTED; "
                    "a forged execution of an incomplete candidate is refused by the ledger")


if __name__ == "__main__":
    C.main(check)

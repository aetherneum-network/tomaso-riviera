"""S02 (boundary) - net edge equal to the threshold does not trade: the condition is strictly greater."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S02") as work:
        world_dir = C.copy_world(HERE, work)
        _engine, ledger, _world = C.replay(world_dir, work / "run")
        ledger.close()
        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        got = C.decisions(rows)
        for cid, net in exp["net_edge_ppm"].items():
            if got[cid]["decomposition"]["net_edge_ppm"] != net:
                problems.append(f"{cid}: net edge {got[cid]['decomposition']['net_edge_ppm']}, expected {net}")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")

        # the never-event, attempted: an EXECUTED row for the candidate whose net edge EQUALS the threshold
        forge, rules, world = C.scratch_ledger(world_dir, work / "forge")
        at_threshold = dict(world.candidates[0], candidate_id="S02-FORGED-1")
        code = C.forged_execution(forge, rules, world.markets, at_threshold)
        if code != exp["forged"]["at_threshold"]:
            problems.append(f"forged execution at the threshold: {code}")

        def lie(body):
            body["decomposition"]["net_edge_ppm"] = 20_001
        lying = dict(world.candidates[0], candidate_id="S02-FORGED-2", t=11)
        code = C.forged_execution(forge, rules, world.markets, lying, lie)
        forge.close()
        if code != exp["forged"]["lying_about_the_edge"]:
            problems.append(f"forged execution with a false net edge: {code}")
    return C.finish("S02", problems, "net edge 20000 (= threshold), 19999 and a cost above the edge do not trade; "
                    "20001 trades; forged executions at the threshold are refused by the ledger")


if __name__ == "__main__":
    C.main(check)

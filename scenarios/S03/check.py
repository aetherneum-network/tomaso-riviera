"""S03 - validators are asymmetric: one veto refuses, approval needs a quorum; a stale price is a veto."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S03") as work:
        world_dir = C.copy_world(HERE, work)
        _engine, ledger, _world = C.replay(world_dir, work / "run")
        ledger.close()
        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        got = C.decisions(rows)
        for cid, want in exp["votes"].items():
            votes = {name: vote.split(":")[0] for name, vote in got[cid]["votes"].items()}
            if votes != want:
                problems.append(f"{cid}: votes {votes}, expected {want}")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")

        forge, rules, world = C.scratch_ledger(world_dir, work / "forge")
        by_id = {c["candidate_id"]: c for c in world.candidates}
        code = C.forged_execution(forge, rules, world.markets, dict(by_id["S03-C1"], candidate_id="S03-FORGED-1"))
        if code != exp["forged"]["with_a_veto"]:
            problems.append(f"forged execution with a veto: {code}")
        code = C.forged_execution(forge, rules, world.markets, dict(by_id["S03-C2"], candidate_id="S03-FORGED-2"))
        if code != exp["forged"]["below_quorum"]:
            problems.append(f"forged execution below quorum: {code}")

        # Known limit, shown rather than hidden: the ledger guard reads the ballot it is given. A forged
        # ballot passes the guard and is caught by the independent audit, which re-derives the votes.
        def fake_ballot(body):
            body["votes"] = {name: "APPROVE:FORGED" for name in body["votes"]}
            body["vetoes"], body["approvals"] = [], 4
        code = C.forged_execution(forge, rules, world.markets,
                                  dict(by_id["S03-C3"], candidate_id="S03-FORGED-3"), fake_ballot)
        forge.close()
        flagged = []
        audit = C.reference_audit(forge.path, world_dir, flagged)
        caught = any(m["what"] == "decision" and m["want"] == "VALIDATOR_VETO" for m in audit["mismatches"])
        if code is not None or not caught:
            problems.append(f"forged ballot: ledger said {code}, audit caught it: {caught}")
    return C.finish("S03", problems, "one veto among three approvals, two approvals only, a stale price, a market "
                    "about to resolve: no trade; quorum of three trades; forged ballots caught")


if __name__ == "__main__":
    C.main(check)

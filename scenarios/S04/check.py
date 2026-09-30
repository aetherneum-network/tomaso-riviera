"""S04 - size = min(quarter Kelly, 5%) as the closed formula says; a conviction label never changes it."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from corpus import reference_kelly  # noqa: E402
from harness import sizing  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S04") as work:
        world_dir = C.copy_world(HERE, work)
        _engine, ledger, _world = C.replay(world_dir, work / "run")
        ledger.close()
        rows = C.rows_of(ledger.path)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(ledger.path, problems)
        C.reference_audit(ledger.path, world_dir, problems)
        got = C.decisions(rows)
        for a, b in exp["pairs_differing_only_in_conviction"]:
            if got[a]["sizing"]["stake_units"] != got[b]["sizing"]["stake_units"]:
                problems.append(f"{a} and {b} differ only in conviction but have different sizes")
        for a, b in exp["pairs_same_estimate_different_confidence"]:
            if got[a]["sizing"]["stake_units"] != got[b]["sizing"]["stake_units"]:
                problems.append(f"{a} and {b} have the same estimate but different sizes")
        for cid, body in got.items():
            if body["decision"] != "EXECUTED":
                continue
            d, s = body["decomposition"], body["sizing"]
            ref = reference_kelly.stake(d["p_hat_ppm"], d["q_eff_ppm"], s["equity_units"])
            if s["stake_units"] != ref:
                problems.append(f"{cid}: stake {s['stake_units']}, closed formula gives {ref}")
        params = set(inspect.signature(sizing.size).parameters)
        if params != set(exp["sizing_parameters"]):
            problems.append(f"sizing.size takes {sorted(params)}: a conviction could reach the size")
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")

        forge, rules, world = C.scratch_ledger(world_dir, work / "forge")
        by_id = {c["candidate_id"]: c for c in world.candidates}

        def oversize(units):
            def mutate(body):
                body["sizing"]["stake_units"] = units
                body["sizing"]["payout_if_win_units"] = units * 1_000_000 // body["decomposition"]["q_eff_ppm"]
            return mutate
        code = C.forged_execution(forge, rules, world.markets, dict(by_id["S04-A2"], candidate_id="S04-FORGED-1"),
                                  oversize(50_000))
        if code != exp["forged"]["cap_instead_of_quarter_kelly"]:
            problems.append(f"forged stake at the cap where quarter Kelly is lower: {code}")
        code = C.forged_execution(forge, rules, world.markets, dict(by_id["S04-B2"], candidate_id="S04-FORGED-2"),
                                  oversize(50_001))
        forge.close()
        if code != exp["forged"]["one_unit_over_the_cap"]:
            problems.append(f"forged stake one unit over the cap: {code}")
    return C.finish("S04", problems, "sizes 38135 and 50000 units as the closed formula gives; identical under "
                    "'low' and 'extreme' conviction; oversized forged rows refused")


if __name__ == "__main__":
    C.main(check)

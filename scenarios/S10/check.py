"""S10 (rule change) - the cap goes from 5% to 4% by editing the rule file; only the sizes above 4% change."""
from __future__ import annotations

import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from corpus import reference_drawdown  # noqa: E402


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S10") as work:
        world_dir = C.copy_world(HERE, work)
        change = C.load_json(world_dir / "rule_change.json")

        _e, ledger_a, world = C.replay(world_dir, work / "run_a")
        ledger_a.close()
        rows_a = C.rows_of(ledger_a.path)
        C.compare_decisions(rows_a, exp["decisions_cap_5_percent"], problems)
        C.check_chain(ledger_a.path, problems)
        C.reference_audit(ledger_a.path, world_dir, problems)

        # the change: one value in one rule file, in a copy of the rules; no code and no output is touched
        rules_b = work / "rules_b"
        shutil.copytree(C.RULES, rules_b)
        target = rules_b / change["file"]
        doc = json.loads(target.read_text(encoding="utf-8"))
        doc.update(change["set"])
        target.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")

        _e, ledger_b, _w = C.replay(world_dir, work / "run_b", rules_dir=rules_b)
        ledger_b.close()
        rows_b = C.rows_of(ledger_b.path)
        C.compare_decisions(rows_b, exp["decisions_cap_4_percent"], problems)
        C.check_chain(ledger_b.path, problems)
        new_cap = {"cap": Fraction(change["set"]["cap_ppm"], 1_000_000)}
        C.reference_audit(ledger_b.path, world_dir, problems, new_cap)

        a, b = C.decisions(rows_a), C.decisions(rows_b)
        changed = sorted(cid for cid in a if (a[cid]["decision"], a[cid]["sizing"]["stake_units"])
                         != (b[cid]["decision"], b[cid]["sizing"]["stake_units"]))
        if changed != exp["changed"]:
            problems.append(f"candidates whose size changed: {changed}, expected {exp['changed']}")
        gen_a, gen_b = rows_a[0]["body"], rows_b[0]["body"]
        if (gen_a["limits"]["cap_ppm"], gen_b["limits"]["cap_ppm"]) != (50_000, 40_000):
            problems.append("the ledgers do not record the cap they were written under")
        if gen_a["rules_sha256"] == gen_b["rules_sha256"]:
            problems.append("the digest of the rules did not change with the rule")

        # the old ledger, judged under the new cap: exactly the two oversized rows are flagged
        audit = reference_drawdown.audit(ledger_a.path, world.markets, new_cap)
        by_seq = {r["seq"]: r["body"]["candidate_id"] for r in rows_a if r["kind"] == "DECISION"}
        flagged = sorted(by_seq[v["seq"]] for v in audit["violations"] if v["code"] == "SIZE_OVER_LIMIT")
        if flagged != exp["flagged_when_old_ledger_is_audited_at_4_percent"] or len(audit["violations"]) != len(flagged):
            problems.append(f"old ledger under the new cap: flagged {flagged}, violations {audit['violations'][:3]}")
        if len(rows_a) != exp["ledger_rows"] or len(rows_b) != exp["ledger_rows"]:
            problems.append(f"{len(rows_a)}/{len(rows_b)} ledger rows, expected {exp['ledger_rows']}")
    return C.finish("S10", problems, "cap 5% -> 4% by one edit in the rule file: sizes 45000 and 50000 become 40000, "
                    "sizes 20000, 38135 and 40000 do not move; the old ledger fails the audit at 4% on exactly those two")


if __name__ == "__main__":
    C.main(check)

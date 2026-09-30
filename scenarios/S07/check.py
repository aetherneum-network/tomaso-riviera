"""S07 - the ledger is append-only: a candidate inserted after the outcome breaks the chain, and is refused."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from corpus import reference_drawdown  # noqa: E402
from harness import signal, sizing, validators  # noqa: E402
from harness.exact import canonical  # noqa: E402
from harness.ledger import Ledger, LedgerError, LimitViolation, row_hash, verify  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict]) -> None:
    path.write_bytes("".join(canonical(r) + "\n" for r in rows).encode("ascii"))


def _sealed(seq: int, t: int, kind: str, body: dict, prev: str) -> dict:
    """A row with a correct hash, as a forger who knows the (public) algorithm would write it."""
    row = {"seq": seq, "t": t, "kind": kind, "mode": "PAPER", "body": body, "prev": prev}
    row["hash"] = row_hash(row)
    return row


def _forged_pair(cand: dict, rules, markets: dict, equity: int, seq: int, prev: str) -> list[dict]:
    dec = signal.decompose(cand, rules)
    tally = validators.evaluate(cand, markets[cand["market_id"]], rules.validators)
    size = sizing.size(dec.p_hat_ppm, dec.q_eff_ppm, equity, Fraction(1), rules.risk)
    body = {"candidate_id": cand["candidate_id"], "market_id": cand["market_id"], "side": cand["side"],
            "decision": "EXECUTED", "rule_id": "G90", "reason": "EDGE_ABOVE_THRESHOLD", "also": [],
            "decomposition": dec.as_body(), "votes": tally.votes, "approvals": tally.approvals,
            "vetoes": tally.vetoes,
            "sizing": size.as_body(equity_units=equity, level=0, multiplier=Fraction(1), cfg=rules.risk)}
    first = _sealed(seq, cand["t"], "CANDIDATE", cand, prev)
    return [first, _sealed(seq + 1, cand["t"], "DECISION", body, first["hash"])]


def _relink(rows: list[dict], start: int, prev: str) -> list[dict]:
    out = []
    for offset, row in enumerate(rows):
        row = _sealed(start + offset, row["t"], row["kind"], row["body"], prev)
        prev = row["hash"]
        out.append(row)
    return out


def check():
    exp, problems = C.expected(HERE), []
    with C.workdir("S07") as work:
        world_dir = C.copy_world(HERE, work)
        engine, ledger, world = C.replay(world_dir, work / "run")
        published = ledger.anchors()          # what an honest operator publishes: head and checkpoints
        ledger.close()
        original = ledger.path
        rows = C.rows_of(original)
        C.compare_decisions(rows, exp["decisions"], problems)
        C.check_chain(original, problems)
        C.reference_audit(original, world_dir, problems)
        if len(rows) != exp["ledger_rows"]:
            problems.append(f"{len(rows)} ledger rows, expected {exp['ledger_rows']}")
        original_sha = _sha(original)
        forged = C.load_json(world_dir / "forged_rows.json")["candidate"]
        at = exp["insert_at_seq"]
        pair = _forged_pair(forged, engine.rules, world.markets, 1_000_000, at, rows[at - 1]["hash"])
        markets = world.markets
        got = {}

        # (a) the pair is inserted where its date says, the rows after it are left as they were
        path_a = work / "a_inserted.jsonl"
        _write(path_a, rows[:at] + pair + rows[at:])
        res = verify(path_a)
        got["a_inserted_tail_untouched"] = {"ok": res["ok"], "first_bad_seq": res["first_bad_seq"], "reason": res["reason"]}
        ref = reference_drawdown.read_rows(path_a)[1]
        got["a_reference"] = {"ok": ref["ok"], "first_bad_seq": ref["first_bad_seq"], "reason": ref["reason"]}

        # (b) the same, and the forger re-hashes every later row: the file is self-consistent
        path_b = work / "b_rewritten.jsonl"
        _write(path_b, rows[:at] + pair + _relink(rows[at:], at + 2, pair[-1]["hash"]))
        res_alone = verify(path_b)
        res_head = verify(path_b, expect_head=published["head"], anchors=published["anchors"])
        audit_b = reference_drawdown.audit(path_b, markets)
        got["b_full_rewrite"] = {
            "self_consistent_without_a_published_head": res_alone["ok"],
            "with_published_head": {"ok": res_head["ok"], "first_bad_seq": res_head["first_bad_seq"],
                                    "reason": res_head["reason"]},
            "reference_audit_violations": [[v["seq"], v["code"]] for v in audit_b["violations"]]}

        # (c) the forged candidate is appended at the end, with its old date
        path_c = work / "c_backdated.jsonl"
        _write(path_c, rows + [_sealed(len(rows), forged["t"], "CANDIDATE", forged, rows[-1]["hash"])])
        res = verify(path_c)
        got["c_backdated_append"] = {"ok": res["ok"], "first_bad_seq": res["first_bad_seq"], "reason": res["reason"],
                                     "all_reasons": sorted({f["reason"] for f in res["findings"]})}

        # (d) a stake is edited in place
        path_d = work / "d_edited.jsonl"
        edited = json.loads(json.dumps(rows))
        edited[exp["edit_seq"]]["body"]["sizing"]["stake_units"] = 50_000
        _write(path_d, edited)
        res = verify(path_d)
        got["d_edited_in_place"] = {"ok": res["ok"], "first_bad_seq": res["first_bad_seq"], "reason": res["reason"]}

        # (e) through the API: the ledger itself refuses, and the file does not change
        path_e = work / "e_api.jsonl"
        shutil.copyfile(original, path_e)
        before, refusals = _sha(path_e), []
        led = Ledger.load(path_e)
        for label, call in (
                ("candidate_dated_before_the_outcome", lambda: led.candidate(forged["t"], forged)),
                ("candidate_dated_now", lambda: led.candidate(41, {**forged, "t": 41})),
                ("raw_backdated_row", lambda: led._commit("CANDIDATE", forged["t"], forged)),
                ("decision_without_candidate", lambda: led.decision(41, pair[1]["body"]))):
            try:
                call()
                refusals.append([label, "ACCEPTED"])
            except LimitViolation as exc:
                refusals.append([label, exc.code])
            except LedgerError as exc:
                refusals.append([label, str(exc).split(":")[0]])
        led.close()
        got["e_api_refusals"] = refusals
        got["e_file_unchanged"] = _sha(path_e) == before

        for key, want in exp["tamper"].items():
            if got.get(key) != want:
                problems.append(f"{key}: {got.get(key)}, expected {want}")
        # nothing is deleted or repaired: verification is read-only
        if _sha(original) != original_sha or not all(p.is_file() for p in (path_a, path_b, path_c, path_d)):
            problems.append("verification modified or removed a file")
    return C.finish("S07", problems, "a candidate inserted after the outcome is detected at the first altered row "
                    "(seq 7), a backdated or edited row too; a full rewrite is caught by the published head and by "
                    "the independent audit; the ledger API refuses all four attempts", {"observed": got})


if __name__ == "__main__":
    C.main(check)

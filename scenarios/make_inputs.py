"""Writes the input files of scenarios S01-S10. Hand-written worlds: no random draw, no real name.

    python scenarios/make_inputs.py            # rewrite scenarios/Sxx/input/
    python scenarios/make_inputs.py --check    # compare with what is on disk, write nothing

Every figure below is chosen so that the expected result can be derived on paper (see each
``expected/expected.json``). The default candidate passes all four validators:
price quoted at the candidate's own step, a consistent book, a market far from resolution, and a
confirmation 50,000 ppm above the price. Default cost: fee 5,000 + slippage 5,000 + no latency = 10,000 ppm.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AS_OF = "2026-10-01T09:00:00+02:00"
PPM = 1_000_000
AUTO = "auto"
DEFAULT_COST = {"fee_ppm": 5_000, "slippage_ppm": 5_000, "latency_steps": 0}
PLACES = {"P": "Portoluna - Regatta heat", "S": "Serrabruna - Fair lot"}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def cand(cid: str, market: str, t: int, *, side="YES", price=400_000, signal=900_000, confidence=800_000,
         price_as_of=AUTO, complement=AUTO, confirm=AUTO, cost=AUTO, conviction="medium") -> dict:
    return {
        "candidate_id": cid, "market_id": market, "t": t, "side": side, "price_ppm": price,
        "price_as_of": t if price_as_of == AUTO else price_as_of,
        "complement_price_ppm": PPM - price if complement == AUTO else complement,
        "prior_ppm": price, "signal_ppm": signal, "confidence_ppm": confidence, "conviction": conviction,
        "confirm_ppm": price + 50_000 if confirm == AUTO else confirm,
        "cost": dict(DEFAULT_COST) if cost == AUTO else cost,
    }


def strong(cid, market, t, **kw):
    """Estimate 0.80 at price 0.40: quarter Kelly is about 16.5%, so the 5% cap binds."""
    return cand(cid, market, t, **{**dict(price=400_000, signal=900_000, confidence=800_000), **kw})


def moderate(cid, market, t, **kw):
    """Estimate 0.50 at price 0.40: quarter Kelly is 9/236 = 3.81%, under the cap."""
    return cand(cid, market, t, **{**dict(price=400_000, signal=600_000, confidence=500_000), **kw})


def world(sid: str, place: str, markets: list[tuple], candidates: list[dict], note: str,
          requests: list[dict] | None = None, extra: dict | None = None) -> dict:
    """markets: (market_id, resolves_at, outcome). Returns {relative path: text}."""
    rows_m = [{"market_id": m, "name": f"{PLACES[place]} {i + 1:03d}", "opens_at": 0, "resolves_at": r}
              for i, (m, r, _o) in enumerate(markets)]
    rows_o = [{"market_id": m, "t": r, "outcome": o} for m, r, o in markets]
    meta = {"world": sid, "code": sid, "seed": None, "as_of": AS_OF, "steps": max(r for _m, r, _o in markets) + 1,
            "baseline_until_t": 0, "markets": len(markets), "candidates": len(candidates), "synthetic": True,
            "generator_version": "hand-written", "stress": False, "note": note}
    files = {
        "meta.json": json.dumps(meta, sort_keys=True, indent=2) + "\n",
        "markets.jsonl": "".join(canonical(r) + "\n" for r in rows_m),
        "candidates.jsonl": "".join(canonical(c) + "\n" for c in candidates),
        "outcomes.jsonl": "".join(canonical(r) + "\n" for r in rows_o),
    }
    if requests:
        files["requests.jsonl"] = "".join(canonical(r) + "\n" for r in requests)
    for name, obj in (extra or {}).items():
        files[name] = json.dumps(obj, sort_keys=True, indent=2) + "\n"
    return files


def build() -> dict:
    out = {}

    # S01 - incomplete candidates are refused, with the reason; a complete one is the control.
    out["S01"] = world("S01", "P", [(f"P-M{i}", 50, "YES") for i in range(1, 5)], [
        moderate("S01-C1", "P-M1", 10, confidence=None),
        moderate("S01-C2", "P-M2", 11, cost=None),
        moderate("S01-C3", "P-M3", 12, cost={"fee_ppm": 5_000, "slippage_ppm": 5_000, "latency_steps": 9}),
        moderate("S01-C4", "P-M4", 13),
        moderate("S01-C5", "P-M1", 14, cost={"fee_ppm": 5_000, "latency_steps": 0}),
        moderate("S01-C6", "P-M2", 15, confidence="high"),
    ], "Hand-written. Candidates without a confidence or without a cost. Not real data.")

    # S02 - the boundary of the execute condition: strictly greater than the threshold (20,000 ppm).
    out["S02"] = world("S02", "S", [(f"S-M{i}", 50, "YES") for i in range(1, 6)], [
        cand("S02-C1", "S-M1", 10, signal=460_000, confidence=500_000),
        cand("S02-C2", "S-M2", 11, signal=460_002, confidence=500_000),
        cand("S02-C3", "S-M3", 12, signal=500_000, confidence=500_000,
             cost={"fee_ppm": 30_000, "slippage_ppm": 25_000, "latency_steps": 0}),
        cand("S02-C4", "S-M4", 13, signal=459_998, confidence=500_000),
        cand("S02-C5", "S-M5", 14, signal=460_001, confidence=500_000),
    ], "Hand-written. Net edge equal to the threshold, one ppm above, one below; cost above the edge. Not real data.")

    # S03 - validators: one veto refuses, approval needs a quorum of three, a stale price is a veto.
    out["S03"] = world("S03", "P", [("P-M1", 50, "YES"), ("P-M2", 50, "YES"), ("P-M3", 50, "YES"),
                                    ("P-M4", 50, "YES"), ("P-M5", 50, "YES"), ("P-M6", 17, "YES")], [
        strong("S03-C1", "P-M1", 10, complement=670_000),
        strong("S03-C2", "P-M2", 11, complement=None, confirm=None),
        strong("S03-C3", "P-M3", 12, price_as_of=7),
        strong("S03-C4", "P-M4", 13, confirm=None),
        strong("S03-C5", "P-M5", 14, price_as_of=11),
        strong("S03-C6", "P-M6", 15),
        strong("S03-C7", "P-M1", 16, price_as_of=17),
        strong("S03-C8", "P-M2", 18, complement=650_000),
    ], "Hand-written. Three approvals and one veto; below quorum; stale price. Not real data.")

    # S04 - the size never grows with conviction.
    out["S04"] = world("S04", "S", [(f"S-M{i}", 50, "YES") for i in range(1, 7)], [
        moderate("S04-A1", "S-M1", 10, conviction="low"),
        moderate("S04-A2", "S-M2", 11, conviction="extreme"),
        strong("S04-B1", "S-M3", 12, conviction="low"),
        strong("S04-B2", "S-M4", 13, conviction="extreme"),
        cand("S04-C1", "S-M5", 14, signal=500_000, confidence=1_000_000, conviction="extreme"),
        cand("S04-D1", "S-M6", 15, signal=440_000, confidence=500_000, conviction="extreme"),
    ], "Hand-written. Same edge, different conviction labels. Not real data.")

    # S05 - six positions of 5% lose one after the other: the breaker halts at a drawdown of exactly 20%.
    out["S05"] = world("S05", "P", [(f"P-M{i}", 19 + i, "NO") for i in range(1, 7)]
                       + [("P-M7", 60, "YES"), ("P-M8", 70, "YES")], [
        *[strong(f"S05-C{i}", f"P-M{i}", i) for i in range(1, 7)],
        strong("S05-C7", "P-M7", 30),
        strong("S05-C8", "P-M7", 35),
        strong("S05-C9", "P-M8", 37),
    ], "Hand-written. A run of losses, then strong signals while halted. Not real data.", requests=[
        {"t": 31, "kind": "REARM_REQUEST"},
        {"t": 32, "kind": "REARM_REQUEST", "consent_file": "consent_agent.json"},
        {"t": 33, "kind": "REARM_REQUEST", "consent_file": "consent_wrong_halt.json"},
        {"t": 34, "kind": "REARM_REQUEST", "consent_file": "consent_missing.json"},
        {"t": 36, "kind": "REARM_REQUEST", "consent_file": "consent_valid.json"},
    ], extra={
        "consent_agent.json": {"kind": "HUMAN_REARM_CONSENT", "operator": "agent-7", "operator_is_human": False,
                               "halt_hash": "0" * 64, "acknowledged_drawdown_ppm": 200_000,
                               "statement": "Automatic re-arm requested by the harness itself.", "signed_at_t": 32},
        "consent_wrong_halt.json": {"kind": "HUMAN_REARM_CONSENT", "operator": "Fixture Operator (test record)",
                                    "operator_is_human": True, "halt_hash": "0" * 64,
                                    "acknowledged_drawdown_ppm": 200_000,
                                    "statement": "I have read the halt and I re-arm the breaker.", "signed_at_t": 33},
        "consent_valid.template.json": {"kind": "HUMAN_REARM_CONSENT", "operator": "Fixture Operator (test record)",
                                        "operator_is_human": True, "halt_hash": "<hash of the HALT row, filled by check.py>",
                                        "acknowledged_drawdown_ppm": 200_000,
                                        "statement": "I have read the halt and I re-arm the breaker.", "signed_at_t": 36},
    })

    # S06 - throttle: shrinks after each loss; comes back one step at a time, only with time AND a higher bar.
    out["S06"] = world("S06", "S", [("S-M1", 5, "NO"), ("S-M2", 10, "NO"), ("S-M3", 15, "YES")]
                       + [(f"S-M{i}", 90, "YES") for i in range(4, 9)], [
        strong("S06-C1", "S-M1", 1),
        strong("S06-C2", "S-M2", 6),
        strong("S06-C3", "S-M3", 11),
        strong("S06-C4", "S-M4", 20),
        cand("S06-C5", "S-M5", 30, signal=500_000, confidence=500_000),
        strong("S06-C6", "S-M6", 31),
        strong("S06-C7", "S-M7", 32),
        strong("S06-C8", "S-M8", 51),
    ], "Hand-written. Two losses, one win, then recovery. Not real data.")

    # S07 - a small ledger to tamper with.
    out["S07"] = world("S07", "P", [("P-M1", 20, "YES"), ("P-M2", 30, "NO"), ("P-M3", 40, "YES")], [
        strong("S07-C1", "P-M1", 5),
        moderate("S07-C2", "P-M2", 8),
        cand("S07-C3", "P-M3", 25, signal=440_000, confidence=500_000),
    ], "Hand-written. Three markets, three candidates. Not real data.", extra={
        "forged_rows.json": {
            "note": "What a forger would add after the outcome of P-M2 (NO at t=30) is known: a candidate "
                    "and an executed decision on the winning side, dated t=9.",
            "candidate": cand("S07-FORGED", "P-M2", 9, side="NO", price=600_000, signal=950_000, confidence=800_000),
        }})

    # S08 - divergence rule: a hand-computable boundary, the two corpus worlds, and the declared rates.
    out["S08"] = {"params.json": json.dumps({
        "as_of": AS_OF, "seed": 20260930, "regime_world": "regime_change", "stationary_world": "planted_edge",
        "study_runs": 300,
        "hand_case": {"baseline_plus_count": 50, "baseline_minus_count": 50, "baseline_magnitude_ppm": 100_000,
                      "window_inside_value_ppm": 69_631, "window_outside_value_ppm": -69_632},
    }, sort_keys=True, indent=2) + "\n"}

    # S09 - a signal function that looks at the future is caught by the leak test.
    out["S09"] = world("S09", "P", [("P-M1", 20, "YES"), ("P-M2", 30, "NO"), ("P-M3", 40, "YES"), ("P-M4", 50, "NO")], [
        cand("S09-C1", "P-M2", 2, price=500_000, signal=505_000, confidence=500_000),
        cand("S09-C2", "P-M1", 3, price=400_000, signal=395_000, confidence=500_000),
        cand("S09-C3", "P-M3", 4, price=300_000, signal=310_000, confidence=500_000),
        cand("S09-C4", "P-M4", 5, price=450_000, signal=452_000, confidence=500_000),
        cand("S09-C5", "P-M1", 6, price=420_000, signal=415_000, confidence=500_000),
        cand("S09-C6", "P-M2", 7, price=500_000, signal=498_000, confidence=500_000),
        cand("S09-C7", "P-M3", 8, price=310_000, signal=305_000, confidence=500_000),
        cand("S09-C8", "P-M4", 9, price=450_000, signal=455_000, confidence=500_000),
    ], "Hand-written null world: the recorded signals carry no information. Not real data.")

    # S10 - the cap goes from 5% to 4% by editing the rule file; only sizes above 4% change.
    out["S10"] = world("S10", "P", [(f"P-M{i}", 50, "YES") for i in range(1, 6)], [
        cand("S10-C1", "P-M1", 10, price=490_000, signal=590_000, confidence=500_000),
        moderate("S10-C2", "P-M2", 11),
        cand("S10-C3", "P-M3", 12, price=490_000, signal=690_000, confidence=500_000),
        strong("S10-C4", "P-M4", 13),
        cand("S10-C5", "P-M5", 14, price=490_000, signal=670_000, confidence=500_000),
    ], "Hand-written. Five sizes: 2%, 3.81%, 4.5%, capped, exactly 4%. Not real data.", extra={
        "rule_change.json": {"file": "risk_limits.json", "set": {"cap_ppm": 40_000},
                             "note": "The decision changes by editing the rule, never the output."}})
    return out


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    differing = 0
    for sid, files in sorted(build().items()):
        for name, text in sorted(files.items()):
            path = HERE / sid / "input" / name
            if check:
                on_disk = path.read_bytes() if path.is_file() else None
                differing += on_disk != text.encode("utf-8")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
    if check:
        print(f"scenario inputs differing from make_inputs.py: {differing}")
        return 0 if differing == 0 else 1
    print("scenario inputs written: S01-S10")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Independent reference: re-derive the whole account from a ledger file and audit every row.

This file does not import the code under test. It reads a ledger (JSON lines), the public list of
markets, and re-computes from scratch:

  * the hash chain (its own SHA-256 over its own canonical encoding);
  * cash, equity, running peak and drawdown after every settlement;
  * the instant at which the breaker must halt, and the instant at which the divergence rule must pause;
  * the decision the pack's claims require for every candidate, with its reason and its exact size
    (execute condition and validators from ``reference_gate``, size from ``reference_kelly``);
  * the throttle level, from the losses and the elapsed time.

It then reports, separately: disagreements with the recorded rows (``mismatches``) and rows that break a
limit whatever the harness thought (``violations`` - the never-event of this pack).

    python corpus/reference_drawdown.py LEDGER.jsonl MARKETS.jsonl
"""
from __future__ import annotations

import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from corpus import reference_gate, reference_kelly  # noqa: E402

ONE = 1_000_000

PARAMS = {
    **reference_gate.PARAMS,
    "kelly_multiplier": Fraction(1, 4),
    "cap": Fraction(1, 20),                      # 5% of equity per position, one position per market
    "max_open_positions_per_market": 1,
    "exposure_ppm": 300_000,                     # open stake / equity
    "drawdown_warn_ppm": 100_000,
    "exposure_when_warned_ppm": 150_000,
    "drawdown_halt_ppm": 200_000,
    "initial_bankroll_units": 1_000_000,
    "throttle_shrink": Fraction(3, 4),
    "throttle_max_level": 4,
    "throttle_cooldown_steps": 20,
    "throttle_reexpansion_bar_ppm": 40_000,
    "divergence_n_sigma": 4,
    "divergence_window_trades": 50,
    "divergence_min_baseline_trades": 50,
}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _frac(text: str) -> Fraction:
    num, den = text.split("/")
    return Fraction(int(num), int(den))


def _drawdown_ppm(equity: int, peak: int) -> int:
    return ONE if peak <= 0 else max(0, ((peak - equity) * ONE) // peak)


def read_rows(path) -> tuple[list[dict], dict]:
    """Rows of the file and the verdict on its chain (first broken row, if any)."""
    rows, chain = [], {"ok": True, "first_bad_seq": None, "reason": None}

    def bad(seq, reason):
        if chain["ok"]:
            chain.update(ok=False, first_bad_seq=seq, reason=reason)

    prev = "0" * 64
    for index, line in enumerate(Path(path).read_bytes().split(b"\n")[:-1]):
        row = json.loads(line.decode("utf-8"))
        body = {k: v for k, v in row.items() if k != "hash"}
        if row.get("seq") != index:
            bad(index, "SEQ")
        if row.get("prev") != prev:
            bad(index, "PREV")
        if hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != row.get("hash"):
            bad(index, "HASH")
        if row.get("mode") != "PAPER":
            bad(index, "NOT_PAPER")
        prev = row.get("hash")
        rows.append(row)
    return rows, chain


def audit(ledger_path, markets: dict, params: dict | None = None) -> dict:
    """``markets``: market_id -> {"resolves_at": ...} (public file of the world, not the truth)."""
    P = dict(PARAMS)
    P.update(params or {})
    rows, chain = read_rows(ledger_path)

    cash = peak = P["initial_bankroll_units"]
    open_positions: dict[str, dict] = {}
    settled: set[str] = set()
    pending: dict[str, dict] = {}
    decided: set[str] = set()
    halted = paused = False
    level, last_change = 0, None
    baseline = baseline_row = None
    window: list[int] = []
    windows_expected = checks_recorded = 0
    mismatches: list[dict] = []
    violations: list[dict] = []
    halts_expected, halts_recorded, pauses_expected, pauses_recorded = [], [], [], []
    executed: list[dict] = []
    realised: list[dict] = []
    reasons: dict[str, int] = {}
    max_dev = max_dd = 0
    expect_next = None   # a HALT or PAUSE row that must follow

    def equity() -> int:
        return cash + sum(p["stake"] for p in open_positions.values())

    def expected_decision(cand: dict, t: int) -> dict:
        reason, est, _ballot = reference_gate.stateless(cand, markets.get(cand.get("market_id")), P)
        if est is None:
            return {"decision": "REJECTED", "reason": reason}
        if halted:
            return {"decision": "REJECTED", "reason": "BREAKER_HALTED"}
        if paused:
            return {"decision": "REJECTED", "reason": "SIGNAL_PAUSED"}
        if reason is not None:
            return {"decision": "REJECTED", "reason": reason}
        lvl = level
        if (lvl > 0 and last_change is not None and t - last_change >= P["throttle_cooldown_steps"]
                and est["net_edge_ppm"] > P["throttle_reexpansion_bar_ppm"]):
            lvl -= 1
        eq = equity()
        stake = reference_kelly.stake(est["p_hat_ppm"], est["q_eff_ppm"], eq, P["throttle_shrink"] ** lvl,
                                      P["kelly_multiplier"], P["cap"])
        if stake < 1:
            return {"decision": "REJECTED", "reason": "SIZE_BELOW_MINIMUM"}
        market_id = cand["market_id"]
        open_total = sum(p["stake"] for p in open_positions.values())
        exposure = (P["exposure_when_warned_ppm"] if _drawdown_ppm(eq, peak) >= P["drawdown_warn_ppm"]
                    else P["exposure_ppm"])
        if market_id in open_positions:
            return {"decision": "REJECTED", "reason": "RISK_POSITION_ALREADY_OPEN"}
        if stake > (P["cap"] * eq).numerator // (P["cap"] * eq).denominator:
            return {"decision": "REJECTED", "reason": "RISK_MARKET_CAP"}
        if open_total + stake > (exposure * eq) // ONE:
            return {"decision": "REJECTED", "reason": "RISK_PORTFOLIO_EXPOSURE"}
        if stake > cash:
            return {"decision": "REJECTED", "reason": "RISK_INSUFFICIENT_CASH"}
        return {"decision": "EXECUTED", "reason": "EDGE_ABOVE_THRESHOLD", "stake_units": stake, "level": lvl,
                "q_eff_ppm": est["q_eff_ppm"]}

    for row in rows:
        kind, body, t, seq = row["kind"], row["body"], row["t"], row["seq"]
        if expect_next is not None:
            if kind != expect_next:
                mismatches.append({"seq": seq, "what": f"expected a {expect_next} row here", "got": kind})
            expect_next = None
        if kind == "CANDIDATE":
            if body.get("market_id") in settled:
                violations.append({"seq": seq, "code": "CANDIDATE_AFTER_OUTCOME"})
            pending[body["candidate_id"]] = body
        elif kind == "DECISION":
            cand = pending.pop(body["candidate_id"], None)
            decided.add(body["candidate_id"])
            if cand is None:
                violations.append({"seq": seq, "code": "DECISION_WITHOUT_CANDIDATE"})
                continue
            want = expected_decision(cand, t)
            if body["decision"] == "EXECUTED":
                stake = body["sizing"]["stake_units"]
                est = reference_gate.estimate(cand, P)
                eq = equity()
                # The never-event, checked on the recorded row whatever the expected decision was.
                if isinstance(est, str):
                    violations.append({"seq": seq, "code": "EXECUTION_OF_INCOMPLETE_CANDIDATE"})
                else:
                    limit = reference_kelly.position_limit(est["p_hat_ppm"], est["q_eff_ppm"], eq,
                                                           P["kelly_multiplier"], P["cap"])
                    if stake > limit:
                        violations.append({"seq": seq, "code": "SIZE_OVER_LIMIT", "stake": stake, "limit": limit})
                    if not est["net_edge_ppm"] > P["threshold_ppm"]:
                        violations.append({"seq": seq, "code": "EDGE_NOT_ABOVE_THRESHOLD"})
                if halted:
                    violations.append({"seq": seq, "code": "EXECUTION_WHILE_HALTED"})
                if paused:
                    violations.append({"seq": seq, "code": "EXECUTION_WHILE_PAUSED"})
                if body["market_id"] in settled:
                    violations.append({"seq": seq, "code": "EXECUTION_AFTER_OUTCOME"})
                if body["market_id"] in open_positions:
                    violations.append({"seq": seq, "code": "POSITION_STACKED"})
                if stake > cash:
                    violations.append({"seq": seq, "code": "INSUFFICIENT_CASH"})
                if want["decision"] == "EXECUTED":
                    max_dev = max(max_dev, abs(stake - want["stake_units"]))
                    if body["sizing"]["throttle_level"] != want["level"]:
                        mismatches.append({"seq": seq, "what": "throttle level", "got": body["sizing"]["throttle_level"],
                                           "want": want["level"]})
                    if want["level"] != level:
                        level, last_change = want["level"], t
                else:
                    mismatches.append({"seq": seq, "what": "decision", "got": "EXECUTED", "want": want["reason"]})
                q_eff = body["decomposition"]["q_eff_ppm"] if isinstance(est, str) else est["q_eff_ppm"]
                open_positions[body["market_id"]] = {
                    "candidate_id": body["candidate_id"], "side": body["side"], "stake": stake,
                    "payout": reference_kelly.payout_if_win(stake, q_eff), "q_eff": q_eff}
                cash -= stake
                executed.append({"seq": seq, "t": t, "candidate_id": body["candidate_id"],
                                 "market_id": body["market_id"], "stake_units": stake, "equity_units": eq,
                                 "q_eff_ppm": q_eff, "level": body["sizing"]["throttle_level"]})
            else:
                reasons[body["reason"]] = reasons.get(body["reason"], 0) + 1
                if want["decision"] != "REJECTED" or want["reason"] != body["reason"]:
                    mismatches.append({"seq": seq, "what": "decision", "got": body["reason"],
                                       "want": want.get("reason") if want["decision"] == "REJECTED" else "EXECUTED"})
        elif kind == "SETTLEMENT":
            market_id = body["market_id"]
            settled.add(market_id)
            pos = open_positions.pop(market_id, None)
            if pos is not None:
                won = pos["side"] == body["outcome"]
                if won:
                    cash += pos["payout"]
                else:
                    level, last_change = min(level + 1, P["throttle_max_level"]), t
                x = (ONE if won else 0) - pos["q_eff"]
                realised.append({"t": t, "market_id": market_id, "x_ppm": x, "won": won})
                if baseline is not None and not paused:
                    window.append(x)
            eq = equity()
            peak = max(peak, eq)
            dd = _drawdown_ppm(eq, peak)
            max_dd = max(max_dd, dd)
            if body["equity_units"] != eq or body["peak_units"] != peak or body["drawdown_ppm"] != dd:
                mismatches.append({"seq": seq, "what": "equity/peak/drawdown", "got": [body["equity_units"],
                                   body["peak_units"], body["drawdown_ppm"]], "want": [eq, peak, dd]})
            if not halted and (peak - eq) * ONE >= P["drawdown_halt_ppm"] * peak:
                halted = True
                halts_expected.append({"t": t, "after_seq": seq, "equity_units": eq, "drawdown_ppm": dd})
                expect_next = "HALT"
            if baseline is not None and len(window) >= P["divergence_window_trades"]:
                n_w, n_b = len(window), baseline["n"]
                mean = Fraction(sum(window), n_w)
                allowed = P["divergence_n_sigma"] ** 2 * baseline["var"] * (Fraction(1, n_w) + Fraction(1, n_b))
                window = []
                windows_expected += 1
                if (mean - baseline["mean"]) ** 2 > allowed and not paused:
                    paused = True
                    pauses_expected.append({"t": t, "after_seq": seq})
        elif kind == "HALT":
            halts_recorded.append({"t": t, "seq": seq, "equity_units": body["equity_units"],
                                   "drawdown_ppm": body["drawdown_ppm"]})
        elif kind == "REARM":
            if body.get("consent", {}).get("operator_is_human") is not True:
                violations.append({"seq": seq, "code": "REARM_WITHOUT_HUMAN_CONSENT"})
            halted = False
            peak = equity()
        elif kind == "DIVERGENCE_CHECK":
            checks_recorded += 1
        elif kind == "BASELINE":
            baseline_row = body
            if body["n"] >= P["divergence_min_baseline_trades"] and _frac(body["variance_ppm2"]) > 0:
                baseline = {"n": body["n"], "mean": _frac(body["mean_ppm"]), "var": _frac(body["variance_ppm2"])}
        elif kind == "PAUSE":
            pauses_recorded.append({"t": t, "seq": seq})
        elif kind == "REFUSED_INPUT":
            known = body["candidate_id"] in pending or body["candidate_id"] in decided
            justified = known if body["reason"] == "DUPLICATE_CANDIDATE" else body["market_id"] in settled
            if not justified:
                mismatches.append({"seq": seq, "what": "refused input", "got": body["reason"], "want": "admitted"})

    if [(h["t"], h["equity_units"]) for h in halts_expected] != [(h["t"], h["equity_units"]) for h in halts_recorded]:
        mismatches.append({"seq": None, "what": "halt instants", "got": halts_recorded, "want": halts_expected})
    if windows_expected != checks_recorded:
        mismatches.append({"seq": None, "what": "divergence windows", "got": checks_recorded,
                           "want": windows_expected})
    if [p["t"] for p in pauses_expected] != [p["t"] for p in pauses_recorded]:
        mismatches.append({"seq": None, "what": "pause instants", "got": pauses_recorded, "want": pauses_expected})
    return {
        "rows": len(rows), "chain": chain, "candidates": sum(1 for r in rows if r["kind"] == "CANDIDATE"),
        "executed": len(executed), "rejected": dict(sorted(reasons.items())),
        "mismatches": mismatches, "violations": violations, "max_stake_deviation_units": max_dev,
        "halts": halts_recorded, "pauses": pauses_recorded, "max_drawdown_ppm": max_dd,
        "final_equity_units": equity(), "executed_rows": executed, "realised": realised,
        "baseline_row": baseline_row, "divergence_windows": checks_recorded,
    }


def baseline_of(realised: list[dict]) -> dict | None:
    """Mean and sample variance of the realised edge, as the BASELINE row must state them."""
    xs = [r["x_ppm"] for r in realised]
    if not xs:
        return None
    mean = Fraction(sum(xs), len(xs))
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else Fraction(0)
    return {"n": len(xs), "mean_ppm": f"{mean.numerator}/{mean.denominator}",
            "variance_ppm2": f"{var.numerator}/{var.denominator}"}


if __name__ == "__main__":
    market_map = {}
    for text in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
        m = json.loads(text)
        market_map[m["market_id"]] = m
    result = audit(sys.argv[1], market_map)
    print(f"rows {result['rows']}; chain {'OK' if result['chain']['ok'] else 'BROKEN'}; executed {result['executed']}; "
          f"mismatches {len(result['mismatches'])}; limit violations {len(result['violations'])}; "
          f"largest size deviation {result['max_stake_deviation_units']} units; halts {len(result['halts'])}; "
          f"pauses {len(result['pauses'])}")
    sys.exit(0 if result["chain"]["ok"] and not result["mismatches"] and not result["violations"] else 1)

"""Report, rebuilt from the ledgers every time - never hand-edited, never remembered.

The headline of the report is the respect of the limits and the integrity of the ledger. The simulated
economic result is shown per world only, under a caption that says what it is: not a performance.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from harness.exact import read_json, read_jsonl, sha256_file, write_json, write_text_lf
from harness.ledger import verify

CAPTION = "synthetic world with planted edge; not a performance"


def _ppm_pct(ppm: int) -> str:
    sign = "-" if ppm < 0 else ""
    ppm = abs(ppm)
    return f"{sign}{ppm // 10000}.{ppm % 10000:04d}%"


def summarise_ledger(path: Path) -> dict:
    check = verify(path)
    genesis = decisions = None
    reasons, kinds = Counter(), Counter()
    executed = staked = settlements = trades_won = trades_lost = windows = 0
    halt = pause = None
    rearm_refused = rearms = refused_inputs = 0
    equity = peak = max_dd = 0
    for row in read_jsonl(path):
        kind, body = row["kind"], row["body"]
        kinds[kind] += 1
        if kind == "GENESIS":
            genesis = body
            equity = peak = body["limits"]["initial_bankroll_units"]
        elif kind == "DECISION":
            if body["decision"] == "EXECUTED":
                executed += 1
                staked += body["sizing"]["stake_units"]
            else:
                reasons[body["reason"]] += 1
        elif kind == "SETTLEMENT":
            settlements += 1
            equity, peak = body["equity_units"], body["peak_units"]
            max_dd = max(max_dd, body["drawdown_ppm"])
            trades_won += sum(1 for p in body["positions"] if p["won"])
            trades_lost += sum(1 for p in body["positions"] if not p["won"])
        elif kind == "HALT" and halt is None:
            halt = {"seq": row["seq"], "t": row["t"], "drawdown_ppm": body["drawdown_ppm"]}
        elif kind == "PAUSE" and pause is None:
            pause = {"seq": row["seq"], "t": row["t"], "window_index": body["window_index"]}
        elif kind == "DIVERGENCE_CHECK":
            windows += 1
        elif kind == "REARM_REFUSED":
            rearm_refused += 1
        elif kind == "REARM":
            rearms += 1
        elif kind == "REFUSED_INPUT":
            refused_inputs += 1
    initial = genesis["limits"]["initial_bankroll_units"]
    return {
        "ledger_sha256": sha256_file(path), "rows": check["rows"], "head": check["head"], "chain_ok": check["ok"],
        "first_bad_seq": check["first_bad_seq"], "mode": "PAPER", "phase": genesis["phase"],
        "candidates": kinds["CANDIDATE"], "refused_inputs": refused_inputs, "executed": executed,
        "rejected": dict(sorted(reasons.items())), "rejected_total": sum(reasons.values()),
        "settlements": settlements, "trades_won": trades_won, "trades_lost": trades_lost,
        "halt": halt, "rearm_refused": rearm_refused, "rearms": rearms, "pause": pause,
        "divergence_windows_checked": windows, "max_drawdown_ppm": max_dd,
        "simulated_result": {"caption": CAPTION, "initial_units": initial, "final_equity_units": equity,
                             "delta_units": equity - initial, "staked_units": staked},
    }


def build(out_root: Path, names: list[str]) -> dict:
    out_root = Path(out_root)
    worlds = []
    for name in names:
        summary = read_json(out_root / name / "summary.json")
        entry = {"world": name, "seed": summary["seed"], "as_of": summary["as_of"],
                 "rules_sha256": summary["rules_sha256"], "divergence_armed": summary["divergence_armed"],
                 "divergence_unarmed_reason": summary["divergence_unarmed_reason"],
                 "replay": summarise_ledger(out_root / name / "ledger.jsonl"), "backtest": None}
        bt_path = out_root / name / "backtest.json"
        if bt_path.is_file():
            bt = read_json(bt_path)
            entry["backtest"] = {"status": bt["status"], "reason": bt["reason"], "baseline": bt["baseline"],
                                 "trades_settled": bt["trades_settled"]}
            if bt["status"] == "OK":
                entry["backtest"]["ledger"] = summarise_ledger(out_root / name / "backtest_ledger.jsonl")
        worlds.append(entry)
    doc = {"title": "Paper-trading replay on synthetic worlds", "mode": "PAPER",
           "disclaimer": "Simulated only. Synthetic data with known probabilities. Not investment advice, "
                         "not a performance claim.",
           "as_of": sorted({w["as_of"] for w in worlds}), "worlds": worlds}
    write_json(out_root / "report.json", doc)
    write_text_lf(out_root / "report.md", render_markdown(doc))
    return doc


def render_markdown(doc: dict) -> str:
    lines = ["# Paper-trading replay on synthetic worlds", "",
             f"**{doc['disclaimer']}**", "",
             f"Data as of: {', '.join(doc['as_of'])} (the as_of written in each world's meta.json).", "",
             "## Limits and ledger (replay segment)", "",
             "| world | seed | candidates | executed | rejected | halt (t) | pause (t) | max drawdown | ledger rows | chain |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for w in doc["worlds"]:
        r = w["replay"]
        lines.append(f"| {w['world']} | {w['seed']} | {r['candidates']} | {r['executed']} | {r['rejected_total']} | "
                     f"{r['halt']['t'] if r['halt'] else '-'} | {r['pause']['t'] if r['pause'] else '-'} | "
                     f"{_ppm_pct(r['max_drawdown_ppm'])} | {r['rows']} | {'OK' if r['chain_ok'] else 'BROKEN'} |")
    lines += ["", "## Refusals by reason (replay segment)", ""]
    for w in doc["worlds"]:
        parts = ", ".join(f"{k} {v}" for k, v in w["replay"]["rejected"].items()) or "none"
        lines.append(f"- **{w['world']}**: {parts}")
    lines += ["", "## Simulated result per world", "",
              f"*Caption for every row: {CAPTION}. The null world is always shown.*", "",
              "| world | initial units | final equity units | delta units |", "|---|---|---|---|"]
    for w in doc["worlds"]:
        s = w["replay"]["simulated_result"]
        lines.append(f"| {w['world']} | {s['initial_units']} | {s['final_equity_units']} | {s['delta_units']} |")
    lines += ["", "## Ledger heads", ""]
    for w in doc["worlds"]:
        lines.append(f"- {w['world']}: `{w['replay']['head']}` (file sha256 `{w['replay']['ledger_sha256']}`)")
    return "\n".join(lines) + "\n"

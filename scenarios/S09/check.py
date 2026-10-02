"""S09 (leak test) - a signal that uses information from the future fails the backtest: no baseline, no trade."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from harness import backtest, rules as rules_mod  # noqa: E402
from harness.stream import load_world  # noqa: E402


def leaky_outcome(cand, view):
    """Looks at the outcome of the candidate's own market."""
    outcome = view.outcome(cand["market_id"])
    if outcome is None:
        return cand["signal_ppm"]
    return 990_000 if outcome == cand["side"] else 10_000


def leaky_last_quote(cand, view):
    """Looks at the last quote of the market in the whole stream: subtler, still the future."""
    return view.quotes(cand["market_id"])[-1][2]


def honest_mean(cand, view):
    """Mean of the quotes seen up to the candidate's own time."""
    prices = [price for t, _side, price in view.quotes(cand["market_id"]) if t <= cand["t"]]
    return sum(prices) // len(prices)


SIGNALS = {"leaky_outcome": leaky_outcome, "leaky_last_quote": leaky_last_quote, "honest_mean": honest_mean}


def check():
    exp, problems, got = C.expected(HERE), [], {}
    with C.workdir("S09") as work:
        world_dir = C.copy_world(HERE, work)
        rules = rules_mod.load(C.RULES)
        for name, fn in SIGNALS.items():
            out = work / name
            result = backtest.run_backtest(load_world(world_dir), rules, out, signal_fn=fn, signal_name=name)
            leak = result["leak_test"]
            got[name] = {"status": result["status"], "reason": result["reason"],
                         "first_leaking_candidate": leak.get("candidate_id"), "checked": leak["checked"],
                         "with_future": leak.get("with_future"), "without_future": leak.get("without_future"),
                         "ledger_written": (out / "backtest_ledger.jsonl").is_file(),
                         "baseline": result["baseline"], "trades_settled": result["trades_settled"]}
            if got[name] != exp["signals"][name]:
                problems.append(f"{name}: {got[name]}, expected {exp['signals'][name]}")
            if result["status"] == "OK":
                ledger_path = out / "backtest_ledger.jsonl"
                rows = C.rows_of(ledger_path)
                C.compare_decisions(rows, exp["honest_decisions"], problems)
                C.check_chain(ledger_path, problems)
                if len(rows) != exp["honest_ledger_rows"]:
                    problems.append(f"{len(rows)} ledger rows, expected {exp['honest_ledger_rows']}")
                sources = {r["body"].get("signal_source") for r in rows if r["kind"] == "CANDIDATE"}
                if sources != {name}:
                    problems.append(f"candidates do not name the signal that produced them: {sources}")
    return C.finish("S09", problems, "a signal reading the outcome and one reading a later quote both fail the "
                    "backtest (LOOKAHEAD) with no ledger and no baseline; the past-only signal passes", {"observed": got})


if __name__ == "__main__":
    C.main(check)

"""Backtest on the first segment of a synthetic stream: baseline of the realised edge, and a leak test.

Leak test (look-ahead): a signal function is called twice for every candidate - once with a view of the
whole stream, once with a view truncated at the candidate's own time. If the two answers differ, the
function used information from the future: the backtest is FAILED and produces no baseline and no trade.

Limit, stated plainly: the test sees only what goes through the view. A function that reads files
behind the harness's back is not detected.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness import divergence
from harness.engine import Engine
from harness.exact import write_json
from harness.ledger import Ledger
from harness.rules import Rules
from harness.stream import World, events


class StreamView:
    """What a signal function may look at. ``upto=None`` is the whole stream (used only by the leak test)."""

    def __init__(self, world: World, upto: int | None):
        self._world = world
        self.upto = upto

    def outcome(self, market_id: str) -> str | None:
        market = self._world.markets[market_id]
        if self.upto is not None and market["resolves_at"] > self.upto:
            return None
        return self._world.outcomes[market_id]

    def quotes(self, market_id: str) -> list[tuple[int, str, int]]:
        """(t, side, price_ppm) of the candidates of a market that are visible in this view."""
        index = self._world.__dict__.get("_by_market")
        if index is None:
            index = {}
            for c in self._world.candidates:
                index.setdefault(c["market_id"], []).append(c)
            self._world.__dict__["_by_market"] = index
        return [(c["t"], c["side"], c["price_ppm"]) for c in index.get(market_id, [])
                if (self.upto is None or c["t"] <= self.upto) and isinstance(c.get("price_ppm"), int)]


def leak_test(signal_fn: Callable, world: World, candidates: list) -> dict:
    full = StreamView(world, None)
    for number, cand in enumerate(candidates, start=1):
        a = signal_fn(cand, full)
        b = signal_fn(cand, StreamView(world, cand["t"]))
        if a != b:
            return {"status": "FAILED", "reason": "LOOKAHEAD", "candidate_id": cand["candidate_id"], "t": cand["t"],
                    "with_future": a, "without_future": b, "checked": number}
    return {"status": "OK", "reason": None, "checked": len(candidates)}


def genesis(world: World, rules: Rules, phase: str) -> dict:
    from harness import VERSION
    return {"world": world.meta["world"], "seed": world.meta.get("seed"), "as_of": world.meta["as_of"],
            "phase": phase, "harness_version": VERSION, "rules_sha256": rules.digest,
            "limits": rules.limits_snapshot(), "costs": rules.costs,
            "min_approvals": rules.validators["quorum"]["min_approvals"],
            "note": "PAPER TRADING ON SYNTHETIC DATA. No real market, no account, no money."}


def run_backtest(world: World, rules: Rules, out_dir: Path, *, until_t: int | None = None,
                 signal_fn: Callable | None = None, signal_name: str = "recorded") -> dict:
    """Replay the segment t < until_t on a fresh paper account. Returns status, leak verdict, baseline."""
    out_dir = Path(out_dir)
    candidates = [c for c in world.candidates if until_t is None or c["t"] < until_t]
    result = {"status": "OK", "reason": None, "signal": signal_name, "until_t": until_t,
              "candidates": len(candidates), "leak_test": None, "baseline": None, "trades_settled": 0,
              "ledger_rows": 0, "ledger_head": None}
    if signal_fn is not None:
        leak = leak_test(signal_fn, world, candidates)
        result["leak_test"] = leak
        if leak["status"] != "OK":
            result.update(status="FAILED", reason=leak["reason"])
            write_json(out_dir / "backtest.json", result)
            return result
        candidates = [{**c, "signal_ppm": signal_fn(c, StreamView(world, c["t"])), "signal_source": signal_name}
                      for c in candidates]
    with Ledger.create(out_dir / "backtest_ledger.jsonl", genesis(world, rules, "BACKTEST")) as ledger:
        engine = Engine(rules, ledger, world.markets, consent_dir=world.dir)
        engine.run(events(world, until_t=until_t, candidates=candidates))
        result.update(baseline=divergence.baseline_from(engine.realised), trades_settled=len(engine.realised),
                      ledger_rows=ledger.rows, ledger_head=ledger.head)
        ledger.write_anchors(out_dir / "backtest_anchors.json")
    write_json(out_dir / "backtest.json", result)
    return result

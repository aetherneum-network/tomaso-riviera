"""A synthetic world on disk, and the order in which the harness is allowed to see it.

Files of a world (all generated, all fictitious):
  meta.json         world name, seed, as_of, steps, baseline_until_t
  markets.jsonl     market_id, name, opens_at, resolves_at
  candidates.jsonl  candidate signals, each with its own time t
  outcomes.jsonl    the outcome of each market, visible to the harness only at resolves_at
  requests.jsonl    optional operator requests (re-arm), used by scenarios

The harness never opens anything under ``gold/``: the true probabilities are for the scorer only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harness.exact import read_json, read_jsonl

SETTLEMENT, REQUEST, CANDIDATE = 0, 1, 2  # order of events inside one time step


@dataclass
class World:
    dir: Path
    meta: dict
    markets: dict          # market_id -> market
    candidates: list       # in (t, candidate_id) order
    outcomes: dict         # market_id -> "YES" | "NO"
    requests: list


def load_world(world_dir: Path) -> World:
    world_dir = Path(world_dir)
    meta = read_json(world_dir / "meta.json")
    markets = {m["market_id"]: m for m in read_jsonl(world_dir / "markets.jsonl")}
    candidates = sorted(read_jsonl(world_dir / "candidates.jsonl"), key=lambda c: (c["t"], c["candidate_id"]))
    outcomes = {o["market_id"]: o["outcome"] for o in read_jsonl(world_dir / "outcomes.jsonl")}
    req_path = world_dir / "requests.jsonl"
    requests = list(read_jsonl(req_path)) if req_path.is_file() else []
    missing = sorted(set(markets) - set(outcomes))
    if missing:
        raise ValueError(f"world {meta.get('world')}: markets without an outcome: {missing[:3]}")
    return World(dir=world_dir, meta=meta, markets=markets, candidates=candidates, outcomes=outcomes,
                 requests=requests)


def events(world: World, from_t: int | None = None, until_t: int | None = None, candidates: list | None = None) -> list:
    """Events with from_t <= t < until_t, ordered: settlements, then requests, then candidates."""

    def inside(t: int) -> bool:
        return (from_t is None or t >= from_t) and (until_t is None or t < until_t)

    out = []
    for market_id, market in world.markets.items():
        if inside(market["resolves_at"]):
            out.append((market["resolves_at"], SETTLEMENT, market_id, (market_id, world.outcomes[market_id])))
    for index, request in enumerate(world.requests):
        if inside(request["t"]):
            out.append((request["t"], REQUEST, f"{index:06d}", request))
    for cand in (world.candidates if candidates is None else candidates):
        if inside(cand["t"]):
            out.append((cand["t"], CANDIDATE, cand["candidate_id"], cand))
    out.sort(key=lambda e: (e[0], e[1], e[2]))
    return out

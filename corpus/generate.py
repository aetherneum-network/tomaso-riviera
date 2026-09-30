"""Deterministic generator of the synthetic corpus: four worlds of fictitious binary-outcome markets.

    python corpus/generate.py                      # development corpus (seed 20260930) -> corpus/out
    python corpus/generate.py --check              # regenerate in memory and compare with MANIFEST.sha256
    python corpus/generate.py --seed N --out DIR   # another seed, another directory
    python corpus/generate.py --seed N --out DIR --stress
    python corpus/generate.py --seed N --out DIR --fifth-world params.json

The generator knows the true probability of every outcome, so the true edge of every candidate is
known by construction. The truth goes to ``gold/`` and is never opened by the harness.

Determinism: integers only, no transcendental function, one seeded Mersenne Twister stream per world
(seeded from SHA-256 of "seed|world"), only ``randrange``. Files are UTF-8, LF, canonical JSON.
This module imports nothing from the harness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from corpus import worlds as W  # noqa: E402

PPM = W.PPM
DEV_SEED = 20260930
MANIFEST = HERE / "MANIFEST.sha256"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _rng(seed: int, world: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}|{world}".encode("ascii")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _rr(rng: random.Random, lo: int, hi: int) -> int:
    """Uniform integer in [lo, hi]."""
    return lo + rng.randrange(hi - lo + 1)


def _tri(rng: random.Random, amplitude: int) -> int:
    """Symmetric triangular integer noise in [-amplitude, +amplitude] (mean of two uniform draws)."""
    if amplitude <= 0:
        rng.randrange(1), rng.randrange(1)
        return 0
    return (_rr(rng, -amplitude, amplitude) + _rr(rng, -amplitude, amplitude)) // 2


def _clamp(value: int) -> int:
    return max(1_000, min(PPM - 1_000, value))


def _regime_at(regimes: list, t: int) -> tuple[int, dict]:
    index = 0
    for i, r in enumerate(regimes):
        if r["from_t"] <= t:
            index = i
    return index, regimes[index]


def generate_world(name: str, spec: dict, seed: int, *, stress: bool = False, steps: int = W.STEPS,
                   n_markets: int = W.MARKETS, baseline_until_t: int = W.BASELINE_UNTIL_T) -> dict:
    """Return {relative path: text} for one world. Pure function of its arguments."""
    rng = _rng(seed, name)
    code = spec.get("code", "W5")
    noise, confirm_noise = spec["noise_ppm"], spec["confirm_noise_ppm"]
    slip_lo, slip_hi = spec["slippage_ppm"]
    lat_lo, lat_hi = spec["latency_steps"]
    gaps = []
    if stress:
        num, den = W.STRESS["noise_num_den"]
        noise, confirm_noise = noise * num // den, confirm_noise * num // den
        num, den = W.STRESS["slippage_num_den"]
        slip_lo, slip_hi = slip_lo * num // den, slip_hi * num // den
        lat_lo, lat_hi = W.STRESS["latency_steps"]
        gaps = list(W.STRESS["gaps"])

    markets, candidates, outcomes, truth_c, truth_m = [], [], [], [], []
    cluster = None
    for i in range(n_markets):
        market_id = f"{code}-M{i:04d}"
        life = _rr(rng, *W.MARKET_LIFE_STEPS)
        opens = rng.randrange(0, steps - W.MARKET_LIFE_STEPS[1])
        driver = rng.randrange(PPM)
        side = "YES" if rng.randrange(2) else "NO"
        if stress:
            if i % W.STRESS["cluster_size"] == 0:
                cluster = (life, opens, driver, side)
            life, opens, driver, side = cluster
        resolves = opens + life
        regime_index, regime = _regime_at(spec["regimes"], opens)
        lo, hi = regime.get("p_side_ppm", spec["p_side_ppm"])
        p_side = (_rr(rng, lo, hi) // 1_000) * 1_000
        p_true = p_side if side == "YES" else PPM - p_side
        outcome = side if driver < p_side else ("NO" if side == "YES" else "YES")
        n_cands = _rr(rng, *W.CANDIDATES_PER_MARKET)
        times: set[int] = set()
        while len(times) < n_cands:
            times.add(opens + rng.randrange(life))
        markets.append({"market_id": market_id, "name": f"{spec.get('place', 'Fifth world')} - "
                        f"{spec.get('event', 'event')} {i + 1:03d}", "opens_at": opens, "resolves_at": resolves})
        outcomes.append({"market_id": market_id, "t": resolves, "outcome": outcome})
        truth_m.append({"market_id": market_id, "side": side, "p_side_true_ppm": p_side, "p_true_ppm": p_true,
                        "outcome": outcome, "regime_index": regime_index})
        for k, t in enumerate(sorted(times)):
            # Every draw is made in a fixed order, whether or not it is used.
            true_edge = regime["true_edge_ppm"][rng.randrange(len(regime["true_edge_ppm"]))]
            signal_noise, confirm_draw = _tri(rng, noise), _tri(rng, confirm_noise)
            confidence = _rr(rng, spec["confidence_ppm"][0] // 1_000, spec["confidence_ppm"][1] // 1_000) * 1_000
            conviction = W.CONVICTIONS[rng.randrange(len(W.CONVICTIONS))]
            overround = _rr(rng, 0, 20_000)
            bad_book, bad_book_size = rng.randrange(PPM), _rr(rng, 60_000, 120_000)
            quote_age, stale, stale_age = _rr(rng, 0, 2), rng.randrange(PPM), _rr(rng, 4, 12)
            slippage = (_rr(rng, slip_lo, slip_hi) // 100) * 100
            latency = _rr(rng, lat_lo, lat_hi)
            defect = rng.randrange(PPM)

            if any(a <= t < b for a, b in gaps):
                continue
            price = _clamp(p_side - true_edge)
            complement = PPM - price + overround + (bad_book_size if bad_book < W.INCONSISTENT_BOOK_PPM else 0)
            price_as_of = t - (stale_age if stale < W.STALE_QUOTE_PPM else quote_age)
            for a, b in gaps:
                if b <= t < b + W.STRESS["stale_after_gap_steps"]:
                    price_as_of = a - 1   # the last quote seen before the gap
            cand = {
                "candidate_id": f"{market_id}-C{k:02d}", "market_id": market_id, "t": t, "side": side,
                "price_ppm": price, "price_as_of": price_as_of, "complement_price_ppm": complement,
                "prior_ppm": price, "signal_ppm": _clamp(p_side + regime["signal_bias_ppm"] + signal_noise),
                "confidence_ppm": confidence, "conviction": conviction,
                "confirm_ppm": _clamp(p_side + regime["signal_bias_ppm"] + confirm_draw),
                "cost": {"fee_ppm": spec["fee_ppm"], "slippage_ppm": slippage, "latency_steps": latency},
            }
            if defect < W.MISSING_CONFIDENCE_PPM:
                cand["confidence_ppm"] = None
            elif defect < W.MISSING_CONFIDENCE_PPM + W.MISSING_COST_PPM:
                cand["cost"] = None
            candidates.append(cand)
            truth_c.append({"candidate_id": cand["candidate_id"], "p_side_true_ppm": p_side,
                            "true_edge_ppm": p_side - price, "regime_index": regime_index})

    candidates.sort(key=lambda c: (c["t"], c["candidate_id"]))
    order = {c["candidate_id"]: n for n, c in enumerate(candidates)}
    truth_c.sort(key=lambda r: order[r["candidate_id"]])
    meta = {"world": name, "code": code, "seed": seed, "as_of": W.AS_OF, "steps": steps,
            "baseline_until_t": baseline_until_t, "markets": len(markets), "candidates": len(candidates),
            "generator_version": W.GENERATOR_VERSION, "stress": stress, "synthetic": True,
            "note": "Fictitious markets generated by a seeded program. Not real data."}
    world_truth = {"world": name, "seed": seed, "stress": stress, "description": spec.get("description", ""),
                   "regimes": spec["regimes"], "noise_ppm": noise, "confirm_noise_ppm": confirm_noise,
                   "fee_ppm": spec["fee_ppm"], "slippage_ppm": [slip_lo, slip_hi],
                   "latency_steps": [lat_lo, lat_hi], "p_side_ppm": spec["p_side_ppm"],
                   "confidence_ppm": spec["confidence_ppm"], "gaps": [list(g) for g in gaps]}

    def jsonl(rows):
        return "".join(canonical(r) + "\n" for r in rows)

    return {
        f"{name}/meta.json": json.dumps(meta, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        f"{name}/markets.jsonl": jsonl(markets),
        f"{name}/candidates.jsonl": jsonl(candidates),
        f"{name}/outcomes.jsonl": jsonl(outcomes),
        f"{name}/gold/truth.jsonl": jsonl(truth_c),
        f"{name}/gold/markets_truth.jsonl": jsonl(truth_m),
        f"{name}/gold/world_truth.json": json.dumps(world_truth, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
    }


def generate(seed: int, *, stress: bool = False, fifth: dict | None = None) -> dict:
    files: dict[str, str] = {}
    for name in W.WORLD_ORDER:
        files.update(generate_world(name, W.WORLDS[name], seed, stress=stress))
    if fifth is not None:
        W.validate_spec(fifth)
        spec = {**fifth, "code": "W5", "place": "Fifth world", "event": "event"}
        files.update(generate_world("fifth", spec, seed, stress=stress))
    return files


def digest_lines(files: dict) -> str:
    return "".join(f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {rel}\n" for rel, text in sorted(files.items()))


def write(files: dict, out: Path) -> None:
    out = Path(out)
    for rel, text in sorted(files.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the synthetic corpus (fictitious markets, known probabilities).")
    ap.add_argument("--seed", type=int, default=DEV_SEED)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--stress", action="store_true", help="values outside the development ranges")
    ap.add_argument("--fifth-world", help="JSON file with the parameters of a fifth world (blind protocol)")
    ap.add_argument("--check", action="store_true", help="regenerate the development corpus and compare with MANIFEST.sha256")
    ap.add_argument("--write-manifest", action="store_true", help="rewrite MANIFEST.sha256 for the development corpus")
    args = ap.parse_args(argv)

    if args.check or args.write_manifest:
        lines = digest_lines(generate(DEV_SEED))
        if args.write_manifest:
            with open(MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(lines)
            print(f"MANIFEST.sha256 written: {len(lines.splitlines())} files, seed {DEV_SEED}")
            return 0
        expected = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else ""
        mismatches = len(set(lines.splitlines()) ^ set(expected.splitlines()))
        print(f"files: {len(lines.splitlines())}; mismatches vs MANIFEST.sha256: {mismatches}")
        return 0 if mismatches == 0 else 1

    fifth = json.loads(Path(args.fifth_world).read_text(encoding="utf-8")) if args.fifth_world else None
    out = Path(args.out)
    marker = out / "CORPUS.json"
    ident = {"seed": args.seed, "stress": args.stress, "fifth_world": fifth is not None,
             "generator_version": W.GENERATOR_VERSION}
    if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")) != ident:
        print(f"REFUSED: {out.name} already holds a different corpus; use a new directory (nothing is overwritten)")
        return 2
    files = generate(args.seed, stress=args.stress, fifth=fifth)
    write(files, out)
    with open(marker, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(ident, sort_keys=True, indent=2) + "\n")
    total = sum(json.loads(files[f"{n}/meta.json"])["candidates"] for n in W.WORLD_ORDER)
    print(f"corpus written: seed {args.seed}, {len(files)} files, {total} candidates in the four worlds"
          + (", stress" if args.stress else "") + (", + fifth world" if fifth else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

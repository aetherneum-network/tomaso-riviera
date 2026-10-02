"""Scorer: generate a corpus, replay it on paper, audit the ledgers with the independent references.

    python eval/score.py --suite dev --out build/eval-dev-001
    python eval/score.py --suite holdout --out build/eval-holdout-001
    python eval/score.py --suite stress --out build/eval-stress-001
    python eval/score.py --detector-study
    python eval/score.py --rehearsal --out build/eval-rehearsal-001
    python eval/score.py --blind --seed N --fifth-world F.json --hand-candidates H.jsonl --runner "NAME" --out DIR

Headline numbers, in this order: rows that violate a limit (must be 0), agreement of the decisions with
the independent reference, largest deviation of a size from the reference, integrity of the hash chain.
The simulated economic result is NOT a metric: it is printed per world, under its caption, and nowhere
else. Every number is an integer or an exact fraction: there is no float in any file written here.

The truth of the generator (``gold/``) is read only here, never by the harness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from corpus import generate, reference_drawdown, reference_gate  # noqa: E402
from corpus import worlds as W  # noqa: E402
from harness import VERSION, divergence, rules as rules_mod, run  # noqa: E402
from harness.exact import canonical, write_json, write_text_lf  # noqa: E402
from harness.report import CAPTION  # noqa: E402

PPM = 1_000_000
SUITES = {
    "dev": {"seed": 20260930, "stress": False},
    "holdout": {"seed": 20261001, "stress": False},
    "stress": {"seed": 20261002, "stress": True},
}
REHEARSAL_SEED = 20260929   # used only to prove that the blind command runs; declared, so never "blind"
RESERVED_SEEDS = {s["seed"] for s in SUITES.values()} | {REHEARSAL_SEED}
BLIND_DIR = ROOT / "eval" / "blind"
BLIND_RESULT = BLIND_DIR / "result.json"
TEMPLATE_FIFTH = BLIND_DIR / "fifth_world.template.json"
TEMPLATE_HAND = BLIND_DIR / "hand_candidates.template.jsonl"

# What the pack declares about the divergence rule (measured by ``detector_study`` and by scenario S08).
DECLARED = {
    "pause_within_settled_trades": 150,      # K: three windows of 50 after the first post-change settlement
    "false_pause_per_run_max_ppm": 20_000,   # at most 2% of stationary runs (5 windows, baseline 90) pause
    "pause_within_K_min_ppm": 850_000,       # at least 85% of regime runs are paused within K trades
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _signed_floor_div(num: int, den: int) -> int:
    return -((-num) // den) if num < 0 else num // den


def score_world(world_dir: Path, run_dir: Path) -> dict:
    meta = json.loads((world_dir / "meta.json").read_text(encoding="utf-8"))
    markets = {m["market_id"]: m for m in _jsonl(world_dir / "markets.jsonl")}
    truth = {r["candidate_id"]: r for r in _jsonl(world_dir / "gold" / "truth.jsonl")}
    market_truth = {r["market_id"]: r for r in _jsonl(world_dir / "gold" / "markets_truth.jsonl")}
    world_truth = json.loads((world_dir / "gold" / "world_truth.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    replay = reference_drawdown.audit(run_dir / "ledger.jsonl", markets)
    bt_path = run_dir / "backtest_ledger.jsonl"
    backtest = reference_drawdown.audit(bt_path, markets) if bt_path.is_file() else None
    segments = [s for s in (backtest, replay) if s is not None]

    total = sum(s["executed"] + sum(s["rejected"].values()) for s in segments)
    mismatches = [m for s in segments for m in s["mismatches"]]
    violations = [v for s in segments for v in s["violations"]]

    baseline_ok = None
    if backtest is not None:
        want = reference_drawdown.baseline_of(backtest["realised"])
        row = replay["baseline_row"]
        baseline_ok = row is not None and want is not None and all(row[k] == want[k] for k in want)

    # Calibration of the paper trades against the generator's truth (all settled trades of both segments).
    n = wins = sum_p = sum_var = sum_true_net = true_pass = 0
    for seg in segments:
        outcome = {r["market_id"]: r["won"] for r in seg["realised"]}
        for e in seg["executed_rows"]:
            if e["market_id"] not in outcome:
                continue   # still open at the end of the segment
            p = truth[e["candidate_id"]]["p_side_true_ppm"]
            n += 1
            wins += 1 if outcome[e["market_id"]] else 0
            sum_p += p
            sum_var += p * (PPM - p)
            sum_true_net += p - e["q_eff_ppm"]
            true_pass += 1 if p - e["q_eff_ppm"] > reference_gate.PARAMS["threshold_ppm"] else 0
    root = math.isqrt(sum_var)
    calibration = {
        "settled_trades": n, "wins": wins, "sum_true_probability_ppm": sum_p,
        "expected_wins_milli": sum_p // 1000,
        "z_milli": None if root == 0 else _signed_floor_div((wins * PPM - sum_p) * 1000, root),
        "mean_true_net_edge_ppm": None if n == 0 else _signed_floor_div(sum_true_net, n),
        "trades_with_true_net_edge_above_threshold": true_pass,
    }

    # The signal against the truth, on every complete candidate of the world (this measures the signal).
    table = {"estimated_pass_true_pass": 0, "estimated_pass_true_fail": 0, "estimated_fail_true_pass": 0,
             "estimated_fail_true_fail": 0, "incomplete": 0}
    for cand in _jsonl(world_dir / "candidates.jsonl"):
        verdict = reference_gate.true_gate(cand, truth[cand["candidate_id"]]["p_side_true_ppm"])
        if verdict is None:
            table["incomplete"] += 1
            continue
        est_pass = verdict["estimated_net_edge_ppm"] > reference_gate.PARAMS["threshold_ppm"]
        table[f"estimated_{'pass' if est_pass else 'fail'}_true_{'pass' if verdict['true_gate'] else 'fail'}"] += 1

    regime = None
    if len(world_truth["regimes"]) > 1:
        change_t = world_truth["regimes"][1]["from_t"]
        pause_t = replay["pauses"][0]["t"] if replay["pauses"] else None
        halt_t = replay["halts"][0]["t"] if replay["halts"] else None
        post = [r for r in replay["realised"] if market_truth[r["market_id"]]["regime_index"] >= 1]
        regime = {
            "change_t": change_t, "pause_t": pause_t, "halt_t": halt_t,
            "delay_steps": None if pause_t is None else pause_t - change_t,
            "post_change_trades_settled_until_pause": None if pause_t is None
            else sum(1 for r in post if r["t"] <= pause_t),
            "within_declared_K": pause_t is not None and sum(1 for r in post if r["t"] <= pause_t)
            <= DECLARED["pause_within_settled_trades"],
        }

    return {
        "world": meta["world"], "seed": meta["seed"], "stress": meta["stress"], "as_of": meta["as_of"],
        "limit_violations": len(violations), "violations": violations[:20],
        "decisions": {"total": total, "agree": total - sum(1 for m in mismatches if m["what"] == "decision"),
                      "other_mismatches": sum(1 for m in mismatches if m["what"] != "decision"),
                      "first_mismatches": mismatches[:10],
                      "max_size_deviation_units": max(s["max_stake_deviation_units"] for s in segments)},
        "ledger": {"rows": replay["rows"], "chain_ok": replay["chain"]["ok"], "head": summary["ledger_head"],
                   "backtest_rows": None if backtest is None else backtest["rows"],
                   "backtest_chain_ok": None if backtest is None else backtest["chain"]["ok"]},
        "replay": {"candidates": replay["candidates"], "executed": replay["executed"], "rejected": replay["rejected"],
                   "halts": [h["t"] for h in replay["halts"]], "pauses": [p["t"] for p in replay["pauses"]],
                   "max_drawdown_ppm": replay["max_drawdown_ppm"]},
        "detector": {"armed": summary["divergence_armed"], "unarmed_reason": summary["divergence_unarmed_reason"],
                     "baseline_trades": None if replay["baseline_row"] is None else replay["baseline_row"]["n"],
                     "baseline_matches_backtest": baseline_ok, "windows_checked": replay["divergence_windows"]},
        "regime": regime, "calibration": calibration, "signal_vs_truth": table,
        "simulated_result": {"caption": CAPTION, "initial_units": reference_drawdown.PARAMS["initial_bankroll_units"],
                             "final_equity_units": replay["final_equity_units"],
                             "delta_units": replay["final_equity_units"]
                             - reference_drawdown.PARAMS["initial_bankroll_units"]},
    }


def headline(worlds: list[dict]) -> dict:
    total = sum(w["decisions"]["total"] for w in worlds)
    agree = sum(w["decisions"]["agree"] for w in worlds)
    return {
        "limit_violations": sum(w["limit_violations"] for w in worlds),
        "decisions_total": total, "decisions_agree": agree,
        "other_mismatches": sum(w["decisions"]["other_mismatches"] for w in worlds),
        "max_size_deviation_units": max(w["decisions"]["max_size_deviation_units"] for w in worlds),
        "ledgers_chain_ok": all(w["ledger"]["chain_ok"] and w["ledger"]["backtest_chain_ok"] is not False
                                for w in worlds),
        "ledger_rows": sum(w["ledger"]["rows"] + (w["ledger"]["backtest_rows"] or 0) for w in worlds),
    }


def _fresh(out: Path) -> Path:
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"REFUSED: {out.name} is not empty; use a new directory (nothing is overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    return out


def run_suite(name: str, seed: int, stress: bool, out: Path, fifth: dict | None = None) -> dict:
    out = _fresh(out)
    generate.write(generate.generate(seed, stress=stress, fifth=fifth), out / "corpus")
    rules = rules_mod.load(ROOT / "rules")
    summaries = run.run_corpus(out / "corpus", ROOT / "rules", out / "run")
    worlds = [score_world(out / "corpus" / s["world"], out / "run" / s["world"]) for s in summaries]
    result = {"suite": name, "seed": seed, "stress": stress, "as_of": W.AS_OF, "harness_version": VERSION,
              "generator_version": W.GENERATOR_VERSION, "rules_sha256": rules.digest, "code_sha256": code_digest(),
              "headline": headline(worlds), "worlds": worlds}
    write_json(out / "score.json", result)
    return result


def code_digest() -> str:
    """One SHA-256 over the harness, the rules, the generator and the references (paths and bytes)."""
    h = hashlib.sha256()
    files = (list((ROOT / "harness").glob("*.py")) + list((ROOT / "rules").glob("*.json"))
             + list((ROOT / "corpus").glob("*.py")) + [ROOT / "eval" / "score.py"])
    # ordered by the relative path as text: the order of Path objects depends on the operating system
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        h.update(path.relative_to(ROOT).as_posix().encode("ascii") + b"\0")
        h.update(hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).digest())
    return h.hexdigest()


# --------------------------------------------------------------------------- divergence rule: operating point
def _stream(rng: random.Random, p_range: tuple[int, int], edges: tuple[int, ...], cost: int = 11_500) -> int:
    p = (p_range[0] + rng.randrange(p_range[1] - p_range[0] + 1)) // 1000 * 1000
    edge = edges[rng.randrange(len(edges))]
    won = rng.randrange(PPM) < p
    return (PPM if won else 0) - (p - edge + cost)


def detector_study(seed: int = SUITES["dev"]["seed"], runs: int = 2000, baseline_trades: int = 90,
                   replay_trades: int = 250, change_after: int = 54) -> dict:
    """Seeded measurement of the divergence rule on synthetic sequences of realised edges (integers only).

    Three kinds of run: a stationary stream like the planted-edge world; a stationary stream of
    favourites like the first regime of the regime-change world; and that stream with the edge removed
    after ``change_after`` settled trades. The detector is the harness's own, with the rule file's values.
    """
    cfg = rules_mod.load(ROOT / "rules").risk["divergence"]
    rng = random.Random(seed)
    planted = ((300_000, 750_000), (60_000, 80_000, 100_000))
    favourite = ((800_000, 950_000), (300_000,))
    flat = ((500_000, 650_000), (0,))

    def one(first, later=None) -> int | None:
        base = divergence.baseline_from([_stream(rng, *first) for _ in range(baseline_trades)])
        det = divergence.Detector(base, cfg)
        for k in range(replay_trades):
            source = first if later is None or k < change_after else later
            verdict = det.observe(_stream(rng, *source))
            if verdict is not None and verdict["diverged"]:
                return k + 1
        return None

    false_planted = sum(1 for _ in range(runs) if one(planted) is not None)
    false_favourite = sum(1 for _ in range(runs) if one(favourite) is not None)
    delays, missed, early = [], 0, 0
    for _ in range(runs):
        at = one(favourite, flat)
        if at is None:
            missed += 1
        elif at <= change_after:
            early += 1
        else:
            delays.append(at - change_after)
    delays.sort()
    within = sum(1 for d in delays if d <= DECLARED["pause_within_settled_trades"])
    return {
        "seed": seed, "runs_per_kind": runs, "baseline_trades": baseline_trades, "replay_trades": replay_trades,
        "windows_per_run": replay_trades // cfg["window_trades"], "rule": cfg, "declared": DECLARED,
        "false_pause_runs_planted_like": false_planted, "false_pause_runs_favourites": false_favourite,
        "regime_runs_paused_before_change": early, "regime_runs_never_paused": missed,
        "regime_runs_paused_within_declared_K": within,
        "regime_delay_trades_median": delays[len(delays) // 2] if delays else None,
        "regime_delay_trades_max": delays[-1] if delays else None,
    }


# --------------------------------------------------------------------------- blind protocol
HAND_CANDIDATES_MIN = 20   # the blind protocol asks for twenty hand-written boundary candidates


def _has_float(obj) -> bool:
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_has_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_float(v) for v in obj)
    return False


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_hand_rows(rows: list[dict]) -> None:
    """Refuse a hand-written file that cannot be replayed exactly. Called before anything is generated or run."""
    seen, markets, outcomes = set(), {}, {}
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SystemExit(f"hand candidate {number}: not a JSON object")
        for key in ("candidate", "market", "outcome", "expected"):
            if key not in row:
                raise SystemExit(f"hand candidate {number}: missing {key!r}")
        cand, market = row["candidate"], row["market"]
        if not isinstance(cand, dict) or not isinstance(market, dict) or not isinstance(row["expected"], dict):
            raise SystemExit(f"hand candidate {number}: candidate, market and expected must be JSON objects")
        if _has_float(row):
            raise SystemExit(f"hand candidate {number}: a decimal number was found; every figure is an integer "
                             "(parts per million, units or steps)")
        if not isinstance(cand.get("candidate_id"), str) or not isinstance(cand.get("market_id"), str):
            raise SystemExit(f"hand candidate {number}: candidate_id and market_id must be strings")
        if cand["market_id"] != market.get("market_id") or not _int(cand.get("t")) or cand["t"] < 0:
            raise SystemExit(f"hand candidate {number}: market_id mismatch, or t not a non-negative integer")
        if not _int(market.get("opens_at")) or not _int(market.get("resolves_at")) or market["resolves_at"] < 1:
            raise SystemExit(f"hand candidate {number}: the market needs integer opens_at and resolves_at")
        if row["outcome"] not in ("YES", "NO") or row["expected"].get("decision") not in ("EXECUTED", "REJECTED"):
            raise SystemExit(f"hand candidate {number}: outcome or expected decision not recognised")
        if cand["candidate_id"] in seen:
            raise SystemExit(f"hand candidate {number}: duplicate candidate_id")
        if markets.setdefault(market["market_id"], market) != market:
            raise SystemExit(f"hand candidate {number}: the same market is described twice, differently")
        if outcomes.setdefault(market["market_id"], row["outcome"]) != row["outcome"]:
            raise SystemExit(f"hand candidate {number}: the same market is given two different outcomes")
        seen.add(cand["candidate_id"])
    if not rows:
        raise SystemExit("hand candidates: the file is empty")


def _hand_world(rows: list[dict], seed: int, out: Path) -> dict:
    """Build a small world from hand-written candidates. Returns candidate_id -> expected."""
    check_hand_rows(rows)
    expected, markets, outcomes, candidates = {}, {}, {}, []
    for row in rows:
        cand, market = row["candidate"], row["market"]
        markets.setdefault(market["market_id"], market)
        outcomes[market["market_id"]] = row["outcome"]
        expected[cand["candidate_id"]] = row["expected"]
        candidates.append(cand)
    steps = max(m["resolves_at"] for m in markets.values()) + 1
    meta = {"world": "hand", "code": "H", "seed": seed, "as_of": W.AS_OF, "steps": steps, "baseline_until_t": 0,
            "markets": len(markets), "candidates": len(candidates), "generator_version": "hand-written",
            "stress": False, "synthetic": True, "note": "Hand-written boundary candidates. Not real data."}
    write_text_lf(out / "meta.json", json.dumps(meta, sort_keys=True, indent=2) + "\n")
    write_text_lf(out / "markets.jsonl", "".join(canonical(m) + "\n" for m in markets.values()))
    write_text_lf(out / "candidates.jsonl", "".join(canonical(c) + "\n" for c in candidates))
    write_text_lf(out / "outcomes.jsonl", "".join(
        canonical({"market_id": k, "t": markets[k]["resolves_at"], "outcome": v}) + "\n" for k, v in outcomes.items()))
    return expected


def _blind_pipeline(name: str, seed: int, fifth_path: Path, hand_path: Path, runner: str, out: Path) -> dict:
    """Four worlds plus a fifth one from the given parameters, then the hand-written candidates."""
    fifth = json.loads(Path(fifth_path).read_text(encoding="utf-8"))
    fifth = {k: v for k, v in fifth.items() if not k.startswith("_")}   # "_note" and the like are comments
    hand_rows = _jsonl(Path(hand_path))
    try:                          # both inputs are checked before anything is generated, run or measured
        W.validate_spec(fifth)
    except (ValueError, AttributeError, TypeError, KeyError) as exc:
        raise SystemExit(f"REFUSED: fifth world: {exc}")
    check_hand_rows(hand_rows)
    result = run_suite(name, seed, False, out, fifth=fifth)
    expected = _hand_world(hand_rows, seed, out / "hand_world")
    summary = run.run_world(out / "hand_world", ROOT / "rules", out / "hand_run")
    markets = {m["market_id"]: m for m in _jsonl(out / "hand_world" / "markets.jsonl")}
    audit = reference_drawdown.audit(out / "hand_run" / "ledger.jsonl", markets)
    rows = {}
    for line in (out / "hand_run" / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["kind"] == "DECISION":
            rows[row["body"]["candidate_id"]] = row["body"]
        elif row["kind"] == "REFUSED_INPUT":
            rows[row["body"]["candidate_id"]] = {"decision": "REJECTED", "reason": row["body"]["reason"], "sizing": None}
    verdicts = []
    for cid, want in expected.items():
        got = rows.get(cid)
        ok = (got is not None and got["decision"] == want["decision"]
              and ("reason" not in want or got["reason"] == want["reason"])
              and ("stake_units" not in want or (got["sizing"] or {}).get("stake_units") == want["stake_units"]))
        verdicts.append({"candidate_id": cid, "expected": want, "agree": ok,
                         "got": None if got is None else {"decision": got["decision"], "reason": got["reason"],
                                                          "stake_units": (got["sizing"] or {}).get("stake_units")}})
    result["hand_candidates"] = {
        "count": len(expected), "agree_with_the_hand": sum(1 for v in verdicts if v["agree"]),
        "reference_mismatches": len(audit["mismatches"]), "limit_violations": len(audit["violations"]),
        "ledger_head": summary["ledger_head"], "verdicts": verdicts}
    result["runner"] = runner
    result["fifth_world_sha256"] = hashlib.sha256(Path(fifth_path).read_bytes()).hexdigest()
    result["hand_candidates_sha256"] = hashlib.sha256(Path(hand_path).read_bytes()).hexdigest()
    write_json(out / "score.json", result)
    return result


def run_blind(seed: int, fifth_path: Path, hand_path: Path, runner: str, out: Path) -> dict:
    if seed in RESERVED_SEEDS:
        raise SystemExit("REFUSED: this seed belongs to the dev, holdout, stress or rehearsal runs; "
                         "a blind seed must be new")
    if BLIND_RESULT.is_file():
        raise SystemExit("REFUSED: a blind run is already recorded in eval/blind/result.json; it is run once")
    if not runner.strip() or "tomaso" in runner.lower():
        raise SystemExit("REFUSED: --runner must name the hand that runs it, and it must not be the author")
    def _spec(path: Path) -> dict:
        return {k: v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items() if not k.startswith("_")}

    if (Path(fifth_path).resolve() == TEMPLATE_FIFTH.resolve() or Path(hand_path).resolve() == TEMPLATE_HAND.resolve()
            or _spec(fifth_path) == _spec(TEMPLATE_FIFTH) or _jsonl(Path(hand_path)) == _jsonl(TEMPLATE_HAND)):
        raise SystemExit("REFUSED: the templates (or copies of them) were written by the author; a blind run "
                         "needs files written by the hand that runs it")
    if len(_jsonl(Path(hand_path))) < HAND_CANDIDATES_MIN:
        raise SystemExit(f"REFUSED: the blind protocol asks for at least {HAND_CANDIDATES_MIN} hand-written "
                         "candidates")
    result = _blind_pipeline("blind", seed, fifth_path, hand_path, runner, out)
    BLIND_RESULT.parent.mkdir(parents=True, exist_ok=True)
    write_json(BLIND_RESULT, result)
    return result


def run_rehearsal(out: Path) -> dict:
    """The blind command, end to end, on the author's own templates and a declared seed. NOT a blind run."""
    return _blind_pipeline("rehearsal", REHEARSAL_SEED, TEMPLATE_FIFTH, TEMPLATE_HAND,
                           "rehearsal by the author on his own templates (not a blind run)", out)


# --------------------------------------------------------------------------- output
def _pct(ppm: int) -> str:
    return f"{ppm // 10000}.{ppm % 10000 // 100:02d}%"


def render(result: dict) -> str:
    h = result["headline"]
    lines = [f"suite {result['suite']} - seed {result['seed']} - as_of {result['as_of']} - PAPER, synthetic data",
             f"  limit violations: {h['limit_violations']}",
             f"  decisions agreeing with the reference: {h['decisions_agree']}/{h['decisions_total']} "
             f"(other mismatches: {h['other_mismatches']})",
             f"  largest size deviation from the reference: {h['max_size_deviation_units']} units",
             f"  ledger chains intact: {'yes' if h['ledgers_chain_ok'] else 'NO'} ({h['ledger_rows']} rows)"]
    for w in result["worlds"]:
        c, r = w["calibration"], w["replay"]
        z = "n/a" if c["z_milli"] is None else f"{'-' if c['z_milli'] < 0 else '+'}{abs(c['z_milli']) // 1000}.{abs(c['z_milli']) % 1000:03d}"
        lines.append(f"  {w['world']}: executed {r['executed']}/{r['candidates']}, halts {r['halts'] or '-'}, "
                     f"pauses {r['pauses'] or '-'}, max drawdown {_pct(r['max_drawdown_ppm'])}; settled trades "
                     f"{c['settled_trades']}: wins {c['wins']} vs expected {c['expected_wins_milli'] // 1000}."
                     f"{c['expected_wins_milli'] % 1000:03d} (z {z}); true net edge above threshold in "
                     f"{c['trades_with_true_net_edge_above_threshold']}")
    lines.append(f"  simulated result per world ({CAPTION}): "
                 + ", ".join(f"{w['world']} {w['simulated_result']['delta_units']:+d}" for w in result["worlds"]))
    if "hand_candidates" in result:
        hc = result["hand_candidates"]
        lines.append(f"  hand-written candidates: {hc['agree_with_the_hand']}/{hc['count']} as the hand expected; "
                     f"reference mismatches {hc['reference_mismatches']}; limit violations {hc['limit_violations']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score a paper replay of a synthetic corpus against independent references.")
    ap.add_argument("--suite", choices=sorted(SUITES))
    ap.add_argument("--detector-study", action="store_true")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--rehearsal", action="store_true", help="the blind command on the author's templates; not blind")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--fifth-world")
    ap.add_argument("--hand-candidates")
    ap.add_argument("--runner", default="")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.detector_study:
        study = detector_study()
        print(json.dumps(study, sort_keys=True, indent=2))
        if args.out:
            write_json(_fresh(Path(args.out)) / "detector_study.json", study)
        return 0
    if args.rehearsal:
        if not args.out:
            ap.error("--rehearsal needs --out")
        result = run_rehearsal(Path(args.out))
    elif args.blind:
        if args.seed is None or not args.fifth_world or not args.hand_candidates or not args.out:
            ap.error("--blind needs --seed, --fifth-world, --hand-candidates, --runner and --out")
        result = run_blind(args.seed, Path(args.fifth_world), Path(args.hand_candidates), args.runner, Path(args.out))
    elif args.suite:
        if not args.out:
            ap.error("--suite needs --out")
        result = run_suite(args.suite, SUITES[args.suite]["seed"], SUITES[args.suite]["stress"], Path(args.out))
    else:
        ap.error("choose --suite, --detector-study, --rehearsal or --blind")
    print(render(result))
    h, hc = result["headline"], result.get("hand_candidates")
    clean = (h["limit_violations"] == 0 and h["ledgers_chain_ok"] and h["decisions_agree"] == h["decisions_total"]
             and h["other_mismatches"] == 0)
    if hc is not None:
        clean = (clean and hc["limit_violations"] == 0 and hc["reference_mismatches"] == 0
                 and hc["agree_with_the_hand"] == hc["count"])
    print("SCORE OK" if clean else "SCORE FAILED - a limit was violated, a chain is broken, the harness and the "
                                   "reference disagree, or a hand-written candidate was decided differently "
                                   "from what the hand expected")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())

"""S08 - the replay is paused when it diverges from the backtested baseline by more than N sigma."""
from __future__ import annotations

import importlib
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _common as C  # noqa: E402

from harness import divergence, rules as rules_mod  # noqa: E402

score = importlib.import_module("eval.score")


def _frac(text: str) -> Fraction:
    num, den = text.split("/")
    return Fraction(int(num), int(den))


def check():
    exp, problems = C.expected(HERE), []
    params = C.load_json(HERE / "input" / "params.json")
    cfg = rules_mod.load(C.RULES).risk["divergence"]
    if cfg != exp["rule"]:
        problems.append(f"divergence rule {cfg}, expected {exp['rule']}")

    # 1. the boundary, computed by hand in expected.json and checked here with exact fractions
    hc, want = params["hand_case"], exp["hand_case"]
    xs = [hc["baseline_magnitude_ppm"]] * hc["baseline_plus_count"] + [-hc["baseline_magnitude_ppm"]] * hc["baseline_minus_count"]
    base = divergence.baseline_from(xs)
    if (base["n"], base["mean_ppm"], base["variance_ppm2"]) != (want["baseline_n"], want["baseline_mean_ppm"],
                                                              want["baseline_variance_ppm2"]):
        problems.append(f"hand baseline {base}")
    for label, value in (("inside", hc["window_inside_value_ppm"]), ("outside", hc["window_outside_value_ppm"])):
        det, verdict = divergence.Detector(base, cfg), None
        for _ in range(cfg["window_trades"]):
            verdict = det.observe(value)
        w = want[label]
        if verdict is None or (verdict["deviation_sq"], verdict["allowed_sq"], verdict["diverged"]) != (
                w["deviation_sq"], want["allowed_sq"], w["diverged"]):
            problems.append(f"hand case {label}: {verdict}")
        if (_frac(w["deviation_sq"]) > _frac(want["allowed_sq"])) != w["diverged"]:
            problems.append(f"hand case {label}: expected.json is inconsistent with itself")
    unarmed = divergence.Detector(divergence.baseline_from(xs[:cfg["min_baseline_trades"] - 1]), cfg)
    if unarmed.armed or unarmed.reason_unarmed != want["unarmed_reason_below_min_baseline"]:
        problems.append(f"a baseline below the minimum armed the detector ({unarmed.reason_unarmed})")

    # 2. the development corpus: the regime world is paused after the change, the stationary world is not
    details = {}
    with C.workdir("S08") as work:
        result = score.run_suite("dev", params["seed"], False, work / "suite")
        worlds = {w["world"]: w for w in result["worlds"]}
        h = result["headline"]
        if h["limit_violations"] or h["decisions_agree"] != h["decisions_total"] or h["other_mismatches"]:
            problems.append(f"the reference audit disagrees with the harness on the corpus: {h}")
        regime, stationary = worlds[params["regime_world"]], worlds[params["stationary_world"]]
        r, d = regime["regime"], exp["declared"]
        details["regime"] = r
        if r["pause_t"] is None:
            problems.append("the regime-change world was never paused")
        else:
            if r["pause_t"] < r["change_t"]:
                problems.append(f"paused at t={r['pause_t']}, before the change at t={r['change_t']}")
            if r["post_change_trades_settled_until_pause"] > d["pause_within_settled_trades"]:
                problems.append(f"paused after {r['post_change_trades_settled_until_pause']} post-change trades")
            if r["halt_t"] is not None and r["halt_t"] <= r["pause_t"]:
                problems.append("the breaker halted before the divergence pause")
            rejected_after = regime["replay"]["rejected"].get("SIGNAL_PAUSED", 0)
            if rejected_after < 1:
                problems.append("no candidate was refused with SIGNAL_PAUSED after the pause")
        if not (regime["detector"]["armed"] and regime["detector"]["baseline_matches_backtest"]):
            problems.append("the regime world's baseline does not come from its backtest")
        if stationary["replay"]["pauses"]:
            problems.append(f"the stationary world was paused at t={stationary['replay']['pauses']}")
        pin = exp["pinned_measurement_dev"]
        measured = {"change_t": r["change_t"], "pause_t": r["pause_t"], "delay_steps": r["delay_steps"],
                    "post_change_trades_settled_until_pause": r["post_change_trades_settled_until_pause"]}
        if measured != pin["regime_change"]:
            problems.append(f"measured {measured}, pinned {pin['regime_change']}")

    # 3. the operating point of the rule on seeded synthetic sequences
    study = score.detector_study(seed=params["seed"], runs=params["study_runs"])
    details["study"] = {k: study[k] for k in ("false_pause_runs_planted_like", "false_pause_runs_favourites",
                                               "regime_runs_paused_within_declared_K", "regime_runs_never_paused",
                                               "regime_runs_paused_before_change", "runs_per_kind")}
    runs = params["study_runs"]
    max_false = runs * d["false_pause_per_run_max_ppm"] // 1_000_000
    min_within = -(-runs * d["pause_within_K_min_ppm"] // 1_000_000)
    for key in ("false_pause_runs_planted_like", "false_pause_runs_favourites"):
        if study[key] > max_false:
            problems.append(f"{key}: {study[key]} of {runs} runs, declared at most {max_false}")
    if study["regime_runs_paused_within_declared_K"] < min_within:
        problems.append(f"paused within K in {study['regime_runs_paused_within_declared_K']} of {runs} runs, "
                        f"declared at least {min_within}")
    if d != score.DECLARED:
        problems.append("expected.json and eval/score.py declare different operating points")
    s = details.get("study", {})
    return C.finish("S08", problems, f"boundary exact (69631 inside, 69632 outside at 4 sigma); regime world paused "
                    f"{details.get('regime', {}).get('post_change_trades_settled_until_pause')} settled trades after "
                    f"the change, stationary world not paused; study of {runs} runs per kind: false pauses "
                    f"{s.get('false_pause_runs_planted_like')} and {s.get('false_pause_runs_favourites')}, paused "
                    f"within K in {s.get('regime_runs_paused_within_declared_K')}", details)


if __name__ == "__main__":
    C.main(check)

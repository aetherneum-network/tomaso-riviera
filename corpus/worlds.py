"""The four synthetic worlds, their parameters and their (fictitious) names.

Every place and event below is invented. No world refers to a real venue, instrument or date.
All quantities are integers: probabilities, prices, edges and costs in parts per million (ppm).

Each market proposes one side (YES or NO) for all its candidates; ``p_side`` is the true probability
of that side. A regime is {"from_t", "true_edge_ppm": [choices], "signal_bias_ppm"} and applies to the
markets that open from ``from_t`` on (a regime may carry its own "p_side_ppm" range):
    price  = p_side - true edge            (so the true edge of every candidate is known)
    signal = p_side + bias + noise
"""
from __future__ import annotations

PPM = 1_000_000
GENERATOR_VERSION = "2.0.0"
AS_OF = "2026-10-01T09:00:00+02:00"   # written into every meta.json; no wall clock is ever read
STEPS = 6000
MARKETS = 400
BASELINE_UNTIL_T = 1500
CANDIDATES_PER_MARKET = (10, 15)
MARKET_LIFE_STEPS = (20, 60)
CONVICTIONS = ("low", "medium", "high", "extreme")

# Rates of planted defects, identical in every world (ppm of candidates).
INCONSISTENT_BOOK_PPM = 30_000
STALE_QUOTE_PPM = 20_000
MISSING_CONFIDENCE_PPM = 10_000
MISSING_COST_PPM = 10_000

WORLD_ORDER = ("null", "planted_edge", "regime_change", "high_cost")

WORLDS = {
    "null": {
        "code": "W1", "place": "Portoluna", "event": "Regatta heat",
        "description": "Signals carry no information: the price equals the true probability.",
        "p_side_ppm": [250_000, 750_000], "confidence_ppm": [300_000, 900_000],
        "regimes": [{"from_t": 0, "true_edge_ppm": [0], "signal_bias_ppm": 0}],
        "noise_ppm": 120_000, "confirm_noise_ppm": 120_000,
        "fee_ppm": 5_000, "slippage_ppm": [2_000, 8_000], "latency_steps": [0, 2],
    },
    "planted_edge": {
        "code": "W2", "place": "Serrabruna", "event": "Fair lot",
        "description": "A planted edge of known size on every candidate.",
        "p_side_ppm": [300_000, 750_000], "confidence_ppm": [300_000, 900_000],
        "regimes": [{"from_t": 0, "true_edge_ppm": [60_000, 80_000, 100_000], "signal_bias_ppm": 0}],
        "noise_ppm": 40_000, "confirm_noise_ppm": 40_000,
        "fee_ppm": 5_000, "slippage_ppm": [2_000, 8_000], "latency_steps": [0, 2],
    },
    "regime_change": {
        "code": "W3", "place": "Borgo Lunasco", "event": "Lantern race",
        "description": "Favourites priced 30 points too low until a known instant; afterwards the price is "
                       "right and the signal keeps claiming the old edge. The edge is deliberately large, "
                       "so that its disappearance is detectable in a world of 400 markets.",
        "p_side_ppm": [800_000, 950_000], "confidence_ppm": [150_000, 250_000],
        "regimes": [{"from_t": 0, "true_edge_ppm": [300_000], "signal_bias_ppm": 0},
                    {"from_t": 2400, "true_edge_ppm": [0], "signal_bias_ppm": 300_000,
                     "p_side_ppm": [500_000, 650_000]}],
        "noise_ppm": 40_000, "confirm_noise_ppm": 40_000,
        "fee_ppm": 5_000, "slippage_ppm": [2_000, 8_000], "latency_steps": [0, 2],
    },
    "high_cost": {
        "code": "W4", "place": "Torre Ventisella", "event": "Kite contest",
        "description": "A small planted edge that does not survive the costs.",
        "p_side_ppm": [250_000, 750_000], "confidence_ppm": [300_000, 900_000],
        "regimes": [{"from_t": 0, "true_edge_ppm": [30_000], "signal_bias_ppm": 0}],
        "noise_ppm": 60_000, "confirm_noise_ppm": 60_000,
        "fee_ppm": 25_000, "slippage_ppm": [15_000, 30_000], "latency_steps": [0, 2],
    },
}

# Stress suite: values outside the ranges the rules were developed on. Declared as diagnosis, not blind.
STRESS = {
    "noise_num_den": (3, 2),          # noise x 3/2
    "slippage_num_den": (5, 2),       # slippage x 5/2
    "latency_steps": [0, 12],         # beyond the cost model's max_latency_steps
    "cluster_size": 6,                # markets sharing one outcome driver, one side, one calendar
    "gaps": [(1800, 1950), (3300, 3450), (4800, 4950)],   # no candidate inside; stale quotes just after
    "stale_after_gap_steps": 10,
}

FIFTH_WORLD_KEYS = {
    "p_side_ppm", "confidence_ppm", "regimes", "noise_ppm", "confirm_noise_ppm", "fee_ppm", "slippage_ppm",
    "latency_steps",
}


def validate_spec(spec: dict, steps: int = STEPS) -> None:
    """Refuse a world specification that the generator cannot honour exactly."""
    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    def pair(name, lo, hi):
        v = spec.get(name)
        if not (isinstance(v, list) and len(v) == 2 and all(is_int(x) for x in v) and lo <= v[0] <= v[1] <= hi):
            raise ValueError(f"{name} must be [low, high] with {lo} <= low <= high <= {hi}")

    missing = FIFTH_WORLD_KEYS - set(spec)
    if missing:
        raise ValueError(f"world specification without {sorted(missing)}")
    pair("p_side_ppm", 100_000, 950_000)
    pair("confidence_ppm", 0, 1_000_000)
    pair("slippage_ppm", 0, 200_000)
    pair("latency_steps", 0, 50)
    for name, hi in (("noise_ppm", 300_000), ("confirm_noise_ppm", 300_000), ("fee_ppm", 200_000)):
        if not is_int(spec[name]) or not 0 <= spec[name] <= hi:
            raise ValueError(f"{name} must be an integer in [0, {hi}]")
    regimes = spec["regimes"]
    if not isinstance(regimes, list) or not regimes or regimes[0].get("from_t") != 0:
        raise ValueError("regimes must be a non-empty list whose first entry starts at from_t 0")
    last = -1
    for r in regimes:
        if not is_int(r.get("from_t")) or not last < r["from_t"] < steps:
            raise ValueError("regimes must have strictly increasing from_t inside the world's steps")
        last = r["from_t"]
        edges = r.get("true_edge_ppm")
        if not (isinstance(edges, list) and edges and all(is_int(e) and -300_000 <= e <= 300_000 for e in edges)):
            raise ValueError("true_edge_ppm must be a non-empty list of integers in [-300000, 300000]")
        if not is_int(r.get("signal_bias_ppm")) or not -300_000 <= r["signal_bias_ppm"] <= 300_000:
            raise ValueError("signal_bias_ppm must be an integer in [-300000, 300000]")
        if "p_side_ppm" in r:
            spec_r = r["p_side_ppm"]
            if not (isinstance(spec_r, list) and len(spec_r) == 2 and all(is_int(x) for x in spec_r)
                    and 100_000 <= spec_r[0] <= spec_r[1] <= 950_000):
                raise ValueError("a regime's p_side_ppm must be [low, high] inside [100000, 950000]")

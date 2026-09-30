"""Append-only paper ledger: a hash chain that also refuses rows that violate a limit.

Row: {"seq", "t", "kind", "mode": "PAPER", "body", "prev", "hash"}; hash = SHA-256 of the canonical JSON
of the row without "hash"; "prev" is the hash of the previous row. One row per line, LF.

The ledger is the last line of defence, independent of the decision engine:
  * a candidate is written BEFORE its decision and before the outcome of its market; a candidate for a
    market whose outcome is already in the ledger is refused, and so is a row dated before the last row;
  * an EXECUTED decision is recomputed from the candidate row already in the ledger and is refused if
    the stake exceeds min(kelly_multiplier x Kelly, cap) x equity, if the breaker is halted, if the
    signal is paused, or if edge - cost is not strictly above the threshold;
  * the account (cash, open stakes, peak, drawdown) is derived from the rows themselves;
  * nothing is ever rewritten or deleted: the file is opened in append mode only.

What a hash chain cannot do alone: detect a full rewrite of the file by someone who recomputes every
hash. For that the head hash (and the periodic anchors) must have been published elsewhere; ``verify``
takes them as ``expect_head`` and ``anchors``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from harness import breaker
from harness.exact import PPM, canonical, fparse, is_int, sha256_text, write_json

GENESIS_PREV = "0" * 64
KINDS = ("GENESIS", "CANDIDATE", "DECISION", "SETTLEMENT", "HALT", "REARM_REFUSED", "REARM", "BASELINE",
         "DIVERGENCE_CHECK", "PAUSE", "REFUSED_INPUT")
ANCHOR_EVERY = 500
_LIMIT_KEYS = ("threshold_ppm", "kelly_multiplier", "cap_ppm", "max_open_positions_per_market",
               "max_portfolio_exposure_ppm", "drawdown_warn_ppm",
               "exposure_when_warned_ppm", "drawdown_halt_ppm", "initial_bankroll_units")


class LedgerError(Exception):
    """The ledger refused an operation. Nothing was written."""


class LimitViolation(LedgerError):
    """The never-event: a row that would violate a limit. Refused before it is written."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class ChainError(LedgerError):
    """The file on disk does not verify."""


@dataclass
class Position:
    candidate_id: str
    side: str
    stake_units: int
    payout_if_win_units: int
    q_eff_ppm: int


@dataclass
class AccountState:
    limits: dict
    costs: dict
    min_approvals: int
    cash_units: int
    peak_units: int
    open_stake_units: int = 0
    positions: dict = field(default_factory=dict)   # market_id -> [Position]
    undecided: dict = field(default_factory=dict)   # candidate_id -> candidate as logged
    decided: set = field(default_factory=set)
    settled: set = field(default_factory=set)
    halted: bool = False
    halt_row: dict | None = None
    paused: bool = False

    @property
    def equity_units(self) -> int:
        return self.cash_units + self.open_stake_units

    @property
    def drawdown_ppm(self) -> int:
        return breaker.drawdown_ppm(self.equity_units, self.peak_units)

    def open_in_market(self, market_id: str) -> int:
        return sum(p.stake_units for p in self.positions.get(market_id, []))


def row_hash(row: dict) -> str:
    return sha256_text(canonical({k: v for k, v in row.items() if k != "hash"}))


def _expected_decomposition(candidate: dict, limits: dict, costs: dict) -> dict | None:
    """Recompute the decomposition from the candidate row alone (independent of harness.signal)."""
    try:
        price, prior, signal, conf = (candidate["price_ppm"], candidate["prior_ppm"], candidate["signal_ppm"],
                                      candidate["confidence_ppm"])
        cost = candidate["cost"]
        fee, slip, lat = cost["fee_ppm"], cost["slippage_ppm"], cost["latency_steps"]
    except (KeyError, TypeError):
        return None
    if not all(is_int(v) for v in (price, prior, signal, conf, fee, slip, lat)):
        return None
    if min(fee, slip, lat) < 0 or fee > costs["max_component_ppm"] or slip > costs["max_component_ppm"]:
        return None
    if lat > costs["max_latency_steps"] or not (0 < price < PPM and 0 <= prior <= PPM and 0 <= signal <= PPM):
        return None
    if not 0 <= conf <= PPM or candidate.get("side") not in ("YES", "NO"):
        return None
    cost_ppm = fee + slip + lat * costs["latency_ppm_per_step"]
    if price + cost_ppm >= PPM:
        return None
    p_hat = prior + (conf * (signal - prior)) // PPM
    return {"p_hat_ppm": p_hat, "price_ppm": price, "cost_ppm": cost_ppm, "q_eff_ppm": price + cost_ppm,
            "net_edge_ppm": p_hat - price - cost_ppm}


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.state: AccountState | None = None
        self.rows = 0
        self.head = GENESIS_PREV
        self.last_t = 0
        self._anchors: list[dict] = []
        self._fh = None

    # ------------------------------------------------------------------ open / close
    @classmethod
    def create(cls, path: Path, genesis: dict) -> "Ledger":
        path = Path(path)
        if path.exists() and path.stat().st_size > 0:
            raise LedgerError(f"refusing to overwrite an existing ledger: {path.name}")
        for key in ("limits", "costs", "min_approvals"):
            if key not in genesis:
                raise LedgerError(f"genesis without {key!r}")
        if any(k not in genesis["limits"] for k in _LIMIT_KEYS):
            raise LedgerError("genesis limits incomplete")
        path.parent.mkdir(parents=True, exist_ok=True)
        led = cls(path)
        led._fh = open(path, "ab")
        led._commit("GENESIS", 0, genesis)
        return led

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        """Re-open an existing ledger: verify the chain, replay the rows to rebuild the account."""
        result = verify(path)
        if not result["ok"]:
            raise ChainError(f"{result['reason']} at seq {result['first_bad_seq']}")
        led = cls(Path(path))
        for line in Path(path).read_bytes().split(b"\n"):
            if line:
                led._apply(json.loads(line.decode("utf-8")))
        led._fh = open(path, "ab")
        return led

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ chain
    def _commit(self, kind: str, t: int, body: dict) -> dict:
        if kind not in KINDS:
            raise LedgerError(f"unknown row kind {kind!r}")
        if not is_int(t) or t < self.last_t:
            raise LedgerError(f"BACKDATED_ROW: t={t!r} is before the last row (t={self.last_t})")
        if self._fh is None:
            raise LedgerError("ledger is closed")
        row = {"seq": self.rows, "t": t, "kind": kind, "mode": "PAPER", "body": body, "prev": self.head}
        row["hash"] = row_hash(row)
        line = (canonical(row) + "\n").encode("ascii")
        self._fh.write(line)  # a failed write raises: the run fails, it is never reported as OK
        self._fh.flush()
        self._apply(row)
        return row

    def _apply(self, row: dict) -> None:
        kind, body = row["kind"], row["body"]
        if kind == "GENESIS":
            limits = body["limits"]
            self.state = AccountState(limits=limits, costs=body["costs"], min_approvals=body["min_approvals"],
                                      cash_units=limits["initial_bankroll_units"],
                                      peak_units=limits["initial_bankroll_units"])
        st = self.state
        if kind == "CANDIDATE":
            st.undecided[body["candidate_id"]] = body
        elif kind == "DECISION":
            st.undecided.pop(body["candidate_id"], None)
            st.decided.add(body["candidate_id"])
            if body["decision"] == "EXECUTED":
                s = body["sizing"]
                st.positions.setdefault(body["market_id"], []).append(Position(
                    body["candidate_id"], body["side"], s["stake_units"], s["payout_if_win_units"],
                    body["decomposition"]["q_eff_ppm"]))
                st.cash_units -= s["stake_units"]
                st.open_stake_units += s["stake_units"]
        elif kind == "SETTLEMENT":
            st.positions.pop(body["market_id"], None)
            st.settled.add(body["market_id"])
            st.cash_units = body["cash_units"]
            st.open_stake_units = body["open_stake_units"]
            st.peak_units = body["peak_units"]
        elif kind == "HALT":
            st.halted, st.halt_row = True, row
        elif kind == "REARM":
            st.halted, st.halt_row = False, None
            st.peak_units = body["peak_reset_to_units"]
        elif kind == "PAUSE":
            st.paused = True
        self.rows = row["seq"] + 1
        self.head = row["hash"]
        self.last_t = row["t"]
        if row["seq"] % ANCHOR_EVERY == 0:
            self._anchors.append({"seq": row["seq"], "hash": row["hash"]})

    def anchors(self) -> dict:
        """Head hash and periodic checkpoints: publish them, and a later rewrite is detectable."""
        return {"rows": self.rows, "head": self.head, "anchors": list(self._anchors)}

    def write_anchors(self, path: Path) -> None:
        write_json(path, self.anchors())

    # ------------------------------------------------------------------ rows
    def candidate(self, t: int, candidate: dict) -> dict:
        cid, market_id = candidate.get("candidate_id"), candidate.get("market_id")
        if not isinstance(cid, str) or not isinstance(market_id, str):
            raise LedgerError("candidate without candidate_id or market_id")
        st = self.state
        if cid in st.undecided or cid in st.decided:
            raise LedgerError(f"DUPLICATE_CANDIDATE: {cid}")
        if market_id in st.settled:
            raise LimitViolation("CANDIDATE_AFTER_OUTCOME", f"{cid}: the outcome of {market_id} is already known")
        if candidate.get("t") != t:
            raise LedgerError(f"candidate {cid}: row time {t} differs from the candidate's own t")
        return self._commit("CANDIDATE", t, candidate)

    def decision(self, t: int, body: dict) -> dict:
        cid = body.get("candidate_id")
        st = self.state
        if cid in st.decided:
            raise LimitViolation("ALREADY_DECIDED", str(cid))
        if cid not in st.undecided:
            raise LimitViolation("DECISION_WITHOUT_CANDIDATE", f"{cid}: no candidate row precedes this decision")
        candidate = st.undecided[cid]
        if body.get("market_id") != candidate["market_id"] or body.get("side") != candidate.get("side"):
            raise LimitViolation("DECISION_DOES_NOT_MATCH_CANDIDATE", str(cid))
        if body.get("decision") == "EXECUTED":
            self._guard_execution(body, candidate)
        elif body.get("decision") != "REJECTED":
            raise LedgerError(f"unknown decision {body.get('decision')!r}")
        return self._commit("DECISION", t, body)

    def _guard_execution(self, body: dict, candidate: dict) -> None:
        st, lim = self.state, self.state.limits
        cid, market_id = body["candidate_id"], body["market_id"]
        if st.halted:
            raise LimitViolation("EXECUTION_WHILE_HALTED", cid)
        if st.paused:
            raise LimitViolation("EXECUTION_WHILE_PAUSED", cid)
        if market_id in st.settled:
            raise LimitViolation("EXECUTION_AFTER_OUTCOME", cid)
        expected = _expected_decomposition(candidate, lim, st.costs)
        if expected is None:
            raise LimitViolation("EXECUTION_OF_INCOMPLETE_CANDIDATE", cid)
        dec = body.get("decomposition") or {}
        for key, value in expected.items():
            if dec.get(key) != value:
                raise LimitViolation("DECOMPOSITION_MISMATCH", f"{cid}: {key} is {dec.get(key)!r}, candidate gives {value}")
        if not expected["net_edge_ppm"] > lim["threshold_ppm"]:
            raise LimitViolation("EDGE_NOT_ABOVE_THRESHOLD",
                                 f"{cid}: net edge {expected['net_edge_ppm']} vs threshold {lim['threshold_ppm']}")
        votes = body.get("votes") or {}
        if any(str(v).startswith("VETO") for v in votes.values()):
            raise LimitViolation("EXECUTION_WITH_VETO", cid)
        if sum(1 for v in votes.values() if str(v).startswith("APPROVE")) < st.min_approvals:
            raise LimitViolation("EXECUTION_WITHOUT_QUORUM", cid)
        sizing = body.get("sizing") or {}
        stake, equity = sizing.get("stake_units"), st.equity_units
        if not is_int(stake) or stake < 1:
            raise LimitViolation("STAKE_NOT_POSITIVE", cid)
        if sizing.get("equity_units") != equity:
            raise LimitViolation("STALE_EQUITY", f"{cid}: sized on {sizing.get('equity_units')}, ledger says {equity}")
        q_eff = expected["q_eff_ppm"]
        kelly = Fraction(expected["p_hat_ppm"] - q_eff, PPM - q_eff)
        used = min(kelly * fparse(lim["kelly_multiplier"]), Fraction(lim["cap_ppm"], PPM))
        limit = (used * equity).numerator // (used * equity).denominator if used > 0 else 0
        if stake > limit:
            raise LimitViolation("SIZE_OVER_LIMIT", f"{cid}: stake {stake} > limit {limit}")
        try:
            mult = fparse(sizing.get("throttle_multiplier"))
        except (ValueError, TypeError, ZeroDivisionError):
            raise LimitViolation("THROTTLE_MULTIPLIER_INVALID", cid) from None
        if not 0 < mult <= 1:
            raise LimitViolation("THROTTLE_MULTIPLIER_INVALID", cid)
        if len(st.positions.get(market_id, [])) >= lim["max_open_positions_per_market"]:
            raise LimitViolation("POSITION_ALREADY_OPEN", cid)
        if st.open_in_market(market_id) + stake > (lim["cap_ppm"] * equity) // PPM:
            raise LimitViolation("MARKET_CAP_EXCEEDED", cid)
        exposure = (lim["exposure_when_warned_ppm"] if st.drawdown_ppm >= lim["drawdown_warn_ppm"]
                    else lim["max_portfolio_exposure_ppm"])
        if st.open_stake_units + stake > (exposure * equity) // PPM:
            raise LimitViolation("PORTFOLIO_EXPOSURE_EXCEEDED", cid)
        if stake > st.cash_units:
            raise LimitViolation("INSUFFICIENT_CASH", cid)
        if sizing.get("payout_if_win_units") != (stake * PPM) // q_eff:
            raise LimitViolation("PAYOUT_MISMATCH", cid)

    def settle(self, t: int, market_id: str, outcome: str) -> tuple[dict, dict | None]:
        """Record the outcome of a market and settle its paper positions. Returns (settlement, halt or None)."""
        st = self.state
        if outcome not in ("YES", "NO"):
            raise LedgerError(f"unknown outcome {outcome!r}")
        if market_id in st.settled:
            raise LedgerError(f"market {market_id} is already settled")
        cash, open_stake, settled = st.cash_units, st.open_stake_units, []
        for p in st.positions.get(market_id, []):
            won = p.side == outcome
            payout = p.payout_if_win_units if won else 0
            cash += payout
            open_stake -= p.stake_units
            settled.append({"candidate_id": p.candidate_id, "side": p.side, "stake_units": p.stake_units,
                            "payout_units": payout, "won": won, "q_eff_ppm": p.q_eff_ppm})
        equity = cash + open_stake
        peak = max(st.peak_units, equity)
        row = self._commit("SETTLEMENT", t, {
            "market_id": market_id, "outcome": outcome, "positions": settled, "cash_units": cash,
            "open_stake_units": open_stake, "equity_units": equity, "peak_units": peak,
            "drawdown_ppm": breaker.drawdown_ppm(equity, peak)})
        halt = None
        if not st.halted and breaker.tripped(equity, peak, st.limits["drawdown_halt_ppm"]):
            halt = self._commit("HALT", t, {
                "trigger_seq": row["seq"], "equity_units": equity, "peak_units": peak,
                "drawdown_ppm": breaker.drawdown_ppm(equity, peak),
                "drawdown_halt_ppm": st.limits["drawdown_halt_ppm"],
                "rearm": "requires a human consent record naming this row's hash"})
        return row, halt

    def rearm(self, t: int, consent: dict | None, load_error: str | None = None) -> dict:
        st = self.state
        reason = load_error or breaker.validate_consent(consent, st.halt_row, t)
        consent_sha = None
        if consent is not None and reason is None:
            try:
                consent_sha = sha256_text(canonical(consent))
            except TypeError:
                reason = "CONSENT_UNREADABLE"
        if reason is not None:
            self._commit("REARM_REFUSED", t, {"reason": reason,
                                              "halt_hash": st.halt_row["hash"] if st.halt_row else None})
            raise breaker.RearmRefused(reason)
        return self._commit("REARM", t, {"halt_hash": st.halt_row["hash"], "consent": consent,
                                         "consent_sha256": consent_sha, "peak_reset_to_units": st.equity_units})

    def refused_input(self, t: int, body: dict) -> dict:
        """An input that may not become a candidate (duplicate id, or its outcome is already known)."""
        return self._commit("REFUSED_INPUT", t, body)

    def baseline(self, t: int, body: dict) -> dict:
        return self._commit("BASELINE", t, body)

    def divergence_check(self, t: int, body: dict) -> dict:
        return self._commit("DIVERGENCE_CHECK", t, body)

    def pause(self, t: int, body: dict) -> dict:
        return self._commit("PAUSE", t, body)


def verify(path: Path, expect_head: str | None = None, anchors: list[dict] | None = None) -> dict:
    """Verify a ledger file without modifying it. Reports the first bad row and why.

    Checks, row by row: canonical encoding, sequence, link to the previous hash, own hash, PAPER label,
    time never going backwards, and the two structural orders (candidate before decision, candidate
    before the outcome of its market). Then the published head and anchors, when given.
    """
    findings: list[dict] = []

    def bad(seq: int, reason: str) -> None:
        findings.append({"seq": seq, "reason": reason})

    raw = Path(path).read_bytes()
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    else:
        bad(len(lines) - 1, "LAST_LINE_NOT_TERMINATED")
    prev, last_t, seen_candidates, settled, hashes = GENESIS_PREV, 0, set(), set(), {}
    for index, line in enumerate(lines):
        try:
            row = json.loads(line.decode("utf-8"))
            ok_shape = isinstance(row, dict) and canonical(row).encode("ascii") == line
        except (ValueError, TypeError):
            row, ok_shape = None, False
        if not ok_shape:
            bad(index, "NOT_CANONICAL_JSON")
            if not isinstance(row, dict):
                continue
        if row.get("seq") != index:
            bad(index, "SEQ_OUT_OF_ORDER")
        if row.get("prev") != prev:
            bad(index, "PREV_HASH_MISMATCH")
        if row.get("hash") != row_hash(row):
            bad(index, "ROW_HASH_MISMATCH")
        if row.get("mode") != "PAPER":
            bad(index, "NOT_LABELLED_PAPER")
        if row.get("kind") not in KINDS:
            bad(index, "UNKNOWN_KIND")
        t = row.get("t")
        if not is_int(t) or t < last_t:
            bad(index, "TIME_GOES_BACKWARDS")
        else:
            last_t = t
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        if row.get("kind") == "CANDIDATE":
            seen_candidates.add(body.get("candidate_id"))
            if body.get("market_id") in settled:
                bad(index, "CANDIDATE_AFTER_OUTCOME")
        elif row.get("kind") == "DECISION":
            if body.get("candidate_id") not in seen_candidates:
                bad(index, "DECISION_WITHOUT_CANDIDATE")
            elif body.get("decision") == "EXECUTED" and body.get("market_id") in settled:
                bad(index, "EXECUTION_AFTER_OUTCOME")
        elif row.get("kind") == "SETTLEMENT":
            settled.add(body.get("market_id"))
        prev = row.get("hash")
        hashes[index] = prev
    head = prev if lines else None
    if expect_head is not None and head != expect_head:
        bad(len(lines) - 1, "HEAD_DIFFERS_FROM_PUBLISHED_HEAD")
    for anchor in anchors or []:
        if hashes.get(anchor["seq"]) != anchor["hash"]:
            bad(anchor["seq"], "ANCHOR_MISMATCH")
    findings.sort(key=lambda f: f["seq"])
    first = findings[0] if findings else None
    return {"ok": not findings, "rows": len(lines), "head": head,
            "first_bad_seq": first["seq"] if first else None, "reason": first["reason"] if first else None,
            "findings": findings[:50]}

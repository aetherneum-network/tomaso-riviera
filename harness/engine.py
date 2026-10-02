"""The decision engine: one candidate in, one ledger row for the candidate, one for the decision.

Order, always: (1) the candidate is written to the ledger as received; (2) the facts are extracted
(decomposition, validator votes, breaker and pause state read from the ledger, size, risk veto);
(3) ``rules/gate.json`` decides, first match wins; (4) the decision is written. The ledger re-checks
every EXECUTED row on its own and refuses it if it violates a limit: the engine does not catch that.
"""
from __future__ import annotations

from pathlib import Path

from harness import breaker, divergence, risk, signal, sizing, validators
from harness.exact import PPM
from harness.ledger import Ledger
from harness.rules import Rules, all_matches, first_match
from harness.stream import CANDIDATE, REQUEST, SETTLEMENT
from harness.throttle import Throttle


class Engine:
    def __init__(self, rules: Rules, ledger: Ledger, markets: dict, *, baseline: dict | None = None,
                 consent_dir: Path | None = None, start_t: int = 0):
        self.rules = rules
        self.ledger = ledger
        self.markets = markets
        self.consent_dir = consent_dir
        self.throttle = Throttle(rules.throttle)
        self.detector = divergence.Detector(baseline, rules.risk["divergence"])
        self.realised: list[int] = []   # realised edge (ppm) of every settled paper trade, in order
        if baseline is not None:
            ledger.baseline(start_t, {**baseline, "armed": self.detector.armed,
                                      "reason_unarmed": self.detector.reason_unarmed,
                                      "source": "backtest on the segment before this replay"})

    # ------------------------------------------------------------------ candidates
    def on_candidate(self, cand: dict) -> dict:
        t = cand["t"]
        st = self.ledger.state                             # state is read now, never remembered
        cid, market_id = cand.get("candidate_id"), cand.get("market_id")
        if isinstance(cid, str) and isinstance(market_id, str):
            refusal = ("DUPLICATE_CANDIDATE" if cid in st.undecided or cid in st.decided
                       else "CANDIDATE_AFTER_OUTCOME" if market_id in st.settled else None)
            if refusal is not None:                        # recorded, but never admitted as a candidate
                return self.ledger.refused_input(t, {"candidate_id": cid, "market_id": market_id,
                                                     "reason": refusal})
        self.ledger.candidate(t, cand)                     # logged BEFORE any decision
        dec = signal.decompose(cand, self.rules)
        tally = validators.evaluate(cand, self.markets.get(cand["market_id"]), self.rules.validators)
        size = level = mult = risk_veto = None
        if (dec.missing is None and dec.gate_pass and tally.veto_count == 0 and tally.quorum_met
                and not st.halted and not st.paused):
            level, mult = self.throttle.preview(t, dec.net_edge_ppm)
            size = sizing.size(dec.p_hat_ppm, dec.q_eff_ppm, st.equity_units, mult, self.rules.risk)
            if size.stake_units >= 1:
                risk_veto = risk.veto(size.stake_units, cand["market_id"], st, self.rules.risk)
        facts = {
            "missing": dec.missing,
            "halted": st.halted,
            "paused": st.paused,
            "veto_count": tally.veto_count,
            "quorum_met": tally.quorum_met,
            "net_edge_minus_threshold_ppm": None if dec.missing else dec.net_edge_ppm - dec.threshold_ppm,
            "stake_units": None if size is None else size.stake_units,
            "risk_veto": risk_veto,
        }
        rule_list = self.rules.gate["decision_rules"]
        rule = first_match(rule_list, facts)
        body = {
            "candidate_id": cand["candidate_id"], "market_id": cand["market_id"], "side": cand.get("side"),
            "decision": rule["decision"], "rule_id": rule["id"], "reason": self._reason(rule, facts),
            "also": [self._reason(r, facts) for r in all_matches(rule_list[:-1], facts)
                     if r["decision"] == "REJECTED" and r["id"] != rule["id"]],
            "decomposition": dec.as_body(), "votes": tally.votes, "approvals": tally.approvals,
            "vetoes": tally.vetoes,
            "sizing": None if size is None else size.as_body(equity_units=st.equity_units, level=level,
                                                             multiplier=mult, cfg=self.rules.risk),
        }
        row = self.ledger.decision(t, body)
        if rule["decision"] == "EXECUTED":
            self.throttle.commit(level, t)
        return row

    @staticmethod
    def _reason(rule: dict, facts: dict) -> str:
        reason = rule["reason"]
        return str(facts[reason[1:]]) if reason.startswith("@") else reason

    # ------------------------------------------------------------------ outcomes
    def on_settlement(self, t: int, market_id: str, outcome: str) -> None:
        row, _halt = self.ledger.settle(t, market_id, outcome)   # the ledger halts by itself
        for pos in row["body"]["positions"]:
            x = (PPM if pos["won"] else 0) - pos["q_eff_ppm"]
            self.realised.append(x)
            if not pos["won"]:
                self.throttle.on_loss(t)
            verdict = self.detector.observe(x)
            if verdict is not None:
                self.ledger.divergence_check(t, verdict)
                if verdict["diverged"] and not self.ledger.state.paused:
                    self.ledger.pause(t, {"window_index": verdict["window_index"],
                                          "reason": "DIVERGENCE_BEYOND_N_SIGMA",
                                          "lift": "needs a new baseline; not automatic"})

    # ------------------------------------------------------------------ operator requests
    def on_request(self, request: dict) -> None:
        if request.get("kind") != "REARM_REQUEST":
            raise ValueError(f"unknown request kind {request.get('kind')!r}")
        name = request.get("consent_file")
        path = None if name is None or self.consent_dir is None else Path(self.consent_dir) / name
        try:
            breaker.request_rearm(self.ledger, path, request["t"])
        except breaker.RearmRefused:
            pass  # the refusal is in the ledger (REARM_REFUSED); the breaker stays halted

    def run(self, events: list) -> None:
        for t, kind, _key, payload in events:
            if kind == SETTLEMENT:
                self.on_settlement(t, payload[0], payload[1])
            elif kind == REQUEST:
                self.on_request(payload)
            elif kind == CANDIDATE:
                self.on_candidate(payload)

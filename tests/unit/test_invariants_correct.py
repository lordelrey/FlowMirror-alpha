#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/unit/test_invariants_correct.py -- INV-FIX regression tests for world.check_invariants.

Two invariants falsely failed every shipped config once their results were actually consumed;
both were defects in the checks themselves, not in the engine. These tests pin the corrections:

(a) a_lagged_signals_only -- `used` is a guba ISO WEEK key and must equal the week that ENDED
    strictly before day t (derived from each signal_audit entry's live_end date via the wk_prev
    rule), never the week containing day t; `day_keys` is a LIST and is reported by length; an
    empty audit is `skipped` with a reason instead of failing.
(b) b_nonholder_never_redeems -- the units ledger is seeded from state["hold0"], each agent's
    OPENING {fund_code: units} snapshot that the loop captures before day 0; the live Inv rows
    in state["agents"] carry CLOSING positions and must never seed it (absent hold0 => the
    check is `skipped`, never silently seeded from the wrong thing). A fund leaves the ledger
    only when a redemption takes the position to zero units; the first offender is reported as
    {agent id, fund, units held at that moment, units redeemed}.

FIX4 also pins the polarity of the second return element: it is the run's DECISION -- True when
the run passed, False when a core check explicitly failed. Every fixed invariant gets an honest
fixture that must pass and a planted GENUINE violation that must be caught. Synthetic
states/events only; no data files, no network, ASCII output.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root when run from a checkout

from flowmirror.engine.world import Fund, Inv, check_invariants  # noqa: E402

NAV_DATES = [date(2025, 1, 2) + timedelta(days=i) for i in range(320)]
FUND_000001 = Fund("000001", "R3", False, "FAM", NAV_DATES, [2.0] * len(NAV_DATES), date(2025, 1, 2))
RUN_CFG = {"modality_level": "run"}


def _agent(aid="A1", cell="C2", hold=None, cash=0.0):
    a = Inv()
    a.id, a.cell, a.rc = aid, cell, "C2"
    hold = dict(hold or {})
    cost = {c: 2.0 for c in hold}
    a.cash, a.other, a.realized, a.fees = cash, 0.0, 0.0, 0.0
    a.hold, a.cost, a.entry = hold, cost, 0
    a.w0 = cash + sum(u * cost[c] for c, u in hold.items())
    return a


def _state(agents, audit=(), active=None):
    # hold0 mirrors loop.py's day-0 snapshot: {agent_id: {fund_code: units}} copied from each
    # agent's holdings BEFORE any day runs. In these fixtures the `hold` planted on the agent
    # IS the opening position, so the snapshot is taken from the same numbers.
    return {"agents": list(agents), "funds": {"000001": FUND_000001}, "end": "2025-12-31",
            "active_per_day": dict(active or {0: 1}), "agent_arms": {}, "signal_audit": list(audit),
            "hold0": {a.id: dict(a.hold or {}) for a in agents}}


def _write_events(tmp_path, rows):
    p = tmp_path / "events.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return p


def _honest_audit():
    # 2025-10-01 (Wed) and 2025-10-02 (Thu) sit in ISO week 2025-W40; the most recent week that
    # ENDED strictly before either day is 2025-W39 (ended Sunday 2025-09-28). Note `used` is a
    # week key and deliberately differs from the prev_live_end DATE -- the old comparator.
    return [{"t": 0, "used": "2025-W39", "live_end": "2025-10-01",
             "prev_live_end": "2025-09-30", "day_keys": ["2025-09-22", "2025-09-26"]},
            {"t": 1, "used": "2025-W39", "live_end": "2025-10-02",
             "prev_live_end": "2025-10-01", "day_keys": []}]


def _dec_row(t=0, i="A1"):
    return {"ev": "dec", "t": t, "d": "2025-10-01", "i": i, "p": "00000", "prompt_sha": "x",
            "raw_sha": "y", "cache_hit": False, "attempts": 1, "status": "ok", "arm": "T",
            "mood": 0, "reason": "r", "violations": []}


def _redeem_row(t=0, i="A1", fund="000001", units=20.0):
    return {"ev": "act", "t": t, "d": "2025-10-01", "i": i, "p": "00000", "kind": "redeem",
            "fund": fund, "amt": units * 2.0, "units": units, "nav": 2.0}


# --- invariant (a): lagged guba signals ---------------------------------------------------------
def test_a_honest_week_keys_pass_and_never_compare_with_prev_live_end(tmp_path):
    checks, _ = check_invariants(_state([_agent()], _honest_audit()),
                                 _write_events(tmp_path, [_dec_row()]), RUN_CFG)
    ent = checks["a_lagged_signals_only"]
    assert ent["pass"] is True
    assert (ent["days_audited"], ent["matched"]) == (2, 2)
    assert ent["first_mismatch"] is None
    assert ent["day_keys_total"] == 2          # day_keys is a LIST: recorded by length, not int()'d


def test_a_flags_the_week_containing_day_t(tmp_path):
    # 2025-10-01 lies inside 2025-W40: consuming W40 on that day is a same-week leak
    audit = [dict(_honest_audit()[0], used="2025-W40")]
    checks, decision = check_invariants(_state([_agent()], audit),
                                        _write_events(tmp_path, [_dec_row()]), RUN_CFG)
    ent = checks["a_lagged_signals_only"]
    assert ent["pass"] is False and decision is False   # FIX4: a failure means decision False
    assert ent["matched"] == 0
    assert ent["first_mismatch"]["used"] == "2025-W40"
    assert ent["first_mismatch"]["expected"] == "2025-W39"


@pytest.mark.parametrize("live_end,expected", [
    ("2025-10-06", "2025-W40"),   # Monday: prior week ended Sunday 2025-10-05
    ("2025-10-09", "2025-W40"),   # mid-week: the containing week 2025-W41 must never be used
    ("2025-10-12", "2025-W40"),   # Sunday: the containing week has not ended strictly before today
    ("2025-10-13", "2025-W41"),   # Monday after W41 closed (Sun 2025-10-12): W41 becomes legal
])
def test_a_expected_key_is_always_the_week_that_ended_before_t(tmp_path, live_end, expected):
    audit = [{"t": 0, "used": expected, "live_end": live_end,
              "prev_live_end": "2025-09-30", "day_keys": []}]
    checks, _ = check_invariants(_state([_agent()], audit),
                                 _write_events(tmp_path, [_dec_row()]), RUN_CFG)
    assert checks["a_lagged_signals_only"]["pass"] is True


def test_a_empty_audit_is_skipped_not_failed(tmp_path):
    checks, _ = check_invariants(_state([_agent()], []),
                                 _write_events(tmp_path, [_dec_row()]), RUN_CFG)
    ent = checks["a_lagged_signals_only"]
    assert ent.get("skipped") is True
    assert isinstance(ent.get("reason"), str) and ent["reason"]


# --- invariant (b): a non-holder never redeems ---------------------------------------------------
def test_b_redemption_of_opening_position_is_not_a_violation(tmp_path):
    # the null run's false violations were exactly this: redeeming a starting position that
    # never produced an act row. hold0 supplies the opening balance the ledger starts from.
    ag = _agent(hold={"000001": 50.0})
    checks, _ = check_invariants(_state([ag]),
                                 _write_events(tmp_path, [_dec_row(), _redeem_row(units=50.0)]), RUN_CFG)
    ent = checks["b_nonholder_never_redeems"]
    assert ent["pass"] is True and ent["violations"] == 0 and ent["redeem_rows"] == 1


def test_b_two_partial_redemptions_are_not_flagged(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    rows = [_dec_row(), _redeem_row(units=20.0), _redeem_row(units=20.0)]
    checks, _ = check_invariants(_state([ag]), _write_events(tmp_path, rows), RUN_CFG)
    assert checks["b_nonholder_never_redeems"]["pass"] is True


def test_b_redeem_after_position_reaches_zero_is_caught(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    rows = [_dec_row(), _redeem_row(units=50.0), _redeem_row(units=1.0)]
    checks, decision = check_invariants(_state([ag]), _write_events(tmp_path, rows), RUN_CFG)
    ent = checks["b_nonholder_never_redeems"]
    assert ent["pass"] is False and decision is False   # FIX4: a failure means decision False
    assert ent["violations"] == 1
    assert ent["first_violation"] == {"i": "A1", "fund": "000001",
                                      "units_held": 0.0, "units_redeemed": 1.0}


def test_b_redeem_of_a_never_held_fund_is_caught_with_offender(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    rows = [_dec_row(), _redeem_row(fund="000002", units=10.0)]
    checks, decision = check_invariants(_state([ag]), _write_events(tmp_path, rows), RUN_CFG)
    ent = checks["b_nonholder_never_redeems"]
    assert ent["pass"] is False and decision is False   # FIX4: a failure means decision False
    assert ent["first_violation"] == {"i": "A1", "fund": "000002",
                                      "units_held": 0.0, "units_redeemed": 10.0}


def test_b_subscribe_then_partial_redeem_stays_clean(tmp_path):
    ag = _agent()
    sub = {"ev": "act", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "kind": "subscribe",
           "fund": "000001", "amt": 100.0, "units": 50.0, "nav": 2.0}
    rows = [_dec_row(), sub, _redeem_row(units=20.0)]
    checks, _ = check_invariants(_state([ag]), _write_events(tmp_path, rows), RUN_CFG)
    assert checks["b_nonholder_never_redeems"]["pass"] is True


# --- FIX4: the ledger seeds from OPENING holdings, and skips when they are absent ----------------
def test_b_seeds_from_hold0_not_from_closing_positions(tmp_path):
    # regression for the null run's inv_00710: opening 162764.12, two halving redemptions, the
    # balance stays positive throughout; seeding from the CLOSING 40691.03 flagged a clean agent.
    ag = _agent(hold={"000001": 40691.03})                # what the live Inv carries at the end
    state = _state([ag])
    state["hold0"] = {"A1": {"000001": 162764.12}}        # what the loop snapshotted at day 0
    rows = [_dec_row(), _redeem_row(units=81382.06), _redeem_row(units=40691.03)]
    checks, decision = check_invariants(state, _write_events(tmp_path, rows), RUN_CFG)
    ent = checks["b_nonholder_never_redeems"]
    assert ent["pass"] is True and ent["violations"] == 0
    assert decision is True


def test_b_without_hold0_the_check_skips_instead_of_ever_using_closing_positions(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    state = _state([ag])
    del state["hold0"]                                    # an older caller supplied no snapshot
    checks, decision = check_invariants(
        state, _write_events(tmp_path, [_dec_row(), _redeem_row(units=20.0)]), RUN_CFG)
    ent = checks["b_nonholder_never_redeems"]
    assert ent.get("skipped") is True
    assert "opening" in ent["reason"]
    assert decision is True                               # skipped never counts against the run


def test_b_holder_of_unrecorded_size_is_not_flagged_on_a_partial_redeem(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    state = _state([ag])
    state["hold0"] = {"A1": {"000001": None}}             # holder, size not on record
    checks, _ = check_invariants(
        state, _write_events(tmp_path, [_dec_row(), _redeem_row(units=20.0)]), RUN_CFG)
    assert checks["b_nonholder_never_redeems"]["pass"] is True


# --- FIX4: the second element is the run's decision ----------------------------------------------
def test_decision_true_when_clean_false_when_one_check_fails(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    post = {"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00000", "intent": "I2",
            "ig": "I2", "fund": None, "img": False}
    imp = {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "arm": "T", "slot": 0,
           "source": "random"}
    co = {"ev": "co", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "fund": "000001",
          "ig": "I2", "act": "redeem", "oc": "match", "oc_cf": "match", "amt": 40.0}
    clean = [post, imp, _dec_row(), co, _redeem_row(units=20.0)]
    checks, ok = check_invariants(_state([ag], _honest_audit()),
                                  _write_events(tmp_path, clean), RUN_CFG)
    assert ok is True                                     # clean run: decision True
    planted = [post, imp, _dec_row(), co, _redeem_row(fund="000002", units=10.0)]
    checks2, ok2 = check_invariants(_state([ag], _honest_audit()),
                                    _write_events(tmp_path, planted), RUN_CFG)
    assert ok2 is False                                   # one failing check: decision False
    assert checks2["b_nonholder_never_redeems"]["pass"] is False


# --- honest run: both fixed invariants pass together ---------------------------------------------
def test_honest_run_passes_both_fixed_invariants_and_overall(tmp_path):
    ag = _agent(hold={"000001": 50.0})
    post = {"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00000", "intent": "I2",
            "ig": "I2", "fund": None, "img": False}
    imp = {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "arm": "T", "slot": 0,
           "source": "random"}
    co = {"ev": "co", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "fund": "000001",
          "ig": "I2", "act": "redeem", "oc": "match", "oc_cf": "match", "amt": 40.0}
    rows = [post, imp, _dec_row(), co, _redeem_row(units=20.0)]
    checks, decision = check_invariants(_state([ag], _honest_audit()), _write_events(tmp_path, rows), RUN_CFG)
    assert checks["a_lagged_signals_only"]["pass"] is True
    assert checks["b_nonholder_never_redeems"]["pass"] is True
    assert all(v.get("pass") is not False for v in checks.values())
    assert decision is True                # FIX4: decision True when nothing failed

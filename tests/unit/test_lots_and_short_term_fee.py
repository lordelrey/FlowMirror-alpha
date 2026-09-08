# -*- coding: utf-8 -*-
"""批次（lots）让"持有期"真实存在；FIFO 消耗是赎回费率的行业惯例；
反事实 st_fee_cf 让短期赎回规则的暴露面零成本可量化。"""

import json
import os
from datetime import date

import pytest
import yaml

from flowmirror.engine.loop import _fake_day, _fake_inv, apply_decision
from flowmirror.regulator.cn_redeem import ShortTermRedeemRule

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Shapes copied from the engine self-test in flowmirror/engine/loop.py: `shown` is keyed by
# post id and carries the landing fund code; `parsed.trade` is where apply_decision reads
# amount_pct / sign_mismatch_confirm from (a flat parsed dict silently yields amount 0).
SHOWN = {"P1": {"post_id": "P1", "org": "orgA", "code": "F1", "intent": "I2",
                "intent_group": "I2"}}
BASE = {"parsed": {"trade": {"amount_pct": 10, "sign_mismatch_confirm": "false"}},
        "likes": [], "saves": [], "follows": [], "aff": {},
        "comments": [], "trade": {"p": "P1", "act": "subscribe", "amount_pct": 10}}
FEES = {"subscribe_rate": 0.0012, "redeem_rate": 0.005}
LOTS2 = [[600.0, 1.0, date(2023, 6, 1)], [400.0, 1.2, date(2023, 12, 30)]]
ST_CFG = {"regulation": {"short_term_redemption":
                         {"enabled": True, "days": 7, "min_fee_rate": 0.015}}}


def _run(cfg_over=None, lots=None, hold=1000.0, cost=1.0, pct=50, kind="redeem"):
    """复刻自检里的一次 apply_decision 调用，返回 (inv, events)。"""
    cfg = {"social": False, "suitability": False}
    cfg.update(cfg_over or {})
    kw = {} if hold is None else {"hold": {"F1": hold}, "cost": {"F1": cost}}
    day, inv = _fake_day(cfg), _fake_inv(**kw)
    if lots is not None:
        inv.lots = {"F1": [list(l) for l in lots]}
    rec = dict(BASE)
    rec["parsed"] = {"trade": {"amount_pct": pct, "sign_mismatch_confirm": "false"}}
    rec["trade"] = {"p": "P1", "act": kind, "amount_pct": pct}
    apply_decision(inv, rec, SHOWN, day, fees=FEES)
    return inv, day.events


def _ev(events, name):
    return [e for e in events if e["ev"] == name][0]


# ---------- A. FIFO 与持有天数 ----------


def test_fifo_50pct_consumes_oldest_lot_only():
    inv, evs = _run(lots=LOTS2, pct=50)
    act = _ev(evs, "act")
    assert act["hold_days"] == (date(2024, 1, 2) - date(2023, 6, 1)).days
    assert inv.hold["F1"] == pytest.approx(500.0, abs=1e-9)
    lots = inv.lots["F1"]
    assert len(lots) == 2
    assert lots[0][0] == pytest.approx(100.0, abs=1e-9)
    assert [lots[0][1], lots[0][2]] == [1.0, date(2023, 6, 1)]
    assert lots[1] == [400.0, 1.2, date(2023, 12, 30)]


def test_fifo_80pct_spans_lots_and_flags_short_term():
    inv, evs = _run(lots=LOTS2, pct=80)
    act = _ev(evs, "act")
    assert act["hold_days"] == 3  # 最年轻被消耗批次 2023-12-30
    assert act["st_units"] == pytest.approx(200.0, abs=1e-9)
    assert inv.lots["F1"] == [[200.0, 1.2, date(2023, 12, 30)]]
    assert inv.hold["F1"] == pytest.approx(200.0, abs=1e-9)


def test_full_redeem_wipes_position_and_lots():
    inv, evs = _run(lots=LOTS2, pct=100)
    _ev(evs, "act")
    assert "F1" not in inv.hold
    assert "F1" not in inv.lots


def test_legacy_state_without_lots_does_not_crash():
    inv, evs = _run(pct=50)  # 不设 lots，走引擎 getattr 安全写法
    act = _ev(evs, "act")
    assert act["hold_days"] is None
    assert act["st_units"] == 0


def test_subscribe_appends_lots_batches():
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    for _ in range(2):
        rec = dict(BASE)
        rec["parsed"] = {"trade": {"amount_pct": 20, "sign_mismatch_confirm": "false"}}
        rec["trade"] = {"p": "P1", "act": "subscribe", "amount_pct": 20}
        apply_decision(inv, rec, SHOWN, day, fees=FEES)
    acts = [e for e in day.events if e["ev"] == "act"]
    lots = inv.lots["F1"]
    assert len(acts) == 2 and len(lots) == 2
    assert lots[0][0] == pytest.approx(acts[0]["units"], abs=1e-6)   # act row rounds units to 6 dp
    assert lots[0][1:] == [1.5, date(2024, 1, 2)]
    assert lots[1][0] == pytest.approx(acts[1]["units"], abs=1e-6)   # act row rounds units to 6 dp
    assert lots[1][1:] == [1.5, date(2024, 1, 2)]
    assert lots[0][0] + lots[1][0] == pytest.approx(inv.hold["F1"], abs=1e-9)


# ---------- B. 费用与反事实 ----------


def test_rule_off_no_extra_fee_but_counterfactual_disclosed():
    inv, evs = _run(lots=LOTS2, pct=80)  # 默认 enabled: false
    act, co = _ev(evs, "act"), _ev(evs, "co")
    assert act["st_fee"] == pytest.approx(0.0, abs=1e-9)
    assert act["fee"] == pytest.approx(800 * 1.5 * 0.005, abs=1e-9)  # 6.0
    assert co["st_fee_cf"] == pytest.approx(200 * 1.5 * (0.015 - 0.005), abs=1e-9)


def test_rule_on_charges_extra_and_shifts_cash_and_fees():
    inv_off, evs_off = _run(lots=LOTS2, pct=80)
    inv_on, evs_on = _run(ST_CFG, lots=LOTS2, pct=80)
    act = _ev(evs_on, "act")
    want = 600 * 1.5 * 0.005 + 200 * 1.5 * 0.015  # 4.5 + 4.5
    assert act["fee"] == pytest.approx(want, abs=1e-9)
    assert act["st_fee"] == pytest.approx(3.0, abs=1e-9)
    assert inv_on.cash == pytest.approx(inv_off.cash - 3.0, abs=1e-9)
    assert getattr(inv_on, "fees", 0.0) == pytest.approx(
        getattr(inv_off, "fees", 0.0) + 3.0, abs=1e-9)


def test_wealth_conservation_with_rule_on():
    inv, evs = _run(ST_CFG, lots=LOTS2, pct=80)
    _ev(evs, "act")
    held = inv.hold.get("F1", 0.0)
    unrealized = held * (1.5 - inv.cost.get("F1", 1.5))
    # opening wealth = 100000 cash + 1000 units at cost 1.0; fees leave the system,
    # realized and unrealized gains enter it; the identity must close to the yuan cent.
    assert abs(inv.cash + held * 1.5 + inv.fees - inv.realized - unrealized
               - (100000.0 + 1000.0 * 1.0)) < 1e-6


def test_short_term_rule_fee_rate():
    rule = ShortTermRedeemRule(days=7, min_fee_rate=0.015)
    assert rule.fee_rate(3, 0.005) == 0.015
    assert rule.fee_rate(30, 0.005) == 0.005
    assert rule.fee_rate(None, 0.005) == 0.005
    assert rule.fee_rate(3, 0.02) == 0.02  # 基础费率已高于下限时不降


# ---------- C. 默认值与 schema ----------


def test_defaults_yaml_short_term_redemption_block():
    with open(os.path.join(PROJ, "config", "engine_defaults.yaml"),
              encoding="utf-8") as f:
        defaults = yaml.safe_load(f)
    assert defaults["regulation"]["short_term_redemption"] == {
        "enabled": False, "days": 7, "min_fee_rate": 0.015, "disclose": False}


def test_event_schema_optional_counterfactual_fields():
    with open(os.path.join(PROJ, "config", "schemas", "event.schema.json"),
              encoding="utf-8") as f:
        schema = json.load(f)

    def block(ev):
        return next(b["then"] for b in schema["allOf"]
                    if b.get("if", {}).get("properties", {}).get("ev", {})
                    .get("const") == ev)

    act, co = block("act"), block("co")
    for key in ("pnl", "hold_days", "st_units", "st_fee"):
        assert key in act["properties"]
        assert key not in act.get("required", [])
    assert "st_fee_cf" in co["properties"]
    assert "st_fee_cf" not in co.get("required", [])

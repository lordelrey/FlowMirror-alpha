"""Tests for agent-view fields and nested mock-model options.

The view exposes structured weekly attention, prior beliefs, and lagged
holding returns without look-ahead. Fixtures derive from the offline demo.
"""
from __future__ import annotations

import json
import math
import os

import pytest

import flowmirror.agents.runtime as rt
import flowmirror.engine.loop as loop_mod
from flowmirror.agents.prompt import render_news
from flowmirror.engine.world import World
from tests.conftest import build_demo_cfg, find_demo_run

needs_demo = pytest.mark.skipif(
    find_demo_run() is None,
    reason="runs/demo_two_arm.json not found (set FLOWMIRROR_DATA_ROOT)")

# The demo cohort's first 10 agents are all C3/C4, so force_c2_r4_click cannot
# fire there at all: the mock only forces the buy for a C2 account.  40 agents
# is the smallest slice of the shipped 400-agent population that contains C2
# clients (verified: 9 forced buys over 3 trading days).
FORCE_AGENTS = 40
FORCE_DAYS = 3

# A volume-only attention row with no directional stance fields.
REAL_SIGNAL_ROW = {"n_posts": 2, "reply_n": 0, "read_n": 229,
                   "z_abnormal": 2.121, "ratio_vs_baseline": 4.0,
                   "baseline_weeks_used": 8}
SIGNAL_WEEK = "2025-W41"
SIGNAL_CODE = "000043"


def _fake_world(code=SIGNAL_CODE, week=SIGNAL_WEEK, row=None):
    """A real World object (slots, no __init__) carrying just what the view reads."""
    W = World()
    W.guba = {code: {week: dict(REAL_SIGNAL_ROW if row is None else row)}}
    W.funds = {code: type("F", (), {"r": "R3"})()}
    W.fund_meta = {code: {"name": "示例混合基金"}}
    return W


def _fake_inv(**over):
    """The Inv attributes _agent_view actually reads."""
    from types import SimpleNamespace
    inv = SimpleNamespace(id="A001", arm="T", rc="C3", cash=25000.0, hold={}, cost={},
                          flag={}, memory=[], reflection="", market_view=3, risk_mood=3,
                          beliefs=[])
    for k, v in over.items():
        setattr(inv, k, v)
    return inv


def _view(cfg, W, inv, guba_view, prev_navdays=(), navday=None):
    return loop_mod._agent_view(inv, "我是测试投资者，稳健型。", {}, W, cfg,
                                navday or {}, [], {}, guba_view, {}, {},
                                prev_navdays=prev_navdays)


# --------------------------------------------------------------------------- E5
@needs_demo
def test_mock_options_nested_keys_reach_mockllm(tmp_path):
    """The nested mock_options keys must be the ones _make_llm reads."""
    cfg = build_demo_cfg(tmp_path / "cfg")
    cfg["mock_options"] = {"force_c2_r4_click": True, "malformed_rate": 0.25}
    llm = loop_mod._make_llm(cfg)
    assert isinstance(llm, rt.MockLLM)
    # Against the pre-fix engine both assertions fail: the top-level names it read
    # are absent, so it built MockLLM(False, 0.05) no matter what the config said.
    assert llm.force_c2_r4 is True
    assert llm.malformed_rate == pytest.approx(0.25)

    cfg["mock_options"] = {"force_c2_r4_click": False, "malformed_rate": 0.0}
    off = loop_mod._make_llm(cfg)
    assert off.force_c2_r4 is False and off.malformed_rate == pytest.approx(0.0)


def test_mock_options_absent_falls_back_to_engine_defaults():
    """A mock config without mock_options falls back to config/engine_defaults.yaml.

    The literal used to be 0.05 and the docstring credited it to run.schema.json, which
    declares no default for either key -- engine_defaults.yaml does, and it says 0.0. A
    fallback that disagrees with the file it cites is the same silent-drift class as the
    original defect, so the two now agree.
    """
    llm = loop_mod._make_llm({"mock_llm": True})
    assert isinstance(llm, rt.MockLLM)
    assert llm.force_c2_r4 is False and llm.malformed_rate == pytest.approx(0.0)
    from flowmirror.engine.world import DEFAULT_CONFIG
    assert llm.malformed_rate == pytest.approx(
        (DEFAULT_CONFIG.get("mock_options") or {}).get("malformed_rate")), \
        "the bare fallback must equal what the defaults file supplies after the merge"


def test_dead_top_level_mock_keys_are_ignored():
    """The old top-level names must NOT be honoured: they exist in no schema, so
    reading them again would resurrect the same silent-drift bug (root cause 1)."""
    llm = loop_mod._make_llm({"mock_llm": True, "mock_force_c2_r4": True,
                              "mock_malformed_rate": 0.9})
    assert llm.force_c2_r4 is False and llm.malformed_rate == pytest.approx(0.0)


@needs_demo
def test_forced_c2_r4_click_produces_a_suitability_confirmation(tmp_path):
    """With the switch on, a short mock run must actually reach the C x R
    confirmation branch -- the scenario the acceptance configs claim to test."""
    out = tmp_path / "forced"
    cfg = build_demo_cfg(out, agents=FORCE_AGENTS, days=FORCE_DAYS)
    cfg["mock_options"] = {"force_c2_r4_click": True, "malformed_rate": 0.05}
    assert loop_mod.run_simulation(cfg) == 0
    outcomes = set()
    with open(os.path.join(str(out), "event_log.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("ev") == "co":
                outcomes.add(row.get("oc"))
    assert outcomes & {"confirm_signed", "confirm_declined"}, \
        f"no suitability confirmation reached; checkout outcomes were {sorted(outcomes)}"


# --------------------------------------------------------------------------- E1
def test_guba_row_lookup_mirrors_the_seed_label_week_keys():
    W = _fake_world()
    assert loop_mod._guba_row(W, SIGNAL_CODE, SIGNAL_WEEK) == REAL_SIGNAL_ROW
    assert loop_mod._guba_row(W, SIGNAL_CODE, "2025-W42") is None   # uncovered week
    assert loop_mod._guba_row(W, "999999", SIGNAL_WEEK) is None     # unknown fund


@needs_demo
def test_render_news_survives_a_real_shaped_signal_row(tmp_path):
    """view["guba"] must be dict-of-dicts and render_news must not raise on it."""
    cfg = build_demo_cfg(tmp_path / "cfg")
    W = _fake_world()
    inv = _fake_inv(hold={SIGNAL_CODE: 1000.0}, cost={SIGNAL_CODE: 1.2})
    guba_view = {SIGNAL_CODE: loop_mod._guba_row(W, SIGNAL_CODE, SIGNAL_WEEK)}
    view = _view(cfg, W, inv, guba_view, navday={SIGNAL_CODE: 1.3})

    entry = view["guba"][SIGNAL_CODE]
    assert isinstance(entry, dict)
    assert set(entry) == {"name", "mult", "bull_ratio"}
    assert entry["name"] == "示例混合基金"                 # via _fund_name(W, code)
    assert entry["mult"] == pytest.approx(4.0)             # = ratio_vs_baseline
    assert entry["bull_ratio"] is None                     # no stance measurement

    lines = render_news(view)                              # AttributeError pre-fix
    assert lines and any("示例混合基金" in ln for ln in lines)


@needs_demo
def test_guba_mult_defaults_to_one_when_the_ratio_is_missing(tmp_path):
    cfg = build_demo_cfg(tmp_path / "cfg")
    W = _fake_world(row={"n_posts": 1, "reply_n": 0, "read_n": 3, "z_abnormal": 0.1})
    inv = _fake_inv(hold={SIGNAL_CODE: 10.0})
    guba_view = {SIGNAL_CODE: loop_mod._guba_row(W, SIGNAL_CODE, SIGNAL_WEEK)}
    view = _view(cfg, W, inv, guba_view)
    assert view["guba"][SIGNAL_CODE]["mult"] == pytest.approx(1.0)
    assert render_news(view)


@needs_demo
def test_guba_view_keeps_the_holdings_or_shown_filter(tmp_path):
    """A fund the agent neither holds nor was shown stays out of the view."""
    cfg = build_demo_cfg(tmp_path / "cfg")
    W = _fake_world()
    guba_view = {SIGNAL_CODE: dict(REAL_SIGNAL_ROW)}
    assert _view(cfg, W, _fake_inv(), guba_view)["guba"] == {}


# -------------------------------------------------------------------------- E10
@needs_demo
def test_view_carries_beliefs_as_a_list(tmp_path):
    cfg = build_demo_cfg(tmp_path / "cfg")
    W = _fake_world()
    assert _view(cfg, W, _fake_inv(), {})["beliefs"] == []
    inv = _fake_inv(beliefs=["先看回撤再看收益", "热帖不等于好基金"])
    got = _view(cfg, W, inv, {})["beliefs"]
    assert isinstance(got, list) and got == inv.beliefs
    assert got is not inv.beliefs           # a copy: the frozen view must not alias state
    # None (an agent that has never reflected) degrades to [], never to None
    assert _view(cfg, W, _fake_inv(beliefs=None), {})["beliefs"] == []


# -------------------------------------------------------------- holdings_1d
def test_holdings_1d_needs_holdings_and_two_prior_days():
    prev, prev2 = {"A": 1.10}, {"A": 1.00}
    assert loop_mod._holdings_1d({}, (prev, prev2)) is None          # no holdings
    assert loop_mod._holdings_1d({"A": 5.0}, ()) is None             # day 0
    assert loop_mod._holdings_1d({"A": 5.0}, (prev, None)) is None   # day 1
    assert loop_mod._holdings_1d({"A": 5.0}, (prev, prev2)) == pytest.approx(0.10)
    # units cancel out of a single-fund return; two funds weight by units x nav_{t-2}
    two = loop_mod._holdings_1d({"A": 10.0, "B": 10.0},
                                ({"A": 1.10, "B": 0.90}, {"A": 1.00, "B": 1.00}))
    assert two == pytest.approx(0.0)
    # a fund priced on only one of the two days contributes nothing rather than crashing
    assert loop_mod._holdings_1d({"A": 5.0, "B": 5.0},
                                 (prev, dict(prev2, B=0.0))) == pytest.approx(0.10)


@needs_demo
def test_view_holdings_1d_absent_without_holdings_present_with(tmp_path):
    cfg = build_demo_cfg(tmp_path / "cfg")
    W = _fake_world()
    lag = ({SIGNAL_CODE: 1.32}, {SIGNAL_CODE: 1.20})
    assert "holdings_1d" not in _view(cfg, W, _fake_inv(), {}, prev_navdays=lag)
    held = _fake_inv(hold={SIGNAL_CODE: 800.0}, cost={SIGNAL_CODE: 1.1})
    view = _view(cfg, W, held, {}, prev_navdays=lag, navday={SIGNAL_CODE: 1.4})
    assert math.isfinite(view["holdings_1d"])
    assert view["holdings_1d"] == pytest.approx(0.10, abs=1e-4)
    assert render_news(view)                # the sentence renders from the number


@needs_demo
def test_run_supplies_holdings_1d_only_from_the_third_trading_day(tmp_path, monkeypatch):
    """End-to-end plumbing of the NAV lag: day 0 and day 1 have no t-2 vector, so
    the key must be absent there and present (finite) on day 2 for holders.

    Also pins the anti-look-ahead rule: the number must NOT equal today's move.
    The spy records the day index alongside the view; loop.py calls the module
    global, so monkeypatching the name is enough."""
    seen = []
    original = loop_mod._agent_view
    # positional order: inv, persona_rec, shown, W, cfg, navday, hist, trend_cache, ...
    NAVDAY = 4

    def spy(inv, *a, **kw):
        view = original(inv, *a, **kw)
        seen.append({"id": inv.id, "hold": dict(inv.hold),
                     "navday": dict(a[NAVDAY]) if len(a) > NAVDAY else {},
                     "prev": kw.get("prev_navdays", ()),
                     "holdings_1d": view.get("holdings_1d")})
        return view

    monkeypatch.setattr(loop_mod, "_agent_view", spy)
    out = tmp_path / "lag"
    assert loop_mod.run_simulation(build_demo_cfg(out, agents=10, days=3)) == 0

    # Views whose lag vectors are incomplete (days 0 and 1) must carry no key.
    early = [s for s in seen if len(s["prev"]) < 2 or not s["prev"][1]]
    assert early and all(s["holdings_1d"] is None for s in early)
    # Day 2 holders must carry a finite number computed from t-1 and t-2 only.
    late = [s for s in seen if len(s["prev"]) == 2 and s["prev"][0] and s["prev"][1]
            and s["hold"]]
    assert late, "no holder was ranked on a day with two prior trading days"
    for s in late:
        assert s["holdings_1d"] is not None and math.isfinite(s["holdings_1d"])
        assert s["holdings_1d"] == pytest.approx(
            loop_mod._holdings_1d(s["hold"], s["prev"]))
    # invariant (a): for at least one holder, the number the view carries must differ
    # from the one today's navday would give -- proof the day-t close is not in there.
    lookahead = [s for s in late
                 if loop_mod._holdings_1d(s["hold"], (s["navday"], s["prev"][0]))
                 not in (None, s["holdings_1d"])]
    if not lookahead:
        pytest.skip("this demo window's NAVs make the two candidates numerically equal")


# --------------------------------------------------- live wrapper: llm knobs
def _knob_cfg(tmp_path, **llm_over):
    llm = {"endpoint": "http://stub.invalid/v1/chat/completions",
           "api_key": "stub-key-not-real", "model": "cfg-model",
           "max_tokens_start": 256, "max_attempts": 1, "workers": 1,
           "cache": str(tmp_path / "llm_cache.jsonl")}
    llm.update(llm_over)
    return {"run_tag": "knob_test", "seed": 1, "llm": llm}


def test_live_call_forwards_temperature_and_provider_attempts(tmp_path, monkeypatch):
    """cfg["llm"] knobs must travel with the call once call_glm accepts them."""
    calls = []

    def stub(messages, max_tokens, model=None, temperature=None,
             max_provider_attempts=None, **kw):
        calls.append({"model": model, "temperature": temperature,
                      "max_provider_attempts": max_provider_attempts})
        return {"parsed": {"ok": True}, "raw": "{}", "attempts": 1}

    monkeypatch.setattr(loop_mod, "call_glm", stub)
    llm = loop_mod._make_llm(_knob_cfg(tmp_path, temperature=0.7,
                                       max_provider_attempts=2))
    llm([{"role": "user", "content": "ping"}], 64)
    assert calls == [{"model": "cfg-model", "temperature": 0.7,
                      "max_provider_attempts": 2}]
    # caller-wins precedence, same as model=
    llm([{"role": "user", "content": "ping"}], 64, temperature=0.0)
    assert calls[1]["temperature"] == 0.0


def test_live_call_omits_knobs_call_glm_cannot_accept(tmp_path, monkeypatch):
    """The parallel runtime.py lane may not have landed yet: an old call_glm
    signature must still be callable, not a TypeError at every live call."""
    calls = []

    def old_stub(messages, max_tokens, model=None, parser=None, governor=None,
                 first_open=False):
        calls.append({"model": model})
        return {"parsed": {"ok": True}, "raw": "{}", "attempts": 1}

    monkeypatch.setattr(loop_mod, "call_glm", old_stub)
    llm = loop_mod._make_llm(_knob_cfg(tmp_path, temperature=0.7,
                                       max_provider_attempts=2))
    llm([{"role": "user", "content": "ping"}], 64)
    assert calls == [{"model": "cfg-model"}]


def test_live_call_omits_knobs_the_config_does_not_carry(tmp_path, monkeypatch):
    """No key in cfg["llm"] -> nothing forwarded, so call_glm keeps its own
    defaults (contract 3 pins those at exactly today's 0.3 / 5): the live path
    stays byte-identical on a config that omits the new keys."""
    calls = []

    def stub(messages, max_tokens, model=None, temperature=None,
             max_provider_attempts=None, **kw):
        calls.append({"temperature": temperature,
                      "max_provider_attempts": max_provider_attempts})
        return {"parsed": {"ok": True}, "raw": "{}", "attempts": 1}

    monkeypatch.setattr(loop_mod, "call_glm", stub)
    llm = loop_mod._make_llm(_knob_cfg(tmp_path))
    llm([{"role": "user", "content": "ping"}], 64)
    assert calls == [{"temperature": None, "max_provider_attempts": None}]

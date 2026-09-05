"""Unit tests for the LIVE (non-mock) branch of flowmirror.engine.loop._make_llm
and for the --dump-prompt renderer (card E1_loop, tasks 1 and 3).

Zero network access: ``flowmirror.engine.loop.call_glm`` -- the module-global
name the live wrapper resolves at call time -- and, defensively,
``flowmirror.agents.runtime.call_glm`` are monkeypatched with a stub that
records the kwargs it receives and returns a well-formed provenance dict.

Regression guard (task 1): the old one-line wrapper ::

    return call_glm(messages, int(max_tokens), model=llm_cfg.get("model"), **kw)

collided with the ``model=`` that ``runtime.decide`` / ``runtime.reflect``
always pass, so every live run died instantly with
"TypeError: call_glm() got multiple values for keyword argument 'model'".
``test_live_decide_and_reflect_no_model_kwarg_collision`` fails against that
version (the duplicate-keyword TypeError fires at the call site, before the
stub's own ``model=None`` default can absorb it).
"""
from __future__ import annotations

import base64
import hashlib

import pytest

import flowmirror.agents.runtime as rt
import flowmirror.engine.loop as loop_mod
from flowmirror.agents.null_policy import NullPolicyLLM
from flowmirror.io.hashing import sha256_text


def _stub_call_glm_factory(recorder):
    """Build a no-network stand-in for call_glm that records kwargs + provenance."""

    def _stub_call_glm(messages, max_tokens, model=None, parser=None, governor=None,
                       first_open=False, **kw):
        recorder.append({"model": model, "max_tokens": int(max_tokens),
                         "first_open": bool(first_open), "extra": sorted(kw),
                         "roles": ([m.get("role") for m in messages]
                                   if isinstance(messages, list) else None)})
        txt = '{"stub": "ok"}'
        parsed = None
        if parser is not None:
            try:
                out = parser(txt)
                parsed = out[0] if isinstance(out, (tuple, list)) else out
            except Exception:
                parsed = None
        if not isinstance(parsed, dict):
            parsed = {"stub": True}
        return {"parsed": parsed, "violations": [], "parser_status": "ok",
                "raw": txt, "raw_sha256": sha256_text(txt), "attempts": 1,
                "response_source": "unit-test-stub"}

    return _stub_call_glm


def _live_cfg(cache_path):
    """A live (non-mock, non-null) config shaped the way runtime reads it."""
    return {
        "run_tag": "live_path_test",
        "seed": 7,
        "channels": {"feed": True, "experience": True, "trend": True,
                     "social": True, "news": True, "direct": True},
        "social": True,
        "llm": {"endpoint": "http://stub.invalid/v1/chat/completions",
                "api_key": "stub-key-not-real",
                "model": "fallback-model-x",
                "vision_model": "vision-model-x",
                "text_model": "text-model-x",
                "max_tokens_start": 512,
                "max_attempts": 1,
                "workers": 1,
                "cache": cache_path},
    }


def _view():
    # Superset of the keys _agent_view() freezes; deliberately free of any
    # regulator-style phrasing so prompt.py's anti-priming guard stays quiet.
    return {"persona_card_zh_rich": "你是测试代理人张三，三十五岁，在一家制造企业工作。",
            "memory": ["D0｜看5条｜未交易｜心情neutral｜无"],
            "last_reflection": "",
            "market_view": 3, "risk_mood": 3, "c_class": "C3", "cash": 20000.0,
            "holdings": [], "last_trade": "", "declined_confirms": [],
            "familiarity": {"甲基金": 1}, "guba": {}, "trend": [], "direct": []}


def _cards():
    return [{"post_id": "p1", "org": "甲基金", "title": "测试帖子标题一",
             "caption": "测试正文内容，长度适中。", "landing": None, "likes": 3,
             "arm": "T", "image_path": None, "image_sha": None, "comments_prev": [],
             "climate": "no_signal", "climate_label": "no_signal",
             "n_comments_prev": 0}]


def _expected_models(cfg):
    """Vision/text model exactly the way runtime itself picks them (defensive
    fallback chain in case the private helper's name or precedence shifts)."""
    try:
        vis, txt, _tok, _att = rt._llm_cfg(cfg)
        return vis, txt
    except Exception:
        llm = cfg.get("llm") or {}
        vis = llm.get("vision_model") or llm.get("model")
        return vis, llm.get("text_model") or vis


def test_live_decide_and_reflect_no_model_kwarg_collision(monkeypatch, tmp_path):
    calls = []
    # loop.py imports call_glm by name, so the closure resolves
    # flowmirror.engine.loop.call_glm at call time -- that is the name to patch;
    # runtime.call_glm is patched too, purely defensively.
    monkeypatch.setattr(loop_mod, "call_glm", _stub_call_glm_factory(calls))
    monkeypatch.setattr(rt, "call_glm", _stub_call_glm_factory(calls))
    cfg = _live_cfg(str(tmp_path / "llm_cache.jsonl"))
    llm = loop_mod._make_llm(cfg)
    assert callable(llm) and not isinstance(llm, rt.MockLLM)
    assert not isinstance(llm, NullPolicyLLM)
    view, cards = _view(), _cards()
    scope = {"pids": ["p1"], "codes": [], "held": [], "orgs": ["甲基金"]}
    try:
        rec = rt.decide(view, cards, cfg, rt.LLMCache(cfg["llm"]["cache"]),
                        rt.BudgetGovernor(50), llm, scope)
    except TypeError as exc:
        pytest.fail(f"live _make_llm wrapper still collides on model=: {exc}")
    assert calls and calls[0]["model"] is not None
    assert rec.get("prompt_sha") and int(rec.get("attempts") or 0) >= 1
    rres = rt.reflect(view, cfg, rt.LLMCache(str(tmp_path / "llm_cache_refl.jsonl")),
                      rt.BudgetGovernor(50), llm)
    assert len(calls) == 2                      # one decide call, one reflect call, no retries
    vis_exp, txt_exp = _expected_models(cfg)
    assert calls[0]["model"] == vis_exp                 # decision -> vision model
    assert calls[1]["model"] == (txt_exp or vis_exp)    # reflection -> text model
    assert rres.get("prompt_sha")


def test_live_wrapper_falls_back_to_config_model(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(loop_mod, "call_glm", _stub_call_glm_factory(calls))
    cfg = _live_cfg(str(tmp_path / "c.jsonl"))
    llm = loop_mod._make_llm(cfg)
    prov = llm([{"role": "user", "content": "ping"}], 128)
    assert prov.get("parsed") is not None
    assert calls[0]["model"] == "fallback-model-x"      # cfg model when caller passes none
    assert calls[0]["max_tokens"] == 128


def test_live_wrapper_caller_model_wins(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(loop_mod, "call_glm", _stub_call_glm_factory(calls))
    cfg = _live_cfg(str(tmp_path / "c.jsonl"))
    llm = loop_mod._make_llm(cfg)
    llm([{"role": "user", "content": "ping"}], 64, model="caller-model-y")
    assert calls[0]["model"] == "caller-model-y"        # caller's choice beats config


def test_image_part_placeholder_reports_sha_and_bytes():
    data = bytes(range(60))
    url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    got = loop_mod._image_part_placeholder({"type": "image_url", "image_url": {"url": url}})
    assert got == f"[image: {hashlib.sha256(data).hexdigest()[:12]}, {len(data)} bytes]"


def test_render_prompt_text_never_leaks_base64():
    data = b"z" * 77
    url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    msgs = [{"role": "system", "content": "SYS-TEXT"},
            {"role": "user", "content": [{"type": "text", "text": "USER-TEXT"},
                                         {"type": "image_url",
                                          "image_url": {"url": url}}]}]
    txt = loop_mod._render_prompt_text(msgs)
    assert "SYS-TEXT" in txt and "USER-TEXT" in txt
    assert "base64," not in txt and url not in txt
    assert f"{len(data)} bytes" in txt
    assert txt.endswith("\n")


def test_dump_target_spec_parsing():
    assert loop_mod._dump_target("first") == (None, None)
    assert loop_mod._dump_target("A3@7") == ("A3", 7)
    for bad in ("no-at-sign", "A3@x", "A3@", "@7"):
        with pytest.raises(ValueError):
            loop_mod._dump_target(bad)

"""Transport failures and model-output failures remain distinct.

The tests cover:
  * a stub whose every call returns cls "http_error" -> failure_kind "transport", and the
    reported parser_status is the transport class itself, never a schema/parse verdict;
  * HTTP 200 with unparseable content                -> failure_kind "model";
  * all four transport classes classify as transport, and truncation/parse classes as model;
  * a warm cache replay derives the same failure_kind as the cold run.

The physical retry ladder (a transient error must not kill a multi-hour run) is pinned next
door in tests/unit/test_call_glm_retry.py.
"""
from __future__ import annotations

import json
import os

import pytest

import flowmirror.agents.runtime as rt
from flowmirror.io.hashing import sha256_text

_SHOWN = {"pids": ["p1"], "codes": ["100001"], "held": [], "orgs": ["ORG01"]}


def _cfg(cache_path):
    """A runtime-shaped config; max_attempts 2 so the macro re-ask path is exercised too."""
    return {"run_tag": "rt1_failure_kinds",
            "llm": {"model": "stub-vision", "text_model": "stub-text",
                    "max_tokens_start": 512, "max_attempts": 2, "workers": 1,
                    "cache": str(cache_path)},
            "social": True,
            "channels": {"feed": True, "experience": True, "trend": True, "social": True}}


def _view():
    # Superset of the agent_view keys prompt.build_decision_messages reads; wording kept free of
    # regulator-style phrasing so prompt.py's anti-priming guard stays quiet.
    return {"agent_id": "A001", "arm": "T", "c_class": "C3", "cash": 20000.0,
            "persona_card_zh_rich": "你是测试代理人李四，四十岁，做事偏稳健。",
            "market_view": 3, "risk_mood": 3, "day": 3, "holdings": [],
            "familiarity": {"ORG01": 1}, "follows": [], "memory": [], "last_trade": None,
            "last_reflection": "", "declined_confirms": [], "guba": {}, "trend": [], "direct": []}


def _cards():
    return [{"post_id": "p1", "org": "ORG01", "intent": "I3", "title": "测试帖子标题",
             "caption": "测试正文内容，长度适中。",
             "landing": {"code": "100001", "name": "示例基金", "R": "R3",
                         "ret_3m": 2.3, "ret_1y": 8.9, "min_buy": 10},
             "likes": 3, "arm": "T", "image_path": None, "image_sha": None,
             "comments_prev": [], "climate_label": "no_signal", "n_comments_prev": 0}]


def _prov(cls, raw=None, status=None, attempts=1):
    """A call_glm-shaped provenance dict for one terminal failure of class `cls`.

    Mirrors call_glm's own bookkeeping: a failed attempt that produced no channel text records
    raw=None / raw_sha256=None, one that produced text keeps both."""
    return {"parsed": None, "raw": raw, "response_source": None, "http_status": status,
            "attempts": int(attempts), "finish_reason": None, "max_tokens_final": 512,
            "parser_status": cls, "raw_sha256": sha256_text(raw) if raw else None,
            "usage": {}, "attempt_log": [{"i": 1, "http_status": status, "cls": cls,
                                          "finish_reason": None, "max_tokens": 512}]}


def _stub(prov):
    """An `llm` callable that always returns the same provenance, counting its invocations."""
    calls = []

    def _call(messages, max_tokens, **kw):
        calls.append(dict(kw, max_tokens=int(max_tokens)))
        return dict(prov)

    return _call, calls


def _decide(cfg, llm):
    return rt.decide(_view(), _cards(), cfg, rt.LLMCache(cfg["llm"]["cache"]),
                     rt.BudgetGovernor(50), llm, _SHOWN)


def test_http_error_is_transport_and_reports_no_schema_verdict(tmp_path):
    """Every attempt is a non-200 -> "transport", and the status is the transport class itself."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, calls = _stub(_prov("http_error", status=429))

    rec = _decide(cfg, llm)

    assert rec["parsed"] is None
    assert rec["failure_kind"] == "transport"
    # The regression: extract_decision("") answers "no_json_object", so the pre-fix run reported a
    # parse verdict for a rate-limit response. No schema/parse label may appear here at all.
    assert rec["parser_status"] == "http_error"
    assert rec["parser_status"] not in ("no_json_object", "schema_invalid", "unparsed",
                                        "ambiguous_reasoning", "truncated_length")
    assert rec["violations"] == []           # nothing was parsed, so nothing was violated
    assert len(calls) == 2                   # macro re-ask schedule


def test_http_200_with_unparseable_content_is_model(tmp_path):
    """A response DID arrive and would not parse -> "model", with the parser's own verdict."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, _calls = _stub(_prov("no_json_object", raw="抱歉，我无法给出 JSON。", status=200))

    rec = _decide(cfg, llm)

    assert rec["parsed"] is None
    assert rec["failure_kind"] == "model"
    # Here the extractor IS the right authority, and its diagnosis must survive into the record.
    assert rec["parser_status"] in ("no_json_object", "schema_invalid")


def test_schema_invalid_response_is_model_not_transport(tmp_path):
    """Complete JSON that fails the decision schema is the model's fault, not the network's."""
    cfg = _cfg(tmp_path / "c.jsonl")
    bad = json.dumps({"reads": "not-a-list", "mood": 99}, ensure_ascii=False)
    llm, _calls = _stub(_prov("schema_invalid", raw=bad, status=200))

    rec = _decide(cfg, llm)

    assert rec["failure_kind"] == "model"
    assert rec["parser_status"] == "schema_invalid"
    assert rec["violations"]                 # the parser's reason is preserved for the dec event


@pytest.mark.parametrize("cls", list(rt.TRANSPORT_FAILURE_CLASSES))
def test_every_transport_class_classifies_as_transport(tmp_path, cls):
    """Contract 2.4 lists exactly four transport classes -- all four, no more, no fewer."""
    cfg = _cfg(tmp_path / f"c_{cls}.jsonl")
    # reasoning_salvage_rejected is the one transport class that DOES hash a channel text
    # (the reasoning trace); raw is still None, which is what used to be re-parsed.
    llm, _calls = _stub(_prov(cls, status=200 if cls.startswith(("empty", "reasoning")) else None))

    rec = _decide(cfg, llm)

    assert rec["failure_kind"] == "transport"
    assert rec["parser_status"] == cls


@pytest.mark.parametrize("cls", ["truncated_length", "no_json_object", "schema_invalid",
                                 "ambiguous_reasoning", "mock", "null"])
def test_non_transport_classes_classify_as_model(cls):
    """Anything outside the four transport classes means a response arrived: the model's failure.

    truncated_length in particular: the provider answered, the answer was cut off -- that is a
    token-budget/model problem and must keep counting against the model."""
    assert rt._failure_kind(cls, None) == "model"
    assert rt._failure_kind(cls, {"mood": 3}) is None       # a success is never a failure


def test_success_has_failure_kind_none(tmp_path):
    """failure_kind is None whenever a decision was produced -- including via the extractor.

    MockLLM returns parsed=None and lets decide() extract the decision from the raw text, so a
    kind derived from prov["parsed"] alone would mislabel mock decisions as failures."""
    cfg = _cfg(tmp_path / "c.jsonl")
    rec = _decide(cfg, rt.MockLLM(malformed_rate=0.0))

    assert rec["parsed"] is not None and rec["failure_kind"] is None
    assert rec["parser_status"] == "ok"


def test_replay_of_a_transport_failure_reproduces_the_same_kind_and_status(tmp_path):
    """Cold run and warm replay must agree on the recorded failure kind."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, calls = _stub(_prov("exception"))

    cold = _decide(cfg, llm)
    n_cold = len(calls)
    warm = _decide(cfg, llm)                 # same key -> served from the cache file on disk

    assert os.path.isfile(cfg["llm"]["cache"])
    assert cold["cache_hit"] is False and warm["cache_hit"] is True
    assert len(calls) == n_cold              # the replay spends no provider attempt
    assert warm["failure_kind"] == cold["failure_kind"] == "transport"
    assert warm["parser_status"] == cold["parser_status"] == "exception"
    assert warm["violations"] == cold["violations"] == []


def test_replay_of_a_model_failure_reproduces_the_same_kind(tmp_path):
    """The other half of the replay identity: a cached model failure stays a model failure."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, _calls = _stub(_prov("no_json_object", raw="没有 JSON", status=200))

    cold = _decide(cfg, llm)
    warm = _decide(cfg, llm)

    assert warm["failure_kind"] == cold["failure_kind"] == "model"
    assert warm["parser_status"] == cold["parser_status"]


def test_reflect_transport_failure_is_transport_and_skips_the_reparse(tmp_path):
    """reflect() too: with no model output there is nothing to re-parse (E12, reflection side)."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, calls = _stub(_prov("http_error", status=503))

    rec = rt.reflect(_view(), cfg, rt.LLMCache(cfg["llm"]["cache"]), rt.BudgetGovernor(50), llm)

    assert rec["parsed"] is None
    assert rec["failure_kind"] == "transport"
    assert rec["parser_status"] == "http_error"
    assert len(calls) == 1                   # reflection has never had a macro retry


def test_reflect_model_failure_is_model(tmp_path):
    """A reflection response that arrived and would not parse stays the model's failure."""
    cfg = _cfg(tmp_path / "c.jsonl")
    llm, _calls = _stub(_prov("no_json_object", raw="今天没什么想法。", status=200))

    rec = rt.reflect(_view(), cfg, rt.LLMCache(cfg["llm"]["cache"]), rt.BudgetGovernor(50), llm)

    assert rec["parsed"] is None and rec["failure_kind"] == "model"


def test_transport_and_model_are_the_only_two_kinds(tmp_path):
    """transport + model == decision_failures, so a failed
    decision is ALWAYS exactly one of the two and a successful one is neither."""
    assert set(rt.TRANSPORT_FAILURE_CLASSES) == {"exception", "http_error", "empty_response",
                                                 "reasoning_salvage_rejected", "rate_limited"}
    for cls in list(rt.TRANSPORT_FAILURE_CLASSES) + ["schema_invalid", "mock", None, ""]:
        assert rt._failure_kind(cls, None) in ("transport", "model")
        assert rt._failure_kind(cls, {"mood": 1}) is None

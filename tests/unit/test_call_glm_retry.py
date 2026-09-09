"""Unit tests for provider retries, parsing, and credential handling.

The HTTP transport and time.sleep are stubbed, so the suite uses no network.

Pinned behaviour:
  * raise -> non-200 -> valid answer  ==> success with attempts == 3, no exception
    (this test FAILS against the old one-line bookkeeping, which raises AttributeError);
  * five consecutive failures         ==> provenance with parsed is None, no exception, and
    raw=None / raw_sha256=None for attempts that produced no channel text (the E3 convention,
    consistent with decide()/reflect() which compute `prov.get("raw_sha256") or
    (sha256_text(raw) if raw else None)`);
  * frozen schedule untouched: one initial attempt + at most 4 retries, no sleep after the 5th,
    BACKOFF_ERROR_S * k for non-empty failures, token ladder never escalates without truncation;
  * no credentials at all             ==> RuntimeError naming all three supported ways to supply
    a key, raised BEFORE any attempt or backoff is spent (nothing is ever cached), echoing no key.

The request payload must carry the configured sampling temperature, and
`max_provider_attempts` must control the provider ladder length.
"""
from __future__ import annotations

import json
import types

import pytest

import flowmirror.agents.runtime as rt

_MESSAGES = [{"role": "user", "content": "decide now"}]
_OK_CONTENT = '{"answer": 42}'


class _Resp:
    """Minimal stand-in for requests.Response: call_glm only touches .status_code and .json()."""

    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _ok_body():
    return {"choices": [{"message": {"content": _OK_CONTENT, "reasoning_content": ""},
                         "finish_reason": "stop"}],
            "usage": {"total_tokens": 99}}


def _parse(text, channel="content"):
    try:
        obj = json.loads(text)
    except Exception:
        return None, None
    return (obj if isinstance(obj, dict) else None), text


def _install(monkeypatch, behavior, sleeps):
    """Replace runtime's `requests` with a scripted transport and `time.sleep` with a recorder.

    `behavior` is a list of step tags; step i governs physical attempt i+1, the last tag repeats:
      "raise"     -> the POST raises (transport exception, cls == "exception")
      "http_500"  -> a non-200 response (cls == "http_error")
      "ok"        -> a 200 whose content parses via the supplied parser (cls == "ok")
    Returns the list of recorded calls (one dict per physical attempt)."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        step = behavior[len(calls)] if len(calls) < len(behavior) else behavior[-1]
        body = json or {}
        # RT2: the payload is recorded whole enough to assert the sampling temperature actually
        # sent, not merely the value _llm_sampling computed.
        calls.append({"url": url, "model": body.get("model"), "temperature": body.get("temperature")})
        if step == "raise":
            raise RuntimeError("transient provider error")
        if step == "http_500":
            return _Resp(500)
        return _Resp(200, _ok_body())

    monkeypatch.setattr(rt, "requests", types.SimpleNamespace(post=fake_post))
    monkeypatch.setattr(rt, "time", types.SimpleNamespace(sleep=lambda s: sleeps.append(s),
                                                          time=lambda: 0.0))
    monkeypatch.setattr(rt, "GLM_KEY", "unit-test-key")
    monkeypatch.setattr(rt, "GLM_EP", "http://unit.test/v1/chat/completions")
    return calls


def test_retry_ladder_survives_exception_then_http_error(monkeypatch):
    """attempt 1 raises, attempt 2 returns non-200, attempt 3 answers -> success, attempts == 3."""
    sleeps = []
    calls = _install(monkeypatch, ["raise", "http_500", "ok"], sleeps)

    prov = rt.call_glm(_MESSAGES, 1024, model="unit-vision-model", parser=_parse)

    assert prov["parsed"] == {"answer": 42}
    assert prov["attempts"] == 3
    assert [e["cls"] for e in prov["attempt_log"]] == ["exception", "http_error", "ok"]
    assert [e["http_status"] for e in prov["attempt_log"]] == [None, 500, 200]
    assert prov["http_status"] == 200 and prov["response_source"] == "content"
    assert prov["raw"] == _OK_CONTENT
    assert prov["raw_sha256"] == rt.sha256_text(_OK_CONTENT)
    assert len(calls) == 3 and all(c["model"] == "unit-vision-model" for c in calls)
    # frozen backoff: exception/http_error are non-empty failures -> BACKOFF_ERROR_S * k (no 15s path)
    assert sleeps == [rt.BACKOFF_ERROR_S * k for k in (1, 2)]
    # no truncation/empty anywhere -> the token ladder must never escalate
    assert prov["max_tokens_final"] == 1024


def test_five_consecutive_failures_return_parsed_none_without_raising(monkeypatch):
    """Every attempt fails -> parsed is None after exactly 5 attempts; no exception escapes."""
    sleeps = []
    calls = _install(monkeypatch, ["raise"], sleeps)

    prov = rt.call_glm(_MESSAGES, 2048, parser=_parse)

    assert prov["parsed"] is None
    assert prov["attempts"] == 5
    assert len(prov["attempt_log"]) == 5
    assert all(e["cls"] == "exception" for e in prov["attempt_log"])
    # E3 convention: a failed attempt with no channel text records raw=None / raw_sha256=None
    assert prov["raw"] is None and prov["raw_sha256"] is None and prov["response_source"] is None
    # exactly 4 sleeps (one initial attempt + at most 4 retries; no sleep after the terminal 5th)
    assert sleeps == [rt.BACKOFF_ERROR_S * k for k in range(1, 5)]
    assert len(calls) == 5


def test_call_glm_without_credentials_fails_fast_with_actionable_message(monkeypatch):
    """No key configured -> RuntimeError naming the three ways to supply one, before any attempt."""
    sleeps = []
    calls = _install(monkeypatch, ["ok"], sleeps)
    monkeypatch.setattr(rt, "GLM_KEY", "")

    with pytest.raises(RuntimeError) as excinfo:
        rt.call_glm(_MESSAGES, 1024, parser=_parse)

    msg = str(excinfo.value)
    for needle in ("config/api.yaml", "config/api_example.yaml",
                   "FLOWMIRROR_GLM_KEY", "FLOWMIRROR_LEGACY_KEY_FILE"):
        assert needle in msg
    assert "unit-test-key" not in msg      # a key (or any part of one) is never echoed
    assert calls == [] and sleeps == []    # fail fast: no attempt and no backoff spent, nothing cached


# Provider sampling and retry parameters.
def test_payload_carries_the_configured_temperature(monkeypatch):
    """`temperature=` reaches the request body; omitting it keeps the frozen 0.3 default.

    The configured value must reach both the request payload and run metadata."""
    sleeps = []
    calls = _install(monkeypatch, ["ok"], sleeps)

    rt.call_glm(_MESSAGES, 1024, parser=_parse, temperature=0.9)
    rt.call_glm(_MESSAGES, 1024, parser=_parse, temperature=0.0)   # greedy is a real setting
    rt.call_glm(_MESSAGES, 1024, parser=_parse)                    # unpassed -> today's constant

    assert [c["temperature"] for c in calls] == [0.9, 0.0, rt.TEMP_DEFAULT]
    assert rt.TEMP_DEFAULT == 0.3          # the default equals the constant it replaced
    # _llm_sampling is the one place cfg["llm"] is read, and 0.0 must survive it (a plain
    # `value or default` would silently restore 0.3 and misreport the run).
    assert rt._llm_sampling({"llm": {"temperature": 0.0}})[0] == 0.0
    assert rt._llm_sampling({})[0] == rt.TEMP_DEFAULT


def test_max_provider_attempts_two_stops_after_exactly_two_attempts(monkeypatch):
    """A configured ladder length is the ladder's real length -- and no sleep after the last."""
    sleeps = []
    calls = _install(monkeypatch, ["raise"], sleeps)

    prov = rt.call_glm(_MESSAGES, 2048, parser=_parse, max_provider_attempts=2)

    assert prov["parsed"] is None
    assert prov["attempts"] == 2 and len(prov["attempt_log"]) == 2 and len(calls) == 2
    assert sleeps == [rt.BACKOFF_ERROR_S * 1]      # one backoff between the two, none after
    # The name collision this key exists to end: llm.max_attempts is the MACRO re-ask switch
    # read by decide(), llm.max_provider_attempts is this physical ladder. Two keys, two
    # helpers, two constants -- _llm_cfg must not answer for the provider ladder.
    assert rt._llm_sampling({"llm": {"max_provider_attempts": 2}})[1] == 2
    assert rt._llm_cfg({"llm": {"max_attempts": 2}})[3] == 2
    assert rt.MAX_PROVIDER_ATTEMPTS_DEFAULT == 5 and rt._llm_sampling({})[1] == 5
    assert not hasattr(rt, "MAX_ATTEMPTS")         # the ambiguous name is gone for good

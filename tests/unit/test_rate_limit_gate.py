# -*- coding: utf-8 -*-
"""Unit tests for rate-limit handling in flowmirror.agents.runtime.

Covers 429 classification with Retry-After, the wait cap and the cumulative
wait budget, retry-ladder preservation under rate limiting, AdaptiveGate
behaviour and its process-wide singleton, gate wiring inside call_glm, and
transport-hole-aware LLMCache lookups plus RuntimeOpts plumbing.
"""

import json
import types

from flowmirror.agents import runtime as rt
from flowmirror.engine.loop import RuntimeOpts

_RL_BODY = {"error": {"code": "1302"}}  # provider body code == rate limited
_OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
    "usage": {"total_tokens": 8},
}


def _parse(text, channel="content"):
    """Same parser test_call_glm_retry.py uses: call_glm only classifies a 200 as
    "ok" when the parser returns a dict, so every call in this file must pass it."""
    try:
        obj = json.loads(text)
    except Exception:
        return None, None
    return (obj if isinstance(obj, dict) else None), text


def _resp(status, body, headers=None):
    """Fake requests response exposing status_code / json() / headers."""
    return types.SimpleNamespace(
        status_code=status, headers=headers or {}, json=lambda: body
    )


def _rl(retry_after="1"):
    """A scripted 429 response carrying a Retry-After header."""
    return _resp(429, _RL_BODY, {"Retry-After": retry_after})


def _install(monkeypatch, responses):
    """Stub requests.post / time.sleep / GLM_KEY like test_call_glm_retry.py.

    The last scripted response repeats forever. Returns collected sleeps.
    """
    sleeps = []

    def fake_post(*args, **kwargs):
        if len(responses) > 1:
            return responses.pop(0)
        return responses[0]

    monkeypatch.setattr(rt, "GLM_KEY", "test-key", raising=False)
    monkeypatch.setattr(rt, "requests", types.SimpleNamespace(post=fake_post))
    monkeypatch.setattr(rt, "time", types.SimpleNamespace(sleep=sleeps.append))
    return sleeps


def _msg(tag):
    return [{"role": "user", "content": tag}]


# --- A. 429 classification and waiting --------------------------------------

def test_429_classified_rate_limited_and_retry_after_honored(monkeypatch):
    sleeps = _install(monkeypatch, [_rl("7"), _resp(200, _OK_BODY)])
    prov = rt.call_glm(_msg("a1"), 64, parser=_parse)
    assert prov["parsed"]
    assert prov["attempt_log"][0]["cls"] == "rate_limited"
    assert 7.0 in sleeps  # Retry-After wins over plain 5.0*k backoff
    assert prov["rate_limit_waits"] == [7.0]


def test_retry_after_is_capped(monkeypatch):
    sleeps = _install(monkeypatch, [_rl("999"), _resp(200, _OK_BODY)])
    prov = rt.call_glm(_msg("a2"), 64, rate_limit_cap_s=10, parser=_parse)
    assert 10.0 in sleeps
    assert prov["rate_limit_waits"] == [10.0]


def test_rate_limit_does_not_consume_retry_ladder(monkeypatch):
    _install(monkeypatch, [_rl(), _rl(), _rl(), _resp(200, _OK_BODY)])
    prov = rt.call_glm(_msg("a3"), 64, max_provider_attempts=2, parser=_parse)
    assert prov["parsed"]
    hits = sum(1 for a in prov["attempt_log"] if a["cls"] == "rate_limited")
    assert hits == 3  # a consumed ladder of 2 would have terminated earlier


def test_cumulative_wait_budget_turns_into_failure(monkeypatch):
    _install(monkeypatch, [_rl("1")])  # every attempt is rate limited
    prov = rt.call_glm(_msg("a4"), 64, max_provider_attempts=1,
                       rate_limit_max_wait_s=1.5, parser=_parse)
    waits = prov["rate_limit_waits"]
    assert prov["parsed"] is None
    assert prov["parser_status"] == "rate_limited"
    assert len(waits) >= 2 and sum(waits) > 1.5  # stops once budget blown


def test_attempt_log_records_elapsed_s(monkeypatch):
    _install(monkeypatch, [_resp(200, _OK_BODY)])
    prov = rt.call_glm(_msg("a5"), 64, parser=_parse)
    entry = prov["attempt_log"][0]
    assert "elapsed_s" in entry
    assert isinstance(entry["elapsed_s"], float)


# --- B. AdaptiveGate --------------------------------------------------------

def test_gate_halves_with_floor_one():
    gate = rt.AdaptiveGate(8)
    gate.on_rate_limited()
    assert gate.snapshot()["permits"] == 4
    gate.on_rate_limited()
    gate.on_rate_limited()
    assert gate.snapshot()["permits"] == 1  # floor: never drops to 0


def test_gate_recovers_by_one_and_never_exceeds_initial():
    gate = rt.AdaptiveGate(4)
    gate.on_rate_limited()
    gate.on_rate_limited()
    assert gate.snapshot()["permits"] == 1
    for _ in range(rt.GATE_RECOVER_AFTER):
        gate.on_success()
    assert gate.snapshot()["permits"] == 2
    for _ in range(rt.GATE_RECOVER_AFTER):
        gate.on_success()
    assert gate.snapshot()["permits"] == 3
    for _ in range(rt.GATE_RECOVER_AFTER * 3):
        gate.on_success()
    snap = gate.snapshot()
    assert snap["permits"] == snap["initial"] == 4  # ceiling == initial


def test_gate_for_returns_process_wide_singleton(monkeypatch):
    monkeypatch.setattr(rt, "_GATE", None, raising=False)
    first = rt.gate_for(4)
    assert rt.gate_for(4) is first
    assert rt.gate_for(99) is first  # later arguments are ignored


def test_call_glm_wires_gate_halving_and_balance(monkeypatch):
    counts = {"acquire": 0, "release": 0}

    class CountingGate(rt.AdaptiveGate):
        def acquire(self):
            counts["acquire"] += 1
            super().acquire()

        def release(self):
            counts["release"] += 1
            super().release()

    _install(monkeypatch, [_rl("1"), _resp(200, _OK_BODY)])
    gate = CountingGate(2)
    prov = rt.call_glm(_msg("b3"), 64, gate=gate, parser=_parse)
    assert prov["parsed"]
    assert gate.snapshot()["permits"] == 1  # halved once on the 429
    assert counts["acquire"] == counts["release"] >= 1


# --- C. Transport holes are retryable cache misses --------------------------

def _write_cache(tmp_path):
    rows = [
        {"key": "k1", "parsed": None, "raw": None, "ts": 0,
         "provenance": {"parser_status": "rate_limited"}},
        {"key": "k2", "parsed": None, "raw": None, "ts": 0,
         "provenance": {"parser_status": "schema_invalid"}},
    ]
    path = tmp_path / "llm_cache.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return str(path)


def test_llm_cache_rows_terminal_by_default(tmp_path):
    cache = rt.LLMCache(_write_cache(tmp_path))
    assert cache.get("k1") is not None  # transport hole stays terminal
    assert cache.get("k2") is not None


def test_llm_cache_retries_transport_holes_only(tmp_path):
    cache = rt.LLMCache(_write_cache(tmp_path), retry_transport_holes=True)
    assert cache.get("k1") is None  # hole treated as a miss
    assert cache.get("k2") is not None  # model failure still terminal


def test_runtime_opts_retry_transport_holes_field():
    assert RuntimeOpts(retry_transport_holes=True).retry_transport_holes is True
    assert RuntimeOpts().retry_transport_holes is False
    text = repr(RuntimeOpts(retry_transport_holes=True))
    assert "retry_transport_holes=True" in text
    assert text.count("=") >= 2  # repr surfaces both fields

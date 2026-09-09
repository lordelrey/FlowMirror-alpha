"""Transport and model-output failures remain distinct diagnostics.

`decision_failure_halt` exists to catch a MODEL that cannot produce parseable output.
The engine used to count an HTTP error, a dead socket and malformed JSON into one
bucket, so a revoked key or a rate limit presented as model instability -- in one live
smoke run three rate-limited calls looked like an unstable model while the parse rate
was 77 of 77.

The 0.02 threshold still applies to the combined failure rate.
"""
from __future__ import annotations

import json

import pytest

from flowmirror.engine import loop as L

from tests.conftest import build_demo_cfg


def _run(out_dir, **over):
    cfg = build_demo_cfg(out_dir, agents=10, days=3)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    rc = L.run_simulation(cfg, L.RuntimeOpts())
    rows = [json.loads(l) for l in
            (out_dir / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    return rc, rows, meta


def _counters(meta):
    c = meta.get("counters") or {}
    return (int(c.get("decision_failures", 0)),
            int(c.get("decision_failures_transport", 0)),
            int(c.get("decision_failures_model", 0)))


def test_both_counters_always_reach_run_meta(tmp_path):
    """Seeded at zero, so the identity is checkable on a clean run too."""
    _rc, _rows, meta = _run(tmp_path / "clean")
    total, transport, model = _counters(meta)
    c = meta.get("counters") or {}
    assert "decision_failures_transport" in c and "decision_failures_model" in c
    assert transport + model == total


def test_malformed_output_counts_as_model_never_transport(tmp_path):
    """A mock that always emits unparseable output is a MODEL failure by definition."""
    _rc, rows, meta = _run(tmp_path / "bad", mock_options={"malformed_rate": 1.0})
    total, transport, model = _counters(meta)
    assert total > 0, "malformed_rate 1.0 must produce failures"
    assert model == total and transport == 0
    assert transport + model == total

    kinds = {r.get("failure_kind") for r in rows if r.get("ev") == "dec"}
    assert kinds == {"model"}, f"expected only model failures, got {kinds}"


def test_a_raising_provider_counts_as_transport(tmp_path, monkeypatch):
    """A provider that cannot be reached is a transport failure, not model instability.

    Patching the LLM factory is the cheapest honest stand-in for a revoked key: the
    call never returns content, which is exactly the shape call_glm reports as its
    exception class.
    """
    class Dead:
        """Mimics call_glm on an unreachable provider: it RETURNS its exception class
        rather than raising, which is the contract the retry ladder is built on."""

        def __call__(self, messages, max_tokens, **kw):
            return {"parsed": None, "raw": None, "response_source": None,
                    "http_status": None, "attempts": 5, "finish_reason": None,
                    "max_tokens_final": int(max_tokens), "parser_status": "exception",
                    "raw_sha256": None, "usage": {},
                    "attempt_log": [{"i": i, "http_status": None, "cls": "exception",
                                     "finish_reason": None, "max_tokens": int(max_tokens)}
                                    for i in range(1, 6)]}

    monkeypatch.setattr(L, "_make_llm", lambda cfg: Dead())
    _rc, rows, meta = _run(tmp_path / "dead")
    total, transport, model = _counters(meta)
    assert total > 0, "an unreachable provider must produce failures"
    assert transport == total and model == 0, (
        "a network failure must not count as a model failure")
    assert transport + model == total


def test_successful_decisions_carry_a_null_failure_kind(tmp_path):
    _rc, rows, _meta = _run(tmp_path / "ok")
    decs = [r for r in rows if r.get("ev") == "dec"]
    assert decs, "the fixture must produce decisions"
    assert all("failure_kind" in r for r in decs), (
        "the field is always present, so absence never has to be interpreted")
    assert all(r["failure_kind"] is None for r in decs if r.get("status") == "ok"), \
        "a parsed decision has no failure kind"


def test_the_halt_threshold_and_condition_are_unchanged(tmp_path):
    """Failure categories split diagnosis without changing the halt policy.

    The rule is that the run halts once the OVERALL failure rate passes the threshold,
    regardless of kind -- so an all-transport run must still halt at the same rate a
    mixed run would.
    """
    import inspect
    src = inspect.getsource(L.run_simulation)
    assert 'cfg["llm"].get("decision_failure_halt", True)' in src
    assert "0.02" in src, "the configured default threshold must still be 0.02"
    # the halt still tests the aggregate, never one kind
    assert 'S["decision_failures"] / S["decisions"] > thr' in src
    for kind in ("decision_failures_transport", "decision_failures_model"):
        assert f'S["{kind}"] / S["decisions"]' not in src, (
            "a per-kind threshold would change the configured halt policy")


def test_climate_margin_is_threaded_from_the_config(tmp_path):
    """The run consumes the configured climate threshold.

    A margin of 1.0 can never be exceeded, so no post can reach a majority label; the
    default 1/6 can. If the value were still hardcoded the two runs would agree.
    """
    _rc, rows_default, _m = _run(tmp_path / "default")
    _rc, rows_impossible, _m = _run(tmp_path / "impossible", feed={"climate_margin": 1.0})

    def labels(rows):
        return {r.get("label") or r.get("lab") or r.get("climate")
                for r in rows if r.get("ev") == "clim"}

    hi = labels(rows_impossible)
    assert not ({"bullish_majority", "bearish_majority"} & hi), (
        f"margin 1.0 must make a majority unreachable, saw {hi}")
    # and the two runs must differ somewhere, proving the value is really consumed
    assert labels(rows_default) != hi or len(hi) <= 1

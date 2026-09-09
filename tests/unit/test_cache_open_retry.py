import builtins
import json
import os
import types

import pytest

from flowmirror.agents import runtime as rt


class FakeHandle:
    def __init__(self, captured=None, write_exc=None):
        self._captured = captured
        self._write_exc = write_exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def write(self, data):
        if self._write_exc is not None:
            raise self._write_exc
        self._captured.append(data)


def _patch_open(monkeypatch, cache, callback):
    real_open = builtins.open
    calls = []

    def fake_open(file, mode, *args, **kwargs):
        if os.fspath(file) == os.fspath(cache.path) and mode == "a":
            calls.append(1)
            return callback()
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    return calls


def _patch_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(rt, "time", types.SimpleNamespace(sleep=sleeps.append))
    return sleeps


def test_retry_then_success(tmp_path, monkeypatch):
    cache = rt.LLMCache(str(tmp_path / "llm_cache.jsonl"))
    captured = []
    row = {"role": "user", "content": "hi"}

    state = {"n": 0}

    def callback():
        state["n"] += 1
        if state["n"] < 3:
            raise PermissionError("locked")
        return FakeHandle(captured=captured)

    calls = _patch_open(monkeypatch, cache, callback)
    sleeps = _patch_sleep(monkeypatch)

    cache.put("k1", row)

    assert len(calls) == 3
    assert sleeps == [0.05, 0.1]
    assert len(captured) == 1
    assert json.loads(captured[0]) == row
    assert captured[0].endswith("\n")
    assert cache.rows["k1"] is row


def test_retry_exhaustion(tmp_path, monkeypatch):
    cache = rt.LLMCache(str(tmp_path / "llm_cache.jsonl"))
    captured = []
    row = {"role": "assistant", "content": "yo"}

    def callback():
        raise PermissionError("locked")

    calls = _patch_open(monkeypatch, cache, callback)
    sleeps = _patch_sleep(monkeypatch)

    with pytest.raises(PermissionError, match="locked"):
        cache.put("k1", row)

    assert len(calls) == 8
    assert sleeps == [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 4.0]
    assert captured == []
    assert cache.rows["k1"] is row


def test_write_failure(tmp_path, monkeypatch):
    cache = rt.LLMCache(str(tmp_path / "llm_cache.jsonl"))
    captured = []
    row = {"role": "system", "content": "cfg"}

    def callback():
        return FakeHandle(captured=captured, write_exc=PermissionError("write denied"))

    calls = _patch_open(monkeypatch, cache, callback)
    sleeps = _patch_sleep(monkeypatch)

    with pytest.raises(PermissionError, match="write denied"):
        cache.put("k1", row)

    assert len(calls) == 1
    assert sleeps == []
    assert captured == []
    assert cache.rows["k1"] is row

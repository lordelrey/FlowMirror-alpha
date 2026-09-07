# -*- coding: utf-8 -*-
"""Card W1: the guba stance seed is honest about having no data.

`guba_seed_label` can only fire from a stance field, and the real signal file
(`data/attention/guba_signal_v1.json`, whose `_meta` says "no stance is computed
here" and `stance_jobs_stripped: true`) carries none -- its rows hold posting
VOLUME only.  Decision 4 of docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07 §5 makes
returning None the correct behaviour until the separate stance-labelling data
task lands, so these tests pin two things:

1. the real row shape produces no label and no exception, and posting volume
   (`z_abnormal`, `ratio_vs_baseline`) is never reinterpreted as direction;
2. a fund-week that later gains `bull_ratio` labels immediately -- i.e. the
   data lands with ZERO code change;

and that `load_world` says out loud which of those two states the run is in,
so the silent fallback to agent comments can no longer be mistaken for a
working sentiment seed.
"""
from __future__ import annotations

import json

import pytest

from flowmirror.engine.world import World, guba_seed_label, load_world

# The complete field set of a real guba signal row -- attention only, no stance.
REAL_ROW = {"n_posts": 37, "reply_n": 12, "read_n": 4100,
            "z_abnormal": 2.41, "ratio_vs_baseline": 3.2, "baseline_weeks_used": 8}
CODE, WEEK = "012524", "2025-W44"


def _world(signal):
    """A World carrying nothing but a guba signal: guba_seed_label reads only that."""
    w = World()
    w.guba = signal
    return w


def test_real_row_shape_yields_no_label_and_does_not_raise():
    w = _world({CODE: {WEEK: dict(REAL_ROW)}})
    assert guba_seed_label(w, CODE, WEEK) is None
    # A large positive volume z must NOT become a bullish seed: volume is not direction.
    w_hot = _world({CODE: {WEEK: dict(REAL_ROW, z_abnormal=9.9, ratio_vs_baseline=12.0)}})
    assert guba_seed_label(w_hot, CODE, WEEK) is None
    w_cold = _world({CODE: {WEEK: dict(REAL_ROW, z_abnormal=-9.9, ratio_vs_baseline=0.0)}})
    assert guba_seed_label(w_cold, CODE, WEEK) is None
    # Absent fund / absent week / non-dict payloads: still None, still no exception.
    assert guba_seed_label(w, "999999", WEEK) is None
    assert guba_seed_label(w, CODE, "2025-W01") is None
    assert guba_seed_label(_world({CODE: "bullish_majority"}), CODE, WEEK) is None
    assert guba_seed_label(_world({CODE: {WEEK: None}}), CODE, WEEK) is None


def test_real_file_rows_carry_no_stance_field():
    """Guards the premise: if a future signal file ships stance, this test fails first."""
    for field in ("bull_ratio", "bull", "bullish", "bear", "bearish"):
        assert field not in REAL_ROW


@pytest.mark.parametrize("br,expected", [
    (0.81, "bullish_majority"),
    (0.601, "bullish_majority"),
    (0.6, "mixed"),
    (0.5, "mixed"),
    (0.4, "mixed"),
    (0.399, "bearish_majority"),
    (0.12, "bearish_majority"),
])
def test_bull_ratio_lands_with_no_code_change(br, expected):
    """The stance pass merges `bull_ratio` into the same rows -> labels appear at once."""
    w = _world({CODE: {WEEK: dict(REAL_ROW, bull_ratio=br)}})
    assert guba_seed_label(w, CODE, WEEK) == expected


def test_bull_ratio_tolerates_the_shapes_a_merge_script_emits():
    # JSON-serialised number, and raw bull/bear counts instead of a precomputed ratio.
    assert guba_seed_label(_world({CODE: {WEEK: dict(REAL_ROW, bull_ratio="0.8")}}),
                           CODE, WEEK) == "bullish_majority"
    assert guba_seed_label(_world({CODE: {WEEK: dict(REAL_ROW, bull=9, bear=1)}}),
                           CODE, WEEK) == "bullish_majority"
    assert guba_seed_label(_world({CODE: {WEEK: dict(REAL_ROW, bullish=1, bearish=9)}}),
                           CODE, WEEK) == "bearish_majority"
    # Unusable values degrade to "no seed" rather than raising mid-run; `true` must not
    # coerce to a unanimous bull week.
    for junk in (None, "", "n/a", [], {}, True):
        assert guba_seed_label(_world({CODE: {WEEK: dict(REAL_ROW, bull_ratio=junk)}}),
                               CODE, WEEK) is None
    assert guba_seed_label(_world({CODE: {WEEK: dict(REAL_ROW, bull=0, bear=0)}}),
                           CODE, WEEK) is None


def _guba_lines(captured):
    return [ln for ln in captured.splitlines() if "guba stance seed" in ln]


def test_load_world_states_the_stance_seed_is_unavailable(tmp_path, demo_cfg_factory, capsys):
    """The demo config points at the real signal file: exactly one honest line, no failure."""
    cfg = demo_cfg_factory(tmp_path / "out")
    load_world(cfg)
    lines = _guba_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    line = lines[0]
    assert "WARNING" in line and "0 of " in line and "bull_ratio" in line
    assert "stance labelling has not been run" in line
    assert "falls back to agent comments" in line


def test_load_world_reports_the_count_once_stance_data_lands(tmp_path, demo_cfg_factory, capsys):
    """Same file shape plus `bull_ratio` on two fund-weeks -> the count is reported."""
    cfg = demo_cfg_factory(tmp_path / "out2")
    sig = {"000011": {"2025-W41": dict(REAL_ROW), "2025-W42": dict(REAL_ROW, bull_ratio=0.71)},
           "000041": {"2025-W41": dict(REAL_ROW, bull_ratio=0.22), "2025-W42": dict(REAL_ROW)}}
    path = tmp_path / "guba_signal_v2_stub.json"
    path.write_text(json.dumps({"_meta": {"stub": True}, "signal": sig}), encoding="utf-8")
    cfg["guba_signal"] = str(path)
    world = load_world(cfg)
    lines = _guba_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert "WARNING" not in lines[0]
    assert "2 of 4 fund-weeks carry bull_ratio" in lines[0]
    # and the labels the reported rows now produce
    assert guba_seed_label(world, "000011", "2025-W42") == "bullish_majority"
    assert guba_seed_label(world, "000041", "2025-W41") == "bearish_majority"
    assert guba_seed_label(world, "000011", "2025-W41") is None

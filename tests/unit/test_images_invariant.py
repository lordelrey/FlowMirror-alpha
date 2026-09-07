#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card W2: the engine reports the image state instead of hiding it.

Three things were missing and are pinned here.

1. `run_meta["images"]` was never written at all, so the web viewer's "this run carried no
   images" chip (web/app.js reads `m.images.root` / `m.images.attached`) showed on every run,
   including runs that did carry images. write_reports now copies `state["images"]` verbatim
   (contract 2.3) and falls back to the honest zero form when the key is absent.

2. `world.image_pool_summary(pool, images_root)` answers "how many of the pool's image
   references resolve to a real file", over the three shapes `image_ids` actually takes in the
   masked content pool (a JSON list, a JSON string, a single-quoted Python repr -- contract
   2.5). Counts only: it never reads an image byte and never returns a path.

3. `INVARIANTS["m_tv_arm_carries_images"]` states which of three situations a run is in.

On (3) this file follows the contract, and it contradicts one line of the older docs. The
2026-09-07 contract section 2.6 -- with plan section 4.1 card W2 and section 7 item 7 -- makes
this invariant a WARN form: `pass` is always True and the run's exit code never depends on it,
because gating belongs on irreversible, cross-system, security or release boundaries and one
simulation run is none of those. "Did pixels reach the TV arm" is answered by the NUMBER
run_meta.images.attached. docs/RUNBOOK.md section 4, config/schemas/run.schema.json and
config/engine_defaults.yaml still carry the pre-decision wording ("the invariant is skipped" /
"FAILS a run"); those three files belong to other lanes and are left to the DOC1 card.

So the assertion below is deliberately "reports the zero, keeps passing": a run with an image
library, an active TV arm and zero attachments must be visible in `attached`, `applicable` and
`reason`, and must NOT fail. Synthetic hand-built states only -- no simulation, no image
bytes, no network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root when run from a checkout

from flowmirror.engine.world import (  # noqa: E402
    INVARIANTS,
    World,
    _invariant_report,
    check_invariants,
    image_pool_summary,
    write_reports,
)

KEY = "m_tv_arm_carries_images"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _events(tmp_path, rows=()):
    """An event log on disk: check_invariants reads its rows, and an empty log is legal (every
    row-driven check then reports skipped, which is not what this file is about)."""
    p = Path(tmp_path) / "events.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return p


def _state(images=None, **over):
    """The minimum state check_invariants accepts. Every other check skips on it; only the
    image entry is under test, so nothing here has to resemble a finished run."""
    st = {"agents": [], "funds": {}, "active_per_day": {}, "agent_arms": {}, "signal_audit": []}
    if images is not None:
        st["images"] = images
    st.update(over)
    return st


def _cfg(factory, tmp_path, **over):
    """Fixture rule (contract section 4): every config in the suite is built from
    tests/conftest.py's build_demo_cfg, whose base runs/demo_two_arm.json has all its inputs
    tracked in git. The demo config already carries modality_level "agent", modality_arms
    ["T", "TV"], images_root null and image_pick "first" after the defaults merge."""
    cfg = factory(tmp_path)
    cfg.update(over)
    return cfg


def _entry(factory, tmp_path, images=None, rows=(), **cfg_over):
    checks, core = check_invariants(_state(images), _events(tmp_path, rows),
                                    _cfg(factory, tmp_path, **cfg_over))
    return checks[KEY], checks, core


def _world_stub():
    """A World carrying only the attributes write_reports reads. World uses __slots__, so an
    attribute this stub forgets raises rather than reading as None -- which is the point."""
    w = World()
    w.nav_synthetic = False
    w.inputs_sha256 = {}
    w.base_codes = []
    w.deferred = []
    w.funds = {}
    w.fund_org = {}
    w.nav_days = []
    return w


# --- the invariant, three situations --------------------------------------------------------
def test_registered_with_a_description():
    """A key missing from INVARIANTS would be written with the "unregistered invariant"
    fallback description, and the report would stop being self-explaining."""
    assert isinstance(INVARIANTS.get(KEY), str) and INVARIANTS[KEY].strip()


def test_tv_arm_with_a_root_and_zero_attachments_reports_the_gap_and_still_passes(
        demo_cfg_factory, tmp_path):
    """The situation the run must make visible: a configured image library, an active TV arm,
    and nothing attached. Contract 2.6: reported, never gating."""
    root = tmp_path / "imgs"
    root.mkdir()
    ent, checks, core = _entry(demo_cfg_factory, tmp_path,
                               images={"root": str(root), "policy": "first", "attached": 0,
                                       "missing": 2, "sha_mismatch": 1},
                               images_root=str(root), modality_arms=["T", "TV"])
    assert ent["pass"] is True                     # never a gate
    assert ent["applicable"] is True               # ...but this run IS the case that matters
    assert ent["images_root_configured"] is True
    assert (ent["attached"], ent["missing"], ent["sha_mismatch"]) == (0, 2, 1)
    assert "zero attachments" in ent["reason"]
    # The run's verdict (second return value) must not move, and nothing may be counted failed.
    assert core is True
    assert [k for k, v in checks.items() if v.get("pass") is False] == []


def test_attached_images_pass_and_the_count_is_reported(demo_cfg_factory, tmp_path):
    root = tmp_path / "imgs"
    root.mkdir()
    ent, _, core = _entry(demo_cfg_factory, tmp_path,
                          images={"root": str(root), "policy": "first", "attached": 3,
                                  "missing": 0, "sha_mismatch": 0},
                          images_root=str(root), modality_arms=["T", "TV"])
    assert ent["pass"] is True and ent["applicable"] is True and core is True
    assert ent["attached"] == 3
    assert "3" in ent["reason"] and "zero attachments" not in ent["reason"]


def test_null_root_names_the_missing_image_library(demo_cfg_factory, tmp_path):
    """images_root null: TV degrades to text-only, so the comparison measures nothing. The
    reason has to say that -- three zeros alone would look like a run that simply failed to
    attach anything."""
    ent, _, core = _entry(demo_cfg_factory, tmp_path,
                          images={"root": None, "policy": "first", "attached": 0,
                                  "missing": 0, "sha_mismatch": 0},
                          images_root=None, modality_arms=["T", "TV"])
    assert ent["pass"] is True and core is True
    assert ent["applicable"] is False
    assert ent["images_root_configured"] is False
    assert "images_root" in ent["reason"] and "text-only" in ent["reason"]


def test_a_run_without_a_tv_arm_says_so(demo_cfg_factory, tmp_path):
    """A two-arm T/TC run: images do not apply, and the reason names the arms so the zeros are
    not read as a broken image path."""
    root = tmp_path / "imgs"
    root.mkdir()
    ent, _, core = _entry(demo_cfg_factory, tmp_path, images=None,
                          images_root=str(root), modality_arms=["T", "TC"])
    assert ent["pass"] is True and core is True
    assert ent["applicable"] is False
    assert ent["images_root_configured"] is True
    assert "no TV arm" in ent["reason"]
    assert ent["arms"] == ["T", "TC"]


def test_run_level_arms_use_the_single_run_arm(demo_cfg_factory, tmp_path):
    """At modality_level "run" every agent receives modality_run_arm, so a modality_arms list
    that mentions TV is not a TV run unless the run arm itself is TV."""
    root = tmp_path / "imgs"
    root.mkdir()
    ent_t, _, _ = _entry(demo_cfg_factory, tmp_path, images=None, images_root=str(root),
                         modality_level="run", modality_run_arm="T",
                         modality_arms=["T", "TV"])
    assert ent_t["applicable"] is False and ent_t["arms"] == ["T"]
    assert "no TV arm" in ent_t["reason"]
    ent_tv, _, _ = _entry(demo_cfg_factory, tmp_path,
                          images={"root": str(root), "attached": 1}, images_root=str(root),
                          modality_level="run", modality_run_arm="TV",
                          modality_arms=["T", "TV"])
    assert ent_tv["applicable"] is True and ent_tv["arms"] == ["TV"]


def test_tv_impressions_are_counted_from_the_event_log(demo_cfg_factory, tmp_path):
    """The verification half: imp.img_idx is the per-impression record of what was attached
    (contract 2.5 item 6). It is read from the log, not from the loop's counters, so the two
    numbers can be compared -- here the state claims one attachment and the log shows two."""
    root = tmp_path / "imgs"
    root.mkdir()
    rows = [{"ev": "imp", "t": 0, "i": "A1", "p": "00000", "arm": "TV", "img_idx": 0},
            {"ev": "imp", "t": 0, "i": "A2", "p": "00000", "arm": "TV", "img_idx": 2},
            {"ev": "imp", "t": 0, "i": "A3", "p": "00000", "arm": "TV", "img_idx": None},
            {"ev": "imp", "t": 0, "i": "A4", "p": "00000", "arm": "T"}]
    ent, _, _ = _entry(demo_cfg_factory, tmp_path,
                       images={"root": str(root), "attached": 1}, rows=rows,
                       images_root=str(root), modality_arms=["T", "TV"])
    assert ent["tv_impressions"] == 3
    assert ent["tv_impressions_with_image"] == 2
    assert ent["attached"] == 1


def test_the_reason_reaches_the_report_and_is_never_counted_as_a_failure(
        demo_cfg_factory, tmp_path):
    """_invariant_report used to copy `reason` only onto skipped entries, which would have
    dropped the whole point of a warn-form check."""
    root = tmp_path / "imgs"
    root.mkdir()
    _, checks, _ = _entry(demo_cfg_factory, tmp_path,
                          images={"root": str(root), "attached": 0, "missing": 5},
                          images_root=str(root), modality_arms=["T", "TV"])
    entries, summary = _invariant_report(checks)
    ent = entries[KEY]
    assert ent["pass"] is True
    assert ent["reason"] and "zero attachments" in ent["reason"]
    assert ent["description"] == INVARIANTS[KEY]
    assert ent["missing"] == 5
    assert summary["failed"] == 0 and summary["all_passed"] is True
    assert KEY in entries and summary["total"] == len(entries)


# --- run_meta["images"] ---------------------------------------------------------------------
def _run_meta(cfg, state, tmp_path, checks=None):
    out = Path(tmp_path) / "reports"
    write_reports(out, state, cfg, _world_stub(), checks or {},
                  {"attempts": 0, "decision_failures": 0}, 0.1)
    with open(out / "run_meta.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_write_reports_writes_the_zero_form_when_state_has_no_images(
        demo_cfg_factory, tmp_path):
    """An older run, or a state built before card L2 lands: the block must still exist, with
    honest zeros, because the viewer's chip reads run_meta.images and an absent block is
    indistinguishable from a run that attached nothing."""
    cfg = _cfg(demo_cfg_factory, tmp_path, images_root=None, image_pick="first")
    meta = _run_meta(cfg, _state(), tmp_path)
    assert meta["images"] == {"root": None, "policy": "first", "attached": 0,
                              "missing": 0, "sha_mismatch": 0}


def test_the_zero_form_still_reports_a_configured_root_and_policy(demo_cfg_factory, tmp_path):
    """The zero form is built from the config, so "a library was configured and nothing was
    attached" stays distinguishable from "no library was configured"."""
    root = tmp_path / "imgs"
    root.mkdir()
    cfg = _cfg(demo_cfg_factory, tmp_path, images_root=str(root), image_pick="random")
    meta = _run_meta(cfg, _state(), tmp_path)
    assert meta["images"]["root"] == str(root)
    assert meta["images"]["policy"] == "random"
    assert meta["images"]["attached"] == 0


def test_write_reports_copies_state_images_verbatim(demo_cfg_factory, tmp_path):
    cfg = _cfg(demo_cfg_factory, tmp_path, images_root="/store/imgs", image_pick="first")
    images = {"root": "/store/imgs", "policy": "random", "attached": 7, "missing": 1,
              "sha_mismatch": 2}
    meta = _run_meta(cfg, _state(images=dict(images)), tmp_path)
    assert meta["images"] == images                # config values must not overwrite the tally


def test_the_report_pair_agrees_on_one_run(demo_cfg_factory, tmp_path):
    """End to end over both halves of this card: the same state drives the invariant entry and
    run_meta, and the run's status stays ok with zero attachments."""
    root = tmp_path / "imgs"
    root.mkdir()
    cfg = _cfg(demo_cfg_factory, tmp_path, images_root=str(root), modality_arms=["T", "TV"])
    state = _state(images={"root": str(root), "policy": "first", "attached": 0, "missing": 4,
                           "sha_mismatch": 0})
    checks, core = check_invariants(state, _events(tmp_path), cfg)
    meta = _run_meta(cfg, state, tmp_path, checks=checks)
    assert core is True
    assert meta["status"] == "ok"
    assert meta["images"]["missing"] == 4
    with open(Path(tmp_path) / "reports" / "invariants_report.json", encoding="utf-8") as fh:
        rep = json.load(fh)
    assert rep["checks"][KEY]["pass"] is True
    assert rep["summary"]["failed"] == 0


# --- image_pool_summary ---------------------------------------------------------------------
def _pool_files(tmp_path, *names):
    root = Path(tmp_path) / "imgs"
    root.mkdir(exist_ok=True)
    for n in names:
        (root / n).write_bytes(b"not a real jpeg")   # existence only: no byte is ever read
    return root


def test_pool_summary_reads_all_three_shapes_of_image_ids(tmp_path):
    """Contract 2.5: the pool holds real lists, JSON strings and single-quoted Python reprs of
    the same column, because more than one script wrote it."""
    root = _pool_files(tmp_path, "one_0.jpg", "two_0.jpg")
    pool = {"orgA": [{"image_ids": ["one_0.jpg"], "image_sha256": [SHA_A]},
                     {"image_ids": '["two_0.jpg"]', "image_sha256": json.dumps([SHA_B])}],
            "orgB": [{"image_ids": "['gone_0.jpg']", "image_sha256": "['%s']" % SHA_A}]}
    s = image_pool_summary(pool, str(root))
    assert s["notes"] == 3 and s["notes_with_refs"] == 3
    assert s["refs"] == 3
    assert s["resolvable"] == 2                    # gone_0.jpg is referenced but absent
    assert s["missing"] == 1
    assert s["refs"] == s["resolvable"] + s["missing"]
    assert s["root"] == str(root)


def test_pool_summary_counts_distinct_ids_and_flags_missing_digests(tmp_path):
    """Two notes may reference the same file (counted once: the number worth printing is how
    many FILES the pool needs), and a reference with no parallel digest can never be attached,
    because attachment requires an exact sha256 match."""
    root = _pool_files(tmp_path, "shared_0.jpg", "nodigest_0.jpg")
    pool = [{"image_ids": ["shared_0.jpg"], "image_sha256": [SHA_A]},
            {"image_ids": ["shared_0.jpg"], "image_sha256": [SHA_A]},
            {"image_ids": ["nodigest_0.jpg"]},
            {"image_ids": ["bad_0.jpg"], "image_sha256": ["deadbeef"]},
            {"note_id": "text only"}]
    s = image_pool_summary(pool, str(root))        # a flat list of rows, not the World shape
    assert s["notes"] == 5 and s["notes_with_refs"] == 4
    assert s["refs"] == 3                          # shared_0 counted once
    assert s["resolvable"] == 2
    assert s["refs_without_digest"] == 2           # the absent one and the 8-char one


def test_pool_summary_with_no_root_resolves_nothing_and_says_so(tmp_path):
    pool = {"orgA": [{"image_ids": ["one_0.jpg"], "image_sha256": [SHA_A]}]}
    for empty in (None, ""):
        s = image_pool_summary(pool, empty)
        assert s["root"] is None
        assert s["refs"] == 1 and s["resolvable"] == 0 and s["missing"] == 1


@pytest.mark.parametrize("pool", [None, 17, "", {}, [], {"orgA": []}, {"orgA": None}])
def test_pool_summary_never_raises_on_junk(pool):
    """A malformed pool row must cost that note its image, never the run."""
    s = image_pool_summary(pool, None)
    assert s["refs"] == 0 and s["resolvable"] == 0 and s["missing"] == 0


def test_pool_summary_reads_no_image_bytes(tmp_path, monkeypatch):
    """Guards the promise in the docstring: existence is all a summary claims. If a future edit
    starts hashing files here, a 400-note pool would read every image on every run start."""
    root = _pool_files(tmp_path, "one_0.jpg")

    def no_open(*a, **kw):
        raise AssertionError("image_pool_summary must not open image files")

    monkeypatch.setattr(os, "open", no_open)
    monkeypatch.setattr("builtins.open", no_open)
    s = image_pool_summary({"orgA": [{"image_ids": ["one_0.jpg"], "image_sha256": [SHA_A]}]},
                           str(root))
    assert s["resolvable"] == 1

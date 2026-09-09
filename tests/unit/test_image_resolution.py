"""Tests for digest-verified TV-arm image attachment.

These tests cover four properties: the pool's image keys are
parsed, the attached bytes are verified against the digest the pool ships, only TV
attaches, and a run with no images_root still produces exactly the text-only bytes.

No image library is needed anywhere here -- only the digest matters, so the fixtures
write raw bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import random

import pytest

from flowmirror.agents.prompt import build_decision_messages
from flowmirror.engine import loop as L
from flowmirror.io.hashing import rng_seed_from, sha256_file

from tests.conftest import build_demo_cfg


# --------------------------------------------------------------------------- helpers

def _write(path, payload: bytes):
    with open(path, "wb") as fh:
        fh.write(payload)
    return hashlib.sha256(payload).hexdigest()


def _note(ids, shas, note_id="n1"):
    return {"note_id": note_id, "image_ids": ids, "image_sha256": shas,
            "title": "标题", "caption": "正文", "ocr_text": "ocr"}


def _rng(*parts):
    return random.Random(rng_seed_from("tag", *parts))


def _card(note, arm, image):
    world = type("W", (), {"funds": {}})()
    post = {"post_id": "P1", "org": "O", "note": note["note_id"], "img": True, "code": None}
    import datetime
    return L._feed_card(world, post, {note["note_id"]: note}, arm, {}, {}, {},
                        datetime.date(2025, 10, 1), {}, image=image)


_VIEW = {"persona_card_zh_rich": "人设", "memory": [], "last_reflection": "",
         "market_view": 3, "risk_mood": 3, "c_class": "C3", "cash": 1000.0,
         "holdings": [], "last_trade": "", "declined_confirms": [], "familiarity": {},
         "guba": {}, "direct": [], "beliefs": []}
_CFG = {"social": True,
        "channels": {"feed": True, "experience": True, "social": True, "news": True}}


def _parts(messages):
    return [p["type"] for m in messages
            for p in (m["content"] if isinstance(m["content"], list) else [])]


# ------------------------------------------------------- the pool's three id shapes

@pytest.mark.parametrize("raw, expected", [
    (["a.jpg", "b.jpg"], ["a.jpg", "b.jpg"]),          # a real JSON list
    ('["a.jpg", "b.jpg"]', ["a.jpg", "b.jpg"]),        # a JSON-encoded string
    ("['a.jpg', 'b.jpg']", ["a.jpg", "b.jpg"]),        # an older pandas repr export
    ("a.jpg", ["a.jpg"]),                              # a bare single filename
    (None, []),
    ("", []),
    ("[not json at all", []),                          # unparseable is "no image", not a crash
])
def test_parse_id_list_accepts_every_shape_in_the_corpus(raw, expected):
    assert L._parse_id_list(raw) == expected


# ------------------------------------------------------------------- resolution

def test_tv_card_attaches_the_file_and_carries_its_real_digest(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    d0 = _write(root / "a.jpg", b"\xff\xd8pretend-jpeg-a")
    d1 = _write(root / "b.jpg", b"\xff\xd8pretend-jpeg-b")
    note = _note(["a.jpg", "b.jpg"], [d0, d1])

    path, sha, idx, status = L._resolve_tv_image(note, str(root), "first", _rng("x"))
    assert status is None and idx == 0
    assert os.path.basename(path) == "a.jpg"
    # the digest is the FILE's own, not a hash of the path string as it used to be
    assert sha == d0 == sha256_file(path)
    assert sha != L.sha256_text(str(path))

    card = _card(note, "TV", (path, sha, idx, status))
    assert card["image_path"] == path and card["image_sha"] == d0


def test_tampered_bytes_attach_nothing_and_report_a_mismatch(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    good = _write(root / "a.jpg", b"\xff\xd8original")
    _write(root / "a.jpg", b"\xff\xd8tampered")          # same name, different content
    note = _note(["a.jpg"], [good])

    path, sha, idx, status = L._resolve_tv_image(note, str(root), "first", _rng("x"))
    assert status == "image_sha_mismatch"
    assert path is None and sha is None and idx == 0


def test_missing_file_and_missing_reference_are_distinguished(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    absent = L._resolve_tv_image(_note(["gone.jpg"], ["0" * 64]), str(root), "first", _rng("x"))
    assert absent[3] == "image_missing" and absent[0] is None
    none_at_all = L._resolve_tv_image(_note([], []), str(root), "first", _rng("x"))
    assert none_at_all[3] == "no_images" and none_at_all[2] is None


def test_random_pick_is_reproducible_for_the_same_impression(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    ids, shas = [], []
    for i in range(6):
        ids.append(f"{i}.jpg")
        shas.append(_write(root / f"{i}.jpg", b"\xff\xd8" + bytes([i]) * 40))
    note = _note(ids, shas)

    # The engine derives this stream from (run_tag, "img", agent, day, post) so a
    # replay of the same impression redraws the same index -- and so that drawing it
    # never consumes from inv.rng, which would shift every later decision and make a
    # TV agent diverge from a T agent for reasons unrelated to the picture.
    first = [L._resolve_tv_image(note, str(root), "random",
                                 _rng("img", "inv_00001", d, "P1"))[2] for d in range(8)]
    again = [L._resolve_tv_image(note, str(root), "random",
                                 _rng("img", "inv_00001", d, "P1"))[2] for d in range(8)]
    assert first == again
    assert len(set(first)) > 1, "a random policy that never varies is not random"
    # a different agent on the same day draws independently
    other = [L._resolve_tv_image(note, str(root), "random",
                                 _rng("img", "inv_00002", d, "P1"))[2] for d in range(8)]
    assert other != first


# ------------------------------------------------------------- only TV sees pixels

def test_only_the_tv_arm_carries_an_image_through_to_the_prompt(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    digest = _write(root / "a.jpg", b"\xff\xd8pretend-jpeg")
    note = _note(["a.jpg"], [digest])
    resolved = L._resolve_tv_image(note, str(root), "first", _rng("x"))

    tv = _card(note, "TV", resolved)
    # T and TC are built with image=None: the engine never enters the resolver for them
    t = _card(note, "T", None)
    tc = _card(note, "TC", None)

    assert t["image_path"] is None and t["image_sha"] is None
    assert tc["image_path"] is None and tc["image_sha"] is None

    m_tv, sha_tv, shas_tv, _ = build_decision_messages(_VIEW, [tv], _CFG)
    m_t, sha_t, shas_t, _ = build_decision_messages(_VIEW, [t], _CFG)

    assert "image_url" in _parts(m_tv), "the TV prompt must carry pixels"
    assert "image_url" not in _parts(m_t)
    assert shas_tv == [digest] and shas_t == []
    # the whole point: the two arms are no longer the same stimulus
    assert sha_tv != sha_t


# ------------------------------------------------ text-only runs are untouched

def test_text_only_run_records_zeros_and_attaches_nothing(tmp_path):
    """With no images_root every path must produce the pre-card bytes.

    This is the constraint that keeps the three shipped demos comparable to their
    published hashes: no new event field with a value, no changed card key, and no
    extra RNG draw, so the only reason a demo sha moves is a decided mechanism change
    elsewhere -- never this card.
    """
    cfg = build_demo_cfg(tmp_path / "run", agents=10, days=3)
    # The demo's own two arms (T/TV) -- a 10-agent cohort cannot host three balanced
    # arms (best split 4/3/3, worst share 0.067 off 1/3, past the 0.01 tolerance), and
    # arm count is irrelevant here: this test is about the absence of an images_root.
    assert not cfg.get("images_root")

    rc = L.run_simulation(cfg, L.RuntimeOpts())
    assert rc == 0

    meta = json.loads((tmp_path / "run" / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["images"] == {"root": None, "policy": "first",
                              "attached": 0, "missing": 0, "sha_mismatch": 0}

    rows = [json.loads(l) for l in
            (tmp_path / "run" / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]
    imps = [r for r in rows if r.get("ev") == "imp"]
    assert imps, "the fixture must produce impressions"
    # img_idx is passed only when a picture was really attached, so it is absent here
    assert all("img_idx" not in r for r in imps)
    assert {r["arm"] for r in imps} <= {"T", "TC", "TV"}


def test_the_images_invariant_reports_rather_than_gating(tmp_path):
    """m_tv_arm_carries_images is a REPORT, not a gate (global rule: gates belong at
    irreversible, cross-system, security or release boundaries -- a simulation run is
    none of those). A text-only run carrying a TV arm must still exit 0."""
    cfg = build_demo_cfg(tmp_path / "run", agents=10, days=3)

    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0
    report = json.loads((tmp_path / "run" / "invariants_report.json").read_text(encoding="utf-8"))
    entries = report.get("entries") if isinstance(report, dict) else None
    blob = json.dumps(report, ensure_ascii=False)
    assert "m_tv_arm_carries_images" in blob
    if isinstance(entries, dict) and "m_tv_arm_carries_images" in entries:
        assert entries["m_tv_arm_carries_images"].get("pass") is not False

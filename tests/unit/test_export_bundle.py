"""Card EXP1: the presentation bundle, and the three things it must never leak.

`web/app.js` has referenced `flowmirror export-bundle` since the viewer was written and
the command did not exist -- a documented surface with nothing behind it.

The prohibitions below are not hypothetical. This project keeps its images out of the
repository, its keys out of git, and its absolute local paths out of anything
publishable; a bundle is the one artefact intended to be shared, so it is exactly where
such a leak would land.
"""
from __future__ import annotations

import json
import re

import pytest

from flowmirror.analysis.export_bundle import export_bundle
from flowmirror.cli import main as cli_main
from flowmirror.engine import loop as L

from tests.conftest import build_demo_cfg


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("bundle_run") / "run"
    cfg = build_demo_cfg(out, agents=10, days=3)
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0
    return out


@pytest.fixture(scope="module")
def bundle(run_dir, tmp_path_factory):
    dest = tmp_path_factory.mktemp("bundle_out")
    export_bundle(str(run_dir), out_dir=str(dest))
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in dest.glob("*.json")}


def test_all_four_files_are_produced(bundle):
    assert set(bundle) == {"bundle", "posts", "agents", "events"}


def test_bundle_carries_run_meta_images_and_invariants(bundle):
    b = bundle["bundle"]
    # the images block the engine now writes: attached is the number that answers
    # "did pixels reach the agents", which is why the viewer's warning chip can finally
    # read something real instead of showing unconditionally
    assert isinstance(b.get("images"), dict)
    assert set(b["images"]) >= {"root", "policy", "attached", "missing", "sha_mismatch"}
    inv = b.get("invariants") or {}
    assert set(inv) >= {"summary", "entries"}
    assert inv["summary"]["total"] >= 12
    assert isinstance(b.get("per_day"), (list, dict))
    assert isinstance(b.get("truncation"), dict)
    assert b["truncation"].get("rule"), "a truncation rule must be stated even when unused"


_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/])|(?:^/(?:home|Users|mnt|opt|var)/)")
# An API key is a long run of base64-ish characters mixing BOTH cases with digits.
# Requiring all three keeps snake_case identifiers out of the net: the first draft
# of this check flagged the invariant name j_displayed_comment_matches_prev_day.
_KEYISH = re.compile(r"\b(?=[A-Za-z0-9_\-]{32,}\b)(?=[^\s]*[a-z])"
                     r"(?=[^\s]*[A-Z])(?=[^\s]*\d)[A-Za-z0-9_\-]{32,}\b")
_ALLOWED_LONG = re.compile(r"^[0-9a-f]{40,64}$")     # sha1/sha256 digests are fine


def _walk_strings(node, path="$"):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{path}[{i}]")


def test_no_absolute_filesystem_path_reaches_the_bundle(bundle):
    hits = []
    for name, doc in bundle.items():
        for path, s in _walk_strings(doc):
            # images.root is the operator's own machine-local config, echoed back for
            # provenance; the demo run leaves it null, which is what we assert.
            if path.endswith("images.root"):
                assert s is None or name == "bundle"
                continue
            if _ABS_PATH.search(s):
                hits.append((name, path, s[:90]))
    assert not hits, f"absolute paths leaked: {hits[:5]}"


def test_no_key_shaped_string_reaches_the_bundle(bundle):
    hits = []
    for name, doc in bundle.items():
        for path, s in _walk_strings(doc):
            for tok in _KEYISH.findall(s):
                if not _ALLOWED_LONG.match(tok):
                    hits.append((name, path, tok[:40]))
    assert not hits, f"key-shaped strings leaked: {hits[:5]}"


def test_no_image_bytes_reach_the_bundle(bundle):
    for name, doc in bundle.items():
        for path, s in _walk_strings(doc):
            assert "base64," not in s and not s.startswith("data:image"), \
                f"image bytes at {name}:{path}"


def test_posts_carry_one_text_per_arm_and_only_a_digest_for_the_image(bundle):
    doc = bundle["posts"]
    # the exporter documents itself: _what / _definitions / arms sit beside the data
    assert doc.get("_what") and doc.get("_definitions")
    items = doc["posts"]
    seen_arms = set()
    for post in items:
        arms = post.get("arms") or {}
        assert arms, "a post with no per-arm slot is useless to the viewer"
        seen_arms |= set(arms)
        for arm, slot in arms.items():
            # rendered by the engine's own _feed_card -> render_card, so the bundle
            # cannot drift from what the agents were actually shown
            assert slot.get("text"), f"post {post['post_id']} arm {arm} rendered nothing"
            assert slot.get("chars") == len(slot["text"])
        img = post.get("image") or {}
        # digests and indices only, by construction
        assert "path" not in img and "bytes" not in img, "only a digest may travel"
        assert set(img) <= {"n_images", "sha", "shown_idx", "sha_shown",
                            "attached_impressions"}
    assert seen_arms, "no arms rendered"
    # the demo's own arms; a two-arm run must not invent a third column
    assert seen_arms <= {"T", "TC", "TV"}
    assert seen_arms == set(doc["arms"]), "the declared arm list must match the data"


def test_agents_carry_opening_state_and_a_per_day_timeline(bundle):
    doc = bundle["agents"]
    assert doc.get("_what") and doc.get("_definitions")
    items = doc["agents"]
    assert items
    a = items[0]
    assert {"cell", "arm"} <= set(a)
    assert "cash0" in a and "hold0" in a, "opening state is what makes a run readable"
    by_day = a.get("by_day") or {}
    assert by_day, "an agent with no timeline cannot be inspected"


def test_a_small_budget_truncates_visibly(run_dir, tmp_path):
    dest = tmp_path / "small"
    export_bundle(str(run_dir), out_dir=str(dest), max_bytes=20_000)
    b = json.loads((dest / "bundle.json").read_text(encoding="utf-8"))
    tr = b["truncation"]
    assert tr["applied"] is True
    assert tr.get("rule") and tr.get("dropped"), \
        "a truncation the reader cannot see is the same as a silent one"


def test_anonymise_orgs_removes_every_real_institution_name(run_dir, tmp_path):
    plain = tmp_path / "plain"
    anon = tmp_path / "anon"
    export_bundle(str(run_dir), out_dir=str(plain))
    export_bundle(str(run_dir), out_dir=str(anon), anonymise_orgs=True)

    real = set((json.loads((plain / "bundle.json").read_text(encoding="utf-8"))
                .get("cfg") or {}).get("orgs") or [])
    assert real, "the fixture must name real institutions"

    blob = "\n".join((anon / f).read_text(encoding="utf-8")
                     for f in ("bundle.json", "posts.json", "agents.json", "events.json"))
    leaked = sorted(o for o in real if o in blob)
    assert not leaked, f"double-blind submission would leak: {leaked}"


def test_the_cli_subcommand_exists_and_runs(run_dir, tmp_path, capsys):
    """web/app.js calls this exact command; a missing subcommand is a broken viewer."""
    rc = cli_main(["export-bundle", str(run_dir), "--out", str(tmp_path / "viacli")])
    assert rc == 0
    assert (tmp_path / "viacli" / "bundle.json").is_file()


def test_a_directory_with_no_event_log_fails_cleanly(tmp_path, capsys):
    rc = cli_main(["export-bundle", str(tmp_path)])
    assert rc == 1
    assert "event_log.jsonl" in capsys.readouterr().err

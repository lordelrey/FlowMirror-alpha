# -*- coding: utf-8 -*-
"""Card MOD1 / audit E2: the modality analyser must find I2 content in a REAL log.

`_is_i2` looked for `source == "I2"` or a post id with an "I2" prefix. The
engine emits neither: `imp.source` is a ranking source in
{follow, fit, trending, spill, random} (channels.feed.rank_feed) and post ids
are numeric, shaped {t:03d}{org_index}{slot} (world.publish_day, e.g. "00031").
The I2 tag lives on the `post` row's `ig` field -- the two-way intent GROUP
{"I2", "nonI2"} that invariant (f) ties to `intent == "I2"`. So on every real
event log the I2 impression set was empty and `click_rate` /
`subscribe_conversion` -- two pre-registered secondary endpoints -- came back
absent.

The failure was masked because `_synthetic_run` hand-planted ids in the
"I2-..." shape, so the module's own self-test exercised a path the engine never
takes. These tests pin both halves of the fix: the aggregation reads post.ig,
and the fixture emits engine-shaped rows.

Run with:  python -m pytest tests/unit/test_modality_i2_detection.py -q
"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flowmirror.analysis.common import load_events           # noqa: E402
from flowmirror.analysis.modality import _synthetic_run, analyze   # noqa: E402

# the engine's real imp.source domain -- "I2" is not and never was a member
REAL_SOURCES = ("follow", "fit", "trending", "spill", "random")


def _write_log(run_dir, rows, meta):
    """Write one <run_dir> the way analyze() expects to read it."""
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "event_log.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(run_dir, "run_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return run_dir


def _engine_shaped_rows():
    """A hand-built log in exactly the shape loop.py / world.py write.

    Two agents (one per arm), one day, four posts: two I2 and two nonI2 with
    NUMERIC ids and the intent group on post.ig. Every impression carries a real
    ranking source, so nothing here would be recognised as I2 by the legacy
    id/source heuristic. Each agent sees all four posts and clicks exactly one
    I2 post; the T agent also clicks a nonI2 post (which must NOT count) and
    subscribes.
    """
    posts = [("00100", "I2", "I2"), ("00101", "I2", "I2"),
             ("00110", "nonI2", "I1"), ("00111", "nonI2", "I3")]
    rows = []
    for pid, ig, intent in posts:
        rows.append({"ev": "post", "t": 1, "d": "2025-10-01", "org": "ORG1",
                     "p": pid, "intent": intent, "ig": ig, "fund": None,
                     "img": True})
    for i, arm in (("inv_00001", "T"), ("inv_00002", "TV")):
        for s, (pid, _ig, _intent) in enumerate(posts):
            rows.append({"ev": "imp", "t": 1, "d": "2025-10-01", "i": i, "p": pid,
                         "arm": arm, "slot": s, "source": REAL_SOURCES[s]})
        rows.append({"ev": "dec", "t": 1, "d": "2025-10-01", "i": i, "status": "ok",
                     "arm": arm, "prompt_sha": "0" * 64, "n_read": 4, "n_like": 1,
                     "n_save": 0, "n_follow": 0, "n_comment": 0, "aff_sum": 1.0})
    # T clicks one I2 post and one nonI2 post: only the first may count
    rows.append({"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "00100", "oc": "to_checkout"})
    rows.append({"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "00110", "oc": "to_checkout"})
    rows.append({"ev": "act", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "00100", "kind": "subscribe", "fund": "160630", "amt": 100.0,
                 "units": 100.0, "nav": 1.0, "fee": 0.12})
    rows.append({"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00002",
                 "p": "00101", "oc": "to_checkout"})
    meta = {"seed": 1, "run_tag": "e2fix",
            "arms": {"inv_00001": "T", "inv_00002": "TV"}}
    return rows, meta


class TestPostRowIsAuthoritative:
    def test_numeric_ids_and_real_source_still_yield_the_trade_endpoints(self, tmp_path):
        """The whole point of E2: engine-shaped rows must produce both endpoints."""
        rows, meta = _engine_shaped_rows()
        rd = _write_log(str(tmp_path / "engine_shaped"), rows, meta)
        res = analyze([rd], level="agent")
        pooled = res["runs"][0]["pooled"]
        # 2 I2 impressions per agent x 2 agents = 4; 1 counted I2 click each
        assert pooled["click_rate"] == pytest.approx(0.5), pooled
        # 1 subscribe over 2 counted I2 clicks
        assert pooled["subscribe_conversion"] == pytest.approx(0.5), pooled
        # both endpoints are measurable per arm, which is what the paper needs
        c = res["runs"][0]["contrasts"]["TV-T"]
        assert c["click_rate"] is not None, c
        assert c["click_rate"]["mean_hi"] == pytest.approx(0.5)
        assert c["click_rate"]["mean_lo"] == pytest.approx(0.5)
        assert c["subscribe_conversion"] is not None, c
        # T subscribed on its one I2 click, TV did not -> TV - T = -1.0
        assert c["subscribe_conversion"]["diff"] == pytest.approx(-1.0), c

    def test_click_on_a_noni2_post_is_not_counted(self, tmp_path):
        """The nonI2 click in the fixture must stay out of i2_clicks.

        If it leaked in, the T agent would show 2 clicks over 2 I2 impressions
        and pooled click_rate would be 0.75, not 0.5."""
        rows, meta = _engine_shaped_rows()
        rd = _write_log(str(tmp_path / "noni2_click"), rows, meta)
        res = analyze([rd], level="agent")
        assert res["runs"][0]["pooled"]["click_rate"] == pytest.approx(0.5)

    def test_post_ig_overrides_the_legacy_heuristic(self, tmp_path):
        """ig="nonI2" wins even when the id and source look I2 to _is_i2.

        A log that carries `ig` is authoritative: it says this post is not I2,
        so there is no I2 denominator and neither trade endpoint exists."""
        rows = [{"ev": "post", "t": 1, "d": "2025-10-01", "org": "ORG1",
                 "p": "I2-777", "intent": "I1", "ig": "nonI2", "fund": None,
                 "img": False},
                {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "I2-777", "arm": "T", "slot": 0, "source": "I2"},
                {"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "I2-777", "oc": "to_checkout"}]
        rd = _write_log(str(tmp_path / "ig_wins"), rows,
                        {"seed": 1, "arms": {"inv_00001": "T"}})
        pooled = analyze([rd], level="agent")["runs"][0]["pooled"]
        assert "click_rate" not in pooled, pooled
        assert "subscribe_conversion" not in pooled, pooled


class TestLegacyFallback:
    def test_log_without_post_rows_uses_the_id_prefix_heuristic(self, tmp_path):
        """Older run directories carry no post block; _is_i2 must still serve them."""
        rows = []
        for i in ("inv_00001", "inv_00002"):
            rows.append({"ev": "imp", "t": 1, "d": "2025-10-01", "i": i,
                         "p": "I2-000", "arm": "T", "slot": 0, "source": "I2"})
            rows.append({"ev": "imp", "t": 1, "d": "2025-10-01", "i": i,
                         "p": "X-001", "arm": "T", "slot": 1, "source": "fit"})
        rows.append({"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                     "p": "I2-000", "oc": "to_checkout"})
        rd = _write_log(str(tmp_path / "legacy_nopost"), rows,
                        {"seed": 1, "arms": {"inv_00001": "T", "inv_00002": "T"}})
        run = analyze([rd], level="agent")["runs"][0]
        # 1 I2 impression per agent (the X-001 row is not I2), 1 click overall
        assert run["pooled"]["click_rate"] == pytest.approx(0.5), run["pooled"]
        assert run["pooled"]["subscribe_conversion"] == pytest.approx(0.0)

    def test_post_rows_without_ig_also_fall_back(self, tmp_path):
        """A post block that predates `ig` is legacy too, not "no I2 content"."""
        rows = [{"ev": "post", "t": 1, "d": "2025-10-01", "org": "ORG1",
                 "p": "I2-000", "intent": "I2", "fund": None},
                {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "I2-000", "arm": "T", "slot": 0, "source": "fit"},
                {"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001",
                 "p": "I2-000", "oc": "to_checkout"}]
        rd = _write_log(str(tmp_path / "legacy_no_ig"), rows,
                        {"seed": 1, "arms": {"inv_00001": "T"}})
        pooled = analyze([rd], level="agent")["runs"][0]["pooled"]
        assert pooled["click_rate"] == pytest.approx(1.0), pooled


class TestFixtureExercisesTheRealPath:
    """The masked half: a fixture in a shape the engine never emits is no fixture."""

    def test_synthetic_run_emits_engine_shaped_post_rows(self, tmp_path):
        rd = str(tmp_path / "fixture")
        _synthetic_run(rd, 1)
        ev = load_events(rd)
        posts = ev["post"]
        assert posts, "the fixture must emit post rows -- the real engine does"
        assert all(str(r["p"]).isdigit() for r in posts), \
            "post ids are numeric ({t:03d}{org}{slot}), never 'I2-...'"
        igs = {r["ig"] for r in posts}
        assert igs == {"I2", "nonI2"}, igs      # both groups present, as in a real run
        # invariant (f): ig == "I2" exactly when intent == "I2"
        assert all((r["ig"] == "I2") == (r["intent"] == "I2") for r in posts)
        # nothing in the fixture may carry the shapes _is_i2 looks for
        assert all(r["source"] in REAL_SOURCES for r in ev["imp"]), \
            "imp.source must come from the engine's ranking-source domain"
        assert all(isinstance(r["slot"], int) for r in ev["imp"])
        assert not any(str(r["p"]).startswith("I2") for r in ev["imp"])

    def test_fixture_click_rate_flows_through_post_ig(self, tmp_path):
        """Delete the post rows and the fixture's I2 metrics disappear.

        This is the regression guard: it can only pass if the counting really
        goes through post.ig rather than the id/source heuristic."""
        rd = str(tmp_path / "fixture2")
        _synthetic_run(rd, 1)
        with_posts = analyze([rd], level="agent")["runs"][0]["pooled"]
        assert with_posts["click_rate"] == pytest.approx(0.2), with_posts
        path = os.path.join(rd, "event_log.jsonl")
        with open(path, encoding="utf-8") as fh:
            kept = [ln for ln in fh if json.loads(ln)["ev"] != "post"]
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        stripped = analyze([rd], level="agent")["runs"][0]["pooled"]
        assert "click_rate" not in stripped, stripped


class TestEndToEnd:
    def test_short_demo_run_reports_a_numeric_click_rate(self, tmp_path):
        """A real engine run must yield a number for at least one arm.

        This is the check the module never had: the self-test's fixture used to
        be the only log the analyser was ever pointed at, so nothing noticed
        that the real event log produced nothing. Fixture base is
        tests/conftest.build_demo_cfg (runs/demo_two_arm.json), whose inputs are
        all tracked in git, so this works on a clean clone with no keys.
        """
        from tests.conftest import build_demo_cfg, find_demo_run

        if find_demo_run() is None:
            pytest.skip("runs/demo_two_arm.json not found (set FLOWMIRROR_DATA_ROOT)")
        from flowmirror.engine.loop import run_simulation

        out = str(tmp_path / "demo")
        cfg = build_demo_cfg(out)
        assert run_simulation(cfg) == 0
        res = analyze([out], level="agent")
        run = res["runs"][0]
        # the engine really published I2 content and really showed it
        assert run["pooled"].get("click_rate") is not None, run["pooled"]
        per_arm = []
        for pair in run["contrasts"].values():
            cr = pair.get("click_rate")
            if cr is not None:
                per_arm.extend([cr["mean_hi"], cr["mean_lo"]])
        assert any(isinstance(v, float) for v in per_arm), run["contrasts"]

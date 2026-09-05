"""Pin flowmirror.channels.feed byte-for-byte to sim/feed.py (research repo).

The reference module is imported lazily from FLOWMIRROR_RESEARCH_ROOT
(default "D:/Desktop/ABM paper/fundmarket-sim"); every test skips with a
reason when that tree is absent.  All comparisons use identical,
freshly-built inputs on both sides (fresh random.Random(7) per call), so
any divergence is a migration bug, not noise.
"""

import os
import random
import sys
from pathlib import Path

import pytest

# Make the package importable no matter where pytest was started from.
_PKG_PARENT = Path(__file__).resolve().parents[2]
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

import flowmirror.channels.feed as fm_feed  # noqa: E402

RESEARCH_ROOT = Path(os.environ.get("FLOWMIRROR_RESEARCH_ROOT",
                                    "D:/Desktop/ABM paper/fundmarket-sim"))


def _sim_feed():
    """Import sim.feed from the research repo, or skip the calling test."""
    if not (RESEARCH_ROOT / "sim" / "feed.py").is_file():
        pytest.skip(f"sim/feed.py not found under {RESEARCH_ROOT}")
    if str(RESEARCH_ROOT) not in sys.path:
        sys.path.insert(0, str(RESEARCH_ROOT))
    import sim.feed as sim_feed
    return sim_feed


def _comments():
    rows = []
    spec = [
        ("p1", "bullish", 2), ("p1", "bearish", 1), ("p1", "watching", 0),
        ("p1", "bullish", 3), ("p2", "bearish", 2), ("p2", "bearish", 1),
        ("p3", "watching", 0),
    ]
    for i, (pid, stance, fam) in enumerate(spec):
        rows.append({"post_id": pid, "agent_id": "inv_%05d" % i, "stance": stance,
                     "text": "t", "fam_level": fam})
    return rows


def _feed_inputs():
    spec = [
        ("pA1", "orgA", "I2", 0.30), ("pA2", "orgA", "nonI2", 0.10), ("pA3", "orgA", "I2", 0.20),
        ("pB1", "orgB", "I2", 0.90), ("pB2", "orgB", "nonI2", 0.40), ("pB3", "orgB", "I2", 0.15),
        ("pC1", "orgC", "I2", 0.70), ("pC2", "orgC", "nonI2", 0.25), ("pC3", "orgC", "I2", 0.05),
        ("pD1", "orgD", "I2", 0.60), ("pD2", "orgD", "nonI2", 0.35), ("pD3", "orgD", "I2", 0.50),
    ]

    def _post(pid, org, ig):
        return {"post_id": pid, "org": org, "intent_group": ig,
                "note": {"note_id": pid}, "age_days": 1,
                "likes": 0, "saves": 0, "comments": 0}

    cands = [_post(p, o, g) for (p, o, g, _h) in spec]
    heat_prev = {p: h for (p, _o, _g, h) in spec}
    clim_prev = {"pB1": "bullish_majority", "pC1": "bearish_majority", "pD2": "mixed"}
    agent = {"follow": {"orgA", "orgC"},
             "trust": {"orgA": 0.6, "orgB": -0.2, "orgC": 0.1, "orgD": 0.0},
             "risk_latent": "tolerant", "core": "chaser"}
    cfg = {"slots": {"follow": 2, "fit": 2, "trending": 2},
           "w_trust": 1.0, "w_fit": 1.0, "w_heat": 1.0, "w_soc": 0.5, "eps": 0.05}
    return agent, cands, heat_prev, clim_prev, cfg


def test_hot_score_matches_reference():
    sf = _sim_feed()
    cases = [(120, 40, 12, 3), (0, 0, 0, 0), (10, 10, 10, 0),
             (None, None, None, None), (-3, -4, -5, -2), (999, 1, 500, 30),
             (5, 0, 0, None)]
    for args in cases:
        assert fm_feed.hot_score(*args) == sf.hot_score(*args)


def test_climate_for_matches_reference_with_and_without_weights():
    sf = _sim_feed()
    cmts = _comments()
    weights = {"inv_00000": 1.0, "inv_00001": 3.0, "inv_00002": 0.5,
               "inv_00003": 2.0, "inv_00004": 1.0, "inv_00005": 0.25,
               "inv_00006": 4.0}
    for pid in ("p1", "p2", "p3", "p9"):
        for w in (None, weights):
            assert fm_feed.climate_for(pid, cmts, weights=w) == sf.climate_for(pid, cmts, weights=w)


def test_top_comments_matches_reference():
    sf = _sim_feed()
    cmts = _comments()
    for k in (0, 1, 3, 10):
        assert fm_feed.top_comments("p1", cmts, k=k) == sf.top_comments("p1", cmts, k=k)
    assert (fm_feed.top_comments("p1", cmts, k=3, weights={"inv_00000": 9.0})
            == sf.top_comments("p1", cmts, k=3, weights={"inv_00000": 9.0}))
    assert fm_feed.top_comments("p9", cmts, k=3) == sf.top_comments("p9", cmts, k=3)


def test_arm_for_agent_matches_reference():
    sf = _sim_feed()
    for tag in ("tag", "rt_alpha", "armbal_007"):
        for i in (0, 1, 39, 400, 12345):
            aid = "inv_%05d" % i
            assert fm_feed.arm_for_agent(tag, aid) == sf.arm_for_agent(tag, aid)
    # The exact pair named in the migration spec.
    assert fm_feed.arm_for_agent("tag", "inv_00001") == sf.arm_for_agent("tag", "inv_00001")


def test_check_arm_balance_matches_reference_on_400_agent_cohort():
    sf = _sim_feed()
    ids = ["inv_%05d" % i for i in range(400)]
    cells = {a: "cell_%02d" % (i % 36) for i, a in enumerate(ids)}
    for tag in ("armbal_000", "armbal_007", "rt_fixed"):
        arms = {a: fm_feed.arm_for_agent(tag, a) for a in ids}
        arms_ref = {a: sf.arm_for_agent(tag, a) for a in ids}
        assert arms == arms_ref
        assert fm_feed.check_arm_balance(arms, cells) == sf.check_arm_balance(arms_ref, cells)
    # A deliberately skewed assignment must fail identically on both sides.
    bad = {a: ("TV" if i < 300 else "T") for i, a in enumerate(ids)}
    assert fm_feed.check_arm_balance(bad, cells) == sf.check_arm_balance(bad, cells)


def test_rank_feed_matches_reference():
    sf = _sim_feed()
    agent, cands, heat_prev, clim_prev, cfg = _feed_inputs()
    for mode in ("three_source", "random"):
        # Fresh random.Random(7) per side: identical seed, identical stream.
        got = fm_feed.rank_feed(agent, cands, heat_prev, clim_prev, cfg,
                                random.Random(7), mode=mode)
        exp = sf.rank_feed(agent, cands, heat_prev, clim_prev, cfg,
                           random.Random(7), mode=mode)
        assert [(p["post_id"], s) for p, s in got] == [(p["post_id"], s) for p, s in exp]
        assert got == exp
    # Starved-source configuration (agent follows an org with no posts).
    agent_z = dict(agent)
    agent_z["follow"] = {"orgZ"}
    got = fm_feed.rank_feed(agent_z, cands, heat_prev, clim_prev, cfg, random.Random(7))
    exp = sf.rank_feed(agent_z, cands, heat_prev, clim_prev, cfg, random.Random(7))
    assert got == exp


def test_self_test_passes_like_the_original(capsys):
    # sim/feed.py signals self-test success by RETURNING A ZERO FAILURE
    # COUNT (main() maps it to exit code 0); mirror exactly that contract.
    assert fm_feed.self_test() == 0

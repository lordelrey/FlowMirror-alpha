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
    # climate_for DELIBERATELY diverges from the frozen reference from
    # 2026-09-07 on -- same shape as the check_arm_balance divergence below.
    # Decision 1 (docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07.md section 5) sets
    # the majority margin to 1/6; the reference hardcodes 1/3, twice as strict
    # as DECISIONS #6 asks for.  The assertion is kept rather than deleted and
    # split into the two halves that must still hold: parity of the
    # AGGREGATION (counts, the min_n floor, the weighting path, post scoping)
    # when v7 is handed the reference's own margin, and a named divergence at
    # the new default, so a revert to 1/3 fails here loudly.
    for pid in ("p1", "p2", "p3", "p9"):
        for w in (None, weights):
            assert (fm_feed.climate_for(pid, cmts, weights=w, margin=1.0 / 3.0)
                    == sf.climate_for(pid, cmts, weights=w))
    # p1 unweighted is 2 bullish / 1 bearish / 1 watching, d = +0.25: strictly
    # between the two margins, and the only mix in this fixture whose label
    # decision 1 moves.  The counts stay identical on both sides -- only the
    # thresholding changed.
    got_lab, got_cnt = fm_feed.climate_for("p1", cmts)
    ref_lab, ref_cnt = sf.climate_for("p1", cmts)
    assert (ref_lab, got_lab) == ("mixed", "bullish_majority")
    assert got_cnt == ref_cnt


def test_fit_bands_default_to_the_frozen_literals():
    """E13: fit()'s +/-0.25 and +0.15 became parameters; defaults must not move.

    The bands are now feed.fit_band_wide / feed.fit_band_narrow so a reviewer
    can read a run config and learn what the suitability prior did.  The
    defaults ARE the reference's literals, so every (risk, core, intent_group)
    combination must still equal sim/feed.py exactly -- that is the whole
    claim that surfacing the two keys moves no hash."""
    sf = _sim_feed()
    for ig in ("I2", "nonI2", None, "other"):
        for risk in ("tolerant", "fragile", "neutral", None):
            for core in ("chaser", "allocator", "other", None):
                st = {"risk_latent": risk, "core": core}
                post = {"intent_group": ig}
                assert fm_feed.fit(st, post) == sf.fit(st, post), (ig, risk, core)


def test_fit_custom_bands_shift_only_the_terms_they_name():
    # Halving both bands must move exactly the wide (risk-latency) and narrow
    # (core-type) terms: 0.5 + 0.125 + 0.075 = 0.7 for tolerant/chaser on I2,
    # 0.5 - 0.125 + 0.075 = 0.45 for fragile/chaser, and the nonI2 allocator
    # nudge takes the narrow band alone.  The [0,1] clamp still applies, so an
    # oversized band saturates instead of leaving the bounded range.
    tol_chaser = {"risk_latent": "tolerant", "core": "chaser"}
    frag_chaser = {"risk_latent": "fragile", "core": "chaser"}
    alloc = {"risk_latent": "fragile", "core": "allocator"}
    i2 = {"intent_group": "I2"}
    non = {"intent_group": "nonI2"}
    half = dict(fit_band_narrow=0.075, fit_band_wide=0.125)
    assert fm_feed.fit(tol_chaser, i2, **half) == pytest.approx(0.7, abs=1e-12)
    assert fm_feed.fit(frag_chaser, i2, **half) == pytest.approx(0.45, abs=1e-12)
    assert fm_feed.fit(alloc, non, **half) == pytest.approx(0.575, abs=1e-12)
    # Zero bands collapse the prior to the flat 0.5 for every combination.
    zero = dict(fit_band_narrow=0.0, fit_band_wide=0.0)
    assert fm_feed.fit(tol_chaser, i2, **zero) == 0.5
    assert fm_feed.fit(alloc, non, **zero) == 0.5
    # Clamp: a wide band past 0.5 cannot push the prior outside [0, 1].
    big = dict(fit_band_narrow=0.4, fit_band_wide=0.9)
    assert fm_feed.fit(tol_chaser, i2, **big) == 1.0
    assert fm_feed.fit(frag_chaser, i2, **big) == 0.0


def test_rank_feed_passes_the_configured_fit_bands_through():
    """The keys only matter if rank_feed actually forwards them.

    rank_feed receives cfg["feed"], so cfg["fit_band_*"] IS feed.fit_band_*.
    Setting w_fit high and eps to zero makes the fit term the sole ordering
    signal, so zeroed bands (a flat 0.5 prior for every post) must produce a
    different fit-stage pick order than the default bands do."""
    agent, cands, heat_prev, clim_prev, cfg = _feed_inputs()
    flat = dict(cfg, w_trust=0.0, w_heat=0.0, w_soc=0.0, w_fit=10.0, eps=0.0)
    with_bands = fm_feed.rank_feed(agent, cands, heat_prev, clim_prev,
                                   flat, random.Random(7))
    zeroed = fm_feed.rank_feed(agent, cands, heat_prev, clim_prev,
                               dict(flat, fit_band_narrow=0.0, fit_band_wide=0.0),
                               random.Random(7))
    assert ([p["post_id"] for p, _ in with_bands]
            != [p["post_id"] for p, _ in zeroed])


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
        # The per-agent draw itself is still bit-identical to the frozen reference.
        assert arms == arms_ref
        # check_arm_balance DELIBERATELY diverges from the reference from 2026-09-06 on.
        # The reference tolerance (|share - 1/k| <= 0.03 overall, <= 0.05 per cell) is not
        # satisfiable by an independent coin at n=400 (35% of run tags failed it) and is
        # mathematically impossible for cells of size 2. v7 checks what the pre-registered
        # stratified block assignment can actually deliver, so it is STRICTER: it rejects
        # the reference's own unbalanced draw. Parity for the pre-registered path is
        # covered by tests/unit/test_arm_balance.py against feed.assign_agent_arms.
        ok_v7, rep_v7 = fm_feed.check_arm_balance(arms, cells)
        ok_ref, _rep_ref = sf.check_arm_balance(arms_ref, cells)
        assert ok_ref is True and ok_v7 is False
        assert rep_v7["n_agents"] == 400
    # A deliberately skewed assignment must fail on both sides (the shared floor).
    bad = {a: ("TV" if i < 300 else "T") for i, a in enumerate(ids)}
    assert fm_feed.check_arm_balance(bad, cells)[0] is False
    assert sf.check_arm_balance(bad, cells)[0] is False


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

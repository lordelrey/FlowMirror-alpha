"""Tests for the climate majority margin and the fit prior's bands.

Two parameters that the engine used to hardcode:

  * feed.climate_for uses a configurable majority margin, defaulting to 1/6,
    so a sensitivity sweep changes a
    config value rather than this module.  Every run with comment traffic
    moves its event-log sha because of it.
  * fit()'s +/-0.25 risk-latency swing and +0.15 core-type nudge
    are configurable as
    fit_band_wide / fit_band_narrow, defaulted to exactly those literals and
    threaded from cfg by rank_feed, so nothing changes until someone sets them.

Every test here is on pure functions of their arguments -- feed.py does no
file I/O and reads no config itself -- so the suite's build_demo_cfg fixture
rule does not apply: there is no run to construct.  The one config-shaped
assertion (the code default agreeing with config/engine_defaults.yaml) reads
DEFAULT_CONFIG rather than building a run, because a code default that has
drifted from the config default is the exact root-cause pattern this round
exists to close.
"""
from __future__ import annotations

import inspect

import pytest

from flowmirror.channels.feed import climate_for, fit

# Two margins used to exercise the threshold boundary.
OLD_MARGIN = 1.0 / 3.0
DEFAULT_MARGIN = 1.0 / 6.0


def _cmts(stances, pid="p1"):
    """Comment rows in this channel's long-key shape (post_id/agent_id)."""
    return [{"post_id": pid, "agent_id": "inv_%05d" % i, "stance": s,
             "text": "t", "fam_level": 0}
            for i, s in enumerate(stances)]


def _mix(bullish, bearish, watching, pid="p1"):
    return _cmts(["bullish"] * bullish + ["bearish"] * bearish
                 + ["watching"] * watching, pid=pid)


# --- FEED1: the margin ------------------------------------------------------

def test_the_default_margin_is_one_sixth():
    """The function default matches the configured public default."""
    got = inspect.signature(climate_for).parameters["margin"].default
    assert got == DEFAULT_MARGIN
    assert got == pytest.approx(1.0 / 6.0, abs=0.0)


def test_the_code_default_agrees_with_the_config_default():
    """The code default and YAML default must stay aligned."""
    from flowmirror.engine.world import DEFAULT_CONFIG
    assert (DEFAULT_CONFIG["feed"]["climate_margin"]
            == inspect.signature(climate_for).parameters["margin"].default)


@pytest.mark.parametrize("margin", [OLD_MARGIN, DEFAULT_MARGIN])
def test_the_mix_exactly_on_the_default_margin_stays_mixed(margin):
    """3 bullish / 2 bearish / 1 watching is d == 1/6, and `d > margin` is STRICT.

    d is (3 - 2) / 6, exactly the same float as 1.0 / 6.0, so the strict
    comparison rejects it at the default margin as well as at 1/3."""
    cmts = _mix(3, 2, 1)
    label, counts = climate_for("p1", cmts, margin=margin)
    d = (counts["bullish"] - counts["bearish"]) / 6.0
    # Stated as an exact float identity: this is the load-bearing fact.
    assert d == DEFAULT_MARGIN
    assert label == "mixed"


@pytest.mark.parametrize("margin,expected", [
    (OLD_MARGIN, "mixed"),
    (DEFAULT_MARGIN, "bullish_majority"),
])
def test_a_mix_strictly_between_the_two_margins_changes_label(margin, expected):
    """4 bullish / 2 bearish / 2 watching is d = 0.25, strictly between 1/6 and 1/3.

    This is mixed at 1/3 and a majority at 1/6."""
    label, counts = climate_for("p1", _mix(4, 2, 2), margin=margin)
    assert (counts["bullish"] - counts["bearish"]) / 8.0 == 0.25
    assert label == expected


@pytest.mark.parametrize("margin", [OLD_MARGIN, DEFAULT_MARGIN])
def test_the_margin_is_symmetric_across_both_branches(margin):
    """Mirroring the stances must mirror the label at either threshold."""
    bull = climate_for("p1", _mix(4, 2, 2), margin=margin)[0]
    bear = climate_for("p1", _mix(2, 4, 2), margin=margin)[0]
    mirror = {"bullish_majority": "bearish_majority",
              "bearish_majority": "bullish_majority", "mixed": "mixed"}
    assert mirror[bull] == bear


@pytest.mark.parametrize("margin", [OLD_MARGIN, DEFAULT_MARGIN])
def test_min_n_floor_is_unchanged_by_the_margin(margin):
    """The floor stays frozen at 4: thin threads must not manufacture proof.

    Below the floor the label is no_signal WHATEVER the margin, including the
    3-comment all-bullish thread whose d = 1.0 clears every threshold."""
    for n in range(0, 4):
        cmts = _mix(n, 0, 0)
        label, counts = climate_for("p1", cmts, margin=margin)
        assert label == "no_signal", (n, counts)
    # And the same thread crosses into a label at exactly 4 comments.
    assert climate_for("p1", _mix(4, 0, 0), margin=margin)[0] == "bullish_majority"


@pytest.mark.parametrize("margin", [OLD_MARGIN, DEFAULT_MARGIN])
def test_all_ones_weights_equal_the_unweighted_result_exactly(margin):
    """Under proportional sampling strat_weight ~= 1.

    The weighted path must then be the numerical identity -- label AND counts,
    since 3.0 == 3 in Python -- at any margin. Exercised on the straddling mix so the
    comparison happens on a thread whose label the margin actually decides."""
    cmts = _mix(4, 2, 2)
    ones = {c["agent_id"]: 1.0 for c in cmts}
    assert (climate_for("p1", cmts, weights=ones, margin=margin)
            == climate_for("p1", cmts, margin=margin))
    # Weighting is not a no-op in general: one bearish commenter at weight 9.0
    # makes the weighted counts 4.0 vs 10.0, d = -0.375, past either margin in
    # the other direction.  Deliberately not a boundary value -- the boundary
    # itself is pinned above.
    heavy = dict(ones)
    heavy["inv_00004"] = 9.0          # index 4 is the first bearish row
    assert climate_for("p1", cmts, weights=heavy, margin=margin)[0] == "bearish_majority"


def test_margin_stays_a_pure_threshold_and_never_touches_the_counts():
    """Only the labelling reads the margin; the aggregation must be identical."""
    cmts = _mix(4, 2, 2)
    _, strict = climate_for("p1", cmts, margin=0.99)
    _, loose = climate_for("p1", cmts, margin=0.0)
    assert strict == loose == {"bullish": 4, "bearish": 2, "watching": 2}


def test_other_posts_comments_are_still_ignored_at_the_new_margin():
    cmts = _mix(4, 2, 2, pid="p1") + _mix(0, 8, 0, pid="p2")
    assert climate_for("p1", cmts)[0] == "bullish_majority"
    assert climate_for("p2", cmts)[0] == "bearish_majority"


# --- FEED2: the fit bands ---------------------------------------------------

# The three values feed.self_test() has asserted since v1.3.  Restating them
# here is the point: the defaults must reproduce them to the bit, or surfacing
# the two keys would silently move every ranked feed.
FROZEN_FIT_CASES = [
    ({"risk_latent": "tolerant", "core": "chaser"}, {"intent_group": "I2"}, 0.9),
    ({"risk_latent": "fragile", "core": "chaser"}, {"intent_group": "I2"}, 0.4),
    ({"risk_latent": "fragile", "core": "allocator"}, {"intent_group": "nonI2"}, 0.65),
]


@pytest.mark.parametrize("state,post,expected", FROZEN_FIT_CASES)
def test_default_bands_reproduce_the_frozen_self_test_values(state, post, expected):
    assert fit(state, post) == expected


def test_the_band_defaults_are_todays_literals():
    """Neither key is in run.schema.json yet, so the defaults are the only source.

    config/schemas/run.schema.json's feed block sets additionalProperties:
    false, so a config that names either key is rejected until a schema entry
    lands (reported as a cross-lane dependency).  Until then every run takes
    these defaults, and they must equal the literals fit() used to carry."""
    params = inspect.signature(fit).parameters
    assert params["fit_band_narrow"].default == 0.15
    assert params["fit_band_wide"].default == 0.25


def test_custom_bands_shift_the_prior_by_exactly_those_bands():
    """The wide band is the risk-latency swing, the narrow one the core nudge."""
    i2, non = {"intent_group": "I2"}, {"intent_group": "nonI2"}
    tol_chaser = {"risk_latent": "tolerant", "core": "chaser"}
    frag_chaser = {"risk_latent": "fragile", "core": "chaser"}
    frag_alloc = {"risk_latent": "fragile", "core": "allocator"}
    bands = dict(fit_band_narrow=0.05, fit_band_wide=0.2)
    # 0.5 + wide + narrow / 0.5 - wide + narrow / 0.5 + narrow.
    assert fit(tol_chaser, i2, **bands) == pytest.approx(0.75, abs=1e-12)
    assert fit(frag_chaser, i2, **bands) == pytest.approx(0.35, abs=1e-12)
    assert fit(frag_alloc, non, **bands) == pytest.approx(0.55, abs=1e-12)


def test_zero_bands_collapse_the_prior_to_the_flat_half():
    """Ablating the fit prior must be a config change, not a code change."""
    zero = dict(fit_band_narrow=0.0, fit_band_wide=0.0)
    for state, post, _expected in FROZEN_FIT_CASES:
        assert fit(state, post, **zero) == 0.5


def test_bands_cannot_push_the_prior_outside_the_bounded_range():
    """The [0,1] clamp is what keeps w_fit stable regardless of agent type."""
    i2 = {"intent_group": "I2"}
    big = dict(fit_band_narrow=0.4, fit_band_wide=0.9)
    assert fit({"risk_latent": "tolerant", "core": "chaser"}, i2, **big) == 1.0
    assert fit({"risk_latent": "fragile", "core": "chaser"}, i2, **big) == 0.0


def test_neither_band_applies_to_an_unknown_intent_group():
    """Only I2 / nonI2 adjust the prior; anything else stays the flat 0.5."""
    st = {"risk_latent": "tolerant", "core": "chaser"}
    for post in ({"intent_group": None}, {"intent_group": "I9"}, {}):
        assert fit(st, post) == 0.5
        assert fit(st, post, fit_band_narrow=0.4, fit_band_wide=0.4) == 0.5

# -*- coding: utf-8 -*-
"""A zero-holdings plan investor has a valid fund target.

The loop's plan block tops up `min(inv.hold)`, which has no argument on an empty
holdings map, so an investor flagged `dca` who happened to draw zero holdings at
initialisation could never place a single instalment -- at the smallest
`n_funds` bin (`N_FUNDS_K["under_5"] == 1`) roughly half of the flagged planners.
`init_investors` now names one buyable fund per such investor in the new
`Inv.dca_target` slot; the loop then spends against that target.

Three properties are pinned here:

1. eligibility is exactly `dca and not hold` -- a planner who holds something
   keeps None (the loop still tops up what it holds), and a non-planner with an
   empty map keeps None;
2. the draw must not perturb `inv.rng`.  Holdings, cost bases and every per-day
   draw seeded from that stream belong to investors who mostly have nothing to
   do with plan investing, so the target hangs off the derived stream
   `rng_for(run_tag, "dca", inv.id)`.  test_holdings_draws_are_untouched
   replays the agent stream independently and demands the same held funds,
   which is only true if the dca draw consumed nothing from it;
3. the target must be tradeable on day 0.  `base_codes` is load_world's
   ">=63 NAVs before start" branch, whose funds carry `active_from ==
   world.start`, so the deferred-activation filter is the identity on any world
   load_world built -- test_deferred_activation_codes_are_never_targeted uses a
   hand-built world to show the filter is really applied rather than assumed.
"""
from __future__ import annotations

from datetime import date, timedelta

from flowmirror.engine.world import (
    N_FUNDS_K,
    Fund,
    Inv,
    World,
    init_investors,
    load_world,
    rng_for,
)


def _invs(cfg):
    """(world, investors) for a mock demo run -- no keys, no network, no research data."""
    world = load_world(cfg)
    return world, init_investors(world, cfg)


def _traits(world, inv):
    """The population row behind an investor, for its n_funds bin."""
    for rec in world.agents:
        if rec["id"] == inv.id:
            return rec.get("traits") or {}
    raise AssertionError(f"investor {inv.id} not found in the population file")


def test_slot_exists_and_class_stays_slotted():
    """The slot is declared, and Inv did NOT quietly grow a __dict__ to hold it."""
    assert "dca_target" in Inv.__slots__
    assert not hasattr(Inv(), "__dict__")


def test_zero_holding_planners_get_a_buyable_target(tmp_path, demo_cfg_factory):
    cfg = demo_cfg_factory(tmp_path / "out")
    world, invs = _invs(cfg)
    eligible = [v for v in invs if v.dca and not v.hold]
    # Guard the premise: a fixture where nobody is eligible would pass vacuously.
    assert eligible, "demo fixture has no zero-holdings plan investor to test"
    base = set(world.base_codes)
    for v in eligible:
        assert v.dca_target is not None, v.id
        assert v.dca_target in base, (v.id, v.dca_target)
        # Tradeable on day 0 -- the whole point of naming a target.
        assert world.funds[v.dca_target].active_from <= world.start


def test_everyone_else_gets_none(tmp_path, demo_cfg_factory):
    cfg = demo_cfg_factory(tmp_path / "out")
    _, invs = _invs(cfg)
    holders = [v for v in invs if v.dca and v.hold]
    assert holders, "demo fixture has no plan investor with holdings to test"
    for v in holders:
        # The loop tops up min(inv.hold) for these; a target would be dead weight.
        assert v.dca_target is None, (v.id, v.dca_target)
    for v in invs:
        if not v.dca:
            assert v.dca_target is None, (v.id, v.dca_target)


def test_same_seed_gives_the_same_targets(tmp_path, demo_cfg_factory):
    """Replay determinism: two constructions from one config name identical funds."""
    cfg = demo_cfg_factory(tmp_path / "out")
    world = load_world(cfg)
    first = {v.id: v.dca_target for v in init_investors(world, cfg)}
    second = {v.id: v.dca_target for v in init_investors(world, cfg)}
    assert first == second
    assert any(t is not None for t in first.values())
    # The target is seeded from run_tag, so rotating it re-draws both the holdings
    # (hence WHICH investors are eligible) and the targets.  What must survive the
    # rotation is the invariant, not the identities: exactly the zero-holdings
    # planners carry a target, and the run is still reproducible.
    rotated_cfg = dict(cfg, run_tag=str(cfg["run_tag"]) + "|rot")
    rotated = init_investors(world, rotated_cfg)
    for v in rotated:
        assert (v.dca_target is not None) == bool(v.dca and not v.hold), v.id
    assert {v.id: v.dca_target for v in init_investors(world, rotated_cfg)} == \
           {v.id: v.dca_target for v in rotated}


def test_holdings_draws_are_untouched(tmp_path, demo_cfg_factory):
    """RNG ordering: the dca draw consumes nothing from `inv.rng`.

    Replays `rng_for(run_tag, "agent", inv.id)` from scratch in the documented
    order (nh -> alloc -> sample) and demands the same held funds.  If the target
    were drawn from `inv.rng` instead of the derived stream, every eligible
    investor's later draws would shift and this would fail."""
    cfg = demo_cfg_factory(tmp_path / "out")
    world, invs = _invs(cfg)
    run_tag, base = cfg["run_tag"], world.base_codes
    for v in invs:
        rng = rng_for(run_tag, "agent", v.id)
        kk = min(N_FUNDS_K[_traits(world, v)["n_funds"]], len(base))
        nh = rng.randint(0, kk)
        expected = []
        if nh:
            rng.uniform(0.3, 0.8)                 # alloc share, drawn before the sample
            expected = rng.sample(base, nh)
        assert sorted(expected) == sorted(v.hold), v.id
        # The derived stream reproduces the target without touching the agent stream.
        want = rng_for(run_tag, "dca", v.id).choice(base) if (v.dca and not v.hold) else None
        assert v.dca_target == want, v.id


def _synthetic_world(active_from_by_code):
    """Hand-built world whose base_codes carry the given activation dates.

    load_world can never produce this (its base branch always sets
    active_from == start), so it is the only way to exercise the filter."""
    start = date(2025, 10, 1)
    dts = [date(2025, 1, 2) + timedelta(days=i) for i in range(320)]
    w = World()
    w.start = start
    w.funds = {code: Fund(code, "R3", False, "FAM", dts, [1.0] * len(dts), afrom)
               for code, afrom in active_from_by_code.items()}
    w.base_codes = sorted(active_from_by_code)
    w.nav_days = [d for d in dts if d >= start][:60]
    w.inputs_sha256, w.deferred, w.fund_org, w.fund_meta = {}, {}, {}, {}
    # n_funds "under_5" -> k == 1, so randint(0, 1) leaves about half with an empty map.
    w.agents = [{"id": f"s{i:03d}", "cell": "C2", "risk_latent": "R3", "reported_C": "C2",
                 "wealth_wan": 10.0, "core": "", "strat_weight": 1.0, "entry_day": 0,
                 "traits": {"invest_share": "10_30pct", "n_funds": "under_5", "dca": "positive"}}
                for i in range(60)]
    return w


_SYN_CFG = {"n_agents": 60, "modality_level": "agent", "modality_arms": ["T", "TV"],
            "modality_run_arm": "TV", "fees": {"subscribe_rate": 0.0012, "redeem_rate": 0.005}}


def test_deferred_activation_codes_are_never_targeted():
    """A code that cannot be bought on day 0 is excluded from the target universe."""
    start = date(2025, 10, 1)
    w = _synthetic_world({"F00001": start + timedelta(days=14),      # not yet buyable
                          "F00002": start,                          # buyable
                          "F00003": date(2025, 1, 2)})              # buyable
    invs = init_investors(w, dict(_SYN_CFG, run_tag="w3|deferred"))
    eligible = [v for v in invs if v.dca and not v.hold]
    assert eligible
    assert {v.dca_target for v in eligible} <= {"F00002", "F00003"}
    assert all(v.dca_target is not None for v in eligible)


def test_no_buyable_code_leaves_the_target_none():
    """Degrades to None rather than raising: the loop must handle an absent target."""
    start = date(2025, 10, 1)
    w = _synthetic_world({"F00001": start + timedelta(days=14),
                          "F00002": start + timedelta(days=30)})
    invs = init_investors(w, dict(_SYN_CFG, run_tag="w3|none_buyable"))
    assert [v for v in invs if v.dca and not v.hold]
    assert all(v.dca_target is None for v in invs)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for controllable opening P&L and its environment-valence report.

Lookback mode samples an earlier cost date without targeting a gain or loss.
Target mode draws toward a requested P&L distribution and records misses when
the available price path cannot reach the requested side. Rising and falling
synthetic series cover both feasible and infeasible targets.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from flowmirror.engine.world import (
    INVARIANTS,
    Fund,
    Inv,
    World,
    _initial_pnl_cfg,
    _invariant_report,
    check_invariants,
    init_investors,
    load_world,
    write_reports,
)

KEY = "m_env_valence_warning"

# Deterministic cost bases for the bundled synthetic inputs. This catches any
# accidental change in RNG draw order that could otherwise look plausible.
_SYNTHETIC_COST_BASES = {
    "inv_00012": {},
    "inv_00015": {"900002": 0.9504, "900003": 1.0},
    "inv_00027": {"900001": 1.0},
    "inv_00120": {"900004": 1.1736},
    "inv_00145": {},
    "inv_00189": {"900002": 1.0, "900003": 0.6139, "900004": 1.005, "900001": 0.928},
    "inv_00244": {},
    "inv_00255": {"900003": 1.0, "900002": 1.0081, "900004": 1.0},
    "inv_00286": {"900002": 0.8032, "900001": 0.9664},
    "inv_00367": {"900004": 1.0},
}


# --- fixtures ---------------------------------------------------------------------------------
def _demo(demo_cfg_factory, tmp_path, **over):
    """(cfg, world, investors) for the self-contained demo config (contract section 4)."""
    cfg = demo_cfg_factory(tmp_path / "out")
    cfg.update(over)
    world = load_world(cfg)
    return cfg, world, init_investors(world, cfg)


def _monotone_world(nav_of_index):
    """Hand-built world whose every fund carries the same fully controlled price path.

    load_world cannot produce a monotone series, and a monotone one is the only fixture in which
    "every holding must be under water" is a fact about the CODE rather than about a particular
    random walk.  `n_funds: over_20` (k == 5) keeps almost every investor holding something, and
    `dca: no` keeps the separate DCA target draw out of the picture entirely."""
    start = date(2025, 10, 1)
    dts = [date(2025, 1, 2) + timedelta(days=i) for i in range(320)]
    navs = [nav_of_index(i) for i in range(len(dts))]
    assert min(navs) > 0.0                        # a non-positive NAV is a different test
    w = World()
    w.start = start
    w.funds = {c: Fund(c, "R3", False, "FAM", dts, list(navs), start)
               for c in ("F00001", "F00002", "F00003", "F00004", "F00005")}
    w.base_codes = sorted(w.funds)
    w.nav_days = [d for d in dts if d >= start][:60]
    w.inputs_sha256, w.deferred, w.fund_org, w.fund_meta = {}, {}, {}, {}
    w.agents = [{"id": f"s{i:03d}", "cell": "C2", "risk_latent": "R3", "reported_C": "C2",
                 "wealth_wan": 10.0, "core": "", "strat_weight": 1.0, "entry_day": 0,
                 "traits": {"invest_share": "10_30pct", "n_funds": "over_20", "dca": "no"}}
                for i in range(40)]
    return w


_SYN_CFG = {"n_agents": 40, "modality_level": "agent", "modality_arms": ["T", "TV"],
            "modality_run_arm": "TV", "fees": {"subscribe_rate": 0.0, "redeem_rate": 0.0}}


def _syn(nav_of_index, run_tag, **ipnl):
    """(investors, world) over a controlled price path with an `initial_pnl` block."""
    w = _monotone_world(nav_of_index)
    cfg = dict(_SYN_CFG, run_tag=run_tag)
    if ipnl:
        cfg["initial_pnl"] = dict(ipnl)
    return init_investors(w, cfg), w


def _rising(i):     # +0.3% per calendar day: every earlier date is CHEAPER than the start
    return 1.0 + 0.003 * i


def _falling(i):    # -0.3% per calendar day: every earlier date is DEARER than the start
    return 2.0 - 0.003 * i


def _events(tmp_path, rows=()):
    p = Path(tmp_path) / "events.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return p


def _imp_rows(invs):
    """One impression per investor, so `e_all_cells_exposed` has its input and the state below
    is a plausible run rather than one with a known unrelated failure in it."""
    return [{"ev": "imp", "t": 0, "i": v.id, "p": "p1", "arm": "T", "slot": 0,
             "source": "random"} for v in invs]


def _pnl_share(invs):
    n = sum(len(v.pnl0) for v in invs)
    return n, sum(1 for v in invs for r in v.pnl0.values() if r < 0.0)


def _residual(inv):
    """|identity (d) residual| at construction: cash + holdings@cost + other - w0."""
    return abs(inv.cash + sum(u * inv.cost[c] for c, u in inv.hold.items())
               + inv.other - inv.w0)


# --- lookback mode: the pre-card behaviour, unchanged ------------------------------------------
def test_lookback_reproduces_the_measured_cost_bases(demo_cfg_factory, tmp_path):
    """The bundled synthetic fixture is stable across repeated constructions."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path)
    assert _initial_pnl_cfg(cfg)["mode"] == "lookback", "the demo uses lookback mode"
    assert {v.id: v.cost for v in invs} == _SYNTHETIC_COST_BASES
    again = init_investors(world, cfg)
    assert {v.id: v.cost for v in again} == {v.id: v.cost for v in invs}
    assert {v.id: v.hold for v in again} == {v.id: v.hold for v in invs}


def test_the_lookback_bounds_are_the_old_literals(demo_cfg_factory, tmp_path):
    """The defaults ARE 60 and 250, and spelling them out changes nothing -- which is what
    keeps a config that omits the block byte-identical to the pre-card tree."""
    assert _initial_pnl_cfg({})["lookback_days_min"] == 60
    assert _initial_pnl_cfg({})["lookback_days_max"] == 250
    _, _, implicit = _demo(demo_cfg_factory, tmp_path)
    _, _, explicit = _demo(demo_cfg_factory, tmp_path,
                           initial_pnl={"mode": "lookback", "lookback_days_min": 60,
                                        "lookback_days_max": 250})
    assert {v.id: v.cost for v in explicit} == {v.id: v.cost for v in implicit}


def test_lookback_records_the_opening_pnl_without_targeting_anything(demo_cfg_factory, tmp_path):
    """pnl0 is recorded in BOTH modes -- the valence report has to work on the demo runs too --
    but nothing is aimed at, so the miss counter must stay at zero."""
    _, world, invs = _demo(demo_cfg_factory, tmp_path)
    assert all(sorted(v.pnl0) == sorted(v.hold) for v in invs)
    assert all(v.pnl0_misses == 0 for v in invs)
    # pnl0 is a RETURN against the day-0 mark, the same form the day loop uses for gain_loss.
    for v in invs:
        for code, r in v.pnl0.items():
            mark = world.funds[code].nav_at(world.start - timedelta(days=1))
            assert r == pytest.approx(mark / v.cost[code] - 1.0, rel=1e-12)


def test_a_reversed_lookback_window_is_reported_not_raised():
    """randint(min, max) with min > max dies inside the stdlib naming neither the key nor the
    run; the config error names both."""
    with pytest.raises(SystemExit):
        _initial_pnl_cfg({"initial_pnl": {"lookback_days_min": 250, "lookback_days_max": 60}})
    with pytest.raises(SystemExit):
        _initial_pnl_cfg({"initial_pnl": {"mode": "sideways"}})


# --- target mode: a controlled P&L distribution ------------------------------------------------
def test_target_mode_puts_every_holding_under_water_when_asked():
    """share_at_loss 1.0 on a falling series: every opening is a loss.

    (The card names a RISING series here; on a rising one no achievable cost basis is a loss at
    all -- see the module docstring.  The falling series is the same assertion in the
    environment where it is satisfiable.)"""
    invs, _ = _syn(_falling, "w56|falling", mode="target", share_at_loss=1.0, tolerance=0.02)
    n_hold, n_loss = _pnl_share(invs)
    assert n_hold > 0, "fixture holds nothing: the assertion would be vacuous"
    assert n_loss == n_hold
    assert all(r < 0.0 for v in invs for r in v.pnl0.values())
    # The environment could deliver, so nothing is counted as a miss.
    assert sum(v.pnl0_misses for v in invs) == 0


def test_target_mode_with_share_zero_puts_nothing_under_water():
    """The other end of the same dial, on the series where a gain is achievable."""
    invs, _ = _syn(_rising, "w56|rising_gain", mode="target", share_at_loss=0.0, tolerance=0.02)
    n_hold, n_loss = _pnl_share(invs)
    assert n_hold > 0 and n_loss == 0
    assert sum(v.pnl0_misses for v in invs) == 0


def test_initial_pnl_misses_counts_targets_the_window_cannot_reach():
    """A monotonically rising fund has no date at which it could have been bought at a loss.
    The closest achievable basis is taken anyway -- the run continues -- and every such holding
    is counted, because the alternative is a silent 0% at loss that looks like a working run."""
    invs, _ = _syn(_rising, "w56|unreachable", mode="target", share_at_loss=1.0, tolerance=0.02)
    n_hold, n_loss = _pnl_share(invs)
    assert n_hold > 0
    assert n_loss == 0                            # the environment simply has no losses to give
    assert sum(v.pnl0_misses for v in invs) == n_hold
    # ...and the closest achievable return really was taken: the smallest gain in the window.
    assert all(r > 0.0 for v in invs for r in v.pnl0.values())


def test_a_tolerance_wide_enough_to_cover_the_gap_counts_no_miss():
    """The counter measures distance to the target, not the sign of the outcome: with a
    tolerance wider than the whole achievable span the same unreachable request is within
    tolerance and is not counted."""
    invs, _ = _syn(_rising, "w56|unreachable", mode="target", share_at_loss=1.0, tolerance=10.0)
    assert sum(v.pnl0_misses for v in invs) == 0


def test_target_mode_is_deterministic_and_leaves_the_agent_stream_alone():
    """The target draws hang off rng_for(run_tag, "initial_pnl", inv.id), so switching mode
    moves the cost bases and NOTHING else -- same investors, same funds, same counts.  Were the
    draws taken from inv.rng, the two modes would differ in the whole population and E11 would
    stop being one manipulation."""
    lb, _ = _syn(_falling, "w56|streams")
    tg, _ = _syn(_falling, "w56|streams", mode="target", share_at_loss=1.0, tolerance=0.02)
    assert [v.id for v in lb] == [v.id for v in tg]
    assert {v.id: sorted(v.hold) for v in lb} == {v.id: sorted(v.hold) for v in tg}
    assert {v.id: v.other for v in lb} == {v.id: v.other for v in tg}
    again, _ = _syn(_falling, "w56|streams", mode="target", share_at_loss=1.0, tolerance=0.02)
    assert {v.id: v.cost for v in again} == {v.id: v.cost for v in tg}
    # The cost bases DID move -- otherwise the test above would pass on a no-op implementation.
    assert {v.id: v.cost for v in lb} != {v.id: v.cost for v in tg}


def test_target_mode_hits_a_requested_share_on_a_two_sided_series(demo_cfg_factory, tmp_path):
    """The point of the card, on the demo's random-walk NAVs: the share at a loss follows the
    dial instead of whatever the price history happened to hand out.

    Not asserted as "share_at_loss is achieved exactly", because it cannot be and should not
    pretend to.  A fund that rose across the whole window offers no date at which it could have
    been bought at a loss, and one that fell offers no gain; on this fixture about a fifth of
    the openings are one-sided that way, which puts a floor under the achievable share and a
    ceiling over it.  `initial_pnl_misses` is the reported size of that constraint, and what is
    pinned here is that the dial has real authority in between and that the constraint is
    visible rather than silent."""
    dial = {}
    for share in (0.05, 0.5, 0.9):
        _, _, invs = _demo(demo_cfg_factory, tmp_path, n_agents=400,
                           initial_pnl={"mode": "target", "share_at_loss": share,
                                        "tolerance": 0.02})
        dial[share] = _pnl_share(invs) + (sum(v.pnl0_misses for v in invs),)
    _, _, lb = _demo(demo_cfg_factory, tmp_path, n_agents=400)
    n_hold, n_lb = _pnl_share(lb)
    assert n_hold > 100                           # enough openings for shares to mean something
    assert all(n == n_hold for n, _, _ in dial.values())        # same population every time
    losses = [dial[s][1] for s in (0.05, 0.5, 0.9)]
    assert losses[0] < losses[1] < losses[2]                    # the dial moves the share
    assert (losses[2] - losses[0]) / n_hold > 0.15              # ...and materially, not a nudge
    assert losses[0] < n_lb < losses[2]           # it straddles what lookback happened to give
    # The gap between the request and the outcome is reported, not hidden.
    assert all(m > 0 for _, _, m in dial.values())


# --- the wealth identity, both modes -----------------------------------------------------------
@pytest.mark.parametrize("ipnl", [
    {},
    {"mode": "lookback"},
    {"mode": "target", "share_at_loss": 1.0, "tolerance": 0.02},
    {"mode": "target", "share_at_loss": 0.5, "tolerance": 0.02},
])
def test_the_wealth_identity_holds_in_both_modes(ipnl, demo_cfg_factory, tmp_path):
    """w0 is computed FROM the cost basis, and invariant (d) checks it every run.  Moving the
    cost basis is exactly the kind of change that breaks it, so both modes are pinned against
    the population file's own wealth figure."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path,
                             **({"initial_pnl": ipnl} if ipnl else {}))
    wealth = {rec["id"]: float(rec["wealth_wan"]) * 1e4 for rec in world.agents}
    for v in invs:
        assert _residual(v) < 1e-6, (v.id, _residual(v))
        assert v.w0 == pytest.approx(wealth[v.id], rel=1e-9)
    syn, _ = _syn(_falling, "w56|identity", **(ipnl or {"mode": "lookback"}))
    for v in syn:
        assert _residual(v) < 1e-6, (v.id, _residual(v))
        assert v.w0 == pytest.approx(1e5, rel=1e-9)


# --- W6: the valence report --------------------------------------------------------------------
def test_the_valence_invariant_is_registered():
    assert isinstance(INVARIANTS.get(KEY), str) and INVARIANTS[KEY].strip()
    assert "never a gate" in INVARIANTS[KEY]


def test_the_valence_entry_carries_both_counts_and_always_passes(demo_cfg_factory, tmp_path):
    """The two numbers that separate "the environment was one-sided" from "the model is
    broken", in one entry, on a run that has both an opening population and climate rows."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path)
    rows = _imp_rows(invs) + [
        {"ev": "clim", "t": 0, "p": "p1", "source": "guba_seed", "label": "bearish_majority"},
        {"ev": "clim", "t": 1, "p": "p1", "source": "cmt_prev", "label": "bearish_majority"},
        {"ev": "clim", "t": 1, "p": "p2", "source": "cmt_prev", "label": "bearish_majority"},
        {"ev": "clim", "t": 2, "p": "p1", "source": "cmt_prev", "label": "mixed"}]
    state = {"agents": invs, "funds": world.funds, "active_per_day": {}, "agent_arms": {},
             "signal_audit": []}
    checks, core = check_invariants(state, _events(tmp_path, rows), cfg)
    ent = checks[KEY]
    n_hold, n_loss = _pnl_share(invs)
    assert ent["pass"] is True                    # never a gate
    assert core is True
    assert [k for k, v in checks.items() if v.get("pass") is False] == []
    assert ent["opening_holdings"] == n_hold
    assert ent["opening_holdings_at_loss"] == n_loss
    assert ent["investors_with_an_opening_loss"] == sum(
        1 for v in invs if any(r < 0.0 for r in v.pnl0.values()))
    assert ent["days_with_bearish_majority_climate"] == 2      # t 0 and t 1, t 2 is mixed
    assert ent["climate_rows"] == 4
    assert ent["climate_rows_by_label"] == {"bearish_majority": 3, "mixed": 1}
    assert ent["mode"] == "lookback"
    assert ent["initial_pnl_misses"] == 0
    assert "share_at_loss_target" not in ent      # meaningless outside target mode
    assert str(n_loss) in ent["reason"] and "bearish_majority" in ent["reason"]


def test_the_valence_entry_names_a_one_sided_environment(demo_cfg_factory, tmp_path):
    """The case the card was written for: zero losing positions anywhere.  The reason has to say
    that uniformly bullish mood is then the environment's doing, because a reader looking at a
    0 would otherwise read it as "no data"."""
    cfg = demo_cfg_factory(tmp_path / "out")
    cfg["initial_pnl"] = {"mode": "target", "share_at_loss": 1.0, "tolerance": 0.02}
    invs, world = _syn(_rising, "w56|one_sided", **cfg["initial_pnl"])
    state = {"agents": invs, "funds": world.funds, "active_per_day": {}, "agent_arms": {},
             "signal_audit": []}
    checks, core = check_invariants(state, _events(tmp_path), cfg)
    ent = checks[KEY]
    assert ent["pass"] is True and core is True
    assert ent["opening_holdings"] > 0 and ent["opening_holdings_at_loss"] == 0
    assert ent["share_of_opening_holdings_at_loss"] == 0.0
    assert ent["days_with_bearish_majority_climate"] == 0
    assert ent["initial_pnl_misses"] == ent["opening_holdings"]
    assert ent["share_at_loss_target"] == 1.0 and ent["tolerance"] == 0.02
    assert "ENVIRONMENT" in ent["reason"]


def test_a_state_without_opening_pnl_says_so_and_still_passes(demo_cfg_factory, tmp_path):
    """A replayed or hand-built state (and any run predating this card) carries no pnl0.  The
    entry must report that it cannot answer -- never claim 0 of 0 openings at a loss, and never
    fail."""
    cfg = demo_cfg_factory(tmp_path / "out")
    bare = Inv()
    bare.id, bare.cell = "A1", "C2"
    state = {"agents": [bare], "funds": {}, "active_per_day": {}, "agent_arms": {},
             "signal_audit": []}
    ent, core = (lambda t: (t[0][KEY], t[1]))(
        check_invariants(state, _events(tmp_path), cfg))
    assert ent["pass"] is True and core is True
    assert ent["investors"] == 1
    assert ent["investors_with_opening_pnl_recorded"] == 0
    assert ent["share_of_opening_holdings_at_loss"] is None
    assert "not recorded" in ent["reason"]


def test_an_empty_population_reports_no_openings_rather_than_a_share(demo_cfg_factory, tmp_path):
    cfg = demo_cfg_factory(tmp_path / "out")
    checks, core = check_invariants(
        {"agents": [], "funds": {}, "active_per_day": {}, "agent_arms": {}, "signal_audit": []},
        _events(tmp_path), cfg)
    ent = checks[KEY]
    assert ent["pass"] is True and core is True
    assert ent["opening_holdings"] == 0 and ent["share_of_opening_holdings_at_loss"] is None


def test_the_entry_reaches_the_report_with_its_reason_and_no_failure(demo_cfg_factory, tmp_path):
    """_invariant_report is what a reader actually opens; a warn-form entry whose reason were
    dropped there would lose its whole point."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path)
    state = {"agents": invs, "funds": world.funds, "active_per_day": {}, "agent_arms": {},
             "signal_audit": []}
    checks, _ = check_invariants(state, _events(tmp_path, _imp_rows(invs)), cfg)
    entries, summary = _invariant_report(checks)
    ent = entries[KEY]
    assert ent["pass"] is True and ent["reason"]
    assert ent["description"] == INVARIANTS[KEY]
    assert ent["opening_holdings"] == sum(len(v.pnl0) for v in invs)
    assert summary["failed"] == 0 and summary["all_passed"] is True


# --- W5: the counter reaches run_meta ----------------------------------------------------------
def _run_meta(cfg, world, invs, tmp_path, name="reports"):
    out = Path(tmp_path) / name
    state = {"agents": invs, "funds": world.funds, "active_per_day": {}, "agent_arms": {},
             "signal_audit": []}
    write_reports(out, state, cfg, world, {}, {"attempts": 0, "decision_failures": 0}, 0.1)
    with open(out / "run_meta.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_run_meta_carries_initial_pnl_misses_beside_the_investor_totals(
        demo_cfg_factory, tmp_path):
    """Contract: the counter joins the investor-level aggregates write_reports already builds,
    not a new top-level key -- and the existing aggregates are untouched."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path)
    meta = _run_meta(cfg, world, invs, tmp_path)
    assert meta["investors"]["total"] == len(invs)
    assert "active_ever" in meta["investors"]
    assert meta["investors"]["initial_pnl_misses"] == 0          # lookback targets nothing


def test_run_meta_reports_the_misses_a_target_run_could_not_reach(demo_cfg_factory, tmp_path):
    cfg = demo_cfg_factory(tmp_path / "out")
    cfg["initial_pnl"] = {"mode": "target", "share_at_loss": 1.0, "tolerance": 0.02}
    invs, world = _syn(_rising, "w56|meta", **cfg["initial_pnl"])
    meta = _run_meta(cfg, world, invs, tmp_path, name="reports_target")
    assert meta["investors"]["initial_pnl_misses"] == sum(v.pnl0_misses for v in invs) > 0


def test_run_meta_survives_agent_rows_without_the_slot(demo_cfg_factory, tmp_path):
    """An Inv that never went through init_investors (a hand-built state) must cost the report
    a number, never the run."""
    cfg, world, invs = _demo(demo_cfg_factory, tmp_path)
    bare = Inv()
    bare.id, bare.entry, bare.fees = "A1", 0, 0.0
    meta = _run_meta(cfg, world, list(invs) + [bare], tmp_path, name="reports_bare")
    assert meta["investors"]["initial_pnl_misses"] == 0


# --- the slots themselves ----------------------------------------------------------------------
def test_the_slots_exist_and_inv_stays_slotted():
    assert "pnl0" in Inv.__slots__ and "pnl0_misses" in Inv.__slots__
    assert not hasattr(Inv(), "__dict__")

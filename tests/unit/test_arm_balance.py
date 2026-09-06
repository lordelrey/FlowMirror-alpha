"""Unit tests: agent-level arm randomisation against PREREG B10 (corrected).

Covers the feed.py half of the stratified-randomisation fix:

* assign_agent_arms() -- the pre-registered agent-level assignment:
  within each population cell, ids ordered by sha256(run_tag|arm|
  agent_id), arms dealt round-robin, per-cell starting rotation from
  sha256(run_tag|cell), corrected so the running global counts stay
  within one agent of exact balance;
* check_arm_balance() -- the achievable tolerances: overall
  |share - 1/k| <= 0.01 and, per cell, as balanced as the cell size
  permits (exactly 1/(2*n_cell) + 1e-9 for the two-arm set);
* arm_for_agent() -- the UNBALANCED cohort-less coin, regression-pinned
  to the frozen v1.3 draw (it must keep working for its existing
  callers; it is not, and does not claim to be, the pre-registered
  assignment).

All cohort-scale assertions run over the REAL frozen 400-agent cohort
(data/population/agents_seed2027.json, 36 cells) and 21 run tags
including the five pre-registered seeds live_q4_2027..live_q4_2031,
for both the two-arm set {T, TV} and the three-arm set {T, TC, TV}.

Runs from anywhere: the repo root is located by walking up from this
file, so `pytest tests/unit/test_arm_balance.py` works the same way the
package's own scripts do.
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest


def _repo_root():
    """Directory containing the flowmirror package (walk up from here)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "flowmirror").is_dir() and (parent / "data").is_dir():
            return parent
    return here.parents[2]


_REPO = _repo_root()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from flowmirror.channels.feed import (  # noqa: E402  (after sys.path bootstrap)
    _arm_for_agent_legacy,
    arm_for_agent,
    assign_agent_arms,
    check_arm_balance,
)

TWO = ("T", "TV")
THREE = ("T", "TC", "TV")
PREREG_TAGS = ["live_q4_%d" % y for y in range(2027, 2032)]
TAGS = PREREG_TAGS + ["armbal_ut_%02d" % i for i in range(16)]  # 21 tags >= 20


@lru_cache(maxsize=1)
def _cohort():
    """The frozen cohort as a tuple of (agent_id, cell) pairs."""
    path = _REPO / "data" / "population" / "agents_seed2027.json"
    if not path.is_file():
        pytest.skip(f"frozen cohort not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("agents"), list):
        records = raw["agents"]
    elif isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        records = [dict(rec or {}, agent_id=aid)
                   for aid, rec in sorted(raw.items()) if isinstance(rec, dict)]
    else:
        raise AssertionError(f"unexpected cohort structure in {path}")
    pairs = []
    for rec in records:
        aid = rec.get("agent_id") or rec.get("id") or rec.get("inv_id")
        cell = rec.get("cell")
        assert aid is not None and cell is not None, f"bad record: {rec!r}"
        pairs.append((str(aid), str(cell)))
    return tuple(pairs)


@lru_cache(maxsize=1)
def _by_cell():
    """cell -> tuple(agent_id, ...) over the frozen cohort (sorted keys)."""
    cells = {}
    for aid, cell in _cohort():
        cells.setdefault(cell, []).append(aid)
    return cells


def test_frozen_cohort_shape():
    pairs = _cohort()
    assert len(pairs) == 400, "frozen cohort must hold 400 agents"
    assert len({c for _a, c in pairs}) == 36, "frozen cohort must hold 36 cells"


@pytest.mark.parametrize("arms", [TWO, THREE], ids=["two-arm", "three-arm"])
def test_overall_tolerance_holds_for_every_run_tag(arms):
    pairs = _cohort()
    cells = dict(pairs)
    for tag in TAGS:
        amap = assign_agent_arms(tag, pairs, arms)
        ok, rep = check_arm_balance(amap, cells, arms=arms)
        assert ok, f"tag={tag} report={rep}"
        assert rep["worst_overall_dev"] <= 0.01, f"tag={tag} report={rep}"
        assert rep["n_out_of_set"] == 0


@pytest.mark.parametrize("arms", [TWO, THREE], ids=["two-arm", "three-arm"])
def test_no_cell_of_size_two_or_more_is_single_armed(arms):
    pairs = _cohort()
    for tag in TAGS:
        amap = assign_agent_arms(tag, pairs, arms)
        for cell, members in sorted(_by_cell().items()):
            if len(members) < 2:
                continue
            assert len({amap[a] for a in members}) >= 2, f"tag={tag} cell={cell}"


@pytest.mark.parametrize("arms", [TWO, THREE], ids=["two-arm", "three-arm"])
def test_each_cell_as_balanced_as_its_size_permits(arms):
    pairs = _cohort()
    k = len(arms)
    for tag in PREREG_TAGS:  # the five pre-registered seeds suffice here
        amap = assign_agent_arms(tag, pairs, arms)
        for cell, members in sorted(_by_cell().items()):
            n_c = len(members)
            counts = [sum(1 for a in members if amap[a] == arm) for arm in arms]
            assert all(n_c // k <= c <= n_c // k + 1 for c in counts), \
                f"tag={tag} cell={cell} counts={counts}"


@pytest.mark.parametrize("arms", [TWO, THREE], ids=["two-arm", "three-arm"])
def test_assignment_is_deterministic(arms):
    pairs = _cohort()
    for tag in TAGS[:6]:
        first = assign_agent_arms(tag, pairs, arms)
        second = assign_agent_arms(tag, list(reversed(list(pairs))), arms)
        assert first == second, f"tag={tag}"
        assert set(first) == {a for a, _c in pairs}


@pytest.mark.parametrize("arms", [TWO, THREE], ids=["two-arm", "three-arm"])
def test_assignment_changes_with_run_tag(arms):
    pairs = _cohort()
    maps = [assign_agent_arms(tag, pairs, arms) for tag in TAGS]
    frozen = [tuple(sorted(m.items())) for m in maps]
    # The rotation and the digest sort order are both sha256-derived, so
    # full-map collisions across tags are impossible in practice; one
    # defensive miss is tolerated so the test can never flake.
    assert len(set(frozen)) >= len(TAGS) - 1
    diff = sum(1 for a in maps[0] if maps[0][a] != maps[1][a])
    assert diff > 0


def test_overall_share_within_one_agent_of_exact():
    pairs = _cohort()
    for tag in PREREG_TAGS:
        amap = assign_agent_arms(tag, pairs, TWO)
        n_tv = sum(1 for v in amap.values() if v == "TV")
        assert abs(n_tv - (400 - n_tv)) <= 1, f"tag={tag} TV={n_tv}"


def test_check_arm_balance_rejects_skewed_assignment():
    pairs = _cohort()
    ids = sorted(a for a, _c in pairs)
    cells = dict(pairs)
    skewed = {a: ("TV" if i < 300 else "T") for i, a in enumerate(ids)}
    ok, rep = check_arm_balance(skewed, cells)
    assert not ok
    assert abs(rep["tv_share"] - 0.75) < 1e-12
    assert rep["worst_overall_dev"] > 0.01
    assert rep["worst_overall_arm"] == "TV"


def test_check_arm_balance_flags_single_armed_cells():
    pairs = _cohort()
    cells = dict(pairs)
    # Force the two largest cells onto one arm each; the rest stay
    # stratified, so the failure must be attributed to those cells.
    big = sorted(_by_cell().items(), key=lambda kv: (-len(kv[1]), kv[0]))[:2]
    amap = assign_agent_arms("live_q4_2027", pairs, TWO)
    for (cell, members), arm in zip(big, ("TV", "T")):
        for a in members:
            amap[a] = arm
    ok, rep = check_arm_balance(amap, cells)
    assert not ok and not rep["ok_cells"]
    assert rep["single_armed_cells"] == sorted(c for c, _m in big)
    assert rep["worst_cell_dev"] >= 0.5 - 1e-9


def test_report_surfaces_numeric_worst_cases():
    pairs = _cohort()
    cells = dict(pairs)
    amap = assign_agent_arms("live_q4_2027", pairs, TWO)
    ok, rep = check_arm_balance(amap, cells, arms=TWO)
    assert ok
    assert rep["n_agents"] == 400 and rep["n_cells"] == 36
    assert isinstance(rep["worst_overall_dev"], float) and rep["worst_overall_dev"] <= 0.01
    assert isinstance(rep["worst_cell_dev"], float)
    assert rep["worst_cell"] in cells.values()
    assert rep["single_armed_cells"] == []
    assert rep["ok_overall"] and rep["ok_cells"]
    assert abs(rep["tv_share"] - 0.5) <= 0.01


def test_arm_for_agent_kept_as_the_unbalanced_cohortless_coin():
    ids = [a for a, _c in _cohort()][:60]
    for tag in PREREG_TAGS[:2]:
        draws = [arm_for_agent(tag, a) for a in ids]
        assert draws == [arm_for_agent(tag, a) for a in ids]  # deterministic
        assert set(draws) <= {"T", "TV"}
    n_diff = sum(1 for a in ids
                 if arm_for_agent(PREREG_TAGS[0], a) != arm_for_agent(PREREG_TAGS[1], a))
    assert n_diff > 0  # tag-sensitive


def test_arm_for_agent_matches_frozen_v13_coin():
    ids = [a for a, _c in _cohort()][:80]
    for tag in PREREG_TAGS:
        for a in ids:
            assert arm_for_agent(tag, a) == _arm_for_agent_legacy(tag, a), \
                f"tag={tag} agent={a}"

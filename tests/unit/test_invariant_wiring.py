"""The engine must consume world.check_invariants results faithfully.

check_invariants returns (checks, core). These tests verify the wiring:

(a) a mock run's invariants_report.json carries real pass/fail entries -- zero
    unexplained skips -- with numeric detail for the wealth identity
    (d_wealth_conservation) and arm balance (h_arm_balance);
(b) a checks dict with one failing invariant and core=False drives engine exit
    code 4 and the failing key, with its detail, is named on stdout;
(c) a malformed check_invariants return must
    raise, never be coerced into a pass.

"""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flowmirror.engine import loop                     # noqa: E402
from flowmirror.engine.loop import run_simulation      # noqa: E402

# The 11 registered invariants (flowmirror.engine.world.INVARIANTS); mirrored
# here so this test fails loudly if the registry and the wiring drift apart.
REGISTRY = (
    "a_lagged_signals_only", "b_nonholder_never_redeems", "c_hard_block_never_subscribes",
    "d_wealth_conservation", "e_all_cells_exposed", "f_post_ig_is_intent_group",
    "g_comments_lagged_only", "h_arm_balance", "i_redeem_checkout_never_blocked",
    "j_displayed_comment_matches_prev_day", "k_dec_matches_active",
    # report-only entries (always pass, carry facts in `reason`): they state whether
    # pixels reached the TV arm and how one-sided the opening environment is
    "m_tv_arm_carries_images", "m_env_valence_warning",
)


def test_registry_mirror_has_not_drifted():
    """This tuple is the repo's only detector of drift between world.INVARIANTS and the
    wiring, and it had itself drifted: 11 mirrored keys against a registry of 13, with
    a comment promising it would fail loudly. Asserting set equality makes the promise
    true, so the next key added to the registry lands here too."""
    from flowmirror.engine import world
    assert set(REGISTRY) == set(world.INVARIANTS), (
        "mirror drift: "
        f"missing here {sorted(set(world.INVARIANTS) - set(REGISTRY))}, "
        f"stale here {sorted(set(REGISTRY) - set(world.INVARIANTS))}")


def _run_mock(tmp_path, agents=None, days=None):
    """Build a runnable mock config from the self-contained demo config."""
    from tests.conftest import DEMO_AGENTS, DEMO_DAYS, build_demo_cfg, find_demo_run

    if find_demo_run() is None:
        pytest.skip("runs/demo_two_arm.json not found (set FLOWMIRROR_DATA_ROOT)")
    return build_demo_cfg(tmp_path,
                          agents=DEMO_AGENTS if agents is None else agents,
                          days=DEMO_DAYS if days is None else days)


def _find_entry(node, key):
    """Depth-first lookup of `key` in the parsed report (layout-agnostic:
    flat mapping, nested under an "invariants" key, or a list of rows)."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            hit = cur.get(key)
            if isinstance(hit, dict):
                return hit
            if cur.get("key") == key or cur.get("name") == key:
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _has_number(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, bool):
            continue
        if isinstance(cur, (int, float)):
            return True
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def _load_report(out_dir):
    with open(os.path.join(out_dir, "invariants_report.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_mock_run_reports_real_invariant_detail(tmp_path):
    """(a) The report holds real per-invariant results, not 'skipped' filler."""
    cfg = _run_mock(tmp_path)
    assert run_simulation(cfg) == 0
    report = _load_report(cfg["out_dir"])
    evaluated, skipped = [], []
    for key in REGISTRY:
        entry = _find_entry(report, key)
        assert entry is not None, f"{key} missing from invariants_report.json"
        if entry.get("skipped"):
            # A skip must be an explicit "does not apply", never the silent
            # "not evaluated" filler the discarded checks tuple produced.
            assert entry.get("reason"), f"{key} skipped without a reason: {entry}"
            skipped.append(key)
        else:
            assert isinstance(entry.get("pass"), bool), \
                f"{key} has no real pass flag: {entry}"
            evaluated.append(key)
    # Zero skips among the invariants that apply at this configuration: the
    # wealth identity and arm balance always apply here.
    assert not set(skipped) & {"d_wealth_conservation", "h_arm_balance"}, \
        f"wealth identity / arm balance must be evaluated, skipped: {skipped}"
    for key in ("d_wealth_conservation", "h_arm_balance"):
        entry = _find_entry(report, key)
        assert _has_number(entry), f"{key} carries no numeric detail: {entry}"
    # Do not require an arbitrary evaluation count: legitimate skips (no
    # redemption act rows, no hard_block checkouts, no displayed comment
    # entries, no redemption checkout rows) could not meet >= 8. What matters: every
    # registered key was found above (no silent absence), every skip above carried a
    # non-empty reason, and the invariants that must be evaluable on ANY run -- the
    # wealth identity and the arm balance -- are evaluated rather than skipped. After
    # Comment-climate checks become evaluable when comments are displayed.
    assert {"d_wealth_conservation", "h_arm_balance"} <= set(evaluated), \
        ("wealth identity and arm balance must be evaluated on every run, not skipped "
         f"(evaluated={evaluated}, skipped={skipped})")


def test_failing_invariant_exits_4_and_is_named(tmp_path, capsys, monkeypatch):
    """core=False with one failing invariant yields exit code 4 and a named check."""
    cfg = _run_mock(tmp_path)
    failing = {"d_wealth_conservation": {"pass": False, "residual_cny": 12.5,
                                         "worst_agent": "A3"}}

    def fake_check_invariants(state, events_path, cfg_):
        checks = {k: {"pass": True, "n": 1} for k in REGISTRY}
        checks.update(failing)
        return checks, False

    monkeypatch.setattr(loop, "check_invariants", fake_check_invariants)
    rc = run_simulation(cfg)
    out = capsys.readouterr().out
    assert rc == 4, f"invariant failure must exit 4, got {rc} (stdout:\n{out})"
    assert "invariants=FAIL" in out, out
    assert "d_wealth_conservation" in out, f"failing invariant key not named:\n{out}"
    assert "residual_cny" in out, f"failing invariant detail not printed:\n{out}"
    assert "exit code 4" in out, out


def test_malformed_check_invariants_result_raises(tmp_path, monkeypatch):
    """(c) A non-(checks, core) return must raise, never become a silent pass."""
    cfg = _run_mock(tmp_path)

    def bad_check_invariants(state, events_path, cfg_):
        return {"d_wealth_conservation": {"pass": True}}

    monkeypatch.setattr(loop, "check_invariants", bad_check_invariants)
    with pytest.raises(TypeError):
        run_simulation(cfg)

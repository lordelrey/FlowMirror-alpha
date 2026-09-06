"""Card R2F-loop: the engine must consume world.check_invariants' real results.

check_invariants returns (checks, core). run_simulation used to test
isinstance(result, dict) -- always False for that 2-tuple -- and fall back to
{"core": {"pass": bool(<tuple>)}}, and bool() of a non-empty tuple is always
True. Every per-invariant result was therefore discarded before write_reports:
invariants_report.json showed the registered invariants as "skipped: not
evaluated" plus one bogus "core: pass", and the console printed
invariants=PASS even if every invariant failed. These tests pin the fixed
wiring:

(a) a mock run's invariants_report.json carries real pass/fail entries -- zero
    unexplained skips -- with numeric detail for the wealth identity
    (d_wealth_conservation) and arm balance (h_arm_balance);
(b) a checks dict with one failing invariant and core=False drives engine exit
    code 4 and the failing key, with its detail, is named on stdout;
(c) a malformed check_invariants return (the pre-R2F bare-dict contract) must
    raise, never be coerced into a pass.

Test (b) fails against the pre-fix engine: the tuple is coerced into a pass,
run_simulation returns 0, and the failing key never reaches stdout.
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
)


def _cfg_path(name="mock_10x3.json"):
    for base in (os.environ.get("FLOWMIRROR_DATA_ROOT"),
                 os.environ.get("FLOWMIRROR_RESEARCH_ROOT"),
                 os.path.join(ROOT, "data"), ROOT):
        if not base:
            continue
        p = os.path.join(base, "runs", name)
        if os.path.exists(p):
            return p
    return None


def _run_mock(tmp_path, agents=None, days=None):
    """Build a runnable mock config from the SELF-CONTAINED demo config.

    Card CI1: this used to derive from runs/mock_10x3.json, whose nav_cache input
    is the git-ignored 4.4 MB research cache, so the suite was green only on a
    machine that happened to hold that file and red on every clean clone."""
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
    # FIX3 (Defect 4): no arbitrary evaluation count -- legitimate, honestly-reasoned
    # skips (no redemption act rows, no hard_block checkouts, no displayed comment
    # entries, no redemption checkout rows) could not meet >= 8. What matters: every
    # registered key was found above (no silent absence), every skip above carried a
    # non-empty reason, and the invariants that must be evaluable on ANY run -- the
    # wealth identity and the arm balance -- are evaluated rather than skipped. After
    # the FIX3 Defect 1 feed repair the comment-climate skips (g/j) disappear on
    # their own, because agents finally see displayed comments.
    assert {"d_wealth_conservation", "h_arm_balance"} <= set(evaluated), \
        ("wealth identity and arm balance must be evaluated on every run, not skipped "
         f"(evaluated={evaluated}, skipped={skipped})")


def test_failing_invariant_exits_4_and_is_named(tmp_path, capsys, monkeypatch):
    """(b) core=False with one failing invariant -> exit code 4 + named on stdout.

    Must FAIL against the pre-R2F engine (which coerced the (checks, core)
    tuple into a pass and returned 0)."""
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
        return {"d_wealth_conservation": {"pass": True}}   # pre-R2F dict contract

    monkeypatch.setattr(loop, "check_invariants", bad_check_invariants)
    with pytest.raises(TypeError):
        run_simulation(cfg)

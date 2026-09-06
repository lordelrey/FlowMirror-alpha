"""Card CI1: one shared place builds the suite's runnable mock-run fixture.

Both tests/unit/test_dump_prompt.py and tests/unit/test_invariant_wiring.py
used to build fixtures from runs/mock_10x3.json, whose nav_cache input is
data/funds/nav_cache.json -- the real 4.4 MB third-party NAV cache, git-ignored
on purpose and present only on the author's machine.  Six tests were therefore
green locally and failed on every clean clone with

    [FATAL] missing input nav_cache: .../data/funds/nav_cache.json

The fixture base is now runs/demo_two_arm.json, the run config whose inputs
(agents_file, content_pool, nav_cache -> data/funds/nav_demo_2025q4.json,
guba_signal, fund_meta_file) are ALL tracked in git -- the property that
tests/unit/test_configs_are_self_contained.py now guards.  Construction lives
here so the two suites can never drift to different bases again."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The self-contained fixture base: every input it names ships in the repo, so a
# clean clone with zero API keys and zero research data can run it end to end.
DEMO_RUN = "demo_two_arm.json"
# The demo config's own shipped regime (10 agents x 3 trading days).  Tests that
# used to ask mock_10x3 for 40 agents cannot be served by the 10-agent demo
# population and must scale down to this, never up.
DEMO_AGENTS = 10
DEMO_DAYS = 3


def find_demo_run():
    """Locate runs/demo_two_arm.json: env overrides first, then the repo.

    Same search order the engine self-test uses, so external trees mounted via
    FLOWMIRROR_DATA_ROOT / FLOWMIRROR_RESEARCH_ROOT keep working when they
    carry their own copy of the demo config."""
    for base in (os.environ.get("FLOWMIRROR_DATA_ROOT"),
                 os.environ.get("FLOWMIRROR_RESEARCH_ROOT"),
                 ROOT):
        if base and os.path.isfile(os.path.join(base, "runs", DEMO_RUN)):
            return os.path.join(base, "runs", DEMO_RUN)
    return None


def build_demo_cfg(out_dir, agents=DEMO_AGENTS, days=DEMO_DAYS):
    """Runnable mock-run config derived from the self-contained demo config.

    All outputs (event log, LLM cache, reports) land under out_dir; the LLM is
    mocked, so the run needs no keys, no network and no research data."""
    from flowmirror.engine.loop import _load_cfg    # deferred: cheap collection

    path = find_demo_run()
    if path is None:
        raise FileNotFoundError(
            f"runs/{DEMO_RUN} not found under FLOWMIRROR_DATA_ROOT / "
            "FLOWMIRROR_RESEARCH_ROOT / the repo root -- the suite's fixture "
            "base must be a config that ships with the repository")
    cfg = _load_cfg(path)
    cfg.update({"mock_llm": True, "n_agents": agents, "out_dir": str(out_dir)})
    cfg["window"]["max_trading_days"] = days
    cfg.setdefault("llm", {})["cache"] = os.path.join(str(out_dir), "llm_cache.jsonl")
    return cfg


@pytest.fixture
def demo_run_path():
    """Path to the self-contained demo config, for CLI entry-point tests."""
    path = find_demo_run()
    if path is None:
        pytest.skip(f"runs/{DEMO_RUN} not found (set FLOWMIRROR_DATA_ROOT)")
    return path


@pytest.fixture
def demo_cfg_factory():
    """Callable(out_dir, agents=DEMO_AGENTS, days=DEMO_DAYS) -> run config dict."""
    if find_demo_run() is None:
        pytest.skip(f"runs/{DEMO_RUN} not found (set FLOWMIRROR_DATA_ROOT)")
    return build_demo_cfg

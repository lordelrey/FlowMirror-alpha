"""Shared fixtures built from the self-contained offline demo configuration."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The self-contained fixture base: every input it names ships in the repo, so a
# clean clone with no API keys can run it end to end.
DEMO_RUN = "demo_two_arm.json"
# Keep shared fixtures small enough for fast local test runs.
DEMO_AGENTS = 10
DEMO_DAYS = 3


def find_demo_run():
    """Locate runs/demo_two_arm.json: env overrides first, then the repo.

    FLOWMIRROR_DATA_ROOT may point to another self-contained data tree."""
    for base in (os.environ.get("FLOWMIRROR_DATA_ROOT"), ROOT):
        if base and os.path.isfile(os.path.join(base, "runs", DEMO_RUN)):
            return os.path.join(base, "runs", DEMO_RUN)
    return None


def build_demo_cfg(out_dir, agents=DEMO_AGENTS, days=DEMO_DAYS):
    """Runnable mock-run config derived from the self-contained demo config.

    All outputs (event log, LLM cache, reports) land under out_dir; the LLM is
    mocked, so the run needs no keys, network, or external datasets."""
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

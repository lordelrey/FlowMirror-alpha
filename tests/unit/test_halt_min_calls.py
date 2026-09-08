"""Guard decision_failure_min_calls: a failure RATE on a tiny sample is noise.

A 10-agent mock run reaches t=3 with only ~40 decisions; one terminal failure
there reads as 2.5% and trips the 2% halt even though the same run's true
terminal-failure rate over 600 calls was 0.33%. With min_calls=100 the halt
must wait until the rate is informative; with min_calls=0 the old behaviour
(halt fires) must be preserved.
"""
from tests.conftest import build_demo_cfg
from flowmirror.engine import loop as L


def _make_cfg(tmp_path, min_calls):
    cfg = build_demo_cfg(str(tmp_path), agents=10, days=4)
    cfg.setdefault("mock_options", {})
    cfg["mock_options"]["malformed_rate"] = 1.0  # every decision fails terminally
    cfg["llm"]["decision_failure_halt"] = 0.02
    cfg["llm"]["decision_failure_min_calls"] = min_calls
    return cfg


def test_halt_waits_for_min_calls(tmp_path):
    """40 decisions < 100: the sample is too small for the rate, run must exit 0."""
    cfg = _make_cfg(tmp_path, min_calls=100)
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0


def test_halt_fires_when_floor_is_zero(tmp_path):
    """min_calls=0 restores the pre-floor behaviour: halt fires, run must exit 3."""
    cfg = _make_cfg(tmp_path, min_calls=0)
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 3

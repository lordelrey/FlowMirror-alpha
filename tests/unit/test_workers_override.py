"""Tests for the --workers CLI concurrency override.

Concurrency (llm.workers) changes wall-clock time only, never results: the parallel phase
collects decisions and applies them serially in sorted agent order, so --replay-check output
is byte-identical across worker counts. That is why the knob lives in RuntimeOpts as a CLI-only
operational switch instead of in simulation configuration.
"""
import unittest

from flowmirror.engine.loop import RuntimeOpts, _apply_workers_override


class WorkersOverrideTests(unittest.TestCase):
    def test_no_override_returns_config_unchanged(self):
        cfg = {"llm": {"workers": 6}, "n_agents": 4}
        for rt in (RuntimeOpts(), None):
            self.assertEqual(_apply_workers_override(cfg, rt), cfg)

    def test_override_replaces_only_workers(self):
        out = _apply_workers_override(
            {"llm": {"workers": 6, "model": "m"}, "n_agents": 4},
            RuntimeOpts(workers=10),
        )
        self.assertEqual(out["llm"]["workers"], 10)
        self.assertEqual(out["llm"]["model"], "m")
        self.assertEqual(out["n_agents"], 4)

    def test_override_does_not_mutate_caller_dict(self):
        cfg = {"llm": {"workers": 6, "model": "m"}, "n_agents": 4}
        _apply_workers_override(cfg, RuntimeOpts(workers=10))
        self.assertEqual(cfg["llm"]["workers"], 6)

    def test_zero_and_none_are_ignored(self):
        cfg = {"llm": {"workers": 6}, "n_agents": 4}
        self.assertEqual(_apply_workers_override(cfg, RuntimeOpts(workers=0)), cfg)
        self.assertEqual(_apply_workers_override(cfg, RuntimeOpts(workers=None)), cfg)
        self.assertIsNone(RuntimeOpts(workers=0).workers)


if __name__ == "__main__":
    unittest.main()

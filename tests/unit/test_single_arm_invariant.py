import json
import pathlib
import tempfile
import unittest

from tests.conftest import build_demo_cfg
from flowmirror.engine import loop as L


def _h_arm_balance(run_dir):
    # The engine writes invariant entries to <run_dir>/invariants_report.json under "checks",
    # not into run_meta.json.
    rep = json.loads((run_dir / "invariants_report.json").read_text(encoding="utf-8"))
    return (rep.get("checks") or {}).get("h_arm_balance")


def _meta(run_dir):
    return json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))


class TestSingleArmInvariant(unittest.TestCase):
    def test_single_modality_skips_agent_balance(self):
        # unittest methods cannot take pytest fixtures; a TemporaryDirectory plays tmp_path.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "one_arm"
            cfg = build_demo_cfg(run_dir, agents=12, days=2)
            cfg["modality_arms"] = ["TC"]
            self.assertEqual(L.run_simulation(cfg, L.RuntimeOpts()), 0)
            meta = _meta(run_dir)
            entry = _h_arm_balance(run_dir)
        arms = meta["arms"]
        values = arms.values() if isinstance(arms, dict) else arms
        self.assertTrue(all(v == "TC" for v in values))
        if entry is None:
            self.fail(f"h_arm_balance not found; meta keys: {sorted(meta.keys())}")
        self.assertIs(entry.get("skipped"), True)
        self.assertIn("single modality arm", entry["reason"])

    def test_three_default_arms_balance_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / "three_arms"
            cfg = build_demo_cfg(run_dir, agents=12, days=2)
            self.assertEqual(L.run_simulation(cfg, L.RuntimeOpts()), 0)
            meta = _meta(run_dir)
            entry = _h_arm_balance(run_dir)
        if entry is None:
            self.fail(f"h_arm_balance not found; meta keys: {sorted(meta.keys())}")
        self.assertIsNot(entry.get("skipped"), True)
        self.assertIsInstance(entry.get("pass"), bool)

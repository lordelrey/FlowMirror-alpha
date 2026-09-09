"""Institution adaptation (F) guards: off-state logs byte-identical; on-state weight updates on spec."""

import hashlib
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from flowmirror.engine.loop import _inst_update

REPO = Path(__file__).resolve().parents[2]
BASE_CONFIG = REPO / "runs" / "demo_three_arm.json"
ENGINE_DEFAULTS = REPO / "config" / "engine_defaults.yaml"
RUN_SCHEMA = REPO / "config" / "schemas" / "run.schema.json"
EVENT_SCHEMA = REPO / "config" / "schemas" / "event.schema.json"
INST_KEYS = ("I1", "I2", "I3")
OFF_SPEC = {"adaptive": False, "period_days": 5, "eta": 0.5, "floor": 0.05}


def _run_cli(config_path, out_dir):
    """Run the CLI once in a subprocess; raise AssertionError with stderr tail on failure."""
    cmd = [sys.executable, "-m", "flowmirror.cli", "run", str(config_path),
           "--mock", "--days", "3", "--agents", "12", "--out", str(out_dir)]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("CLI failed rc=%d\n%s" % (proc.returncode, proc.stderr[-2000:]))
    return Path(out_dir) / "event_log.jsonl"


def _run(target):
    """Run one config (dict -> tmpdir/cfg.json, or absolute Path) in its own temp dir."""
    with tempfile.TemporaryDirectory() as tmp:
        if isinstance(target, dict):
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(json.dumps(target), encoding="utf-8")
            target = cfg_path.resolve()
        log = _run_cli(target, tmp)
        raw = log.read_bytes()
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return events, raw


def _load_base_config():
    with open(BASE_CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


class TestInstUpdatePure(unittest.TestCase):
    """Pure-function checks of the weight update rule."""

    def test_inst_update_zero_responses_keeps_weights(self):
        w = {"I1": 0.2, "I2": 0.5, "I3": 0.3}
        self.assertEqual(_inst_update(dict(w), {}, 0.5, 0.05), w)
        self.assertEqual(_inst_update(dict(w), {"I1": 0, "I2": 0, "I3": 0}, 0.5, 0.05), w)

    def test_inst_update_full_eta_moves_to_share_with_floor(self):
        w_old = {"I1": 1 / 3, "I2": 1 / 3, "I3": 1 / 3}
        w = _inst_update(w_old, {"I2": 10}, 1.0, 0.05)
        self.assertAlmostEqual(w["I2"], 1 / 1.1, delta=1e-12)
        self.assertAlmostEqual(w["I1"], 0.05 / 1.1, delta=1e-12)
        self.assertAlmostEqual(w["I3"], 0.05 / 1.1, delta=1e-12)
        self.assertLess(abs(sum(w.values()) - 1.0), 1e-9)

    def test_inst_update_half_eta_is_midpoint_before_floor(self):
        w = _inst_update({"I1": 0.5, "I2": 0.5, "I3": 0.0}, {"I1": 0, "I2": 4}, 0.5, 0.0)
        self.assertAlmostEqual(w["I1"], 0.25)
        self.assertAlmostEqual(w["I2"], 0.75)
        self.assertAlmostEqual(w["I3"], 0.0)

    def test_inst_update_never_below_floor_and_sums_to_one(self):
        rng = random.Random(7)
        for _ in range(20):
            raw = [rng.uniform(0.0, 1.0) for _ in INST_KEYS]
            w_old = {k: 0.05 + 0.85 * v / sum(raw) for k, v in zip(INST_KEYS, raw)}
            conv = {k: rng.randint(1, 4) for k in INST_KEYS}
            w = _inst_update(w_old, conv, rng.random(), 0.05)
            self.assertAlmostEqual(sum(w.values()), 1.0, delta=1e-9)
            for value in w.values():
                self.assertGreaterEqual(value, 0.05 - 1e-12)


class TestInstitutionDeclarations(unittest.TestCase):
    """Defaults and schemas must declare the institutions knob and the inst event."""

    def test_defaults_and_schemas_declare_institutions(self):
        with open(ENGINE_DEFAULTS, encoding="utf-8") as fh:
            self.assertEqual(yaml.safe_load(fh)["institutions"], OFF_SPEC)
        with open(RUN_SCHEMA, encoding="utf-8") as fh:
            self.assertIn("institutions", json.load(fh)["properties"])
        with open(EVENT_SCHEMA, encoding="utf-8") as fh:
            self.assertIn("inst", json.load(fh)["properties"]["ev"]["enum"])


class TestAdaptationIntegration(unittest.TestCase):
    """End-to-end: off is invisible; on emits per-period inst rows."""

    def test_adaptive_off_is_byte_identical_to_baseline(self):
        cfg = _load_base_config()
        cfg["institutions"] = dict(OFF_SPEC)
        events_base, raw_base = _run(BASE_CONFIG)
        events_off, raw_off = _run(cfg)
        self.assertEqual(hashlib.sha256(raw_base).hexdigest(),
                         hashlib.sha256(raw_off).hexdigest())
        for events in (events_base, events_off):
            self.assertFalse(any(e.get("ev") == "inst" for e in events))

    def test_adaptive_on_emits_inst_rows_with_valid_weights(self):
        cfg = _load_base_config()
        cfg["institutions"] = {"adaptive": True, "period_days": 1, "eta": 0.5, "floor": 0.05}
        events, _ = _run(cfg)
        inst_rows = [e for e in events if e.get("ev") == "inst"]
        post_orgs = {e.get("org") for e in events if e.get("ev") == "post"}
        self.assertEqual({r["t"] for r in inst_rows}, {1, 2})
        for t in (1, 2):
            self.assertEqual({r["org"] for r in inst_rows if r["t"] == t}, post_orgs)
        for row in inst_rows:
            self.assertEqual(set(row["w"]), set(INST_KEYS))
            # Event rows round each of three weights to four decimals.
            self.assertAlmostEqual(sum(row["w"].values()), 1.0, delta=2e-4)
            for value in row["w"].values():
                self.assertGreaterEqual(value, 0.05 - 1e-9)
            self.assertEqual(set(row["conv"]), set(INST_KEYS))
            for value in row["conv"].values():
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 0)


if __name__ == "__main__":
    unittest.main()

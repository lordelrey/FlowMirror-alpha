### FILE: tests/unit/test_heat_seed.py
"""Guards for the seed heat experiment (C): off stays byte-identical, on only
changes displayed numbers, and group labels ride along on post rows."""
import hashlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path

import yaml

from flowmirror.engine.loop import _feed_card

REPO = Path(__file__).resolve().parents[2]
BASE_CFG = REPO / "runs" / "demo_three_arm.json"
DEFAULTS = REPO / "config" / "engine_defaults.yaml"
RUN_SCHEMA = REPO / "config" / "schemas" / "run.schema.json"
EVENT_SCHEMA = REPO / "config" / "schemas" / "event.schema.json"
HEAT_OFF = {"enabled": False, "k": 10, "p_treat": 0.5, "focus_fund": None}


class HeatSeedTests(unittest.TestCase):
    """Invariants for the heat_seed experiment (C)."""

    @staticmethod
    def _feed(seed_bonus):
        W = types.SimpleNamespace(funds={})
        post = {"post_id": "P1", "org": "orgA", "note": None, "code": None}
        return _feed_card(W, post, notes_by_id={}, arm="A", heat_prev={},
                          clim_prev={}, top_prev={}, dt_cur=date(2024, 1, 2),
                          n_prev={}, likes_prev={"P1": 3.4}, seed_bonus=seed_bonus)

    def _run_cli(self, tmp, heat_cfg):
        """Run the CLI on demo_three_arm.json plus an optional heat_seed block."""
        cfg = json.loads(BASE_CFG.read_text(encoding="utf-8"))
        if heat_cfg is not None:
            cfg["heat_seed"] = heat_cfg
        cfg_path = Path(tmp) / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        out = Path(tmp) / "out"
        cmd = [sys.executable, "-m", "flowmirror.cli", "run", str(cfg_path),
               "--mock", "--days", "3", "--agents", "12", "--out", str(out)]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        raw = (out / "event_log.jsonl").read_bytes()
        events = [json.loads(ln) for ln in raw.decode("utf-8").splitlines() if ln.strip()]
        return events, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _posts(events):
        return [e for e in events if e.get("ev") == "post"]

    def test_feed_card_adds_seed_bonus_to_displayed_likes_only(self):
        base = self._feed(0)
        boosted = self._feed(10)
        self.assertEqual(base["likes"], 3)
        self.assertEqual(boosted["likes"], 13)
        strip = lambda c: {k: v for k, v in c.items() if k != "likes"}
        self.assertEqual(strip(base), strip(boosted))

    def test_defaults_and_schemas_declare_heat_seed(self):
        defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
        self.assertEqual(defaults["heat_seed"], HEAT_OFF)
        run_schema = json.loads(RUN_SCHEMA.read_text(encoding="utf-8"))
        self.assertIn("heat_seed", run_schema["properties"])
        ev_schema = json.loads(EVENT_SCHEMA.read_text(encoding="utf-8"))
        post_blocks = [b["then"] for b in ev_schema.get("allOf", [])
                       if b.get("if", {}).get("properties", {}).get("ev", {})
                       .get("const") == "post"]
        self.assertEqual(len(post_blocks), 1)
        self.assertEqual(post_blocks[0]["properties"]["heat_seed"]["enum"],
                         ["plus", "ctrl", "na"])

    def test_off_is_byte_identical_and_writes_no_field(self):
        with tempfile.TemporaryDirectory() as d_base, \
                tempfile.TemporaryDirectory() as d_off:
            _, sha_base = self._run_cli(d_base, None)
            events, sha_off = self._run_cli(d_off, HEAT_OFF)
        self.assertEqual(sha_off, sha_base)
        self.assertFalse(any("heat_seed" in e for e in self._posts(events)))

    def test_on_assigns_every_post_and_keeps_first_day_exposure(self):
        on_cfg = {"enabled": True, "k": 10, "p_treat": 0.5, "focus_fund": None}
        with tempfile.TemporaryDirectory() as d_off, \
                tempfile.TemporaryDirectory() as d_on:
            off_events, _ = self._run_cli(d_off, HEAT_OFF)
            on_events, _ = self._run_cli(d_on, on_cfg)
        posts = self._posts(on_events)
        labels = [e.get("heat_seed") for e in posts]
        self.assertTrue(labels)
        self.assertTrue(all(l in ("plus", "ctrl") for l in labels))
        if not any(l == "plus" for l in labels) or not any(l == "ctrl" for l in labels):
            self.skipTest("degenerate split: one arm received no posts")
        exposure = lambda ev: sorted((e["i"], e["p"]) for e in ev
                                     if e.get("ev") == "imp" and e.get("t") == 0)
        self.assertEqual(exposure(on_events), exposure(off_events))
        stripped = [{k: v for k, v in e.items() if k != "heat_seed"} for e in posts]
        self.assertEqual(stripped, self._posts(off_events))

    def test_focus_fund_marks_other_posts_na(self):
        cfg = {"enabled": True, "k": 10, "p_treat": 0.5, "focus_fund": "999999"}
        with tempfile.TemporaryDirectory() as tmp:
            events, _ = self._run_cli(tmp, cfg)
        posts = self._posts(events)
        self.assertTrue(posts)
        self.assertTrue(all(e.get("heat_seed") == "na" for e in posts))


if __name__ == "__main__":
    unittest.main()

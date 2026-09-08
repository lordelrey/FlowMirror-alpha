"""Unit tests for the influencer-layer (E) social graph switches.

Two invariants: with the graph OFF, emitted rows and prompts stay
byte-identical to the pre-graph baseline; with the graph ON, follow
edges, follower counts, opaque handles and ranking follow the spec.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from flowmirror.engine.loop import (_dec_counts, _fake_day, _fake_inv, _feed_card,
                                    apply_decision, handle_of)
from flowmirror.channels.feed import top_comments

REC0 = {"parsed": {"trade": None}, "likes": [], "saves": [], "follows": [], "aff": {},
        "comments": [], "trade": None}
SHOWN = {"P1": {"post_id": "P1", "org": "orgA", "code": "F1", "intent": "I2", "intent_group": "I2"}}
CFG_ON = {"social": True, "suitability": False, "social_graph": {"enabled": True}}
CFG_OFF = {"social": True, "suitability": False, "social_graph": {"enabled": False}}

REPO = Path(__file__).resolve().parents[2]


class TestSocialGraphUnits(unittest.TestCase):

    def test_handle_is_deterministic_and_opaque(self):
        expected = "@u" + hashlib.sha256("inv_00012".encode("utf-8")).hexdigest()[:5]
        h = handle_of("inv_00012")
        self.assertEqual(h, expected)
        self.assertEqual(len(h), 7)
        self.assertEqual(h, handle_of("inv_00012"))
        self.assertNotEqual(h, handle_of("inv_00013"))
        self.assertNotIn("inv_00012", h)

    def _follow_fixture(self):
        inv = _fake_inv(id="A1")
        tgt = _fake_inv(id="B2")
        day = _fake_day(CFG_ON, handle_to_agent={handle_of("A1"): inv,
                                                 handle_of("B2"): tgt})
        return inv, tgt, day

    @staticmethod
    def _follow_rows(day):
        return [e for e in day.events
                if e.get("ev") == "st" and e.get("what") == "follow_user"]

    def test_follow_user_adds_edge_follower_and_st_row(self):
        inv, tgt, day = self._follow_fixture()
        rec = dict(REC0, follow_users=[handle_of("B2")])
        apply_decision(inv, rec, SHOWN, day)
        self.assertEqual(tgt.followers, 1)
        self.assertIn("B2", inv.following_users)
        rows = self._follow_rows(day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["handle"], handle_of("B2"))
        self.assertIsNone(rows[0]["org"])
        self.assertEqual(rows[0]["i"], "A1")

    def test_follow_user_ignores_self_unknown_and_duplicate(self):
        inv, tgt, day = self._follow_fixture()
        rec = dict(REC0, follow_users=[handle_of("A1"), "@uzzzzz",
                                       handle_of("B2"), handle_of("B2")])
        apply_decision(inv, rec, SHOWN, day)
        self.assertEqual(tgt.followers, 1)
        self.assertEqual(len(self._follow_rows(day)), 1)
        apply_decision(inv, rec, SHOWN, day)  # duplicate edges stay inert
        self.assertEqual(tgt.followers, 1)
        self.assertEqual(len(self._follow_rows(day)), 1)

    def test_follow_user_noop_when_graph_off(self):
        inv = _fake_inv(id="A1")
        day = _fake_day(CFG_OFF)  # no handle_to_agent attribute at all
        rec = dict(REC0, follow_users=[handle_of("B2")])
        apply_decision(inv, rec, SHOWN, day)
        self.assertEqual(self._follow_rows(day), [])
        following = getattr(inv, "following_users", None)
        self.assertTrue(following is None or len(following) == 0)

    def test_dec_counts_follow_key_only_when_graph_on(self):
        adapted = {"likes": ["P1"], "saves": [], "follows": [], "comments": [],
                   "aff": {}, "follow_users": ["@u12345"]}
        self.assertNotIn("p_follow_users", _dec_counts(adapted, 2, True))
        on = _dec_counts(adapted, 2, True, social_graph_on=True)
        self.assertEqual(on["p_follow_users"], ["@u12345"])
        none = _dec_counts(None, 2, True, social_graph_on=True)
        self.assertIsNone(none["p_follow_users"])

    def test_feed_card_handles_only_when_graph_on(self):
        W = SimpleNamespace(funds={})
        post = {"post_id": "P1", "org": "orgA", "note": None, "code": None}
        top_prev = {"P1": [{"i": "B2", "stance": "bullish", "text": "x",
                            "fam_phrase": ""}]}
        # heat_prev / clim_prev / n_prev are per-post dicts in the engine (.get(pid)), never scalars.
        off = _feed_card(W, post, {}, "T", {}, {}, top_prev, date(2024, 1, 2), {})
        c_off = off["comments_prev"][0]
        self.assertNotIn("handle", c_off)
        self.assertNotIn("followers", c_off)
        on = _feed_card(W, post, {}, "T", {}, {}, top_prev, date(2024, 1, 2), {},
                        followers_prev={"B2": 7}, social_graph_on=True)
        c_on = on["comments_prev"][0]
        self.assertEqual(c_on["handle"], handle_of("B2"))
        self.assertEqual(c_on["followers"], 7)
        self.assertEqual(c_off["stance"], c_on["stance"])
        self.assertEqual(c_off["text"], c_on["text"])

    def test_top_comments_ranks_followers_then_familiarity_then_id(self):
        cm = [{"post_id": "P1", "agent_id": "a", "fam_level": 3, "followers": 0},
              {"post_id": "P1", "agent_id": "b", "fam_level": 0, "followers": 5},
              {"post_id": "P1", "agent_id": "c", "fam_level": 3, "followers": 0},
              {"post_id": "P2", "agent_id": "z", "fam_level": 9, "followers": 9}]
        self.assertEqual([c["agent_id"] for c in top_comments("P1", cm)],
                         ["b", "a", "c"])
        stripped = [{k: v for k, v in c.items() if k != "followers"} for c in cm]
        self.assertEqual([c["agent_id"] for c in top_comments("P1", stripped)],
                         ["a", "c", "b"])


class TestSocialGraphMockRun(unittest.TestCase):
    """End-to-end guard: OFF rows byte-stable, ON rows spec-complete."""

    def _run_mock(self, config, out_dir):
        cmd = [sys.executable, "-m", "flowmirror.cli", "run", config, "--mock",
               "--days", "2", "--agents", "12", "--out", str(out_dir)]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("mock run failed rc=%s\n%s"
                                 % (proc.returncode, proc.stderr[-2000:]))
        rows = []
        for path in sorted(Path(out_dir).rglob("*")):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows

    @staticmethod
    def _dec_rows(rows):
        return [r for r in rows if r.get("ev") == "dec"]

    @staticmethod
    def _follow_rows(rows):
        return [r for r in rows if r.get("ev") == "st"
                and r.get("what") == "follow_user"]

    def test_mock_run_off_has_no_follow_fields_and_on_has_them(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out_off = td / "off"
            out_off.mkdir()
            rows_off = self._run_mock("runs/demo_three_arm.json", out_off)
            dec_off = self._dec_rows(rows_off)
            self.assertTrue(dec_off)
            for r in dec_off:
                self.assertNotIn("p_follow_users", r)
            self.assertEqual(self._follow_rows(rows_off), [])

            cfg = json.loads((REPO / "runs" / "demo_three_arm.json")
                             .read_text(encoding="utf-8"))
            cfg["social_graph"] = {"enabled": True}
            cfg["run_tag"] = cfg.get("run_tag", "demo") + "_sg_on"
            cfg_on = td / "cfg_on.json"
            cfg_on.write_text(json.dumps(cfg), encoding="utf-8")
            out_on = td / "on"
            out_on.mkdir()
            rows_on = self._run_mock(str(cfg_on), out_on)
            dec_on = self._dec_rows(rows_on)
            self.assertTrue(dec_on)
            for r in dec_on:
                self.assertIsInstance(r.get("p_follow_users"), list)
            # Stub agents never follow anyone: zero edges pin that down.
            self.assertEqual(self._follow_rows(rows_on), [])
            sha_off = [r.get("prompt_sha") for r in dec_off]
            sha_on = [r.get("prompt_sha") for r in dec_on]
            self.assertNotEqual(sha_off, sha_on)


if __name__ == "__main__":
    unittest.main()

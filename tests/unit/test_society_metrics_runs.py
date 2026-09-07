# -*- coding: utf-8 -*-
"""Tests for flowmirror.analysis.society_metrics on tiny synthetic runs.

Each fixture is a hand-written event log (one literal dict per event, no
generation loops) inside its own TemporaryDirectory sandbox, so nothing is
ever written near runs/out/ and every expected value is countable by hand.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

from flowmirror.analysis import society_metrics as sm

_POSTS = [
    {"ev": "post", "t": 0, "d": "2025-10-01", "org": "A基金", "p": "00000", "intent": "I1", "ig": "nonI2", "fund": None, "img": True},
    {"ev": "post", "t": 0, "d": "2025-10-01", "org": "A基金", "p": "00001", "intent": "I2", "ig": "nonI1", "fund": None, "img": False},
    {"ev": "post", "t": 1, "d": "2025-10-01", "org": "B基金", "p": "00002", "intent": "I1", "ig": "nonI2", "fund": "001701", "img": True},
    {"ev": "post", "t": 1, "d": "2025-10-02", "org": "B基金", "p": "00003", "intent": "I3", "ig": "nonI2", "fund": None, "img": False},
    {"ev": "post", "t": 2, "d": "2025-10-02", "org": "A基金", "p": "00004", "intent": "I2", "ig": "nonI1", "fund": "160630", "img": True},
]

# Run A: 00000 seen by 6 distinct investors, 00001 by 2, 00002 by 1,
# 00003/00004 never; plus 5 cmt, 2 click and 3 act rows (one p=None redeem).
_EVENTS_A = _POSTS + [
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00001", "p": "00000", "arm": "T", "slot": 0, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00002", "p": "00000", "arm": "T", "slot": 1, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00003", "p": "00000", "arm": "T", "slot": 2, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00004", "p": "00000", "arm": "TV", "slot": 3, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00005", "p": "00000", "arm": "TV", "slot": 4, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00006", "p": "00000", "arm": "TV", "slot": 5, "source": "fit"},
    {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00007", "p": "00001", "arm": "T", "slot": 0, "source": "feed"},
    {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00008", "p": "00001", "arm": "TV", "slot": 1, "source": "feed"},
    {"ev": "imp", "t": 2, "d": "2025-10-01", "i": "inv_00009", "p": "00002", "arm": "T", "slot": 0, "source": "search"},
    {"ev": "cmt", "t": 1, "d": "2025-10-01", "i": "inv_00001", "p": "00000", "stance": "bullish", "text": "a1"},
    {"ev": "cmt", "t": 1, "d": "2025-10-01", "i": "inv_00002", "p": "00000", "stance": "bearish", "text": "a2"},
    {"ev": "cmt", "t": 2, "d": "2025-10-01", "i": "inv_00004", "p": "00000", "stance": "watching", "text": "a3"},
    {"ev": "cmt", "t": 3, "d": "2025-10-02", "i": "inv_00005", "p": "00000", "stance": "bullish", "text": "a4"},
    {"ev": "cmt", "t": 3, "d": "2025-10-02", "i": "inv_00007", "p": "00001", "stance": "watching", "text": "a5"},
    {"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00001", "p": "00000", "oc": "to_checkout"},
    {"ev": "click", "t": 2, "d": "2025-10-01", "i": "inv_00004", "p": "00000", "oc": "to_checkout"},
    {"ev": "act", "t": 0, "d": "2025-10-01", "i": "inv_00001", "p": "00000", "kind": "subscribe", "fund": "160630", "amt": 1000.0, "units": 1000.0, "nav": 1.0, "fee": 1.0},
    {"ev": "act", "t": 0, "d": "2025-10-01", "i": "inv_00004", "p": "00000", "kind": "subscribe", "fund": "160630", "amt": 2000.0, "units": 2000.0, "nav": 1.0, "fee": 2.0},
    {"ev": "act", "t": 0, "d": "2025-10-01", "i": "inv_00013", "p": None, "kind": "redeem", "fund": "160630", "amt": 500.0, "units": 500.0, "nav": 1.0, "fee": 0.5},
]

# Run B reuses the byte-identical post rows (cross-run identity requires the
# same slot ids to carry the same creative) but reshuffles the interactions.
_EVENTS_B = _POSTS + [
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00021", "p": "00000", "arm": "T", "slot": 0, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00022", "p": "00000", "arm": "TV", "slot": 1, "source": "fit"},
    {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00023", "p": "00000", "arm": "T", "slot": 2, "source": "fit"},
    {"ev": "imp", "t": 1, "d": "2025-10-01", "i": "inv_00024", "p": "00001", "arm": "TV", "slot": 0, "source": "feed"},
    {"ev": "cmt", "t": 2, "d": "2025-10-01", "i": "inv_00021", "p": "00002", "stance": "bullish", "text": "b1"},
    {"ev": "cmt", "t": 2, "d": "2025-10-02", "i": "inv_00022", "p": "00002", "stance": "bearish", "text": "b2"},
    {"ev": "cmt", "t": 3, "d": "2025-10-02", "i": "inv_00023", "p": "00003", "stance": "watching", "text": "b3"},
    {"ev": "click", "t": 1, "d": "2025-10-01", "i": "inv_00024", "p": "00001", "oc": "to_checkout"},
    {"ev": "act", "t": 3, "d": "2025-10-02", "i": "inv_00021", "p": "00002", "kind": "subscribe", "fund": "160630", "amt": 500.0, "units": 500.0, "nav": 1.0, "fee": 0.5},
]

# Run C publishes a different id set: slot 00004 is swapped for 00010.
_POSTS_C = _POSTS[:4] + [{"ev": "post", "t": 2, "d": "2025-10-02", "org": "A基金", "p": "00010", "intent": "I2", "ig": "nonI1", "fund": None, "img": False}]
_EVENTS_C = _POSTS_C + [
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00031", "p": "00000", "arm": "T", "slot": 0, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00032", "p": "00001", "arm": "TV", "slot": 0, "source": "feed"},
]

# Run D keeps ids 00000..00004 but slot 00000 holds a different creative
# (intent I1 -> I2, fund None -> 001701), i.e. what a re-seeded run does.
_POSTS_D = [{"ev": "post", "t": 0, "d": "2025-10-01", "org": "A基金", "p": "00000", "intent": "I2", "ig": "nonI1", "fund": "001701", "img": True}] + _POSTS[1:]
_EVENTS_D = _POSTS_D + [
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00041", "p": "00000", "arm": "T", "slot": 0, "source": "fit"},
    {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "inv_00042", "p": "00000", "arm": "TV", "slot": 1, "source": "fit"},
]

_FUNDS = {
    "160630": {"r": "R3", "qdii": False, "family": "F1", "org": "A基金", "active_from": "2025-10-01"},
    "001701": {"r": "R1", "qdii": False, "family": "F2", "org": "B基金", "active_from": "2025-10-01"},
}
_META_A = {
    "arms": {"inv_00001": "T", "inv_00002": "T", "inv_00003": "T", "inv_00007": "T", "inv_00009": "T",
             "inv_00004": "TV", "inv_00005": "TV", "inv_00006": "TV", "inv_00008": "TV", "inv_00013": "TV"},
    "funds": _FUNDS,
}
_META_B = {"arms": {"inv_00021": "T", "inv_00022": "TV", "inv_00023": "T", "inv_00024": "TV"}, "funds": _FUNDS}
_META_C = {"arms": {"inv_00031": "T", "inv_00032": "TV"}, "funds": _FUNDS}
_META_D = {"arms": {"inv_00041": "T", "inv_00042": "TV"}, "funds": _FUNDS}


class SocietyMetricsRunsTest(unittest.TestCase):

    def _make_run(self, name, events, meta):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = os.path.join(tmp.name, name)
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "event_log.jsonl"), "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        with open(os.path.join(run_dir, "run_meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        return run_dir

    def _default_run(self, name="run_a"):
        return self._make_run(name, _EVENTS_A, _META_A)

    def _sole_run(self, result):
        self.assertEqual(len(result["runs"]), 1)
        return next(iter(result["runs"].values()))

    def test_census_counts_match_hand_written_log(self):
        run = self._sole_run(sm.analyze([self._default_run()]))
        per_post = run["census"]["per_post"]
        self.assertEqual(set(per_post), {"00000", "00001", "00002", "00003", "00004"})
        for pid in ("00003", "00004"):
            for key in ("imp", "click", "cmt", "sub", "sub_amt"):
                self.assertEqual(per_post[pid][key], 0)
        self.assertEqual(run["census"]["rows"]["imp"], 9)
        self.assertEqual(run["census"]["act_subscribe"], 2)
        self.assertEqual(run["census"]["act_redeem"], 1)

    def test_redeem_without_post_is_skipped_not_fatal(self):
        run = self._sole_run(sm.analyze([self._default_run()]))
        self.assertEqual(sum(v["sub"] for v in run["census"]["per_post"].values()), 2)
        self.assertTrue(isinstance(run["lsv"]["note"], str) and run["lsv"]["note"])

    def test_comment_concentration_matches_hand_count(self):
        run = self._sole_run(sm.analyze([self._default_run()]))
        block = run["concentration"]["by_post"]["cmt"]
        self.assertEqual(block["n_support"], 5)
        self.assertEqual(block["n_nonzero"], 2)
        self.assertAlmostEqual(block["top1_share"], 0.8, places=9)

    def test_by_post_by_arm_covers_exactly_the_meta_arms(self):
        run = self._sole_run(sm.analyze([self._default_run()]))
        by_arm = run["concentration"]["by_post_by_arm"]
        self.assertEqual(set(by_arm), set(_META_A["arms"].values()))
        for arm in by_arm:
            self.assertEqual(by_arm[arm]["imp"]["n_support"], 5)

    def test_main_writes_society_json_by_default(self):
        run_dir = self._default_run("run_write")
        with contextlib.redirect_stdout(io.StringIO()):
            sm.main([run_dir])
        out_path = os.path.join(run_dir, "analysis", "society.json")
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertTrue(isinstance(payload, dict))

    def test_main_no_write_leaves_no_file(self):
        run_dir = self._default_run("run_nowrite")
        with contextlib.redirect_stdout(io.StringIO()):
            sm.main([run_dir, "--no-write"])
        self.assertFalse(os.path.isfile(os.path.join(run_dir, "analysis", "society.json")))

    def test_missing_run_meta_degrades_gracefully(self):
        run_dir = self._default_run("run_nometa")
        os.remove(os.path.join(run_dir, "run_meta.json"))
        result = sm.analyze([run_dir])
        run = self._sole_run(result)
        self.assertTrue(result["warnings"])
        self.assertEqual(len(run["concentration"]["by_post_by_arm"]), 1)

    def test_cross_run_accepts_identical_post_rows(self):
        run_a = self._default_run("run_a")
        run_b = self._make_run("run_b", _EVENTS_B, _META_B)
        result = sm.analyze([run_a, run_b])
        self.assertIsNotNone(result["cross_run"])
        for metric in ("imp", "click", "cmt", "sub"):
            block = result["cross_run"]["metrics"][metric]
            tau = block["kendall_tau_mean"]
            self.assertTrue(tau is None or -1.0 <= tau <= 1.0)
            share_var = block["share_var"]
            self.assertTrue(share_var is None or share_var >= 0.0)
            self.assertEqual(len(block["pair_taus"]), 1)

    def test_cross_run_rejects_different_post_id_sets(self):
        run_a = self._default_run("run_a")
        run_c = self._make_run("run_c", _EVENTS_C, _META_C)
        result = sm.analyze([run_a, run_c])
        self.assertIsNone(result["cross_run"])
        text = " ".join(result["warnings"]).lower()
        self.assertTrue("post" in text or "帖" in text or "集合" in text)

    def test_cross_run_rejects_same_id_with_different_creative(self):
        run_a = self._default_run("run_a")
        run_d = self._make_run("run_d", _EVENTS_D, _META_D)
        result = sm.analyze([run_a, run_d])
        self.assertIsNone(result["cross_run"])
        self.assertTrue(any("00000" in w for w in result["warnings"]))

    def test_single_run_has_no_cross_run(self):
        result = sm.analyze([self._default_run()])
        self.assertIsNone(result["cross_run"])


if __name__ == "__main__":
    unittest.main()

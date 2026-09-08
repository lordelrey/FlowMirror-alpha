# -*- coding: utf-8 -*-
"""X1 log-field regression tests (new file; no existing files touched).

Pinned changes and why they exist:
* card "likes" is the integer cumulative like count frozen at t-1 (heat is
  not likes; the old field leaked a composite hot-score float);
* dec events carry p_like/p_save: per-post likers/savers, because per-post
  likes are the dependent variable of the social treatment;
* post events carry note=<note_id>: the cross-run identity of a note;
* run_meta.json carries "openings" (opening holds/costs per agent) so
  disposition-effect reconstruction can rebaseline wealth.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from flowmirror.engine.loop import _dec_counts  # noqa: E402

MOCK_ARGS = ["--mock", "--days", "3", "--agents", "12"]


class X1LogFieldsTest(unittest.TestCase):
    """Subprocess mock runs are shared in setUpClass; tests only read files."""

    @classmethod
    def _run_mock(cls, extra):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cmd = [sys.executable, "-m", "flowmirror.cli", "run",
               # demo_three_arm ships with the repo (synthetic NAV); mock_10x3* needs the unshipped real NAV cache and fails on a clean clone.
               "runs/demo_three_arm.json", *MOCK_ARGS, "--out", tmp.name,
               *extra]
        proc = subprocess.run(cmd, cwd=str(REPO), check=False,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("mock run failed:\n%s" % proc.stderr[-2000:])
        return Path(tmp.name)

    @classmethod
    def setUpClass(cls):
        cls.run_a = cls._run_mock([])  # base artifacts for B3-B6
        cls.run_b = cls._run_mock(["--dump-prompt", "first"])  # day-0 prompts
        cls.run_c = cls._run_mock([])  # replay comparand for C8

    @classmethod
    def _events(cls, root):
        events = []
        with (root / "event_log.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def test_dec_counts_returns_sorted_pid_lists(self):
        adapted = {"likes": ["00002", "00000"], "saves": ["00001"],
                   "follows": [], "comments": [], "aff": {}}
        out = _dec_counts(adapted, 6, True)
        self.assertEqual(out["p_like"], ["00000", "00002"])
        self.assertEqual(out["p_save"], ["00001"])
        self.assertEqual(out["n_like"], 2)
        self.assertEqual(out["n_save"], 1)

    def test_dec_counts_none_adapted_yields_nulls(self):
        out = _dec_counts(None, 6, True)
        self.assertIsNone(out["p_like"])
        self.assertIsNone(out["p_save"])
        self.assertIsNone(out["n_like"])

    def test_post_lines_carry_nonempty_note(self):
        posts = [e for e in self._events(self.run_a) if e.get("ev") == "post"]
        self.assertTrue(posts)
        for ev in posts:
            self.assertTrue(isinstance(ev.get("note"), str) and ev["note"])

    def test_ok_dec_lines_pid_lists_sorted_and_consistent(self):
        decs = [e for e in self._events(self.run_a)
                if e.get("ev") == "dec" and e.get("status") == "ok"]
        self.assertTrue(decs)
        for ev in decs:
            self.assertIsInstance(ev.get("p_like"), list)
            self.assertIsInstance(ev.get("p_save"), list)
            self.assertEqual(ev["p_like"], sorted(ev["p_like"]))
            self.assertEqual(ev["p_save"], sorted(ev["p_save"]))
            self.assertEqual(len(ev["p_like"]), ev["n_like"])
            self.assertEqual(len(ev["p_save"]), ev["n_save"])

    def test_non_ok_dec_lines_have_null_p_like(self):
        for ev in self._events(self.run_a):
            if ev.get("ev") == "dec" and ev.get("status") != "ok":
                self.assertIsNone(ev.get("p_like"))

    def test_run_meta_openings_shape(self):
        meta = json.loads((self.run_a / "run_meta.json").read_text(encoding="utf-8"))
        openings = meta.get("openings")
        self.assertIsInstance(openings, dict)
        for entry in openings.values():
            hold, cost = entry["hold"], entry["cost"]
            self.assertTrue(hold)  # only agents with opening positions
            for units in hold.values():
                self.assertGreater(units, 0)
            self.assertTrue(set(hold) == set(cost)
                            or all(v is None for v in cost.values()))

    def test_day0_prompt_card_likes_all_zero(self):
        pdir = self.run_b / "prompts"
        if not pdir.is_dir():
            self.skipTest("--dump-prompt first produced no prompts directory")
        files = sorted(pdir.glob("*_d0.txt"))
        self.assertTrue(files)
        hits = 0
        for path in files:
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"热度：(\d+) 赞", text):
                hits += 1
                self.assertEqual(m.group(1), "0")
        self.assertGreaterEqual(hits, 1)

    def test_event_log_replayable_sha256(self):
        def sha(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(sha(self.run_a / "event_log.jsonl"),
                         sha(self.run_c / "event_log.jsonl"))


if __name__ == "__main__":
    unittest.main()

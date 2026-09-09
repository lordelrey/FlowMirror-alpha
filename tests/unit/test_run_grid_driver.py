import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from script import run_grid


def write_meta(out_dir, obj):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "run_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


class TestRunMetaStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "tagA")

    def test_missing(self):
        self.assertIsNone(run_grid._run_meta_status(self.out))
        self.assertFalse(run_grid.run_meta_is_complete(self.out))

    def test_malformed(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "run_meta.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(run_grid._run_meta_status(self.out), "unreadable")
        self.assertFalse(run_grid.run_meta_is_complete(self.out))

    def test_non_dict(self):
        write_meta(self.out, ["status"])
        self.assertEqual(run_grid._run_meta_status(self.out), "unreadable")

    def test_missing_status(self):
        write_meta(self.out, {"other": 1})
        self.assertEqual(run_grid._run_meta_status(self.out), "missing-status")
        self.assertFalse(run_grid.run_meta_is_complete(self.out))

    def test_invariant_failure(self):
        write_meta(self.out, {"status": "invariant_failure"})
        self.assertEqual(run_grid._run_meta_status(self.out), "invariant_failure")
        self.assertFalse(run_grid.run_meta_is_complete(self.out))

    def test_ok(self):
        write_meta(self.out, {"status": "ok"})
        self.assertEqual(run_grid._run_meta_status(self.out), "ok")
        self.assertTrue(run_grid.run_meta_is_complete(self.out))

    def test_dry_run_report_completed_tag_is_skipped(self):
        runs_dir = os.path.join(self.tmp, "runs")
        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(runs_dir)
        os.makedirs(os.path.join(out_dir, "tagA"))
        with open(os.path.join(runs_dir, "tagA.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        write_meta(os.path.join(out_dir, "tagA"), {"status": "ok"})
        buf = io.StringIO()
        with patch.object(run_grid, "RUNS_DIR", runs_dir), \
                patch.object(run_grid, "OUT_DIR", out_dir), \
                patch.object(run_grid, "_DRIVER_LOG", None), \
                redirect_stdout(buf):
            run_grid.dry_run_report("tagA", SimpleNamespace(workers=6))
        text = buf.getvalue()
        self.assertIn("status=ok", text)
        self.assertNotIn("将运行", text)


def fake_probe(rc=0, stdout=""):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=rc, stdout=stdout)

    return runner, calls


class TestCountRunningEnginesWindows(unittest.TestCase):
    def test_nonzero_rc_raises(self):
        runner, calls = fake_probe(rc=1, stdout="2")
        with self.assertRaises(RuntimeError):
            run_grid.count_running_engines(runner=runner, platform_name="nt")

    def test_blank_raises(self):
        runner, _ = fake_probe(rc=0, stdout="   ")
        with self.assertRaises(RuntimeError):
            run_grid.count_running_engines(runner=runner, platform_name="nt")

    def test_noninteger_raises(self):
        runner, _ = fake_probe(rc=0, stdout="abc")
        with self.assertRaises(RuntimeError):
            run_grid.count_running_engines(runner=runner, platform_name="nt")

    def test_oserror_raises(self):
        def runner(cmd, **kwargs):
            raise OSError("no powershell")
        with self.assertRaises(RuntimeError):
            run_grid.count_running_engines(runner=runner, platform_name="nt")

    def test_integer_returns(self):
        runner, calls = fake_probe(rc=0, stdout="3\n")
        self.assertEqual(run_grid.count_running_engines(runner=runner, platform_name="nt"), 3)
        self.assertEqual(len(calls), 1)

    def test_non_windows_zero(self):
        runner, calls = fake_probe(rc=0, stdout="5")
        self.assertEqual(run_grid.count_running_engines(runner=runner, platform_name="posix"), 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

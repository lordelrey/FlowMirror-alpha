# -*- coding: utf-8 -*-
"""Unit tests for flowmirror.analysis.modality and common.

Builds synthetic event logs with planted effects (TV engagement +0.10 over T
in seeds 1 and 3, +0.15 in seed 2; no trade effect anywhere) for 3 fake seeds
and checks the three-way verdicts, seed-level t-interval coverage (compared
with a 1e-9 floating-point tolerance), legacy-log fallbacks, arm-map fallback,
run-level mode with homogeneous single-arm runs paired by seed, and the
unpaired-seed warning path.

With one run, analyze([one_run]) must report every verdict
as "insufficient_runs" with null seed-level CIs on both scales (diff and
effect) while still reporting the mean, with df = 0 and one
"n_runs=1: descriptive only" warning per affected contrast/metric. The same
guard fires in run-level mode when fewer than 2 seed pairs exist, and runs
with n >= 2 keep the real t-interval (df = n - 1, no warnings).

Run with:  python -m unittest tests.unit.test_analysis_modality -v   (or pytest)
"""
import contextlib
import io
import json
import math
import os
import tempfile
import unittest

from flowmirror.analysis import common
from flowmirror.analysis.modality import _synthetic_run, analyze, main


class TestCommon(unittest.TestCase):
    def test_t_interval_df2(self):
        mean, sd, lo, hi, df = common.seed_t_interval([1.0, 2.0, 3.0])
        self.assertAlmostEqual(mean, 2.0)
        self.assertAlmostEqual(sd, 1.0)
        self.assertEqual(df, 2)
        half = 4.303 / math.sqrt(3.0)
        self.assertAlmostEqual(lo, 2.0 - half, places=9)
        self.assertAlmostEqual(hi, 2.0 + half, places=9)

    def test_t_interval_edges(self):
        m, sd, lo, hi, df = common.seed_t_interval([5.0])
        self.assertEqual((m, lo, hi, df), (5.0, 5.0, 5.0, 0))
        self.assertEqual(sd, 0.0)
        m, _sd, lo, hi, df = common.seed_t_interval([float(x) for x in range(12)])
        self.assertEqual(df, 11)
        self.assertTrue(lo < m < hi)
        with self.assertRaises(ValueError):
            common.seed_t_interval([1.0, 2.0], alpha=0.10)

    def test_effect_sizes(self):
        self.assertAlmostEqual(common.cohen_h(0.5, 0.5), 0.0, places=12)
        want = 2.0 * math.asin(math.sqrt(0.3)) - 2.0 * math.asin(math.sqrt(0.2))
        self.assertAlmostEqual(common.cohen_h(0.3, 0.2), want, places=12)
        self.assertEqual(common.cohen_d(2.0, 1.0, 0.5), 2.0)
        self.assertEqual(common.cohen_d(1.0, 1.0, 0.0), 0.0)

    def test_loaders(self):
        with tempfile.TemporaryDirectory() as td:
            _synthetic_run(td, 1)
            ev = common.load_events(td)
            self.assertEqual(len(ev["imp"]), 2 * 10 * 10 * 20)
            self.assertEqual(len(ev["clim"]), 10)
            meta = common.load_run_meta(td)
            self.assertEqual(meta["seed"], 1)
            self.assertIn("arms", meta)


class TestAgentLevel(unittest.TestCase):
    def test_planted_effects(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for s in (1, 2, 3):
                rd = os.path.join(td, "s%d" % s)
                _synthetic_run(rd, s, like_tv=(7 if s == 2 else 6))
                rds.append(rd)
            res = analyze(rds, level="agent")
            v = res["verdicts"]["TV-T"]
            self.assertEqual(v["engagement_rate"], "supported")
            self.assertIn(v["subscribe_conversion"], ("bounded_null", "indeterminate"))
            self.assertEqual(v["aff_sum"], "bounded_null")
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            self.assertEqual(e["df"], 2)
            self.assertAlmostEqual(e["per_run"][0], 0.10, places=12)
            self.assertAlmostEqual(e["per_run"][1], 0.15, places=12)
            # the seed-level t-interval covers the planted effect; per-run
            # diffs are 0.0999999... in fp, so compare with 1e-9 slack
            self.assertIsNotNone(e["lo"])
            self.assertLessEqual(e["lo"] - 1e-9, 0.10)
            self.assertGreaterEqual(e["hi"] + 1e-9, 0.10)
            # strict-JSON safe (no NaN / Infinity anywhere)
            json.dumps(res, allow_nan=False)
            # no degenerate cells -> no warnings at all
            self.assertEqual(res["warnings"], [])
            # meso block
            m1 = res["meso"]["s1"]
            self.assertEqual(m1["heat_source"], "imp_counts")
            self.assertAlmostEqual(m1["heat_gini_posts"], 0.0, places=12)
            self.assertEqual(m1["climate_label_distribution"], {"neutral": 10})
            fam = m1["familiarity"]["ORG1"]
            self.assertEqual(fam["median_day_lv1"], 4.5)
            self.assertEqual(fam["median_day_lv2"], 6)
            self.assertEqual(res["sesoi"], {"h": 0.10, "d": 0.20})
            # pooled sanity: click funnel identical across arms
            self.assertAlmostEqual(res["runs"][0]["pooled"]["click_rate"], 0.2, places=12)

    def test_legacy_log_and_arm_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "legacy")
            _synthetic_run(rd, 1, legacy=True, with_meta_arms=False)
            res = analyze([rd], level="agent")
            run = res["runs"][0]
            self.assertFalse(run["modern_micro"])
            # arm fallback: majority imp.arm per agent
            self.assertEqual(run["arm_counts"], {"T": 10, "TV": 10})
            c = res["contrasts"]["TV-T"]
            self.assertIsNone(c["engagement_rate"]["mean"])
            # Sign convention: canonical order T < TC < TV and contrast =
            # hi - lo, so TV-T = mean(TV) - mean(T). The single legacy
            # commenter is a TV-arm agent (10 comments over 200 imps = 0.05,
            # averaged over the 10 TV agents), so TV-T = +0.005 and T is 0.
            self.assertAlmostEqual(c["comment_rate"]["per_run"][0], 0.005, places=12)
            self.assertAlmostEqual(run["pooled"]["comment_rate"], 10.0 / 4000.0, places=12)
            self.assertNotIn("engagement_rate", run["pooled"])


class TestInsufficientRunsGuard(unittest.TestCase):
    """A single run or seed pair is descriptive only."""

    def test_single_run_agent_level(self):
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "solo")
            _synthetic_run(rd, 1)
            res = analyze([rd], level="agent")
            self.assertEqual(len(res["runs"]), 1)
            self.assertEqual(sorted(res["contrasts"]), ["TV-T"])
            for pair, metrics in res["contrasts"].items():
                for metric, e in metrics.items():
                    self.assertEqual(res["verdicts"][pair][metric],
                                     "insufficient_runs",
                                     (pair, metric, res["verdicts"][pair][metric]))
                    self.assertIsNone(e["lo"], (pair, metric))
                    self.assertIsNone(e["hi"], (pair, metric))
                    self.assertEqual(e["df"], 0, (pair, metric))
                    eff = e["effect"]
                    self.assertIsNone(eff["lo"], (pair, metric))
                    self.assertIsNone(eff["hi"], (pair, metric))
                    self.assertEqual(eff["df"], 0, (pair, metric))
                    # the mean is still reported (descriptive)
                    self.assertIsNotNone(e["mean"], (pair, metric))
                    self.assertEqual(len(e["per_run"]), 1, (pair, metric))
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            self.assertAlmostEqual(e["mean"], 0.10, places=9)
            self.assertAlmostEqual(e["per_run"][0], 0.10, places=12)
            # one warning per affected contrast/metric, flagging descriptive-only
            warned = [w for w in res["warnings"] if "descriptive only" in w]
            self.assertEqual(len(warned), len(res["contrasts"]["TV-T"]))
            self.assertTrue(all("n_runs=1" in w for w in warned))
            self.assertTrue(any("TV-T" in w and "engagement_rate" in w
                                for w in warned))
            json.dumps(res, allow_nan=False)  # strict-JSON safe

    def test_single_seed_pair_run_level(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for arm in ("T", "TV"):
                rd = os.path.join(td, "%s1" % arm)
                _synthetic_run(rd, 1, run_arm=arm)
                rds.append(rd)
            res = analyze(rds, level="run")
            for pair, metrics in res["contrasts"].items():
                for metric, e in metrics.items():
                    self.assertEqual(res["verdicts"][pair][metric],
                                     "insufficient_runs",
                                     (pair, metric, res["verdicts"][pair][metric]))
                    self.assertIsNone(e["lo"], (pair, metric))
                    self.assertIsNone(e["hi"], (pair, metric))
                    self.assertEqual(e["df"], 0, (pair, metric))
                    self.assertIsNone(e["effect"]["lo"], (pair, metric))
                    self.assertIsNone(e["effect"]["hi"], (pair, metric))
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            self.assertEqual(e["n_common_seeds"], 1)
            # mean kept: T run 4/20 = 0.20, TV run 6/20 = 0.30 -> +0.10
            self.assertAlmostEqual(e["mean"], 0.10, places=9)
            self.assertTrue(any("n_runs=1" in w and "descriptive only" in w
                                for w in res["warnings"]))
            json.dumps(res, allow_nan=False)

    def test_two_runs_still_inferential(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for s in (1, 2):
                rd = os.path.join(td, "s%d" % s)
                _synthetic_run(rd, s, like_tv=(6 if s == 1 else 7))
                rds.append(rd)
            res = analyze(rds, level="agent")
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            self.assertEqual(e["df"], 1)
            self.assertIsNotNone(e["lo"])
            self.assertIsNotNone(e["hi"])
            self.assertLess(e["lo"], e["hi"])
            self.assertAlmostEqual(e["mean"], 0.125, places=9)
            # wide df=1 interval on both scales -> not supported, not null
            self.assertEqual(res["verdicts"]["TV-T"]["engagement_rate"],
                             "indeterminate")
            self.assertEqual([w for w in res["warnings"] if "descriptive" in w], [])


class TestRunLevelAndCli(unittest.TestCase):
    def test_run_level(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for s in (1, 2, 3):
                for arm in ("T", "TV"):
                    rd = os.path.join(td, "%s%d" % (arm, s))
                    _synthetic_run(rd, s, like_tv=(7 if s == 2 else 6), run_arm=arm)
                    rds.append(rd)
            res = analyze(rds, level="run")
            self.assertTrue(all(r["arm"] in ("T", "TV") for r in res["runs"]))
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            # Arithmetic: each run is single-arm -- 10 agents x 10 days x 20 I2
            # imps; T run likes 4/day -> pooled rate 4/20 = 0.20; TV run likes
            # like_tv/day -> 6/20 = 0.30 (seeds 1,3) and 7/20 = 0.35 (seed 2).
            # Per-seed TV-T diffs: +0.10, +0.15, +0.10 -> mean
            # (0.10 + 0.15 + 0.10) / 3, matched by seed, df = n_seeds - 1 = 2.
            self.assertEqual(len(e["per_run"]), 3)
            self.assertEqual(sorted(e["per_seed"]), ["1", "2", "3"])
            self.assertEqual(e["df"], 2)
            self.assertEqual(e["n_common_seeds"], 3)
            self.assertAlmostEqual(e["mean"], (0.10 + 0.15 + 0.10) / 3.0, places=9)
            self.assertEqual(res["verdicts"]["TV-T"]["engagement_rate"], "supported")
            json.dumps(res, allow_nan=False)

    def test_run_level_unpaired_seed_warned(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for s in (1, 2, 3):
                for arm in ("T", "TV"):
                    rd = os.path.join(td, "%s%d" % (arm, s))
                    _synthetic_run(rd, s, run_arm=arm)
                    rds.append(rd)
            # a T run whose seed has no TV partner: must be excluded from the
            # TV-T contrast and reported in res["warnings"], not crash
            lone = os.path.join(td, "T99")
            _synthetic_run(lone, 99, run_arm="T")
            res = analyze(rds + [lone], level="run")
            e = res["contrasts"]["TV-T"]["engagement_rate"]
            self.assertEqual(len(e["per_run"]), 3)
            self.assertEqual(e["n_common_seeds"], 3)
            self.assertEqual(e["df"], 2)
            warned = [w for w in res.get("warnings", []) if "99" in w]
            self.assertTrue(warned, res.get("warnings"))
            self.assertIn("TV", warned[0])
            json.dumps(res, allow_nan=False)

    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as td:
            rds = []
            for s in (1, 2, 3):
                rd = os.path.join(td, "s%d" % s)
                _synthetic_run(rd, s)
                rds.append(rd)
            out = os.path.join(td, "summary.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(rds + ["--out", out])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as fh:
                loaded = json.load(fh)
            for key in ("runs", "contrasts", "meso", "sesoi", "verdicts", "warnings"):
                self.assertIn(key, loaded)
            self.assertEqual(loaded["verdicts"]["TV-T"]["engagement_rate"], "supported")
            text = buf.getvalue()
            self.assertIn("supported", text)
            self.assertIn("insufficient_runs", text)  # legend mentions the guard
            self.assertTrue(text.isascii())  # console output stays ASCII


if __name__ == "__main__":
    unittest.main()

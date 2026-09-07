# -*- coding: utf-8 -*-
"""Unit tests for the pure numeric helpers in flowmirror/analysis/society_metrics.py.

concentration(), _af(), _stance_summary(), and _kendall_tau_b() carry the whole
numeric core of the society-level report and touch no disk, so their edge-case
behaviour can be pinned exactly:

* concentration() must separate "perfectly equal" (gini == 0) from "no data"
  (all four stats None when total == 0), and must charge never-displayed posts
  (support keys holding value 0) to the denominator -- cumulative-advantage
  runs depend on that.
* _af(N, p) = E|X/N - p| with X ~ Binomial(N, p) is the LSV adjustment factor;
  at p == 1 there are no redemptions, AF collapses to 0 and so does LSV, i.e.
  the metric is uninformative on buy-only data, not "no herding".
* _stance_summary() gates the normalized entropy behind min_n because entropy
  from a handful of comments is noise.
* _kendall_tau_b() must return None (never 0.0) when a side is fully tied or
  fewer than two points are given.
"""

import math
import unittest

from flowmirror.analysis import society_metrics as sm


class ConcentrationTests(unittest.TestCase):
    """Degenerate inputs of concentration(): emptiness, zeros, support size."""

    def test_equal_values_on_three_keys_have_zero_gini(self):
        res = sm.concentration({"a": 1, "b": 1, "c": 1}, {"a", "b", "c"})
        self.assertAlmostEqual(res["gini"], 0.0, places=9)
        self.assertAlmostEqual(res["hhi"], 1.0 / 3.0, places=9)
        self.assertAlmostEqual(res["top1_share"], 1.0 / 3.0, places=9)
        self.assertEqual(res["n_nonzero"], 3)

    def test_single_nonzero_key_is_maximally_concentrated(self):
        res = sm.concentration({"a": 1, "b": 0, "c": 0}, {"a", "b", "c"})
        self.assertAlmostEqual(res["gini"], 2.0 / 3.0, places=9)
        self.assertAlmostEqual(res["hhi"], 1.0, places=9)
        self.assertAlmostEqual(res["top1_share"], 1.0, places=9)
        self.assertEqual(res["n_nonzero"], 1)

    def test_all_zero_values_yield_none_not_zero_gini(self):
        # total == 0 means "no data" (say, a run with zero subscriptions);
        # a gini of 0 here would misread it as a "perfectly equal society".
        res = sm.concentration({"a": 0, "b": 0}, {"a", "b"})
        self.assertEqual(res["total"], 0)
        for key in ("gini", "hhi", "top1_share", "top5_share"):
            self.assertIsNone(res[key], "%s must be None when total == 0" % key)

    def test_support_larger_than_values_counts_missing_keys(self):
        # Never-displayed posts widen the support and dilute the denominator
        # even though they carry no activity: n_support 4, n_nonzero 1.
        res = sm.concentration({"a": 5}, {"a", "b", "c", "d"})
        self.assertEqual(res["n_support"], 4)
        self.assertEqual(res["n_nonzero"], 1)
        self.assertAlmostEqual(res["hhi"], 1.0, places=9)
        self.assertIsNotNone(res["gini"])

    def test_single_key_support_returns_none_for_all_stats(self):
        res = sm.concentration({"a": 7}, {"a"})
        self.assertEqual(res["n_support"], 1)
        for key in ("gini", "hhi", "top1_share", "top5_share"):
            self.assertIsNone(res[key], "%s must be None when n_support < 2" % key)

    def test_top5_share_is_one_on_small_support_and_never_exceeds_one(self):
        res = sm.concentration({"a": 1, "b": 2, "c": 3}, {"a", "b", "c"})
        self.assertAlmostEqual(res["top5_share"], 1.0, places=9)
        wide = {"a": 5, "b": 4, "c": 3, "d": 2, "e": 1, "f": 1}
        res6 = sm.concentration(wide, set(wide))
        self.assertAlmostEqual(res6["top5_share"], 15.0 / 16.0, places=9)
        for values in ({"a": 1, "b": 1, "c": 1}, {"a": 9, "b": 1}, wide,
                       {"a": 4, "b": 0, "c": 3, "d": 3}):
            share = sm.concentration(values, set(values))["top5_share"]
            self.assertIsNotNone(share)
            self.assertLessEqual(share, 1.0)


class AdjustmentFactorTests(unittest.TestCase):
    """_af(N, p) = E|X/N - p|, X ~ Binomial(N, p), summed exactly."""

    def test_af_n2_p_half_equals_quarter(self):
        # X in {0, 1, 2} with probs 1/4, 1/2, 1/4 and |p_hat - 0.5| = .5, 0, .5,
        # so E = 0.25*0.5 + 0.5*0 + 0.25*0.5 = 0.25.
        self.assertAlmostEqual(sm._af(2, 0.5), 0.25, places=9)

    def test_af_is_zero_when_p_bar_is_one(self):
        # Zero redemptions force p == 1 identically, so |p - p_bar| and AF are
        # both 0 and LSV == 0: not "no herding", the metric simply carries no
        # information on buy-only data.
        self.assertAlmostEqual(sm._af(5, 1.0), 0.0, places=9)

    def test_af_stays_inside_unit_interval(self):
        for n_traders, p_bar in ((3, 0.0), (10, 0.1), (7, 0.3), (25, 0.9)):
            self.assertGreaterEqual(sm._af(n_traders, p_bar), 0.0)
            self.assertLessEqual(sm._af(n_traders, p_bar), 1.0)


class StanceSummaryTests(unittest.TestCase):
    """Shannon entropy over stance labels (bits) behind the min_n gate."""

    def test_uniform_three_labels_hit_max_entropy(self):
        res = sm._stance_summary([0] * 10 + [1] * 10 + [2] * 10, 3)
        self.assertAlmostEqual(res["entropy_bits"], math.log2(3.0), places=9)
        self.assertAlmostEqual(res["normalized"], 1.0, places=9)

    def test_one_label_one_bin_entropy_zero_normalized_none(self):
        res = sm._stance_summary([0, 0, 0], 1)
        self.assertAlmostEqual(res["entropy_bits"], 0.0, places=9)
        self.assertIsNone(res["normalized"])

    def test_below_min_n_keeps_raw_entropy_but_normalized_is_none(self):
        # Entropy from fewer than min_n comments is noise; the module keeps the
        # raw bits but reports normalized as None.
        res = sm._stance_summary([0, 1, 2], 3, min_n=5)
        self.assertIsNone(res["normalized"])
        self.assertIsInstance(res["entropy_bits"], float)
        self.assertGreaterEqual(res["entropy_bits"], 0.0)
        self.assertEqual(res["n"], 3)


class KendallTauBTests(unittest.TestCase):
    """Kendall tau-b corner cases: monotone, fully tied, too-short vectors."""

    def test_perfect_order_plus_one_and_reverse_minus_one(self):
        self.assertAlmostEqual(
            sm._kendall_tau_b([1, 2, 3, 4], [10.0, 20.0, 30.0, 40.0]), 1.0, places=9)
        self.assertAlmostEqual(
            sm._kendall_tau_b([1, 2, 3, 4], [40.0, 30.0, 20.0, 10.0]), -1.0, places=9)

    def test_fully_tied_side_returns_none_not_zero(self):
        self.assertIsNone(sm._kendall_tau_b([0, 0, 0, 0], [1, 2, 3, 4]))
        self.assertIsNone(sm._kendall_tau_b([1, 2, 3, 4], [7, 7, 7, 7]))

    def test_fewer_than_two_points_returns_none(self):
        self.assertIsNone(sm._kendall_tau_b([1], [2]))
        self.assertIsNone(sm._kendall_tau_b([], []))


class SelfTestTests(unittest.TestCase):
    """The module's bundled _self_test() must run to completion."""

    def test_module_self_test_runs_without_raising(self):
        # Its only contract is "returns silently on success, raises on failure".
        sm._self_test()


if __name__ == "__main__":
    unittest.main()

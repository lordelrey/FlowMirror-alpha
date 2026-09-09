# -*- coding: utf-8 -*-
"""Shared loaders and small statistics helpers for flowmirror analysis modules.

Stdlib only, deterministic (no RNG lives here; bootstrap seeds belong to the
callers). `seed_t_interval` implements the seed-level Student-t interval used
as the inferential statement across run seeds:
df = n_seeds - 1, built-in two-sided 95% table for df 1..10, normal quantile
beyond that. Frozen convention: only alpha = 0.05 is supported.

Usage:
  python -m flowmirror.analysis.common --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

# t_{0.975} for df = 1..10 (two-sided 95%); df > 10 falls back to the normal quantile.
T_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
         6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}
Z_975 = 1.959964


def load_events(run_dir):
    """<run_dir>/event_log.jsonl -> {ev_name: [rows]} in file order.

    Returns a defaultdict(list) subclassing dict, so `ev.get(name, [])` also works.
    """
    path = os.path.join(run_dir, "event_log.jsonl")
    if not os.path.isfile(path):
        raise FileNotFoundError("event_log.jsonl not found in %s" % run_dir)
    ev = defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                row = json.loads(line)
                ev[row["ev"]].append(row)
    return ev


def load_run_meta(run_dir):
    """<run_dir>/run_meta.json -> dict ({} when absent, so callers can .get freely)."""
    path = os.path.join(run_dir, "run_meta.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def seed_t_interval(values, alpha=0.05):
    """Seed-level Student-t interval -> (mean, sd, lo, hi, df).

    sd is the sample sd (n-1 denominator); df = n-1; the table covers df 1..10,
    larger df uses the normal quantile. n == 1 collapses to the value itself
    (df = 0); empty / all-None input -> nan components. Only alpha = 0.05 is
    supported by this implementation.
    """
    if alpha != 0.05:
        raise ValueError("only alpha=0.05 is supported by the built-in t table")
    vals = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(vals)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"), float("nan"), 0)
    mean = sum(vals) / n
    if n == 1:
        return (mean, 0.0, mean, mean, 0)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    df = n - 1
    half = T_975.get(df, Z_975) * sd / math.sqrt(n)
    return (mean, sd, mean - half, mean + half, df)


def cohen_h(p1, p2):
    """Arcsine effect size for two proportions (clips inputs into [0, 1])."""
    def phi(p):
        p = 0.0 if p < 0.0 else (1.0 if p > 1.0 else p)
        return 2.0 * math.asin(math.sqrt(p))
    return phi(p1) - phi(p2)


def cohen_d(m1, m2, s_pooled):
    """Standardized mean difference with a pre-computed pooled sd."""
    if s_pooled and s_pooled > 0:
        return (m1 - m2) / s_pooled
    if m1 == m2:
        return 0.0
    return float("inf") if m1 > m2 else float("-inf")


def _self_test():
    m, sd, lo, hi, df = seed_t_interval([1.0, 2.0, 3.0])
    assert abs(m - 2.0) < 1e-12 and abs(sd - 1.0) < 1e-12 and df == 2, (m, sd, df)
    assert abs(lo - (2.0 - 4.303 / math.sqrt(3.0))) < 1e-9, lo
    assert abs(hi - (2.0 + 4.303 / math.sqrt(3.0))) < 1e-9, hi
    m, sd, lo, hi, df = seed_t_interval([5.0])
    assert (m, lo, hi, df) == (5.0, 5.0, 5.0, 0) and sd == 0.0
    m, _sd, lo, hi, df = seed_t_interval([float(x) for x in range(12)])
    assert df == 11 and lo < m < hi
    try:
        seed_t_interval([1.0, 2.0], alpha=0.10)
        raise AssertionError("expected ValueError for alpha != 0.05")
    except ValueError:
        pass
    assert abs(cohen_h(0.5, 0.5)) < 1e-12
    assert abs(cohen_h(0.3, 0.2) -
               (2.0 * math.asin(math.sqrt(0.3)) - 2.0 * math.asin(math.sqrt(0.2)))) < 1e-12
    assert abs(cohen_h(1.0, 0.0) - math.pi) < 1e-12
    assert cohen_d(2.0, 1.0, 0.5) == 2.0
    assert cohen_d(1.0, 1.0, 0.0) == 0.0
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "event_log.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ev": "imp", "i": "a", "t": 1, "d": 1}) + "\n")
            fh.write("\n")
            fh.write(json.dumps({"ev": "dec", "i": "a", "t": 1, "d": 1}) + "\n")
        ev = load_events(td)
        assert len(ev["imp"]) == 1 and len(ev["dec"]) == 1
        assert ev.get("nope") is None
        assert load_run_meta(td) == {}
        with open(os.path.join(td, "run_meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"seed": 7}, fh)
        assert load_run_meta(td) == {"seed": 7}
    print("common self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m flowmirror.analysis.common")
    ap.add_argument("--self-test", action="store_true", help="run built-in checks and exit")
    a = ap.parse_args(argv)
    if a.self_test:
        _self_test()
        return 0
    ap.error("nothing to do; use --self-test")
    return 0


if __name__ == "__main__":
    sys.exit(main())

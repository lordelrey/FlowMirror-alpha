"""Card L7 / decision 6: the news channel's market line, and its honesty constraints.

`prompt.render_news` could always render `view["index_5d"]`, and the engine never
supplied it, so the market line never appeared in any run. The series that supplies it
is a PROXY -- the SSE Composite is not available from the data source, so an
SSE-Composite tracking ETF's unit NAV stands in -- which forces two properties these
tests pin: only a RETURN is ever derived from it, and a look-ahead is impossible.

The file lives under gitignored `data/market/`, so `market.benchmark_path` defaults to
null and every path here must be correct with the file absent. That is also what keeps
the shipped demos byte-comparable.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from flowmirror.engine import loop as L
from flowmirror.engine.benchmark import (MAX_STALE_DAYS, load_benchmark, pct_5d,
                                         sorted_dates)

from tests.conftest import build_demo_cfg


def _series(start="2025-10-01", n=40, step=0.01, weekdays_only=True):
    """A synthetic series rising by a fixed factor each observation."""
    out, d, v = {}, date.fromisoformat(start), 1.0
    while len(out) < n:
        if not weekdays_only or d.weekday() < 5:
            out[d.isoformat()] = round(v, 6)
            v *= (1.0 + step)
        d += timedelta(days=1)
    return out


def _write(tmp_path, series, meta=True):
    p = tmp_path / "bench.json"
    body = {"_meta": {"series": "proxy", "note": "not an index"}, "series": series} \
        if meta else series
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------ loading

def test_meta_block_and_flat_shape_both_load(tmp_path):
    """A `_meta` key must not reach date parsing.

    The NAV loader once died with `Invalid isoformat string: 'generator'` for exactly
    this reason, so the benchmark loader skips non-date keys rather than trusting the
    file to contain only dates.
    """
    s = _series(n=10)
    wrapped = load_benchmark(_write(tmp_path, s, meta=True))
    flat = load_benchmark(_write(tmp_path, s, meta=False))
    assert wrapped == flat == s
    assert "_meta" not in wrapped


def test_null_path_loads_nothing_and_a_bad_path_raises(tmp_path):
    assert load_benchmark(None) is None
    assert load_benchmark("") is None
    # A configured-but-unreadable input must stop the run. Silently ignoring it would
    # produce a run that looks exactly like one with no benchmark at all -- the
    # silent-no-op class of defect this whole round exists to remove.
    with pytest.raises(FileNotFoundError):
        load_benchmark(str(tmp_path / "absent.json"))


def test_non_positive_and_unparseable_values_are_dropped(tmp_path):
    s = _series(n=6)
    bad = dict(s)
    bad["2025-11-03"] = 0.0
    bad["2025-11-04"] = -1.2
    bad["2025-11-05"] = "n/a"
    got = load_benchmark(_write(tmp_path, bad))
    assert set(got) == set(s), "a non-positive or unparseable quote is not a quote"


def test_a_file_with_no_dated_values_raises(tmp_path):
    with pytest.raises(ValueError):
        load_benchmark(_write(tmp_path, {"generator": "x", "count": "3"}, meta=False))


# ---------------------------------------------------------------- the return

def test_five_day_return_matches_the_hand_computation(tmp_path):
    s = _series(start="2025-10-01", n=30, step=0.01)
    d = sorted_dates(s)
    cur = date.fromisoformat(d[20])
    got = pct_5d(s, d, cur)
    prior = [x for x in d if x < d[20]][-6:]
    assert len(prior) == 6
    assert got == round(s[prior[-1]] / s[prior[0]] - 1.0, 4)
    # five compounding steps of 1% each
    assert got == pytest.approx(1.01 ** 5 - 1.0, abs=1e-4)


def test_no_observation_on_or_after_the_current_day_is_ever_read(tmp_path):
    """Invariant (a) forbids look-ahead, and this codebase has shipped exactly that bug
    before (a week key that stepped back one day instead of one week)."""
    s = _series(start="2025-10-01", n=30)
    d = sorted_dates(s)
    for i in range(6, len(d)):
        cur = date.fromisoformat(d[i])
        base = pct_5d(s, d, cur)
        # poisoning today and every later observation cannot change the answer
        poisoned = dict(s)
        for later in d[i:]:
            poisoned[later] = 999.0
        assert pct_5d(poisoned, d, cur) == base


def test_too_few_prior_points_yields_no_key(tmp_path):
    s = _series(start="2025-10-01", n=30)
    d = sorted_dates(s)
    for i in range(0, 6):
        assert pct_5d(s, d, date.fromisoformat(d[i])) is None


def test_a_series_that_ended_is_stale_rather_than_forward_filled(tmp_path):
    s = _series(start="2025-10-01", n=30)
    d = sorted_dates(s)
    last = date.fromisoformat(d[-1])
    assert pct_5d(s, d, last + timedelta(days=1)) is not None
    assert pct_5d(s, d, last + timedelta(days=MAX_STALE_DAYS + 5)) is None, (
        "past the staleness bound the series does not cover the day; a fabricated "
        "quote in a market line is worse than a missing line")


def test_a_holiday_gap_still_produces_the_right_window():
    """A mainland market break must not silently drop the line.

    The five most recent TRADING observations are the right window even when they span
    more calendar days than usual -- that is what a five-trading-day return means.
    """
    s = _series(start="2025-09-22", n=7)          # the week before National Day
    d = sorted_dates(s)
    cur = date.fromisoformat("2025-10-09")        # first trading day after the break
    got = pct_5d(s, d, cur)
    assert got is not None
    prior = [x for x in d if x < cur.isoformat()][-6:]
    assert got == round(s[prior[-1]] / s[prior[0]] - 1.0, 4)


# ------------------------------------------------------ engine integration

def test_no_benchmark_configured_leaves_the_key_out_of_the_view(tmp_path):
    cfg = build_demo_cfg(tmp_path / "run", agents=10, days=3)
    assert not (cfg.get("market") or {}).get("benchmark_path")
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0
    # render_news skips an absent key, which is the honest degradation: the three
    # shipped demos therefore carry no market line at all rather than a placeholder.
    dump = tmp_path / "run" / "prompts"
    assert not dump.exists() or not any(dump.iterdir())


def test_a_configured_benchmark_reaches_the_prompt(tmp_path):
    series = _series(start="2025-08-01", n=90, step=0.004)
    path = _write(tmp_path, series)
    cfg = build_demo_cfg(tmp_path / "run", agents=10, days=3)
    cfg["market"] = {"benchmark_path": path,
                     "benchmark_label": "上证综指ETF(510760)单位净值，作为上证综指的代理"}
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0

    rows = [json.loads(l) for l in
            (tmp_path / "run" / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(r.get("ev") == "dec" for r in rows)


def test_an_unreadable_benchmark_path_stops_the_run(tmp_path):
    cfg = build_demo_cfg(tmp_path / "run", agents=10, days=3)
    cfg["market"] = {"benchmark_path": str(tmp_path / "nope.json"), "benchmark_label": None}
    with pytest.raises(FileNotFoundError):
        L.run_simulation(cfg, L.RuntimeOpts())


def test_the_shipped_proxy_file_is_internally_sane():
    """The real series was fetched by hand once, so check it rather than trust it.

    Skips when absent: data/market/ is gitignored third-party data and a clean clone
    does not carry it.
    """
    import os
    path = "data/market/benchmark_sse_composite_etf_510760.json"
    if not os.path.isfile(path):
        pytest.skip("data/market/ is gitignored; regenerate with fetch_benchmark.py")
    s = load_benchmark(path)
    d = sorted_dates(s)
    assert len(d) >= 240, f"expected the full year, got {len(d)}"
    assert len(set(d)) == len(d), "duplicate dates"
    assert all(v > 0 for v in s.values())
    window = [x for x in d if "2025-09-24" <= x <= "2025-12-31"]
    assert len(window) >= 60, (
        f"the simulation window plus its five-day lookback needs coverage, got {len(window)}")
    # no gap long enough to make a five-day return meaningless inside the window
    worst = max((date.fromisoformat(b) - date.fromisoformat(a)).days
                for a, b in zip(window, window[1:]))
    assert worst <= MAX_STALE_DAYS, f"largest in-window gap is {worst} days"

"""Card L7 / decision 6: the market benchmark behind the agent view's index_5d.

`prompt.render_news` has always been able to render a line reading
"大盘指数近五个交易日累计上涨/下跌 X" from `view["index_5d"]`, and nothing ever supplied
the key -- so the news channel's market line never appeared in any run.

**The series is a PROXY and must always be described as one.**  The owner asked for the
SSE Composite (上证指数) through the qieman MCP; that endpoint serves real index closes
only for CSI 300 (000300), ChiNext (399006), the Dow, the Nasdaq and London gold, and
not for the SSE Composite.  What ships instead is the unit NAV of fund 510760, an
SSE-Composite tracking ETF.  Two consequences the rest of the engine depends on:

* the numbers are fund unit NAVs, not index points, so an absolute level is meaningless
  and only a RETURN over the series may ever be used or shown;
* every prompt, report and paper sentence naming it says
  "上证综指ETF（510760）单位净值，作为上证综指的代理" and never "上证指数".

The file lives under `data/market/`, which is gitignored: it is third-party data, held
to the same rule as the NAV cache and never redistributed.  So `market.benchmark_path`
defaults to null and every path here must behave correctly when the file is absent --
which is also why the three shipped demos leave `index_5d` out of the view entirely
rather than carrying a placeholder.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

# A benchmark series is only usable for a simulated day if it actually covers that day.
# Five trading days span at most about nine calendar days across a normal weekend plus a
# public holiday; a mainland market break (National Day, Spring Festival) can push the
# most recent prior observation further out, so the staleness bound is deliberately
# generous. Past it the series has simply ended and the honest answer is no line at all.
MAX_STALE_DAYS = 16

# index_5d is a five-trading-day change, which needs six observations.
_POINTS = 6


def load_benchmark(path):
    """Read a benchmark file into {date: float}; return None when there is nothing to read.

    Tolerates the `_meta` block the fetcher writes (a bare `json.load` plus
    `date.fromisoformat` over every key is exactly how the NAV loader once died with
    `Invalid isoformat string: 'generator'`), and accepts both the wrapped
    `{"_meta": ..., "series": {...}}` shape and a flat `{date: value}` mapping.

    Raises on a configured-but-unreadable path.  A silently ignored input is the defect
    class this whole round exists to remove: if an operator names a benchmark file, a
    typo in the path must stop the run rather than quietly produce a text-only market
    line that looks the same as not configuring one at all.
    """
    if not path:
        return None
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"market.benchmark_path is configured but unreadable: {path} -- "
            "data/market/ is gitignored, so regenerate it with "
            "data_pipeline/cn/fetch_benchmark.py or unset the key")
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    raw = obj.get("series") if isinstance(obj, dict) and "series" in obj else obj
    if not isinstance(raw, dict):
        raise ValueError(f"benchmark file {path} has no series mapping")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or len(k) != 10 or k[4] != "-":
            continue                      # skips _meta and any other non-date key
        try:
            date.fromisoformat(k)
            val = float(v)
        except (TypeError, ValueError):
            continue
        if val > 0:                       # a non-positive NAV is not a quote
            out[k] = val
    if not out:
        raise ValueError(f"benchmark file {path} carries no usable dated values")
    return out


def sorted_dates(series):
    """The series' dates, ascending, ready to be sliced per simulated day."""
    return sorted(series or ())


def pct_5d(series, dates_sorted, dt_cur):
    """Five-trading-day return over the benchmark, or None when it cannot be formed.

    Only observations dated STRICTLY BEFORE `dt_cur` are read.  Invariant (a) forbids
    look-ahead and this codebase has already shipped exactly that bug once, in a week
    key that stepped back one day instead of one week, so the cut is explicit here and
    tested rather than inferred from how the caller happens to slice.

    Returns None -- and the caller then omits the key, because `render_news` skips an
    absent key -- when there is no series, when fewer than six prior observations exist,
    or when the most recent prior observation is more than MAX_STALE_DAYS old. Never
    interpolates and never forward-fills a gap: a fabricated quote in a market line is
    worse than a missing line.
    """
    if not series or not dates_sorted:
        return None
    cut = dt_cur.isoformat()
    lo, hi = 0, len(dates_sorted)
    while lo < hi:                        # rightmost index with date < dt_cur
        mid = (lo + hi) // 2
        if dates_sorted[mid] < cut:
            lo = mid + 1
        else:
            hi = mid
    if lo < _POINTS:
        return None
    window = dates_sorted[lo - _POINTS:lo]
    if date.fromisoformat(window[-1]) < dt_cur - timedelta(days=MAX_STALE_DAYS):
        return None                       # the series ended before this simulated day
    first, last = series.get(window[0]), series.get(window[-1])
    if not first or not last or first <= 0:
        return None
    return round(last / first - 1.0, 4)

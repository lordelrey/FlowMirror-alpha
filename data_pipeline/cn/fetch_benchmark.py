"""Fetch the benchmark series behind the news channel's market line.

`data/market/` is git-ignored, so a clean clone carries no benchmark and
`flowmirror.engine.benchmark.load_benchmark` aborts with a message pointing here. This
script is that recovery path.

**What it fetches, and why it is a proxy.** The owner asked for the SSE Composite
(上证指数). The qieman/盈米 endpoint serves real index closes only for CSI 300 (000300),
ChiNext (399006), the Dow, the Nasdaq and London gold -- the SSE Composite is not among
them, and in fund-code space `000001` is 华夏成长混合, not the index. So this fetches the
unit NAV of `510760`, an SSE-Composite tracking ETF, and the output records that it is
a proxy. Two rules follow and are enforced downstream:

* the values are fund unit NAVs, not index points, so only a RETURN over the series is
  ever used or shown (`benchmark.pct_5d`);
* every prompt, report and paper sentence naming it says
  "上证综指ETF（510760）单位净值，作为上证综指的代理" and never "上证指数". The engine
  refuses to run a configured benchmark whose `market.benchmark_label` is empty, because
  that label is what the agent-facing line names.

Credentials: the key is read from a file OUTSIDE this repository (default
`../tools/.qieman_key`, i.e. a sibling of the checkout) or from `QIEMAN_API_KEY`. It is
never printed, never logged and never written into the output.

Usage:
    python data_pipeline/cn/fetch_benchmark.py
    python data_pipeline/cn/fetch_benchmark.py --code 510760 --out data/market/bench.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ENDPOINT = "https://stargate.yingmi.com/mcp/v2"
DEFAULT_CODE = "510760"
DEFAULT_LABEL = "上证综指ETF（510760）单位净值（上证综指的代理）"
DEFAULT_OUT = "data/market/benchmark_sse_composite_etf_510760.json"
# The simulation window plus the five trading days index_5d looks back over.
WINDOW = ("2025-09-24", "2025-12-31")
MIN_TRADING_DAYS = 240
MIN_IN_WINDOW = 60

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
KEY_CANDIDATES = (
    os.path.join(ROOT, os.pardir, "tools", ".qieman_key"),
    os.path.join(ROOT, os.pardir, os.pardir, "tools", ".qieman_key"),
)


def read_key(explicit=None) -> str:
    """The key, from an explicit path, the environment, or a sibling tools directory.

    Never returned to a caller that prints it: every call site here passes it straight
    into a request header.
    """
    if explicit:
        with open(explicit, encoding="utf-8") as fh:
            return fh.read().strip()
    env = os.environ.get("QIEMAN_API_KEY")
    if env and env.strip():
        return env.strip()
    for cand in KEY_CANDIDATES:
        cand = os.path.abspath(cand)
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                got = fh.read().strip()
            if got:
                return got
    raise SystemExit(
        "[FATAL] no qieman key: set QIEMAN_API_KEY, pass --key-file, or place the key in "
        "a .qieman_key file in a tools/ directory OUTSIDE this repository (it must never "
        "be committed)")


def rpc(key: str, method: str, params=None, rpc_id=1) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                       "params": params or {}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "x-api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[FATAL] {method} failed: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"[FATAL] {method} failed: {exc.reason}") from None


# The tool answers with a compact YAML-ish text rather than JSON: rows read
#   data[N]{navDate,nav,dailyReturn}
# with the date as YYYY年MM月DD日. Parsing the digits directly is far more robust than
# guessing at the envelope, which has changed shape before.
_ROW = re.compile(r"(\d{4})年(\d{2})月(\d{2})日,([\d.]+),")


def fetch_series(key: str, code: str) -> dict:
    rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "flowmirror", "version": "1"}}, 1)
    rpc(key, "notifications/initialized", {}, 2)
    raw = rpc(key, "tools/call", {"name": "BatchGetFundNavHistory",
                                  "arguments": {"fundCodes": [code], "isDesc": False,
                                                "dimensionType": "oneYear"}}, 3)
    out = {}
    for y, m, d, nav in _ROW.findall(raw):
        try:
            val = float(nav)
        except ValueError:
            continue
        if val > 0:
            out[f"{y}-{m}-{d}"] = val
    if not out:
        raise SystemExit(
            "[FATAL] no dated rows parsed from the response. The tool's text shape may "
            "have changed; inspect it before trusting any series built from it.")
    return dict(sorted(out.items()))


def check(series: dict, code: str) -> None:
    """Refuse to write a series that cannot support a five-trading-day return.

    Fetched by hand once, so it gets checked rather than trusted.
    """
    days = sorted(series)
    problems = []
    if len(days) < MIN_TRADING_DAYS:
        problems.append(f"only {len(days)} trading days (want >= {MIN_TRADING_DAYS})")
    in_win = [d for d in days if WINDOW[0] <= d <= WINDOW[1]]
    if len(in_win) < MIN_IN_WINDOW:
        problems.append(f"only {len(in_win)} days inside {WINDOW[0]}..{WINDOW[1]} "
                        f"(want >= {MIN_IN_WINDOW}); the window plus its five-day "
                        f"lookback would be uncovered")
    if any(v <= 0 for v in series.values()):
        problems.append("a non-positive NAV is not a quote")
    if in_win:
        gaps = [(a, b, (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days)
                for a, b in zip(in_win, in_win[1:])]
        worst = max(gaps, key=lambda g: g[2]) if gaps else None
        if worst and worst[2] > 16:
            problems.append(f"in-window gap of {worst[2]} days ({worst[0]} -> {worst[1]}) "
                            f"exceeds the staleness bound benchmark.py enforces")
    if problems:
        raise SystemExit("[FATAL] series rejected for " + code + ":\n  - "
                         + "\n  - ".join(problems))
    print(f"[ok] {len(days)} trading days {days[0]}..{days[-1]}; "
          f"{len(in_win)} inside {WINDOW[0]}..{WINDOW[1]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--code", default=DEFAULT_CODE, help=f"fund code (default {DEFAULT_CODE})")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"output path (default {DEFAULT_OUT})")
    ap.add_argument("--label", default=DEFAULT_LABEL,
                    help="the disclosing label; goes into _meta and is what "
                         "market.benchmark_label should be set to")
    ap.add_argument("--key-file", default=None, help="path to the API key file")
    args = ap.parse_args(argv)

    series = fetch_series(read_key(args.key_file), args.code)
    check(series, args.code)

    out = {
        "_meta": {
            "what": "Benchmark series for the news channel's index_5d line.",
            "series": f"unit NAV of {args.code}, standing in for the SSE Composite",
            "label": args.label,
            "why_a_proxy": "the qieman/yingmi endpoint exposes real index closes only for "
                           "000300, 399006, DJIA, IXIC and London gold; the SSE Composite "
                           "itself is not available there, so an index-tracking ETF's NAV "
                           "stands in and must be disclosed as a proxy, never as the index",
            "source": "qieman MCP (stargate.yingmi.com/mcp/v2), BatchGetFundNavHistory, "
                      "dimensionType=oneYear",
            "fetched_at": datetime.date.today().isoformat(),
            "trading_days": len(series),
            "range": [min(series), max(series)],
            "units": "fund unit NAV (CNY), not index points; only RETURNS over this "
                     "series are meaningful",
            "third_party": True,
            "redistribute": False,
        },
        "series": series,
    }
    path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"[ok] wrote {path} ({os.path.getsize(path) / 1024:.1f} KB)")
    print(f"[ok] set market.benchmark_label to: {args.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

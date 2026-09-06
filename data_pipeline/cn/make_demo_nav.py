#!/usr/bin/env python
"""data_pipeline/cn/make_demo_nav.py -- deterministic SYNTHETIC NAV cache for offline demos.

Card E4_nav (task 4). Offline artefact builder:

  python data_pipeline/cn/make_demo_nav.py \
      --pool data/creatives/cn/content_pool_v1_masked.jsonl \
      --out data/funds/nav_demo_2025q4.json

Why: the real NAV cache (data/funds/nav_cache.json, 4.4 MB, third-party) is
git-ignored, so a fresh clone cannot run any demo. This script reads the
SHIPPED content pool, harvests every valid fund code it references, and
writes one synthetic NAV series per code so the demo configs work offline.

Output shape (compact UTF-8 JSON, ASCII content, `_meta` first):

  {"_meta": {...},
   "005827": {"2025-01-02": 1.0, "2025-01-03": 0.9987, ...},
   ...}

Guarantees:
  - window 2025-01-02 .. 2025-12-31, weekdays Mon-Fri only (no trading
    calendar; the engine intersects the series with its own window anyway);
  - per code: rng = random.Random(rng_seed_from("nav_demo", code)); the draw
    order is FROZEN (and pinned in _meta.seed_rule): vol_annual ~ U(0.12,
    0.30), drift_annual ~ U(-0.10, 0.15), then one normalvariate(drift/252,
    vol/sqrt(252)) log-return per weekday AFTER the anchor;
  - the first weekday NAV is exactly 1.0 (no draw consumed on the anchor
    day); NAVs are rounded to 4 decimals; dates ascend strictly;
  - `_meta` (first key) labels the file synthetic=True with the generator
    path, the seed rule and a never-use-for-research warning. It is the
    ONLY non-6-digit key; consumers that iterate raw top-level keys should
    skip keys not matching ^[0-9]{6}$ (--no-meta exists as a last-resort
    escape hatch if such a consumer cannot be fixed);
  - fund risk levels / families are NOT written here; they live in the
    existing fund metadata inputs and are untouched.

Determinism: same pool + same flowmirror.io.hashing.rng_seed_from =>
byte-identical output (the post-write self-check regenerates the first and
last series and compares). Sets are sorted before iteration; no hash().
The output is written atomically (tempfile in the target dir + os.replace);
the pool file is never modified. Expected size for the 341-code pool is
~1.9 MB (target: < ~3 MB; the script warns if exceeded).

Console output is ASCII-only. Pure stdlib + the flowmirror package (hashing
helpers only; no network, no engine behaviour changed). The repo bootstrap
below the imports lets the documented plain-script invocation run from any
working directory, with or without an editable install.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# --- repo bootstrap (DEFECT 4) ---------------------------------------------------
# Running this file directly (the documented invocation) puts only its own
# directory on sys.path, so `import flowmirror` raises ModuleNotFoundError
# unless the package was pip-installed. Probe the import; on failure, walk
# up from __file__ to the first ancestor directory containing the flowmirror
# package (the repo root) and insert it into sys.path. No-op when flowmirror
# is already importable (python -m from the repo root, or pip install -e .).
# Paste-able verbatim into any repo-local script directly above its first
# flowmirror import (it needs only `import sys` above it).
try:
    import flowmirror  # noqa: F401 -- probe only; the real imports follow
except ModuleNotFoundError:
    from pathlib import Path
    for _p in Path(__file__).resolve().parents:
        if (_p / "flowmirror").is_dir():
            sys.path.insert(0, str(_p))
            break
    else:
        raise  # not inside a checkout and not installed: keep the real error

from flowmirror.io.hashing import rng_seed_from, sha256_text  # noqa: E402 (after bootstrap)

# --- frozen constants (NEVER edit: _meta.seed_rule and reproducibility pin them) ---
START_DATE = date(2025, 1, 2)        # first weekday of the window (2025-01-01 is a Wednesday)
END_DATE = date(2025, 12, 31)        # last day of the window
TRADING_DAYS_PER_YEAR = 252          # annualisation convention only
VOL_ANNUAL_RANGE = (0.12, 0.30)      # annualised volatility band
DRIFT_ANNUAL_RANGE = (-0.10, 0.15)   # annualised drift band
SEED_NAMESPACE = "nav_demo"          # rng_seed_from(SEED_NAMESPACE, code)

CODE_RE = re.compile(r"^[0-9]{6}$")
CODE_TOKEN_RE = re.compile(r"(?<![0-9])[0-9]{6}(?![0-9])")
META_KEY = "_meta"
GENERIC_KEYS = ("rows", "notes", "pool")  # mirrors engine _read_rows for .json pools

SEED_RULE = (
    "random.Random(rng_seed_from('nav_demo', <6-digit code>)); draw order: "
    "vol_annual=uniform(0.12,0.30); drift_annual=uniform(-0.10,0.15); then one "
    "normalvariate(drift_annual/252, vol_annual/sqrt(252)) per weekday after the "
    "anchor; nav[first weekday 2025-01-02]=1.0 (no draw); "
    "nav=round(prev*exp(r),4); weekdays Mon-Fri 2025-01-02..2025-12-31"
)
WARNING = ("Synthetic NAVs for offline demos only. NOT market data. "
           "Never use for any empirical claim.")


def _ascii_console() -> None:
    """Best-effort UTF-8 console (Windows GBK); everything printed stays ASCII."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # not a TextIOWrapper / already closed
            pass


def _die(msg: str) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(1)


def _weekday_dates(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _pool_rows(pool: Path) -> tuple[list[dict], str]:
    """Read .jsonl (one object per line) or .json (list / {rows|notes|pool}); -> (rows, sha256)."""
    try:
        text = pool.read_text(encoding="utf-8")
    except OSError as e:
        _die(f"cannot read content pool {pool}: {e}")
    pool_sha = sha256_text(text)
    if pool.suffix.lower() == ".jsonl":
        rows: list = []
        for ln, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                _die(f"{pool}:{ln}: line is not valid JSON")
            if isinstance(obj, dict):
                rows.append(obj)
        return rows, pool_sha
    try:
        obj = json.loads(text)
    except ValueError as e:
        _die(f"{pool}: not valid JSON: {e}")
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        rows = []
        for key in GENERIC_KEYS:
            val = obj.get(key)
            if isinstance(val, list):
                rows = val
                break
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)], pool_sha


def _codes_from_value(value: object) -> list[str]:
    """Defensively extract 6-digit codes from a `fund_codes_valid` field.

    Accepts real lists, JSON-encoded strings ('["005827", 110022]', '"005827"'),
    bare numeric codes, and free text (regex fallback); keeps only ^[0-9]{6}$.
    Blank/unreadable values yield [] and are never fatal.
    """
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            return _codes_from_value(json.loads(s))  # decoded payload shrinks each hop
        except ValueError:
            return CODE_TOKEN_RE.findall(s)
    if isinstance(value, (int, float)):
        if float(value).is_integer() and 100000 <= float(value) < 1000000:
            return [f"{int(value):06d}"]
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_codes_from_value(item))
        return out
    return []


def _collect_codes(rows: list[dict]) -> list[str]:
    codes: set[str] = set()
    for row in rows:
        codes.update(_codes_from_value(row.get("fund_codes_valid")))
    return sorted(codes)  # sorted before any iteration (determinism rule)


def _series_for_code(code: str, weekdays: list[date]) -> dict[str, float]:
    rng = random.Random(rng_seed_from(SEED_NAMESPACE, code))
    vol_annual = rng.uniform(*VOL_ANNUAL_RANGE)
    drift_annual = rng.uniform(*DRIFT_ANNUAL_RANGE)
    mu = drift_annual / TRADING_DAYS_PER_YEAR
    sigma = vol_annual / math.sqrt(TRADING_DAYS_PER_YEAR)
    series: dict[str, float] = {}
    nav = 1.0
    for i, d in enumerate(weekdays):
        if i > 0:  # anchor day keeps NAV exactly 1.0 and consumes no draw
            nav = round(nav * math.exp(rng.normalvariate(mu, sigma)), 4)
        series[d.isoformat()] = nav
    return series


def _atomic_write_json(out: Path, obj: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=True, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _self_check(out: Path, codes: list[str], weekdays: list[date], with_meta: bool) -> None:
    obj = json.loads(out.read_text(encoding="utf-8"))
    problems: list[str] = []
    expect = set(codes) | ({META_KEY} if with_meta else set())
    if set(obj) != expect:
        problems.append("top-level key set mismatch")
    series_codes = [k for k in obj if k != META_KEY]
    if series_codes != codes:
        problems.append("code keys differ from the sorted collected codes")
    n_days = len(weekdays)
    first_iso, last_iso = weekdays[0].isoformat(), weekdays[-1].isoformat()
    for code in series_codes:
        s = obj.get(code)
        if not isinstance(s, dict) or len(s) != n_days:
            problems.append(f"{code}: expected {n_days} NAV entries")
            continue
        keys = list(s)
        if keys != sorted(keys) or keys[0] != first_iso or keys[-1] != last_iso:
            problems.append(f"{code}: dates not ascending or outside the window")
            continue
        if any(date.fromisoformat(k).weekday() >= 5 for k in keys):
            problems.append(f"{code}: a date is not a weekday")
        if float(s[first_iso]) != 1.0:
            problems.append(f"{code}: anchor NAV != 1.0")
        if any(float(v) <= 0.0 for v in s.values()):
            problems.append(f"{code}: non-positive NAV")
    if with_meta:
        meta = obj.get(META_KEY)
        if not (isinstance(meta, dict) and meta.get("synthetic") is True
                and isinstance(meta.get("warning"), str)):
            problems.append("_meta missing or not labelled synthetic")
    for code in (series_codes[0], series_codes[-1]) if series_codes else ():
        if obj.get(code) != _series_for_code(code, weekdays):
            problems.append(f"{code}: regenerated series differs from the file")
    if problems:
        for p in problems[:10]:
            print(f"[make_demo_nav] SELF-CHECK FAILED: {p}", file=sys.stderr)
        _die(f"self-check failed with {len(problems)} problem(s); {out} is NOT trustworthy")
    extra = " + '_meta'" if with_meta else ""
    print(f"[make_demo_nav] self-check OK: {len(series_codes)} codes{extra}, "
          f"{n_days} weekday NAVs each, ascending dates, anchor=1.0, "
          f"regeneration is deterministic")


def _build(pool: Path, out: Path, with_meta: bool) -> None:
    weekdays = _weekday_dates(START_DATE, END_DATE)
    rows, pool_sha = _pool_rows(pool)
    if not rows:
        _die(f"content pool {pool} contains no JSON object rows")
    codes = _collect_codes(rows)
    if not codes:
        _die(f"no valid 6-digit fund codes found under 'fund_codes_valid' in {pool}")
    obj: dict = {}
    if with_meta:
        obj[META_KEY] = {
            "synthetic": True,
            "generator": "data_pipeline/cn/make_demo_nav.py",
            "seed_rule": SEED_RULE,
            "warning": WARNING,
            "n_codes": len(codes),
            "window": f"{START_DATE.isoformat()}..{END_DATE.isoformat()} weekdays",
            "pool_sha256": pool_sha,
        }
    for code in codes:  # sorted -> deterministic key order in the file
        obj[code] = _series_for_code(code, weekdays)
    _atomic_write_json(out, obj)
    size = out.stat().st_size
    print(f"[make_demo_nav] pool={pool} rows={len(rows)} codes={len(codes)} "
          f"days={len(weekdays)} window={weekdays[0]}..{weekdays[-1]}")
    print(f"[make_demo_nav] wrote {out} ({size} bytes)")
    if size > 3_000_000:
        print(f"[make_demo_nav] WARNING: {out} exceeds the ~3 MB budget ({size} bytes)")
    _self_check(out, codes, weekdays, with_meta)


def main(argv: list[str] | None = None) -> int:
    _ascii_console()
    ap = argparse.ArgumentParser(
        prog="make_demo_nav.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=("Generate a deterministic SYNTHETIC NAV cache (NOT market data; "
                     "the output is labelled synthetic in its '_meta' block)."),
        epilog=("documented invocation:\n"
                "  python data_pipeline/cn/make_demo_nav.py "
                "--pool data/creatives/cn/content_pool_v1_masked.jsonl "
                "--out data/funds/nav_demo_2025q4.json"),
    )
    ap.add_argument("--pool", default="data/creatives/cn/content_pool_v1_masked.jsonl",
                    help="content pool to harvest codes from (.jsonl, or .json list/dict)")
    ap.add_argument("--out", default="data/funds/nav_demo_2025q4.json",
                    help="output NAV cache path (written atomically)")
    ap.add_argument("--no-meta", action="store_true",
                    help="omit the '_meta' key; last-resort fallback ONLY for consumers "
                         "that cannot skip non-code keys (loses the in-file synthetic label)")
    args = ap.parse_args(argv)
    pool, out = Path(args.pool), Path(args.out)
    if not pool.is_file():
        _die(f"content pool not found: {pool}")
    _build(pool, out, with_meta=not args.no_meta)
    print("[make_demo_nav] synthetic NAV cache ready -- point the demo run configs at "
          f"{out}; remember it is NOT market data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

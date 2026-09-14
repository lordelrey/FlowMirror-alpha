"""Aggregated, point-in-time market phase summaries over MarketTape snapshots.

Read-only and deterministic: builds a MarketTape from run configuration, takes
whitelisted snapshots for the requested phase and its predecessor, and reports
per-quote changes plus per-group statistics. Never invents dates or prices.

Production shape: ``spec["phase_times"]`` is a list of aware ISO-8601 strings
indexed from zero (phase 0 is the first phase). If the list is absent or empty,
market observations are disabled.
"""
from __future__ import annotations

import math
import statistics

from flowmirror.market.tape import MarketTape, instant

EPS = 1e-10
STALE_AFTER_SECONDS = 86400


def _instant_key(value):
    """Parse an ISO instant using the tape's own parser; naive values stay naive."""
    parsed = instant(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=None), False
    return parsed, True


def _compare_instants(left, right):
    """Order two instants without inventing timezones for naive values."""
    a, a_aware = _instant_key(left)
    b, b_aware = _instant_key(right)
    if a_aware != b_aware:
        return False
    return a > b


def _validate_phase(phase, times):
    if isinstance(phase, bool) or not isinstance(phase, int):
        raise ValueError("phase must be an integer index into phase_times")
    if phase < 0 or phase >= len(times):
        raise ValueError(
            "phase %r outside configured phase_times range [0, %d)" % (phase, len(times))
        )


def _support_universe(spec):
    """Union of market_data definitions and trading.instruments, keyed by (market, instrument_id)."""
    support = {}
    for row in spec.get("market_data") or []:
        if not isinstance(row, dict):
            continue
        key = (row.get("market"), row.get("instrument_id"))
        if key[0] is None or key[1] is None:
            continue
        if key not in support:
            support[key] = (row.get("currency"), row.get("kind"))
    trading = spec.get("trading") or {}
    for inst in trading.get("instruments") or []:
        if not isinstance(inst, dict):
            continue
        key = (inst.get("market"), inst.get("instrument_id"))
        if key[0] is None or key[1] is None:
            continue
        if key not in support:
            support[key] = (inst.get("currency"), inst.get("kind"))
    return support


def _quote_maps(snapshot):
    """Index snapshot quotes by (market, instrument_id) once for O(N) lookups."""
    if not snapshot:
        return {}
    return {
        (q["market"], q["instrument_id"]): q for q in snapshot.get("quotes", [])
    }


def market_phase(spec, phase):
    """Return a read-only market phase summary for the run's configured support."""
    result = {
        "enabled": False,
        "as_of": None,
        "previous_as_of": None,
        "price_mode": "exogenous_observations",
        "is_live_feed": False,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "groups": [],
        "quotes": [],
        "notice": "",
    }

    times = spec.get("phase_times") or []
    if not isinstance(times, list):
        raise ValueError("phase_times must be a list of ISO-8601 instant strings")
    rows = spec.get("market_data") or []

    if not times:
        result["notice"] = "no phase time configured; market observations disabled"
        return result

    _validate_phase(phase, times)
    support = _support_universe(spec)

    result["enabled"] = True
    result["as_of"] = times[phase]
    result["previous_as_of"] = times[phase - 1] if phase > 0 else None

    if not support:
        result["notice"] = "no instruments configured for this run"
        return result

    if not rows:
        result["quotes"] = [
            {"market": m, "instrument_id": i, "currency": c, "kind": k,
             "price": None, "change_pct": None, "comparison_status": "missing"}
            for (m, i), (c, k) in sorted(support.items())
        ]
        result["groups"] = _build_groups(result["quotes"])
        result["notice"] = "no market_data configured; all support quotes missing"
        return result

    tape = MarketTape(rows)
    current_map = _quote_maps(tape.snapshot(times[phase]))
    prior_map = (
        _quote_maps(tape.snapshot(times[phase - 1]))
        if result["previous_as_of"] is not None else {}
    )

    quotes = []
    for key in sorted(support):
        market, instrument_id = key
        currency, kind = support[key]
        cur = current_map.get(key)
        if cur is None:
            quotes.append({
                "market": market, "instrument_id": instrument_id,
                "currency": currency, "kind": kind,
                "price": None, "change_pct": None,
                "comparison_status": "missing",
            })
            continue
        prev = prior_map.get(key)
        quote = {
            "market": cur["market"], "instrument_id": cur["instrument_id"],
            "currency": cur["currency"], "kind": cur["kind"],
            "price": cur["price"],
            "observed_at": cur["observed_at"], "available_at": cur["available_at"],
            "source": cur["source"], "synthetic": cur["synthetic"],
            "age_seconds": cur["age_seconds"],
            "stale": cur["age_seconds"] > STALE_AFTER_SECONDS,
            "previous_price": None, "change_pct": None,
            "comparison_status": "first_phase",
            "quote_advanced": False,
        }
        if prev is None:
            if result["previous_as_of"] is None:
                quote["comparison_status"] = "first_phase"
            else:
                quote["comparison_status"] = "missing_previous"
        else:
            quote["previous_price"] = prev["price"]
            if prev["price"]:
                change = (cur["price"] / prev["price"] - 1.0) * 100.0
                if math.isfinite(change):
                    quote["change_pct"] = change
                    quote["comparison_status"] = "comparable"
                else:
                    quote["comparison_status"] = "not_comparable"
            else:
                quote["comparison_status"] = "not_comparable"
            quote["quote_advanced"] = _compare_instants(
                cur["observed_at"], prev["observed_at"]
            )
        quotes.append(quote)

    result["quotes"] = quotes
    result["groups"] = _build_groups(quotes)
    result["notice"] = "point-in-time exogenous observations; no future data exposed"
    return result


def _build_groups(quotes):
    grouped = {}
    for quote in quotes:
        key = (quote["market"], quote["currency"], quote["kind"])
        grouped.setdefault(key, []).append(quote)
    groups = []
    for key in sorted(grouped, key=lambda k: tuple(str(x) for x in k)):
        members = grouped[key]
        changes = [q["change_pct"] for q in members
                   if q.get("comparison_status") == "comparable"
                   and q.get("change_pct") is not None]
        up = sum(1 for c in changes if c > EPS)
        down = sum(1 for c in changes if c < -EPS)
        flat = len(changes) - up - down
        groups.append({
            "market": key[0], "currency": key[1], "kind": key[2],
            "support_count": len(members),
            "quoted_count": sum(1 for q in members if q.get("price") is not None),
            "missing_count": sum(1 for q in members if q.get("price") is None),
            "comparable_count": len(changes),
            "up_count": up, "down_count": down, "flat_count": flat,
            "mean_change_pct": (sum(changes) / len(changes)) if changes else None,
            "median_change_pct": statistics.median(changes) if changes else None,
            "advanced_count": sum(1 for q in members if q.get("quote_advanced")),
            "stale_count": sum(1 for q in members if q.get("stale")),
            "synthetic_count": sum(1 for q in members if q.get("synthetic")),
            "observed_source_count": sum(1 for q in members
                                         if q.get("synthetic") is False),
        })
    return groups

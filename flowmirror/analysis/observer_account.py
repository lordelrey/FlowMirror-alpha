"""Reconstruct an aggregate (weighted-average cost) account projection
from recorded openings and executed actions. Pure stdlib, no I/O."""

import math
from datetime import date

_KINDS = {"subscribe", "redeem", "dca"}


def _iso(d):
    return date(int(d[0:4]), int(d[5:7]), int(d[8:10]))


def _finite_nonneg(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0


def _valid_nav(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _r2(x):
    return None if x is None else round(x + 0.0, 2)


def reconstruct_account(opening, actions, nav_cache, asof):
    # -- validate asof (REQUIRED, ISO YYYY-MM-DD) --
    if not isinstance(asof, str) or len(asof) != 10 or asof[4] != "-" or asof[7] != "-":
        raise ValueError("invalid asof")
    try:
        asof_d = _iso(asof)
    except ValueError:
        raise ValueError("invalid asof")

    notes = []
    warnings = 0

    hold = dict(opening.get("hold") or {})
    cost = dict(opening.get("cost") or {})

    units_h = {}
    basis = {}
    cost_known = {}

    def init(code):
        if code not in units_h:
            u = hold.get(code, 0)
            units_h[code] = float(u) if _finite_nonneg(u) else 0.0
            c = cost.get(code)
            if code in cost and _valid_nav(c):
                cost_known[code] = True
                basis[code] = units_h[code] * float(c)
            else:
                cost_known[code] = (units_h[code] == 0.0)
                basis[code] = 0.0
                if units_h[code] > 0:
                    notes.append("opening cost basis missing for %s; cost fields unknown" % code)

    # REQ 1: initialize EVERY opening hold code up front, not lazily per action
    for code in hold:
        if isinstance(code, str) and code:
            init(code)

    cash_change = 0.0
    fees_total = 0.0
    realized_total = 0.0
    uncertain = False

    for act in actions:
        kind = act.get("kind")
        if kind not in _KINDS:
            # unknown (non-financial) kinds may be skipped...
            notes.append("unrecognized action kind: %r skipped" % (kind,))
            warnings += 1
            continue
        # ...but malformed REAL trades must raise, never silently skip
        code = act.get("fund") or act.get("code")
        amt = act.get("amt")
        fee = act.get("fee", 0) or 0
        nav = act.get("nav")
        if not isinstance(code, str) or not code:
            raise ValueError("malformed %s action: missing fund code" % kind)
        if not (_finite_nonneg(amt) and _finite_nonneg(fee) and _valid_nav(nav)):
            raise ValueError("malformed %s action: invalid amt/fee/nav for %s" % (kind, code))
        amt, fee, nav = float(amt), float(fee), float(nav)
        if fee > amt:
            raise ValueError("malformed %s action: fee exceeds amt for %s" % (kind, code))
        init(code)

        # REQ 2: explicit units take precedence when present; must be valid
        explicit_units = act.get("units")
        has_explicit = "units" in act

        if kind in ("subscribe", "dca"):
            net = amt - fee
            if has_explicit:
                if not _finite_nonneg(explicit_units):
                    raise ValueError("malformed %s action: invalid explicit units for %s" % (kind, code))
                units = float(explicit_units)
            else:
                units = net / nav
            units_h[code] += units
            basis[code] += net
            cash_change -= amt
            fees_total += fee
        else:  # redeem
            if has_explicit:
                if not _finite_nonneg(explicit_units):
                    raise ValueError("malformed redeem action: invalid explicit units for %s" % code)
                units = float(explicit_units)
            else:
                units = amt / nav
            avg = (basis[code] / units_h[code]) if cost_known[code] and units_h[code] > 0 else None
            basis_before = basis[code]
            if units_h[code] > 0:
                basis[code] = basis_before * (1 - units / units_h[code])
            units_h[code] -= units
            cash_change += amt - fee
            fees_total += fee
            pnl = act.get("pnl")
            if isinstance(pnl, (int, float)) and not isinstance(pnl, bool) and math.isfinite(pnl):
                realized_total += float(pnl)
            elif avg is not None:
                realized_total += amt - units * avg
            else:
                notes.append("realized pnl unknown for one %s redeem (no cost basis)" % code)
                uncertain = True

    # clamp tiny negatives from recorded rounding
    for code in list(units_h):
        if -1e-5 <= units_h[code] < 0:
            units_h[code] = 0.0
            notes.append("tiny negative units for %s clamped to 0 (recorded rounding)" % code)
        if units_h[code] < 0:
            raise ValueError("inconsistent recorded holdings")

    # NAV as-of: max eligible date <= asof per fund, positive finite only
    positions = []
    mv_total = 0.0
    mv_known = True
    for code in sorted(units_h):
        u = units_h[code]
        if u <= 0:
            continue
        best = None
        series = nav_cache.get(code) or {}
        for d, v in series.items():
            if not isinstance(d, str) or len(d) != 10:
                continue
            try:
                if _iso(d) > asof_d:
                    continue
            except ValueError:
                continue
            if not _valid_nav(v):
                continue
            if best is None or d > best[0]:
                best = (d, float(v))
        nav_v = best[1] if best else None
        nav_date = best[0] if best else None  # REQ 4: actual date of the NAV used
        cnav = (basis[code] / u) if cost_known[code] and u > 0 else None
        mv = nav_v * u if nav_v is not None else None
        if mv is None:
            mv_known = False
        else:
            mv_total += mv
        positions.append({
            "fund": code,
            "units": u,
            "cost_nav": cnav,
            "nav": nav_v,
            "nav_date": nav_date,
            "market_value": _r2(mv),
            "unrealized_pnl": _r2(mv - basis[code]) if (mv is not None and cost_known[code]) else None,
        })

    market_value = _r2(mv_total) if mv_known else None
    if not mv_known:
        notes.append("market value unavailable: at least one positive position lacks NAV on/before asof")
    notes.append("realized_pnl is BEFORE fees; fees reported separately")
    notes.append("cash and return_pct are null: initial cash is not recorded")

    return {
        "cash": None,
        "cash_change": _r2(cash_change),
        "fees": _r2(fees_total),
        "positions": positions,
        "market_value": market_value,
        "realized_pnl": _r2(realized_total) if not uncertain else None,
        "return_pct": None,
        "valuation_date": asof,
        "source": "recorded_openings_and_actions",
        "notes": notes,
        "phase": "end_of_day",
    }

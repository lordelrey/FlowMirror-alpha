"""Observer-only macro finance aggregation for phase reports.

Pure, deterministic, JSON-safe reconstruction from broker views supplied
upstream. No world access, no writes, no network.
"""
from __future__ import annotations

import statistics
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MONEY_CENT = Decimal("0.01")
ZERO = Decimal("0")

_NO_CASH_EVENTS = ("submitted", "cancelled", "rejected")
_MONEY_EVENT = ("submitted",)  # events that carry no cash by broker schema
_COUNTED_EVENTS = ("submitted", "filled", "settled", "cancelled", "rejected")

_EVENT_KEYS = ("agent_id", "order_id", "index", "event", "at", "market",
               "instrument_id", "currency", "units", "price", "fee", "gross",
               "cash_delta", "receivable", "realized_pnl_net", "reserved_cash",
               "reserved_units", "reason", "side", "status")

_ORDER_KEYS = ("agent_id", "order_id", "market", "instrument_id", "currency",
               "kind", "side", "status", "submitted_at", "gross", "fee",
               "units", "source_post_id")

_ACCOUNT_KEYS = ("agent_id", "currency", "cash", "available_cash", "reserved_cash",
                 "receivable", "market_value", "known_market_value", "equity",
                 "fees", "return_pct", "realized_pnl_net", "unrealized_pnl_net",
                 "missing_valuations")

_CURRENCY_KEYS = ("agent_count", "valued_count", "return_count", "loss_count",
                  "loss_rate", "cash", "available_cash", "reserved_cash",
                  "receivable", "known_market_value", "equity", "fees",
                  "known_equity", "missing_equity_count",
                  "return_min_pct", "return_median_pct", "return_max_pct")


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _money2(value):
    if value is None:
        return None
    return float(value.quantize(MONEY_CENT, rounding=ROUND_HALF_UP))


def _num(value):
    result = _dec(value)
    return None if result is None else float(result)


def _sum_dec(values):
    """Sum Decimals; any unknown (None) propagates as None."""
    total = ZERO
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _sub_ref(submission):
    if not submission:
        return None
    return {"phase": submission.get("phase"),
            "step": submission.get("step"),
            "index": submission.get("index")}


def _ledger_map(ending):
    seen = {}
    for agent_id, view in (ending or {}).items():
        for event in view.get("ledger", []) or []:
            index = event.get("index")
            if index is None:
                continue
            key = (agent_id, index)
            if key not in seen:
                seen[key] = event
    return seen


def _order_lookup(ending):
    lookup = {}
    for agent_id, view in (ending or {}).items():
        for order in view.get("orders", []) or []:
            lookup[(agent_id, order.get("order_id"))] = order
    return lookup


def _event_cash_delta(event):
    """Per broker schema: absent cash_delta on submitted/cancelled/rejected
    means no movement (0). Present null, or filled/settled without
    cash_delta, means unknown (None)."""
    name = event.get("event")
    if "cash_delta" not in event:
        return ZERO if name in _NO_CASH_EVENTS else None
    return _dec(event.get("cash_delta"))


def finance_phase(spec, ending, previous_ending, phase_frames, submissions, phase):
    """Aggregate broker views and phase action frames into a JSON-safe report."""
    if not isinstance(spec, dict) or "trading" not in spec or not ending:
        return {"enabled": False, "accounts": [], "currencies": [],
                "flows": [], "events": [], "orders": [],
                "actions": {"accepted": {}, "rejected": {}},
                "notice": "no simulated accounts/trading"}

    submissions = submissions or {}
    phase_frames = phase_frames or []
    previous_ending = previous_ending or {}

    current = _ledger_map(ending)
    prior = _ledger_map(previous_ending)
    new_keys = [key for key in current if key not in prior]

    # events (new this phase, deduped by (actor, index))
    events = []
    by_order = {}
    for key in new_keys:
        event = current[key]
        actor, _index = key
        clean = {k: _num(event[k]) if k in ("units", "price", "fee", "gross",
                                            "cash_delta", "receivable",
                                            "realized_pnl_net", "reserved_cash",
                                            "reserved_units")
                 else event.get(k) for k in _EVENT_KEYS if k in event}
        clean["observed_phase"] = phase
        events.append(clean)
        by_order.setdefault((actor, event.get("order_id")), []).append(clean["event"])
    events.sort(key=lambda e: (e.get("agent_id"), e.get("index")))

    # orders (cumulative as of phase endpoint)
    orders = []
    seen_orders = set()
    orders_lookup = _order_lookup(ending)
    for agent_id in sorted(ending):
        for order in ending[agent_id].get("orders", []) or []:
            oid = order.get("order_id")
            okey = (agent_id, oid)
            if okey in seen_orders or oid is None:
                continue
            seen_orders.add(okey)
            kind = order.get("kind")
            if not isinstance(kind, str):
                kind = None
            row = {k: order.get(k) for k in _ORDER_KEYS}
            row["gross"] = _money2(_dec(order.get("gross")))
            row["fee"] = _money2(_dec(order.get("fee")))
            row["units"] = _num(order.get("units"))
            row["submission"] = _sub_ref(submissions.get((agent_id, oid)))
            row["phase_events"] = by_order.get(okey, [])
            orders.append(row)

    # flows grouped by instrument over NEW events
    groups = {}
    for key in new_keys:
        event = current[key]
        gkey = (event.get("market"), event.get("instrument_id"), event.get("currency"))
        groups.setdefault(gkey, []).append(event)
    flows = []
    for gkey in sorted(groups, key=lambda k: (str(k[0]), str(k[1]), str(k[2]))):
        gevents = groups[gkey]
        counts = {"submitted": 0, "filled": 0, "settled": 0,
                  "cancelled": 0, "rejected": 0}
        buy_gross, sell_gross, fees = [], [], []
        cash_deltas = []
        for event in gevents:
            name = event.get("event")
            if name in counts:
                counts[name] += 1
            if name == "filled":
                order = orders_lookup.get((event.get("agent_id"), event.get("order_id")))
                if order is None:
                    buy_gross.append(None)
                    sell_gross.append(None)
                    fees.append(None)
                else:
                    gross = _dec(order.get("gross"))
                    fee = _dec(order.get("fee"))
                    if gross is not None:
                        (buy_gross if order.get("side") == "buy" else sell_gross).append(gross)
                    else:
                        buy_gross.append(None)
                        sell_gross.append(None)
                    fees.append(fee)
            cash_deltas.append(_event_cash_delta(event))
        buy_total = _sum_dec(buy_gross)
        sell_total = _sum_dec(sell_gross)
        row = {"market": gkey[0], "instrument_id": gkey[1], "currency": gkey[2],
               **counts,
               "buy_gross": _money2(buy_total),
               "sell_gross": _money2(sell_total),
               "fees": _money2(_sum_dec(fees)),
               "cash_delta": _money2(_sum_dec(cash_deltas)),
               "net_executed_gross": _money2(
                   buy_total - sell_total
                   if buy_total is not None and sell_total is not None else None)}
        flows.append(row)

    # actions from phase frames
    accepted, rejected = {}, {}
    for frame in phase_frames:
        action = frame.get("action") or {}
        result = frame.get("result") or {}
        kind = action.get("kind")
        if not isinstance(kind, str):
            kind = "invalid"
        status = result.get("status")
        bucket = accepted if status == "accepted" else rejected
        bucket[kind] = bucket.get(kind, 0) + 1

    # accounts
    accounts = []
    by_currency = {}
    for agent_id in sorted(ending):
        view = ending[agent_id]
        cash = _dec(view.get("cash"))
        avail = _dec(view.get("available_cash"))
        reserved = _dec(view.get("reserved_cash"))
        receivable = _dec(view.get("receivable"))
        fees = _dec(view.get("fees"))
        known_mv = _dec(view.get("known_market_value"))
        missing = view.get("missing_valuations") or []
        equity = _dec(view.get("equity"))
        known_equity = _sum_dec([cash, known_mv, receivable])
        equity_unknown = equity is None
        row = {k: view.get(k) for k in _ACCOUNT_KEYS}
        row["agent_id"] = agent_id
        row["cash"] = _money2(cash)
        row["available_cash"] = _money2(avail)
        row["reserved_cash"] = _money2(reserved)
        row["receivable"] = _money2(receivable)
        row["market_value"] = None if equity_unknown else _money2(_dec(view.get("market_value")))
        row["known_market_value"] = _money2(known_mv)
        row["equity"] = _money2(equity)
        row["fees"] = _money2(fees)
        row["return_pct"] = _num(view.get("return_pct"))
        row["realized_pnl_net"] = _money2(_dec(view.get("realized_pnl_net")))
        row["unrealized_pnl_net"] = None if equity_unknown else _money2(_dec(view.get("unrealized_pnl_net")))
        row["missing_valuations"] = [{"market": p.get("market"),
                                      "instrument_id": p.get("instrument_id")}
                                     for p in missing]
        row["positions"] = [{"market": p.get("market"),
                             "instrument_id": p.get("instrument_id"),
                             "units": _num(p.get("units")),
                             "market_value": _money2(_dec(p.get("market_value"))),
                             "cost_basis": _money2(_dec(p.get("cost_basis")))}
                            for p in (view.get("positions") or [])]
        row["known_equity"] = _money2(known_equity)
        row["missing_equity_count"] = 1 if equity_unknown else 0
        accounts.append(row)
        bucket = by_currency.setdefault(view.get("currency"), {
            "agent_count": 0, "valued_count": 0, "return_count": 0, "loss_count": 0,
            "cash": [], "available_cash": [], "reserved_cash": [], "receivable": [],
            "known_market_value": [], "equity": [], "fees": [],
            "known_equity": [], "missing_equity_count": 0, "returns": []})
        bucket["agent_count"] += 1
        if equity is not None:
            bucket["valued_count"] += 1
            bucket["equity"].append(equity)
        if equity_unknown:
            bucket["missing_equity_count"] += 1
        bucket["known_equity"].append(known_equity)
        for name, decval in (("cash", cash), ("available_cash", avail),
                             ("reserved_cash", reserved), ("receivable", receivable),
                             ("known_market_value", known_mv), ("fees", fees)):
            bucket[name].append(decval)
        r = _dec(view.get("return_pct"))
        if r is not None and equity is not None:
            bucket["returns"].append(r)
            bucket["return_count"] += 1
            if r < 0:
                bucket["loss_count"] += 1

    currencies = []
    for currency in sorted(by_currency, key=str):
        b = by_currency[currency]
        returns = sorted(b["returns"])
        losses = b["loss_count"]
        denom = b["return_count"]
        equity_vals = b["equity"]
        currencies.append({
            "currency": currency,
            "agent_count": b["agent_count"],
            "valued_count": b["valued_count"],
            "return_count": denom, "loss_count": losses,
            "loss_rate": (losses / denom) if denom else None,
            "cash": _money2(_sum_dec(b["cash"])),
            "available_cash": _money2(_sum_dec(b["available_cash"])),
            "reserved_cash": _money2(_sum_dec(b["reserved_cash"])),
            "receivable": _money2(_sum_dec(b["receivable"])),
            "known_market_value": _money2(_sum_dec(b["known_market_value"])),
            "equity": _money2(_sum_dec(equity_vals)) if len(equity_vals) == b["agent_count"] else None,
            "fees": _money2(_sum_dec(b["fees"])),
            "known_equity": _money2(_sum_dec(b["known_equity"])),
            "missing_equity_count": b["missing_equity_count"],
            "return_min_pct": _num(returns[0]) if returns else None,
            "return_median_pct": _num(statistics.median(returns)) if returns else None,
            "return_max_pct": _num(returns[-1]) if returns else None})

    return {"enabled": True, "accounts": accounts, "currencies": currencies,
            "flows": flows, "events": events, "orders": orders,
            "actions": {"accepted": accepted, "rejected": rejected},
            "notice": "Simulated broker accounts observed at phase endpoint."}

from decimal import Decimal

from flowmirror.analysis.macro_finance import finance_phase


SPEC = {"trading": {"instruments": [{"market": "CN", "instrument_id": "F1",
                                     "currency": "CNY"}]}}
NO_TRADING_SPEC = {"seed": 1}

_UNSET = object()


def view(agent, *, ledger_events=(), orders=(), cash="1000.00", equity=_UNSET,
         known_mv="0", missing=None, return_pct=None, currency="CNY",
         reserved="0", receivable="0", fees="0"):
    if equity is _UNSET:
        if missing:
            equity = None
        else:
            equity = str(Decimal(cash) + Decimal(known_mv) + Decimal(receivable))
    ledger_events = list(ledger_events)
    return {"currency": currency, "cash": cash,
            "available_cash": str(Decimal(cash) - Decimal(reserved)),
            "reserved_cash": reserved, "receivable": receivable,
            "known_market_value": known_mv,
            "market_value": None if missing else known_mv,
            "equity": equity, "fees": fees,
            "return_pct": return_pct, "realized_pnl_net": "0",
            "unrealized_pnl_net": "0" if equity is not None else None,
            "missing_valuations": missing or [],
            "positions": [], "orders": list(orders),
            "ledger": [{"index": i, **e} for i, e in enumerate(ledger_events)]}


def filled_event(agent_id, order_id, **kw):
    base = {"agent_id": agent_id, "order_id": order_id, "event": "filled",
            "market": "CN", "instrument_id": "F1", "currency": "CNY",
            "at": "2024-01-01T00:00:00", "units": 10, "price": "10.0",
            "cash_delta": "-101.00"}
    base.update(kw)
    return base


def order(agent_id, oid, **kw):
    base = {"agent_id": agent_id, "order_id": oid, "market": "CN",
            "instrument_id": "F1", "currency": "CNY", "kind": "subscribe",
            "side": "buy", "status": "submitted",
            "submitted_at": "2024-01-01", "units": 10, "gross": None, "fee": None}
    base.update(kw)
    return base


def test_same_order_id_two_actors_kept_separate_with_orders():
    e1 = view("a1", ledger_events=[
        {"agent_id": "a1", "order_id": "order_1", "event": "submitted",
         "market": "CN", "instrument_id": "F1", "currency": "CNY",
         "at": "2024-01-01T00:00:00"}],
        orders=[order("a1", "order_1")])
    e2 = view("a2", ledger_events=[
        {"agent_id": "a2", "order_id": "order_1", "event": "submitted",
         "market": "CN", "instrument_id": "F1", "currency": "CNY",
         "at": "2024-01-01T00:00:00"}],
        orders=[order("a2", "order_1")])
    prev = {"a1": view("a1"), "a2": view("a2")}
    res = finance_phase(SPEC, {"a1": e1, "a2": e2}, prev, [], {}, "p1")
    ids = {(o["agent_id"], o["order_id"]) for o in res["orders"]}
    assert ("a1", "order_1") in ids and ("a2", "order_1") in ids
    assert len(res["events"]) == 2
    assert res["flows"][0]["submitted"] == 2


def test_repeated_cumulative_ledger_no_double_count():
    ev = filled_event("a1", "order_1", fee="1.00", index=0)
    end = view("a1", ledger_events=[ev, dict(ev), dict(ev)],
               orders=[order("a1", "order_1", status="filled",
                             gross="100.00", fee="1.00")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    assert len(res["events"]) == 1
    assert res["flows"][0]["filled"] == 1
    assert res["flows"][0]["buy_gross"] == 100.0
    assert res["flows"][0]["cash_delta"] == -101.0


def test_new_fill_and_settle_same_phase_counts_gross_once():
    events = [
        {"agent_id": "a1", "order_id": "order_1", "event": "submitted",
         "market": "CN", "instrument_id": "F1", "currency": "CNY",
         "at": "2024-01-01T00:00:00"},
        filled_event("a1", "order_1", cash_delta="-101.00"),
        {"agent_id": "a1", "order_id": "order_1", "event": "settled",
         "market": "CN", "instrument_id": "F1", "currency": "CNY",
         "at": "2024-01-01T00:10:00", "cash_delta": "0"},
    ]
    end = view("a1", ledger_events=events,
               orders=[order("a1", "order_1", status="settled",
                             gross="100.00", fee="1.00")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["filled"] == 1 and flow["settled"] == 1
    assert flow["buy_gross"] == 100.0  # not 200
    assert flow["cash_delta"] == -101.0


def test_cancellation_no_flow():
    ev = [{"agent_id": "a1", "order_id": "order_1", "event": "submitted",
           "market": "CN", "instrument_id": "F1", "currency": "CNY",
           "at": "2024-01-01T00:00:00"},
          {"agent_id": "a1", "order_id": "order_1", "event": "cancelled",
           "market": "CN", "instrument_id": "F1", "currency": "CNY",
           "at": "2024-01-01T00:00:00"}]
    end = view("a1", ledger_events=ev,
               orders=[order("a1", "order_1", status="cancelled")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["cancelled"] == 1
    assert flow["buy_gross"] == 0.0 and flow["fees"] == 0.0
    assert flow["cash_delta"] == 0.0  # absent cash_delta means no movement


def test_immediate_reject_is_action_not_order_or_ledger():
    end = view("a1")
    frames = [{"index": 0, "phase": "p1", "agent_id": "a1", "step": 1,
               "action": {"kind": "subscribe", "amount": 5},
               "result": {"status": "rejected",
                          "reason": "insufficient_available_cash"}}]
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, frames, {}, "p1")
    assert res["actions"]["rejected"]["subscribe"] == 1
    assert res["events"] == [] and res["orders"] == [] and res["flows"] == []


def test_missing_valuation_is_null_not_zero():
    end = view("a1", known_mv="150.00", cash="100.00",
               missing=[{"market": "CN", "instrument_id": "X"}])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    acct = res["accounts"][0]
    assert acct["equity"] is None
    assert acct["market_value"] is None
    assert acct["known_market_value"] == 150.0
    assert acct["known_equity"] == 250.0
    cur = res["currencies"][0]
    assert cur["equity"] is None and cur["valued_count"] == 0
    assert cur["known_equity"] == 250.0 and cur["missing_equity_count"] == 1


def test_currencies_kept_separate():
    end = {"a1": view("a1", currency="CNY", cash="100.00", known_mv="0"),
           "a2": view("a2", currency="USD", cash="50.00", known_mv="0")}
    res = finance_phase(SPEC, end,
                        {k: view(k, currency=v["currency"]) for k, v in end.items()},
                        [], {}, "p1")
    assert {c["currency"] for c in res["currencies"]} == {"CNY", "USD"}
    cmap = {c["currency"]: c for c in res["currencies"]}
    assert cmap["CNY"]["cash"] == 100.0 and cmap["USD"]["cash"] == 50.0
    assert cmap["CNY"]["known_equity"] == 100.0
    assert cmap["USD"]["known_equity"] == 50.0


def test_cash_reserved_once_and_return_pct_untouched():
    end = view("a1", cash="900.00", reserved="100.00", return_pct=-2.5)
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    cur = res["currencies"][0]
    assert cur["cash"] == 900.0
    assert cur["available_cash"] + cur["reserved_cash"] == 900.0
    assert cur["available_cash"] == 800.0
    acct = res["accounts"][0]
    assert acct["return_pct"] == -2.5  # already percent
    assert cur["return_count"] == 1
    assert cur["loss_rate"] == 1.0


def test_disabled_without_trading_spec():
    res = finance_phase(NO_TRADING_SPEC, {}, {}, [], {}, "p1")
    assert res["enabled"] is False
    assert res["notice"] == "no simulated accounts/trading"
    assert res["accounts"] == [] and res["orders"] == []


def test_unknown_filled_amount_nulls_side_gross_and_net():
    ev = filled_event("a1", "order_1", fee="1.00")
    end = view("a1", ledger_events=[ev],
               orders=[order("a1", "order_1", status="filled",
                             gross=None, fee="1.00")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["buy_gross"] is None and flow["sell_gross"] is None
    assert flow["net_executed_gross"] is None
    assert flow["fees"] == 1.0
    assert flow["cash_delta"] == -101.0


def test_missing_order_on_filled_nulls_everything_money():
    end = view("a1", ledger_events=[filled_event("a1", "order_1")], orders=[])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["buy_gross"] is None and flow["sell_gross"] is None
    assert flow["fees"] is None and flow["net_executed_gross"] is None


def test_two_accounts_mixed_missing_equity_currency_null():
    end = {"a1": view("a1", cash="100.00", known_mv="50.00", missing=None),
           "a2": view("a2", cash="200.00", known_mv="70.00",
                      missing=[{"market": "CN", "instrument_id": "X"}])}
    res = finance_phase(SPEC, end, {"a1": view("a1"), "a2": view("a2")}, [], {}, "p1")
    cur = res["currencies"][0]
    assert cur["agent_count"] == 2
    assert cur["valued_count"] == 1
    assert cur["missing_equity_count"] == 1
    assert cur["equity"] is None  # any account missing -> null
    assert cur["known_equity"] == 420.0  # 150 + 270 partial sum
    assert cur["cash"] == 300.0


def test_even_sized_median_averages_two_middle():
    end = {"a1": view("a1", return_pct=-4.0, cash="100.00"),
           "a2": view("a2", return_pct=-2.0, cash="100.00"),
           "a3": view("a3", return_pct=1.0, cash="100.00"),
           "a4": view("a4", return_pct=6.0, cash="100.00")}
    prev = {a: view(a) for a in end}
    res = finance_phase(SPEC, end, prev, [], {}, "p1")
    cur = res["currencies"][0]
    assert cur["return_count"] == 4
    assert cur["return_median_pct"] == -0.5  # (-2.0 + 1.0) / 2
    assert cur["return_min_pct"] == -4.0 and cur["return_max_pct"] == 6.0
    assert cur["loss_rate"] == 0.5


def test_submission_is_whitelist_dict():
    end = view("a1", orders=[order("a1", "order_1", status="submitted")])
    subs = {("a1", "order_1"): {"phase": "p1", "step": 3, "index": 7}}
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], subs, "p1")
    assert res["orders"][0]["submission"] == {"phase": "p1", "step": 3, "index": 7}
    res2 = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    assert res2["orders"][0]["submission"] is None


def test_zero_fills_zero_money():
    end = view("a1", ledger_events=[
        {"agent_id": "a1", "order_id": "order_1", "event": "submitted",
         "market": "CN", "instrument_id": "F1", "currency": "CNY",
         "at": "2024-01-01T00:00:00"}],
        orders=[order("a1", "order_1", status="submitted")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["submitted"] == 1 and flow["filled"] == 0
    assert flow["buy_gross"] == 0.0 and flow["sell_gross"] == 0.0
    assert flow["fees"] == 0.0 and flow["cash_delta"] == 0.0
    assert flow["net_executed_gross"] == 0.0


def test_missing_fee_on_filled_propagates_null():
    end = view("a1", ledger_events=[filled_event("a1", "order_1", cash_delta="-100.00")],
               orders=[order("a1", "order_1", status="filled",
                             gross="100.00", fee=None)])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    flow = res["flows"][0]
    assert flow["buy_gross"] == 100.0
    assert flow["fees"] is None
    assert flow["net_executed_gross"] == 100.0


def test_null_cash_delta_is_unknown():
    end = view("a1", ledger_events=[filled_event("a1", "order_1", cash_delta=None)],
               orders=[order("a1", "order_1", status="filled",
                             gross="100.00", fee="1.00")])
    res = finance_phase(SPEC, {"a1": end}, {"a1": view("a1")}, [], {}, "p1")
    assert res["flows"][0]["cash_delta"] is None

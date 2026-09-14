import math

import pytest

from flowmirror.analysis.macro_market import market_phase


def _row(iid, market, currency, kind, price, observed, available, synthetic=False):
    return {"instrument_id": iid, "market": market, "currency": currency,
            "kind": kind, "price": price, "observed_at": observed,
            "available_at": available, "source": "provider", "synthetic": synthetic}


def _spec(rows, times):
    return {"market_data": rows, "phase_times": times, "trading": {}}


T1 = "2024-01-02T00:00:00+00:00"
T2 = "2024-01-06T00:00:00+00:00"


def test_disabled_without_times_or_data():
    out = market_phase(_spec([], []), 0)
    assert out["enabled"] is False
    assert out["quotes"] == [] and out["groups"] == []
    out2 = market_phase(_spec([_row("A", "CN", "CNY", "fund_nav", 1.0,
                                    "2024-01-01T00:00:00+00:00",
                                    "2024-01-02T00:00:00+00:00")], []), 0)
    assert out2["enabled"] is False


def test_invalid_phase_raises_when_times_present():
    spec = _spec([], [T1, T2])
    with pytest.raises(ValueError):
        market_phase(spec, 2)
    with pytest.raises(ValueError):
        market_phase(spec, -1)
    with pytest.raises(ValueError):
        market_phase(spec, "1")
    with pytest.raises(ValueError):
        market_phase(spec, True)


def test_empty_market_data_with_configured_support():
    spec = {"market_data": [],
            "phase_times": [T1],
            "trading": {"instruments": [{"market": "CN", "instrument_id": "A",
                                         "currency": "CNY", "kind": "fund_nav"}]}}
    out = market_phase(spec, 0)
    assert out["enabled"] is True
    q = out["quotes"][0]
    assert q["price"] is None and q["comparison_status"] == "missing"
    g = out["groups"][0]
    assert g["support_count"] == 1 and g["missing_count"] == 1
    assert g["mean_change_pct"] is None and g["median_change_pct"] is None


def test_availability_lag_hides_observations():
    rows = [_row("A", "CN", "CNY", "fund_nav", 2.0,
                 "2024-01-01T00:00:00+00:00", "2024-01-05T00:00:00+00:00")]
    spec = _spec(rows, [T1])
    out = market_phase(spec, 0)
    q = out["quotes"][0]
    assert q["price"] is None and q["comparison_status"] == "missing"


def test_no_future_leakage():
    rows = [_row("A", "US", "USD", "exchange_price", 10.0,
                 "2024-01-04T00:00:00+00:00", "2024-01-04T00:00:00+00:00")]
    spec = _spec(rows, ["2024-01-02T00:00:00+00:00", "2024-01-05T00:00:00+00:00"])
    out1 = market_phase(spec, 0)
    assert out1["quotes"][0]["price"] is None
    out2 = market_phase(spec, 1)
    assert out2["quotes"][0]["price"] == 10.0
    assert "observed_at" in out2["quotes"][0]


def test_first_phase_null_change():
    rows = [_row("A", "CN", "CNY", "fund_nav", 5.0,
                 "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00")]
    spec = _spec(rows, [T1])
    q = market_phase(spec, 0)["quotes"][0]
    assert q["change_pct"] is None and q["previous_price"] is None
    assert q["comparison_status"] == "first_phase"


def test_groups_cn_us_and_nav_exchange():
    rows = [
        _row("NAV1", "CN", "CNY", "fund_nav", 10.0,
             "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        _row("NAV1", "CN", "CNY", "fund_nav", 11.0,
             "2024-01-05T00:00:00+00:00", "2024-01-05T00:00:00+00:00"),
        _row("STK", "US", "USD", "exchange_price", 100.0,
             "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        _row("STK", "US", "USD", "exchange_price", 90.0,
             "2024-01-05T00:00:00+00:00", "2024-01-05T00:00:00+00:00"),
    ]
    spec = _spec(rows, [T1, T2])
    groups = market_phase(spec, 1)["groups"]
    assert [(g["market"], g["kind"]) for g in groups] == [("CN", "fund_nav"),
                                                          ("US", "exchange_price")]
    nav, stk = groups
    assert nav["comparable_count"] == 1 and nav["up_count"] == 1
    assert nav["mean_change_pct"] == pytest.approx(10.0)
    assert stk["down_count"] == 1 and stk["up_count"] == 0
    assert stk["mean_change_pct"] == pytest.approx(-10.0)


def test_missing_support_quote():
    spec = {"market_data": [_row("A", "CN", "CNY", "fund_nav", 5.0,
                                 "2024-01-01T00:00:00+00:00",
                                 "2024-01-01T00:00:00+00:00")],
            "phase_times": [T1],
            "trading": {"instruments": [{"market": "CN", "instrument_id": "B",
                                         "currency": "CNY", "kind": "fund_nav"}]}}
    out = market_phase(spec, 0)
    q = {x["instrument_id"]: x for x in out["quotes"]}
    assert q["B"]["price"] is None and q["B"]["comparison_status"] == "missing"
    g = out["groups"][0]
    assert g["support_count"] == 2 and g["quoted_count"] == 1 and g["missing_count"] == 1


def test_stale_same_quote_vs_advanced():
    rows = [
        _row("A", "CN", "CNY", "fund_nav", 5.0,
             "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        _row("B", "CN", "CNY", "fund_nav", 7.0,
             "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        _row("B", "CN", "CNY", "fund_nav", 7.0,
             "2024-01-03T00:00:00+00:00", "2024-01-03T00:00:00+00:00"),
    ]
    spec = _spec(rows, ["2024-01-02T00:00:00+00:00", "2024-01-20T00:00:00+00:00"])
    out = market_phase(spec, 1)
    q = {x["instrument_id"]: x for x in out["quotes"]}
    assert q["A"]["change_pct"] == pytest.approx(0.0)
    assert q["A"]["quote_advanced"] is False
    assert q["A"]["comparison_status"] == "comparable"
    assert q["A"]["stale"] is True and q["A"]["age_seconds"] > 86400
    assert q["B"]["quote_advanced"] is True and q["B"]["change_pct"] == pytest.approx(0.0)
    g = out["groups"][0]
    assert g["stale_count"] == 2 and g["advanced_count"] == 1
    assert g["flat_count"] == 2


def test_signed_changes_and_median():
    rows = []
    prices = {"A": (10.0, 11.0), "B": (10.0, 9.0), "C": (10.0, 10.5)}
    for iid, (p1, p2) in prices.items():
        rows.append(_row(iid, "US", "USD", "exchange_price", p1,
                         "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"))
        rows.append(_row(iid, "US", "USD", "exchange_price", p2,
                         "2024-01-05T00:00:00+00:00", "2024-01-05T00:00:00+00:00"))
    spec = _spec(rows, [T1, T2])
    out = market_phase(spec, 1)
    changes = {q["instrument_id"]: q["change_pct"] for q in out["quotes"]}
    assert math.isclose(changes["A"], 10.0)
    assert math.isclose(changes["B"], -10.0)
    assert math.isclose(changes["C"], 5.0)
    g = out["groups"][0]
    assert g["up_count"] == 2 and g["down_count"] == 1 and g["flat_count"] == 0
    assert math.isclose(g["median_change_pct"], 5.0)
    assert g["synthetic_count"] == 0 and g["observed_source_count"] == 3


def test_missing_previous_vs_first_phase():
    rows = [
        _row("A", "CN", "CNY", "fund_nav", 5.0,
             "2024-01-03T00:00:00+00:00", "2024-01-03T00:00:00+00:00"),
    ]
    spec = _spec(rows, ["2024-01-02T00:00:00+00:00", "2024-01-04T00:00:00+00:00"])
    q1 = market_phase(spec, 0)["quotes"][0]
    assert q1["comparison_status"] == "missing"
    q2 = market_phase(spec, 1)["quotes"][0]
    assert q2["comparison_status"] == "missing_previous"
    assert q2["change_pct"] is None and q2["previous_price"] is None


def test_missing_previous_quote_absent_at_phase_zero():
    rows = [
        _row("A", "CN", "CNY", "fund_nav", 5.0,
             "2024-01-03T00:00:00+00:00", "2024-01-03T00:00:00+00:00"),
    ]
    spec = _spec(rows, ["2024-01-01T00:00:00+00:00", "2024-01-04T00:00:00+00:00"])
    q0 = market_phase(spec, 0)["quotes"][0]
    assert q0["comparison_status"] == "missing"
    assert q0["price"] is None
    q1 = market_phase(spec, 1)["quotes"][0]
    assert q1["comparison_status"] == "missing_previous"

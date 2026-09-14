"""Point-in-time, read-only market observations for CN/US simulation clocks.

No downloads, execution, FX conversion, or learned price dynamics. A NAV is not
an exchange quote. Both its observation time and availability time are retained.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone


def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with UTC offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp needs an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


class MarketTape:
    def __init__(self, rows):
        if not isinstance(rows, list):
            raise ValueError("market_data must be a list")
        self._rows = []
        identities, instruments = set(), {}
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("market row must be an object")
            row = copy.deepcopy(raw)
            for key in ("instrument_id", "source"):
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise ValueError("market row needs " + key)
            if row.get("market") not in ("CN", "US"):
                raise ValueError("market must be CN or US")
            if row.get("currency") not in ("CNY", "USD"):
                raise ValueError("currency must be explicit CNY or USD")
            if row.get("kind") not in ("fund_nav", "exchange_price"):
                raise ValueError("kind must distinguish NAV from exchange price")
            if type(row.get("synthetic")) is not bool:
                raise ValueError("synthetic provenance must be explicit")
            price = row.get("price")
            if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                raise ValueError("price must be positive and finite")
            observed, available = instant(row["observed_at"]), instant(row["available_at"])
            if available < observed:
                raise ValueError("an observation cannot be available before it exists")
            identity = (row["market"], row["instrument_id"], observed, available)
            if identity in identities:
                raise ValueError("duplicate market observation")
            identities.add(identity)
            key = (row["market"], row["instrument_id"])
            definition = row["currency"], row["kind"]
            if key in instruments and instruments[key] != definition:
                raise ValueError("instrument currency/kind changed")
            instruments[key] = definition
            self._rows.append((observed, available, row))

    def snapshot(self, as_of, *, market=None):
        clock = instant(as_of)
        if market is not None and market not in ("CN", "US"):
            raise ValueError("unknown market")
        latest = {}
        for observed, available, row in self._rows:
            if available > clock or observed > clock or (market and row["market"] != market):
                continue
            key = (row["market"], row["instrument_id"])
            prior = latest.get(key)
            if prior is None or (observed, available) > prior[:2]:
                latest[key] = (observed, available, row)
        quotes = []
        for key in sorted(latest):
            observed, _, row = latest[key]
            # Whitelist public data; never deliver arbitrary provider credentials/metadata.
            quote = {k: row[k] for k in ("instrument_id", "market", "currency", "kind", "price",
                                         "observed_at", "available_at", "source", "synthetic")}
            quote["age_seconds"] = (clock - observed).total_seconds()
            quotes.append(quote)
        return {"as_of": as_of, "market": market, "quotes": quotes,
                "price_mode": "exogenous_observations", "is_live_feed": False}

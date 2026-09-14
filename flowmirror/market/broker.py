"""Deterministic simulation broker; no network, real orders, or price formation.

An explicit product schedule supplies cutoff, valuation and settlement times.
Orders reserve resources, wait for their designated published quote, then settle.
Internal accounting uses Decimal; observations are plain JSON data.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, localcontext
from functools import wraps

from flowmirror.market.tape import MarketTape, instant

TRADE_KINDS = ("subscribe", "redeem", "buy", "sell", "cancel_order")
ZERO = Decimal("0")
CENT = Decimal("0.01")


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("invalid numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid numeric value") from exc
    if (not result.is_finite() or result < 0 or result > Decimal('1e18')
            or (result and result < Decimal('1e-18')) or (positive and result == 0)):
        raise ValueError("value must be zero or within the positive numeric range 1e-18..1e18")
    return result


def accounting_context(method):
    """Keep large notionals and small prices independent of ambient precision."""
    @wraps(method)
    def call(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return method(*args, **kwargs)
    return call


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return copy.deepcopy(value)


class SimBroker:
    @accounting_context
    def __init__(self, config, agents, market_rows, initial_time):
        if not isinstance(config, dict) or not isinstance(config.get("instruments"), list):
            raise ValueError("trading needs an instruments list")
        self.products = {}
        self.orders = []
        self.accounts = {}
        self.ledger = []
        self.clock = instant(initial_time)
        self.tape = MarketTape(market_rows)
        self.rows = copy.deepcopy(market_rows)
        for raw in config["instruments"]:
            product = copy.deepcopy(raw)
            if not isinstance(product, dict):
                raise ValueError("invalid trading product")
            key = self._key(product)
            if key in self.products or key[0] not in ("CN", "US") or not key[1]:
                raise ValueError("duplicate or invalid trading product")
            if product.get("currency") not in ("CNY", "USD"):
                raise ValueError("product needs currency")
            if product.get("kind") not in ("fund_nav", "exchange_price"):
                raise ValueError("product needs a supported quote kind")
            for name in ("buy_fee_rate", "sell_fee_rate"):
                product[name] = number(product.get(name, 0))
                if product[name] >= 1:
                    raise ValueError("fee rate must be less than one")
            precision = product.get("unit_decimals", 8)
            if type(precision) is not int or not 0 <= precision <= 8:
                raise ValueError("unit_decimals must be 0..8")
            product["unit_decimals"] = precision
            windows = product.get("execution_windows")
            if not isinstance(windows, list):
                raise ValueError("product needs execution_windows")
            previous = None
            for window in windows:
                cutoff, observed, settle = [instant(window[k]) for k in
                                            ("accept_until", "observed_at", "settle_at")]
                if not cutoff <= observed <= settle:
                    raise ValueError("execution times out of order")
                if previous is not None and (cutoff <= previous[0] or observed <= previous[1]):
                    raise ValueError("execution windows must strictly increase")
                previous = cutoff, observed
            for row in self.rows:
                if self._key(row) == key and (row["currency"], row["kind"]) != (product["currency"], product["kind"]):
                    raise ValueError("product and market tape disagree")
                number(row['price'], positive=True)
            self.products[key] = product
        for agent in agents:
            config = agent.get("account")
            if not isinstance(config, dict) or config.get("currency") not in ("CNY", "USD"):
                raise ValueError("each trading agent needs an account with currency")
            cash = number(config.get("cash"))
            if money(cash) != cash:
                raise ValueError("opening cash must have at most two decimal places")
            account = {"cash": cash, "currency": config["currency"], "lots": [],
                       "fees": ZERO, "realized_pnl_net": ZERO, "opening_equity": None}
            positions = config.get("positions", [])
            if not isinstance(positions, list):
                raise ValueError("opening positions must be a list")
            for index, position in enumerate(positions):
                key = self._key(position)
                product = self.products.get(key)
                if not product or product["currency"] != account["currency"]:
                    raise ValueError("opening position has unknown or incompatible instrument")
                units = number(position.get("units"), positive=True)
                if units != self._units(units, product):
                    raise ValueError("opening position exceeds unit precision")
                acquired = position.get("acquired_at", initial_time)
                if instant(acquired) > self.clock:
                    raise ValueError("opening position is from the future")
                account["lots"].append({"lot_id": f"opening_{agent['id']}_{index}",
                                        "market": key[0], "instrument_id": key[1], "units": units,
                                        "cost": money(units * number(position.get("unit_cost"))),
                                        "acquired_at": acquired, "settle_at": initial_time})
            account["lots"].sort(key=lambda lot: instant(lot["acquired_at"]))
            self.accounts[agent["id"]] = account
        for actor in self.accounts:
            self.accounts[actor]["opening_equity"] = self._valuation(actor, initial_time)["equity"]

    @staticmethod
    def _key(record):
        market, instrument = record.get("market"), record.get("instrument_id")
        if not isinstance(market, str) or not isinstance(instrument, str):
            raise ValueError("instrument needs market and instrument_id")
        return market, instrument

    @staticmethod
    def _units(value, product):
        return value.quantize(Decimal(1).scaleb(-product["unit_decimals"]), rounding=ROUND_DOWN)

    def _own_orders(self, actor):
        return [o for o in self.orders if o["agent_id"] == actor]

    def _available_cash(self, actor):
        reserved = sum((o["amount"] for o in self._own_orders(actor)
                        if o["status"] == "submitted" and o["side"] == "buy"), ZERO)
        return self.accounts[actor]["cash"] - reserved

    def _available_units(self, actor, key):
        settled = sum((lot["units"] for lot in self.accounts[actor]["lots"]
                       if self._key(lot) == key and instant(lot["settle_at"]) <= self.clock), ZERO)
        reserved = sum((o["units"] for o in self._own_orders(actor)
                        if o["status"] == "submitted" and o["side"] == "sell" and self._key(o) == key), ZERO)
        return settled - reserved

    def _event(self, order, event, at, **fields):
        item = {"index": sum(e['agent_id'] == order['agent_id'] for e in self.ledger), "agent_id": order["agent_id"],
                "order_id": order["order_id"], "event": event, "at": at,
                "market": order["market"], "instrument_id": order["instrument_id"],
                "currency": order["currency"], **fields}
        self.ledger.append(item)

    @accounting_context
    def submit(self, actor, action, as_of):
        """Resource validation is atomic; rejected intentions do not alter accounts."""
        if instant(as_of) != self.clock:
            raise ValueError("advance broker to the decision time first")
        if actor not in self.accounts:
            raise ValueError("unknown account")
        reject = lambda reason: {"status": "rejected", "reason": reason}
        if action.get("agent_id", actor) != actor:
            return reject("forged_agent_id")
        kind = action.get("kind")
        if kind == "cancel_order":
            order = next((o for o in self._own_orders(actor) if o["order_id"] == action.get("order_id")), None)
            if order is None:
                return reject("unknown_own_order")
            if order["status"] != "submitted" or self.clock >= instant(order["window"]["accept_until"]):
                return reject("order_not_cancellable")
            order["status"] = "cancelled"
            self._event(order, "cancelled", as_of)
            return {"status": "accepted", "reason": "order_cancelled", "order_id": order["order_id"]}
        try:
            key = self._key(action)
        except (ValueError, AttributeError):
            return reject("invalid_instrument")
        product = self.products.get(key)
        if product is None:
            return reject("unknown_instrument")
        if product["currency"] != self.accounts[actor]["currency"]:
            return reject("currency_mismatch_no_fx")
        permitted = ("subscribe", "redeem") if product["kind"] == "fund_nav" else ("buy", "sell")
        if kind not in permitted:
            return reject("action_incompatible_with_product")
        window = next((w for w in product["execution_windows"] if self.clock < instant(w["accept_until"])), None)
        if window is None:
            return reject("no_future_execution_window")
        side = "buy" if kind in ("subscribe", "buy") else "sell"
        try:
            quantity = number(action.get("amount" if side == "buy" else "units"), positive=True)
            if side == "buy":
                if quantity != money(quantity):
                    return reject("amount_precision")
                if quantity > self._available_cash(actor):
                    return reject("insufficient_available_cash")
            else:
                if quantity != self._units(quantity, product):
                    return reject("units_precision")
                if quantity > self._available_units(actor, key):
                    return reject("insufficient_settled_units")
        except ValueError:
            return reject("invalid_quantity")
        order = {"order_id": f"order_{len(self._own_orders(actor)) + 1:06d}", "agent_id": actor,
                 "market": key[0], "instrument_id": key[1], "currency": product["currency"],
                 "kind": kind, "side": side, "status": "submitted", "submitted_at": as_of,
                 "amount": quantity if side == "buy" else ZERO,
                 "units": quantity if side == "sell" else ZERO, "window": copy.deepcopy(window)}
        if side == 'sell':
            order['reserved_lots'] = self._reserve_lots(actor, key, quantity)
        if isinstance(action.get("post_id"), str):
            order["source_post_id"] = action["post_id"]
        self.orders.append(order)
        self._event(order, "submitted", as_of, reserved_cash=order["amount"], reserved_units=order["units"])
        return {"status": "accepted", "reason": "order_submitted_not_filled", "order_id": order["order_id"]}

    def _quote(self, order):
        observed = instant(order["window"]["observed_at"])
        choices = [row for row in self.rows if self._key(row) == self._key(order)
                   and instant(row["observed_at"]) == observed and instant(row["available_at"]) <= self.clock]
        # Use the first published price, not a hindsight correction arriving later.
        return min(choices, key=lambda row: instant(row["available_at"])) if choices else None

    def _reserve_lots(self, actor, key, quantity):
        reserved = {}
        for order in self._own_orders(actor):
            if order['status'] == 'submitted' and order['side'] == 'sell':
                for part in order['reserved_lots']:
                    reserved[part['lot_id']] = reserved.get(part['lot_id'], ZERO) + part['units']
        parts = []
        for lot in self.accounts[actor]['lots']:
            if self._key(lot) != key or instant(lot['settle_at']) > self.clock:
                continue
            units = min(quantity, lot['units'] - reserved.get(lot['lot_id'], ZERO))
            if units > 0:
                parts.append({'lot_id': lot['lot_id'], 'units': units})
                quantity -= units
            if not quantity:
                return parts
        raise ValueError('insufficient unreserved lots')

    def _consume(self, account, parts):
        cost = ZERO
        for part in parts:
            lot = next((l for l in account['lots'] if l['lot_id'] == part['lot_id']), None)
            used = part['units']
            if lot is None or lot['units'] < used:
                raise ValueError('reserved lot unavailable')
            basis = lot["cost"] if used == lot["units"] else money(lot["cost"] * used / lot["units"])
            lot["units"] -= used
            lot["cost"] -= basis
            cost += basis
        account["lots"] = [lot for lot in account["lots"] if lot["units"]]
        return cost

    def _fill(self, order, quote):
        product = self.products[self._key(order)]
        account = self.accounts[order["agent_id"]]
        price = number(quote["price"], positive=True)
        at = quote["available_at"]
        if order["side"] == "buy":
            net_budget = (order['amount'] / (1 + product['buy_fee_rate'])).quantize(CENT, rounding=ROUND_DOWN)
            units = self._units(net_budget / price, product)
            gross = money(units * price)
            if not units or not gross:
                order["status"] = "rejected"
                self._event(order, "rejected", at, reason="below_unit_precision")
                return
            fee = money(gross * product['buy_fee_rate'])
            debit = gross + fee
            account["cash"] -= debit
            account["lots"].append({"lot_id": order['order_id'], "market": order["market"], "instrument_id": order["instrument_id"],
                                    "units": units, "cost": debit, "acquired_at": quote["observed_at"],
                                    "settle_at": order["window"]["settle_at"]})
            order.update(units=units, cash_delta=-debit, receivable=ZERO, realized_pnl_net=ZERO)
        else:
            units = order["units"]
            gross = money(units * price)
            fee = money(gross * product["sell_fee_rate"])
            cost = self._consume(account, order['reserved_lots'])
            profit = gross - fee - cost
            account["realized_pnl_net"] += profit
            order.update(cash_delta=ZERO, receivable=gross - fee, realized_pnl_net=profit, cost_basis=cost)
        account["fees"] += fee
        order.update(status="filled", price=price, gross=gross, fee=fee, filled_at=at,
                     quote_observed_at=quote["observed_at"], synthetic=quote["synthetic"], source=quote["source"])
        self._event(order, "filled", at, units=units, price=price, fee=fee,
                    cash_delta=order["cash_delta"], receivable=order["receivable"],
                    realized_pnl_net=order["realized_pnl_net"])

    @accounting_context
    def advance(self, as_of):
        clock = instant(as_of)
        if clock < self.clock:
            raise ValueError("broker clock cannot move backwards")
        self.clock = clock
        # Deterministic creation order. Every submit already reserved its resources.
        for order in self.orders:
            if order["status"] == "submitted" and clock >= instant(order["window"]["accept_until"]):
                quote = self._quote(order)
                if quote is not None:
                    self._fill(order, quote)
            if order["status"] == "filled" and clock >= instant(order["window"]["settle_at"]):
                account = self.accounts[order["agent_id"]]
                delta = order["receivable"]
                account["cash"] += delta
                order["receivable"] = ZERO
                order["status"] = "settled"
                settled_at = max((order["window"]["settle_at"], order["filled_at"]), key=instant)
                self._event(order, "settled", settled_at, cash_delta=delta)

    def _valuation(self, actor, as_of):
        account = self.accounts[actor]
        quotes = {self._key(q): q for q in self.tape.snapshot(as_of)["quotes"]}
        positions = []
        missing = []
        total, unrealized = ZERO, ZERO
        for key in sorted({self._key(lot) for lot in account["lots"]}):
            lots = [lot for lot in account["lots"] if self._key(lot) == key]
            units, cost = sum((lot["units"] for lot in lots), ZERO), sum((lot["cost"] for lot in lots), ZERO)
            quote = quotes.get(key)
            value = money(units * number(quote["price"])) if quote else None
            if value is None:
                missing.append({"market": key[0], "instrument_id": key[1]})
            else:
                total += value
                unrealized += value - cost
            positions.append({"market": key[0], "instrument_id": key[1], "units": units,
                              "available_units": self._available_units(actor, key), "cost_basis": cost,
                              "price": quote["price"] if quote else None,
                              "price_observed_at": quote["observed_at"] if quote else None,
                              "market_value": value, "unrealized_pnl_net": value - cost if value is not None else None})
        receivable = sum((o["receivable"] for o in self._own_orders(actor) if o["status"] == "filled"), ZERO)
        return {"positions": positions, "market_value": None if missing else total,
                "known_market_value": total, "missing_valuations": missing,
                "receivable": receivable, "equity": None if missing else account["cash"] + total + receivable,
                "unrealized_pnl_net": None if missing else unrealized}

    @accounting_context
    def view(self, actor, as_of):
        if instant(as_of) != self.clock:
            raise ValueError("account view must match current clock")
        account = self.accounts[actor]
        valuation = self._valuation(actor, as_of)
        opening, equity = account["opening_equity"], valuation["equity"]
        result = {**valuation, "currency": account["currency"], "cash": account["cash"],
                  "available_cash": self._available_cash(actor),
                  "reserved_cash": account["cash"] - self._available_cash(actor),
                  "fees": account["fees"], "realized_pnl_net": account["realized_pnl_net"],
                  "opening_equity": opening, "pnl_since_start": equity - opening if equity is not None and opening is not None else None,
                  "return_pct": (equity / opening - 1) * 100 if opening and equity is not None else None,
                  "orders": self._own_orders(actor), "ledger": [e for e in self.ledger if e["agent_id"] == actor],
                  "as_of": as_of, "execution_mode": "scheduled_exogenous_quotes",
                  "notice": "Simulated cash account; explicit schedule, no FX/margin/tax/dividends/order-book liquidity."}
        return json_value(result)

    def catalog(self, actor):
        currency = self.accounts[actor]["currency"]
        rows = []
        for product in self.products.values():
            if product['currency'] != currency:
                continue
            row = {k: product[k] for k in ("market", "instrument_id", "currency", "kind",
                                           "buy_fee_rate", "sell_fee_rate", "unit_decimals")}
            row['next_window'] = next((w for w in product['execution_windows']
                                       if self.clock < instant(w['accept_until'])), None)
            rows.append(row)
        return json_value(rows)

    def snapshot(self, as_of):
        """Researcher-only final accounts, never supplied wholesale to any actor."""
        return {actor: self.view(actor, as_of) for actor in sorted(self.accounts)}

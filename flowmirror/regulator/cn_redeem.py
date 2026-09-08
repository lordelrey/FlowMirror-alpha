"""CSRC 2017-08-31 《公开募集开放式证券投资基金流动性风险管理规定》： redeeming
open-end fund units held less than 7 days costs a redemption fee of at least
1.5% (punitive short-term redemption fee; effective 2017-10-01).

Simplifications, documented on purpose:
  * Money-market funds and ETFs, which the rule text exempts, are NOT exempted
    here; `exempt_r` is a reserved hook for that, unused by the engine today.
  * The rule only *prices* redemption -- it never gates, queues or blocks it --
    so base.py's invariant "Redemptions are never gated by any Regulator"
    still holds with this rule enabled.
  * The 1.5% floor applies only to the short-term slice (units younger than
    `days`); the rest of the redemption keeps the contractual base rate.
"""


class ShortTermRedeemRule:
    """CSRC 2017 liquidity rule: redemptions of units held < `days` pay at least
    `min_fee_rate` (1.5%); money-market funds and ETFs are exempt in the rule text --
    that exemption is NOT modelled here (documented simplification; see exempt_r).

    fee_rate(hold_days, base_rate) -> effective rate for the short-term part:
        max(base_rate, min_fee_rate) when hold_days is not None and hold_days < days,
        else base_rate.
    split(units, lots_consumed) is not needed: the engine passes st_units directly."""

    def __init__(self, days=7, min_fee_rate=0.015, exempt_r=()):
        self.days = int(days)
        self.min_fee_rate = float(min_fee_rate)
        self.exempt_r = tuple(exempt_r)

    def fee_rate(self, hold_days, base_rate):
        # The rule is a fee floor, not a replacement: a contractual base rate
        # above 1.5% still wins; below it, the CSRC floor binds.
        if hold_days is not None and hold_days < self.days:
            return max(float(base_rate), self.min_fee_rate)
        return float(base_rate)

    def short_term_fee(self, st_units, other_units, nav, base_rate):
        """-> (fee_total, st_extra).

        st_units are by construction younger than `days`, so their slice is
        priced at the floored rate; other_units keep base_rate. st_extra is the
        marginal fee the rule would add: the engine logs it on co rows as
        st_fee_cf even when the rule is off, measuring exposure at zero cost.
        """
        base_rate = float(base_rate)
        eff = self.fee_rate(0, base_rate)  # 0 < days, so this is the floor rate
        st_extra = st_units * nav * (eff - base_rate) if st_units > 0 else 0.0
        fee_total = (st_units + other_units) * nav * base_rate + st_extra
        return fee_total, st_extra

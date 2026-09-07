# -*- coding: utf-8 -*-
"""Pin the parsing contract between prompt.py's holdings rendering and
runtime._held_codes.

_held_codes used to match only three legacy markers ("held:", "held：",
lines starting with "持仓") while prompt.py renders holdings as e.g.
"003142 / ... / 73,845.06份 × 净值0.9600 / 浮动盈亏 -4.1%" -- none of them.
So _held_codes always returned [] and the redeem branch of MockLLM._trade
has been dead code since day one (43 mock runs on disk, zero redeems).
The fix also accepts lines containing both "份 × 净值" and "浮动盈亏"
while keeping the legacy markers.

Crucially, these tests feed _held_codes with output from the REAL prompt
generator, never a hand-copied holdings string: if the rendering changes,
this file goes red immediately instead of the redeem branch silently
dying again for months.
"""

import json
import random
import unittest

from flowmirror.agents.prompt import render_experience
from flowmirror.agents.runtime import MockLLM, _held_codes


def _holding(code, units=73845.06, nav=0.96, pnl=-4.1):
    """One holdings row exactly as carried in the agent view dict."""
    return {"code": code, "name": code, "r": 3,
            "units": units, "nav": nav, "pnl_pct": pnl}


def _render(holdings):
    # Always go through the real generator so format drift breaks HERE.
    out = render_experience({"holdings": list(holdings)})
    if isinstance(out, (list, tuple)):  # tolerate a list-of-lines generator
        out = "\n".join(out)
    return out


def _action_fund(decision):
    # Accept dict, JSON string, or positional (action, fund, ...) tuple.
    if isinstance(decision, str):
        decision = json.loads(decision)
    if isinstance(decision, dict):
        return decision.get("action"), decision.get("fund")
    return decision[0], decision[1]


class MockHoldingsParseTests(unittest.TestCase):

    def test_single_holding_end_to_end(self):
        # The headline regression: a rendered holding line must yield its code.
        self.assertEqual(_held_codes(_render([_holding("003142")])), ["003142"])

    def test_two_holdings_sorted(self):
        text = _render([_holding("003142"), _holding("000858")])
        self.assertEqual(_held_codes(text), ["000858", "003142"])

    def test_empty_holdings(self):
        text = _render([])
        self.assertIn("你目前没有持有任何基金。", text)
        self.assertEqual(_held_codes(text), [])

    def test_noise_lines_are_not_swallowed(self):
        # Post card header and cash line must not leak "00021" or "55,668".
        text = _render([_holding("003142")])
        noisy = (text + "\n【00021】机构：汇添富基金 ｜ 热度：0.0 赞"
                 + "\n账户可投闲钱：55,668 元。")
        self.assertEqual(_held_codes(noisy), ["003142"])

    def test_legacy_markers_still_recognized(self):
        # Guard against someone "simplifying away" the old markers.
        self.assertEqual(_held_codes("held: 000001"), ["000001"])
        self.assertEqual(_held_codes("持仓 000002"), ["000002"])
        self.assertEqual(_held_codes("held：000003"), ["000003"])

    def test_redeem_branch_is_reachable(self):
        # Positive proof of the fixed bug: with real generator output fed
        # through _trade, "redeem" must actually fire and pick a held code.
        held = {"003142", "000858"}
        text = _render([_holding(code) for code in sorted(held)])
        llm = MockLLM()
        rng = random.Random(7)
        redeemed = []
        for _ in range(200):
            action, fund = _action_fund(llm._trade(text, rng))
            if action == "redeem":
                redeemed.append(fund)
        self.assertTrue(redeemed, "redeem never fired in 200 seeded trials")
        for fund in redeemed:
            self.assertIn(fund, held)


if __name__ == "__main__":
    unittest.main()

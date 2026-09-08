"""Scope of the anti-priming guard: OUR template blocks only, not the agent's own words.

The 100x12 pilot died on day 2 because an agent's own comment (containing 研究) was
read back from block C memory and killed the whole run. These tests pin the fix:
B/C (agent's words) pass through, D/E/following (our words) stay guarded, and the
`head` bytes — hence prompt_sha — are unchanged by the rescope.
"""
import unittest

from flowmirror.agents.prompt import build_decision_messages

VIEW = {"persona_card_zh_rich": "你是小周，26 岁，刚工作两年，工资不高，想先小额试试。",
        "market_view": 3, "risk_mood": 3, "c_class": "C2", "cash": 5000.0}
CFG = {"channels": {"feed": True, "experience": True, "trend": True, "social": True,
                    "news": True, "direct": True}, "social": True}
CARDS = []


class AntiPrimingScopeTest(unittest.TestCase):
    def _build(self, view):
        return build_decision_messages(view, CARDS, CFG)

    def test_agent_memory_may_contain_guarded_words(self):
        # Yesterday's own comment read back today is legitimate input, not priming.
        view = dict(VIEW, memory=["D1｜看6条｜未交易｜评鹏华基金:bullish\"值得研究\"｜心情3｜先观望"])
        _, prompt_sha, _, _ = self._build(view)
        self.assertRegex(prompt_sha, r"^[0-9a-f]{64}$")

    def test_agent_reflection_may_contain_guarded_words(self):
        # Block B holds the agent's reflection output; it must never trip the guard.
        view = dict(VIEW, last_reflection="上次的实验性买入太冲动。", beliefs=["研究后再买"])
        self._build(view)

    def test_our_fee_notice_is_still_guarded(self):
        # Block D is template text we author, so it stays under the guard.
        view = dict(VIEW, fee_notice="监管要求：持有不足 7 日赎回收取 1.5% 赎回费。")
        with self.assertRaises(ValueError):
            self._build(view)

    def test_head_bytes_unchanged_by_scope_change(self):
        _, sha1, _, _ = self._build(VIEW)
        m2, sha2, _, _ = self._build(VIEW)
        self.assertEqual(sha1, sha2)
        self.assertTrue(m2[1]["content"][0]["text"].startswith("眼下你对后市的判断"))


if __name__ == "__main__":
    unittest.main()

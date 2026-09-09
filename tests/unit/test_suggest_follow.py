"""Unit tests for the platform "worth following" block (F3 / E8B).

Covers the renderer itself and that build_decision_messages splices the block
in only when the engine actually produced rows for it.
"""
import unittest

from flowmirror.agents.prompt import render_suggest_follow, build_decision_messages

MIN_VIEW = {
    "persona_card_zh_rich": "你是小周。",
    "market_view": 3,
    "risk_mood": 3,
    "c_class": "C2",
    "cash": 5000.0,
}
CFG = {
    "channels": {"feed": True, "experience": True, "trend": True,
                 "social": True, "news": True, "direct": True},
    "social": True,
}


def _user_text(result):
    """Pull the text part out of the user message.

    build_decision_messages returns (messages, prompt_sha, image_shas, notes), so the message
    list has to be unpacked first; indexing the tuple hands back the sha string instead.
    """
    messages = result[0] if isinstance(result, tuple) else result
    return messages[1]["content"][0]["text"]


class RenderSuggestFollowTest(unittest.TestCase):
    def test_empty_when_absent(self):
        """No key or empty list -> empty string, block simply absent."""
        self.assertEqual(render_suggest_follow({}), "")
        self.assertEqual(render_suggest_follow({"suggest_follow": []}), "")

    def test_renders_rows_with_counts(self):
        """A row renders handle, follower count and yesterday's post count."""
        text = render_suggest_follow(
            {"suggest_follow": [{"handle": "@u3f9a2", "followers": 12, "n_cmt": 2}]})
        self.assertIn("平台推荐关注（按粉丝数）：", text)
        self.assertIn("@u3f9a2", text)
        self.assertIn("粉丝 12", text)
        self.assertIn("昨日发言 2 条", text)

    def test_caps_at_three_rows(self):
        """At most three rows render: header plus three lines."""
        rows = [{"handle": "@u%03d" % i, "followers": 9 - i, "n_cmt": 0} for i in range(5)]
        self.assertEqual(len(render_suggest_follow({"suggest_follow": rows}).splitlines()), 4)

    def test_skips_rows_without_handle(self):
        """A dict without a handle is not a row, so nothing renders."""
        self.assertEqual(render_suggest_follow({"suggest_follow": [{"followers": 3}]}), "")

    def test_no_stance_leaks(self):
        """A stray stance key must not turn into stance wording."""
        text = render_suggest_follow(
            {"suggest_follow": [{"handle": "@u3f9a2", "followers": 5,
                                 "n_cmt": 1, "stance": "bullish"}]})
        self.assertIn("@u3f9a2", text)
        self.assertNotIn("看多", text)


class PromptSpliceTest(unittest.TestCase):
    def test_block_absent_from_prompt_when_empty(self):
        """Empty rows -> block absent from the user prompt; rows -> present."""
        agent_view = dict(MIN_VIEW)
        # signature is (agent_view, feed_cards, cfg) -- passing cfg second hands a dict to
        # feed_cards and a list to cfg, which blows up inside the renderer.
        messages = build_decision_messages(agent_view, [], CFG)
        self.assertNotIn("平台推荐关注", _user_text(messages))
        agent_view["suggest_follow"] = [{"handle": "@u3f9a2", "followers": 1, "n_cmt": 0}]
        messages = build_decision_messages(agent_view, [], CFG)
        self.assertIn("平台推荐关注", _user_text(messages))


if __name__ == "__main__":
    unittest.main()

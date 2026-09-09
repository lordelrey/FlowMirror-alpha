"""An agent's declared beliefs must reach its next decision prompt.

Beliefs are clamped by parse_reflection and fed into decision prompts only.
Absent, empty, and invalid shapes render no belief block.
"""
from __future__ import annotations

import pytest

from flowmirror.agents.prompt import (build_decision_messages, parse_reflection,
                                      render_belief)

# Runnable fixtures derive from the self-contained demo configuration.
from tests.conftest import build_demo_cfg, find_demo_run

needs_demo = pytest.mark.skipif(
    find_demo_run() is None,
    reason="runs/demo_two_arm.json not found (set FLOWMIRROR_DATA_ROOT)")

# Second-person register, exactly as the engine builds it: market_view / risk_mood ints,
# a last reflection, and the one card the decision prompt needs to have something to show.
_BASE_VIEW = {
    "persona_card_zh_rich": "你是王女士，42 岁，在制造企业做行政，攒下的钱想稳中求进。",
    "market_view": 4, "risk_mood": 2,
    "last_reflection": "上月觉得震荡市要保守一点。",
    "memory": ["周一：没怎么打开 App。", "周二：看了一条债基帖子，没操作。"],
    "c_class": "C3", "cash": 80000.0, "holdings": [], "last_trade": "",
    "declined_confirms": [], "familiarity": {}, "direct": [],
}
_CARD = {"post_id": "p1", "org": "华夏基金", "title": "震荡市里的债底仓思路",
         "caption": "短文本一条。", "landing": None, "likes": 12, "arm": "T",
         "image_path": None, "image_sha": None, "comments_prev": [], "climate": "no_signal"}

_BELIEFS = ["债基适合当底仓", "不追短期热门主题"]

# Frozen expected text of the pre-change render_belief for _BASE_VIEW: the market-view /
# risk-mood sentence plus the last-reflection line, and nothing else.  Written out
# literally rather than recomputed from the function under test, so a future edit to the
# block cannot silently redefine what "byte-identical to before" means.
_BASELINE_BELIEF_TEXT = (
    "眼下你对后市的判断偏「偏乐观」；面对账户可能的亏损，你的心情是「比较怕亏」。\n"
    "你上次给自己的小结：上月觉得震荡市要保守一点。")
# The one lead-in the card adds; asserted ABSENT whenever the agent has declared nothing,
# so an empty list can never leave a dangling header behind.
_BELIEF_LEAD_IN = "你现在相信的判断："


def _view(**extra):
    v = dict(_BASE_VIEW)
    v.update(extra)
    return v


def _decision_text(view, cfg):
    """The full TEXT of the decision messages: system block plus every text part.

    Byte-level comparison target -- the image parts carry base64 payloads and are
    excluded from prompt_sha for the same reason.
    """
    messages, _sha, _ishas, _notes = build_decision_messages(view, [dict(_CARD)], cfg)
    parts = messages[1]["content"]
    return messages[0]["content"] + "\n" + "\n".join(
        p["text"] for p in parts if p.get("type") == "text")


def _decision_sha(view, cfg):
    """prompt_sha of the decision messages -- the hash the dec event actually carries."""
    return build_decision_messages(view, [dict(_CARD)], cfg)[1]


@needs_demo
def test_two_beliefs_reach_the_decision_messages(tmp_path):
    """Declared beliefs become decision-prompt text."""
    cfg = build_demo_cfg(tmp_path)
    text = _decision_text(_view(beliefs=list(_BELIEFS)), cfg)
    for b in _BELIEFS:
        assert b in text, f"belief {b!r} never reached the decision prompt"


@needs_demo
def test_absent_key_renders_byte_identically(tmp_path):
    """A view with no beliefs key preserves the base prompt text."""
    cfg = build_demo_cfg(tmp_path)
    view = _view()
    assert "beliefs" not in view
    assert render_belief(view) == _BASELINE_BELIEF_TEXT
    text = _decision_text(view, cfg)
    assert _BASELINE_BELIEF_TEXT in text
    assert _BELIEF_LEAD_IN not in text


@needs_demo
def test_empty_list_renders_byte_identically(tmp_path):
    """`inv.beliefs` is [] for every agent that has not reflected yet (world.py:473).

    Every "nothing declared yet" shape must hash to the same prompt_sha as the absent key.
    """
    cfg = build_demo_cfg(tmp_path)
    assert render_belief(_view(beliefs=[])) == _BASELINE_BELIEF_TEXT
    absent = _decision_sha(_view(), cfg)
    assert _decision_sha(_view(beliefs=[]), cfg) == absent
    assert _decision_sha(_view(beliefs=None), cfg) == absent
    # the negative control: real beliefs MUST move the prompt hash, or the feed-forward
    # would be invisible to the event log and the fix unverifiable downstream
    assert _decision_sha(_view(beliefs=list(_BELIEFS)), cfg) != absent


def test_non_list_and_blank_beliefs_render_byte_identically():
    """Defensive shapes: a bare string must not be iterated per character, and a list of
    blanks must not leave a dangling lead-in line."""
    for bad in (None, "债基适合当底仓", 3, {"a": 1}, ["", "   "], [None, 7]):
        assert render_belief(_view(beliefs=bad)) == _BASELINE_BELIEF_TEXT, f"beliefs={bad!r}"


def test_at_most_three_beliefs_render():
    """parse_reflection clamps to 3; render_belief re-clamps so any other producer is safe."""
    five = ["判断一", "判断二", "判断三", "判断四", "判断五"]
    text = render_belief(_view(beliefs=five))
    assert [b for b in five if b in text] == five[:3]
    # one lead-in line + three belief lines on top of the two baseline lines
    assert len(text.split("\n")) == len(_BASELINE_BELIEF_TEXT.split("\n")) + 4


def test_parse_reflection_clamps_are_untouched():
    """Rendering keeps the three-item, 30-character clamps."""
    parsed, viol = parse_reflection({"summary": "s", "beliefs": ["x" * 40, "b", "c", "d"],
                                     "market_view": 4, "risk_mood": 2})
    assert viol == []
    assert parsed["beliefs"] == ["x" * 30, "b", "c"]

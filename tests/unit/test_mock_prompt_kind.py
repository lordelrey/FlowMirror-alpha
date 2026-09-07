"""MockLLM prompt-kind routing: decision vs reflection.

Guards the TC-arm regression: OCR'd creative copy often contains the words the
retired sniffer keyed on, and only the TC arm embeds OCR text into decision
prompts, so a word-based classifier silently drops TC decisions in a
material-dependent (non-random) way.
"""

import inspect
import json
from pathlib import Path

import pytest

from flowmirror.agents.prompt import (REFLECTION_PROMPT_ZH, build_decision_messages,
                                      build_reflection_messages)
from flowmirror.agents.runtime import MockLLM, _looks_like_reflection

REPO_ROOT = Path(__file__).resolve().parents[2]
POOL_PATH = REPO_ROOT / "data" / "creatives" / "cn" / "content_pool_v1_masked.jsonl"

# Minimal fixtures, same shapes as tests/unit/test_image_resolution.py.
_VIEW = {
    "agent_id": "TEST-0001",
    "day": 4,
    "cash": 100000.0,
    "positions": {"012345": {"shares": 8000.0, "cost_nav": 1.012}},
    "total_pnl_pct": -1.8,
    "risk_mood": "cautious",
    "market_view": "neutral",
    "records": [
        {"day": 1, "action": "buy", "fund_code": "012345", "amount": 5000.0},
        {"day": 2, "action": "hold", "cards_seen": 3},
        {"day": 3, "action": "sell", "fund_code": "012345", "amount": 1000.0},
    ],
}

_CFG = {
    "arm": "TC",
    "prompt": {"lang": "zh", "max_reads": 3},
    "mock": {"malformed_rate": 0.0, "seed": 7},
}

_LAST_REFLECTION = {
    "summary": "held through the dip, kept the weekly buy",
    "beliefs": ["bond fund yields stay roughly flat"],
    "market_view": "neutral",
    "risk_mood": "cautious",
}

# Trips every clause of the retired sniffer ("复盘" anywhere; "反思" and
# "reflection" inside the first 512 chars) but is just marketing copy.
_OCR_TRIP_WORDS = "理财笔记 复盘7：本周定投反思与加仓复盘，a quick reflection on my trades"


def _tc_cards():
    return [{
        "card_id": "note-0001",
        "arm": "TC",
        "fund_code": "012345",
        "title": "博主晒单：我的理财笔记",
        "ocr_text": _OCR_TRIP_WORDS,
        "image": {"width": 1080, "height": 1440},
    }]


def _to_text(messages):
    if isinstance(messages, str):
        return messages
    if isinstance(messages, dict):
        return str(messages.get("content", ""))
    return "\n".join(
        str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in messages
    )


def _make_llm():
    try:
        return MockLLM()
    except TypeError:
        return MockLLM(_CFG)


def _complete(messages):
    llm = _make_llm()
    return llm(messages, 2048)["raw"]


def _as_dict(raw):
    return raw if isinstance(raw, dict) else json.loads(raw)


def _build_reflection(view):
    supply = {
        "view": view,
        "agent": view,
        "state": view,
        "records": view.get("records", []),
        "history": view.get("records", []),
        "events": view.get("records", []),
        "last": _LAST_REFLECTION,
        "last_reflection": _LAST_REFLECTION,
        "prev": _LAST_REFLECTION,
        "cfg": _CFG,
    }
    return build_reflection_messages(supply)[0]


def test_decision_prompt_with_trip_words_stays_decision():
    messages = build_decision_messages(_VIEW, _tc_cards(), _CFG)
    text = _to_text(messages)
    # Sanity: the retired trigger words really are inside this decision prompt.
    assert "复盘" in text
    assert "反思" in text
    out = _as_dict(_complete(messages))
    assert "reads" in out
    assert "beliefs" not in out


def test_reflection_prompt_routes_to_reflection_json():
    messages = _build_reflection(_VIEW)
    assert REFLECTION_PROMPT_ZH[:40] in _to_text(messages)
    out = _as_dict(_complete(messages))
    for key in ("summary", "beliefs", "market_view", "risk_mood"):
        assert key in out
    assert "reads" not in out


def test_predicate_ignores_trip_words_in_plain_copy():
    assert not _looks_like_reflection(_OCR_TRIP_WORDS)
    assert not _looks_like_reflection("")
    assert not _looks_like_reflection(None)


def test_predicate_matches_reflection_instruction_block():
    assert _looks_like_reflection(REFLECTION_PROMPT_ZH)
    assert _looks_like_reflection("header\n" + REFLECTION_PROMPT_ZH)


def test_factory_pool_has_ocr_that_would_trip_old_sniffer():
    if not POOL_PATH.exists():
        pytest.skip("content pool not present in clean CI clone")
    seen = False
    for line in POOL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        ocr = item.get("ocr_masked") or item.get("ocr_text") or ""
        if "复盘" in ocr:
            seen = True
            break
    assert seen

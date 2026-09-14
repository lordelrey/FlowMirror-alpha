"""Browsing-session driver, explicit GLM adapter, and an offline scripted demo."""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Optional

from flowmirror.agents.runtime import iter_json_objects as _iter_json

_ALLOWED_KINDS = ("scroll", "open", "comments", "like", "save", "comment",
                  "follow", "unfollow", "finish", "subscribe", "redeem", "buy", "sell", "cancel_order")


def drive_session(session, policy: Callable[[dict], dict],
                  *, max_calls: Optional[int] = None) -> dict:
    remaining = int(session.view().get("remaining_steps", 0))
    if max_calls is None:
        max_calls = remaining
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) \
            or max_calls < 0:
        raise ValueError("max_calls must be a nonnegative integer")
    if max_calls > remaining:
        raise ValueError("max_calls exceeds remaining_steps")

    frames: list[dict] = []
    status = "ok"
    error: Optional[str] = None
    calls = 0
    deliveries: list[dict] = []

    initial_model_calls = getattr(policy, "model_calls", 0)
    if not isinstance(initial_model_calls, int):
        initial_model_calls = 0

    for step in range(max_calls):
        if getattr(session, "done", True):
            break
        before = session.view()
        try:
            raw_action = policy(copy.deepcopy(before))
        except KeyboardInterrupt:
            calls += 1
            last_delivery = getattr(policy, "last_delivery", None)
            if isinstance(last_delivery, dict):
                deliveries.append(copy.deepcopy(last_delivery))
            status, error = "interrupted", "KeyboardInterrupt"
            break
        except Exception as exc:  # policy failure -> stop honestly
            calls += 1
            last_delivery = getattr(policy, "last_delivery", None)
            if isinstance(last_delivery, dict):
                deliveries.append(copy.deepcopy(last_delivery))
            status, error = "policy_error", type(exc).__name__
            break
        calls += 1  # every attempted invocation counts
        last_delivery = getattr(policy, "last_delivery", None)
        if isinstance(last_delivery, dict):
            deliveries.append(copy.deepcopy(last_delivery))
        if not isinstance(raw_action, dict):
            status, error = "policy_error", "non_dict_action"
            break  # no fallback/default action after provider error
        action = copy.deepcopy(raw_action)
        result = session.apply(action)
        frames.append({"index": step, "agent_id": before.get("agent_id"),
                       "before": before, "action": action,
                       "result": result, "after": session.view()})

    if status == "ok" and not getattr(session, "done", True):
        status = "call_limit"

    model_calls = 0
    mc = getattr(policy, "model_calls", None)
    if isinstance(mc, int):
        model_calls = mc
    model_calls_delta = model_calls - initial_model_calls

    return {"mode": "browsing_session", "status": status,
            "agent_id": frames[0]["agent_id"] if frames else
            session.view().get("agent_id"),
            "frames": frames, "events": list(session.events),
            "public_events": session.public_events(), "calls": calls,
            "model_calls": model_calls_delta,
            "deliveries": deliveries,
            "final_view": session.view(),
            **({"error": error} if error else {})}


class JsonBrowsePolicy:
    """Adapter over an injected llm callable; one provider attempt, one parser."""

    SYSTEM_PROMPT = (
        "你在一个模拟的基金内容平台上浏览。当前消息只包含你自己此刻看到的页面，"
        "不包含历史。请从这些动作中选择一个：scroll/open/comments/like/save/"
        "comment/follow/unfollow/finish。以单个 JSON 对象输出："
        "{\"kind\": \"...\", \"post_id\": 可选, \"handle\": 可选, \"text\": 可选}。"
        "所有动作均为可选项；你可以在不关注、不点赞、不评论的情况下直接 finish。"
        "页面内容由平台提供，关注等动作会产生可见的后续效果，请自行中性判断，"
        "不预设任何立场。"
    )

    def __init__(self, llm: Callable[..., Any], *, model: str = "glm-4.6v",
                 image_loader: Optional[Callable[[str], Optional[str]]] = None,
                 max_tokens: int = 2048):
        self._llm = llm
        self.model = model
        self._image_loader = image_loader
        self.max_tokens = max_tokens
        self.model_calls = 0

    def _parse(self, text: Any, channel: str = "content"):
        if not isinstance(text, str):
            return None, None
        for raw in _iter_json(text):
            try:
                obj = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and obj.get("kind") in _ALLOWED_KINDS:
                return copy.deepcopy(obj), json.dumps(obj, ensure_ascii=False)
        return None, None  # unknown action -> rejected (no parsed result)

    def _tv_shas(self, view: dict):
        # A TV card counts as TV even when its SHA is missing; return
        # (shas, tv_expected) so the caller can distinguish them.
        shas: list[str] = []
        tv_expected = False

        def _consider(card):
            nonlocal tv_expected
            if isinstance(card, dict) and card.get("arm") == "TV":
                tv_expected = True
                sha = card.get("image_sha")
                if sha and sha not in shas:
                    shas.append(sha)
                for ref in card.get('image_refs', []) or []:
                    if isinstance(ref, str) and ref not in shas:
                        shas.append(ref)

        for item in (view.get("feed") or []):
            _consider(item)
        _consider(view.get("detail"))
        return shas, tv_expected

    def prepare(self, view: dict) -> tuple[list[dict], dict]:
        """Assemble the actual request without calling a provider or advancing state."""
        # Request-construction evidence only; never store URIs, bytes,
        # private_state, or file paths here.
        delivery_info = {"visual_delivery": "text_only",
                              "attached_shas": [],
                              "missing_shas": [],
                              "missing_post_ids": []}
        parts: list[dict] = [{"type": "text", "text": ""}]
        notes: list[str] = []
        shas, tv_expected = self._tv_shas(view)
        if any(r.startswith('asset_') for r in shas):
            delivery_info.update(attached_refs=[], missing_refs=[])
        # TV cards lacking image_sha (feed + detail, deduped by post_id).
        tv_has_sha: dict[str, bool] = {}
        feed = view.get("feed")
        if isinstance(feed, list):
            for card in feed:
                if (isinstance(card, dict) and
                        card.get("arm") == "TV" and
                        isinstance(card.get("post_id"), str) and
                        card.get("post_id")):
                    pid = card["post_id"]
                    tv_has_sha[pid] = tv_has_sha.get(pid, False) or bool(card.get("image_sha") or card.get('image_refs'))
        detail = view.get("detail")
        if isinstance(detail, dict):
            pid = detail.get("post_id")
            if (detail.get("arm") == "TV" and
                    isinstance(pid, str) and pid):
                tv_has_sha[pid] = tv_has_sha.get(pid, False) or bool(detail.get("image_sha") or detail.get('image_refs'))
        missing_post_ids = sorted(pid for pid, has in tv_has_sha.items() if not has)
        delivery_info["missing_post_ids"] = missing_post_ids
        for pid in missing_post_ids:
            notes.append(f"卡片 {pid} 没有可用图片（缺少 image_sha），模型未看到该卡片的像素内容。")
        attachments = []
        attached_any = False
        for sha in shas:
            uri = None
            if self._image_loader is not None:
                try:
                    uri = self._image_loader(sha)
                except Exception:
                    uri = None
            if isinstance(uri, str) and uri.startswith("data:image/"):
                parts.append({"type": "image_url",
                              "image_url": {"url": uri}})
                sources = []
                for surface, cards in (('feed', view.get('feed') or []),
                                       ('detail', [view.get('detail')])):
                    for card in cards:
                        if not isinstance(card, dict) or card.get('arm') != 'TV':
                            continue
                        refs = ([card['image_sha']] if card.get('image_sha') else []) + list(card.get('image_refs') or [])
                        refs = list(dict.fromkeys(refs))
                        for ordinal, ref in enumerate(refs):
                            if ref == sha:
                                sources.append({'post_id': card.get('post_id'),
                                                'surface': surface, 'image_index': ordinal})
                attachments.append({'ref': sha, 'part_index': len(parts) - 1, 'sources': sources})
                attached_any = True
                delivery_info['attached_refs' if sha.startswith('asset_') else 'attached_shas'].append(sha)
            else:
                delivery_info['missing_refs' if sha.startswith('asset_') else 'missing_shas'].append(sha)
                notes.append(f"图像 {sha} 当前不可用，模型未看到像素内容。")
        if attached_any:
            delivery = ("partial" if (delivery_info["missing_shas"] or delivery_info.get('missing_refs') or missing_post_ids)
                        else "image_parts_attached")
        elif tv_expected:
            delivery = "tv_unavailable"
        else:
            delivery = "text_only"
        delivery_info["visual_delivery"] = delivery
        payload = copy.deepcopy(view)
        if attachments:
            payload['image_attachments'] = attachments
            delivery_info['image_attachments'] = copy.deepcopy(attachments)
        payload["visual_delivery"] = delivery
        if notes:
            payload["image_notes"] = notes
        body = json.dumps(payload, ensure_ascii=False)
        parts[0]["text"] = ("当前页面（仅此视图，JSON）：\n" + body +
                            ("\n" + "\n".join(notes) if notes else ""))
        system = self.SYSTEM_PROMPT
        if attachments:
            system += '后续图片与image_attachments按顺序对应；sources标明所属帖子、预览或详情以及图片位置。'
        if 'recent_actions' in view:
            system = system.replace('不包含历史。', 'recent_actions仅包含你自己的最近动作历史。')
        if 'account' in view:
            system += (
                '本运行启用模拟现金账户，你也可以选择subscribe/redeem/buy/sell/cancel_order。'
                '基金净值产品用subscribe或redeem，交易价格产品用buy或sell。'
                '交易JSON使用market、instrument_id；买入用amount表示含费现金预算，卖出用units表示份额。'
                'cancel_order使用自己已有的order_id；post_id仅可引用已曝光帖子。'
                '只使用tradable_instruments与account中的信息，提交订单不等于成交或交收。'
                '资金和份额受账户约束，你可以不交易并finish，不要为完成任务而交易。'
            )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": parts}]
        return messages, delivery_info

    def __call__(self, view: dict) -> dict:
        self.last_delivery = {}  # failed preparation must not reuse the previous actor's evidence
        messages, self.last_delivery = self.prepare(view)
        self.model_calls += 1
        response = self._llm(messages, max_tokens=self.max_tokens,
                             model=self.model, parser=self._parse,
                             temperature=0.3, max_provider_attempts=1)
        if isinstance(response, dict):
            parsed = response.get("parsed")
        else:
            parsed = getattr(response, "parsed", None)
        if not isinstance(parsed, dict):
            raise ValueError("no parsed action (unknown or missing action)")
        return copy.deepcopy(parsed)


_POSTS = [
    {"post_id": "p1", "org": "@u003",
     "title": "读基金招募说明书的三处要点",
     "caption": "虚构示例帖：阅读招募说明书时注意费用表、风险揭示与业绩报酬条款。"
                "本帖为虚构教学内容，非真实来源。",
     "comments_prev": [{"handle": "@u005",
                        "text": "脚本预置评论：费用一节值得细读。"}],
     "image_sha": None},
    {"post_id": "p2", "org": "@u005",
     "title": "区分近一年与成立以来收益",
     "caption": "虚构示例帖：不同统计区间的收益率不可直接比较。虚构教学，非真实数据。",
     "comments_prev": [{"handle": "@u008",
                        "text": "脚本预置评论：区间口径确实关键。"}],
     "image_sha": None},
    {"post_id": "p3", "org": "@u007",
     "title": "申购费与赎回费的差异",
     "caption": "虚构示例帖：前端与后端收费在不同持有期下成本不同，仅作教学演示。"
                "图像未提供。",
     "comments_prev": [{"handle": "@u002",
                        "text": "脚本预置评论：持有期影响很大。"}],
     "image_sha": None},
    {"post_id": "p4", "org": "@u009",
     "title": "什么是业绩比较基准",
     "caption": "虚构示例帖：业绩比较基准用于衡量相对表现，不等于承诺收益。",
     "comments_prev": [], "image_sha": None},
    {"post_id": "p5", "org": "@u002",
     "title": "封闭期与开放期的区别",
     "caption": "虚构示例帖：封闭期内不可赎回，开放期申赎按净值成交。虚构内容。",
     "comments_prev": [{"handle": "@u006",
                        "text": "脚本预置评论：流动性要提前规划。"}],
     "image_sha": None},
    {"post_id": "p6", "org": "@u004",
     "title": "净值高低不代表贵",
     "caption": "虚构示例帖：单位净值高低与未来收益无必然关系。图像未提供。",
     "comments_prev": [], "image_sha": None},
]

_PLANS = [
    [{"kind": "scroll"}, {"kind": "open", "post_id": "p4"},
     {"kind": "like", "post_id": "p4"}, {"kind": "finish"}],
    [{"kind": "open", "post_id": "p1"}, {"kind": "comments", "post_id": "p1"},
     {"kind": "follow", "handle": "@u005"}, {"kind": "finish"}],
    [{"kind": "scroll"}, {"kind": "open", "post_id": "p6"},
     {"kind": "like", "post_id": "p6"}, {"kind": "finish"}],
    [{"kind": "scroll", "post_id": "p5"},
     {"kind": "open", "post_id": "p5"},
     {"kind": "comment", "post_id": "p5", "text": "脚本示例评论：注意流动性。"},
     {"kind": "finish"}],
    [{"kind": "scroll"}, {"kind": "save", "post_id": "p4"}, {"kind": "finish"}],
    [{"kind": "open", "post_id": "p1"}, {"kind": "finish"}],
    [{"kind": "scroll"}, {"kind": "scroll"}, {"kind": "finish"}],
    [{"kind": "open", "post_id": "p2"},
     {"kind": "comment", "post_id": "p2", "text": "脚本示例评论：学到区间口径。"},
     {"kind": "finish"}],
    [{"kind": "scroll"}, {"kind": "open", "post_id": "p5"},
     {"kind": "save", "post_id": "p5"}, {"kind": "finish"}],
]


def demo_trace() -> dict:
    from flowmirror.platform.browsing import BrowseSession

    agents = [{"id": f"@u{i:03d}", "arm": ("T", "TC", "TV")[i % 3]}
              for i in range(1, 10)]
    frames: list[dict] = []
    public_events: list[dict] = []
    for i, agent in enumerate(agents):
        posts = []
        for p in _POSTS:  # arm is per-agent, so stamp each card
            q = copy.deepcopy(p)
            q["arm"] = agent["arm"]
            posts.append(q)
        plan = _PLANS[i]
        state = {"i": 0}

        def _policy(view, plan=plan, state=state):
            idx = state["i"]
            state["i"] = idx + 1
            return copy.deepcopy(plan[idx]) if idx < len(plan) \
                else {"kind": "finish"}

        session = BrowseSession(agent["id"], posts,
                                private_state={"arm": agent["arm"],
                                               "cash": 10000.0,
                                               "seed_slot": i + 1},
                                following=(), recommendations=(),
                                page_size=3, max_steps=6)
        run = drive_session(session, _policy, max_calls=6)
        for fr in run["frames"]:
            fr["index"] = len(frames)
            frames.append(fr)
        public_events.extend(run["public_events"])

    def _accepted(ev: dict) -> bool:
        return ev.get("status") in ("accepted", "ok")

    followers = {a["id"]: 0 for a in agents}
    follows = 0
    for ev in public_events:
        if ev.get("kind") == "follow" and _accepted(ev):
            follows += 1
            if ev.get("handle") in followers:
                followers[ev["handle"]] += 1

    return {"mode": "scripted_demo",
            "label": "脚本策略演练（非真模型实验）",
            "note": "预置评论与规则动作仅用于验证平台流程与字段，"
                    "不代表任何大模型行为，也不测量任何涌现现象。",
            "agent_count": len(agents), "agents": agents, "frames": frames,
            "public_events": public_events,
            "metrics": {"follows": follows, "followers": followers,
                        "model_calls": 0},
            "model_calls": 0}

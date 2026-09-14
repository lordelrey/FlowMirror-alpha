"""Pure per-agent bounded browsing sessions for FlowMirror.

Used by a model-driven controller and a labelled deterministic demo.
Stdlib only; no network, filesystem, persistence, or registries.
"""

from __future__ import annotations

import copy
from typing import Any

_PREVIEW_KEYS = ("post_id", "org", "title", "caption", "arm")
_PRIVATE_KEYS = ("persona", "cash", "holdings", "memory", "beliefs", "risk_class")
_MAX_CAPTION = 140
_MAX_COMMENT = 200
_PAGE_SLOTS = 3

_POLICY_NOTE = (
    "Bounded browsing: previews expose post_id/org/title/arm and a clipped "
    "caption; image_sha only for TV arms; full text and comments appear only "
    "after opening; only visibly revealed handles or platform recommendations "
    "may be followed."
)


def _pos_int(value: int, name: str, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class BrowseSession:
    """One agent's bounded, pure browsing session over pre-ranked public cards."""

    def __init__(self, agent_id, cards, *, private_state=None, following=(),
                 recommendations=(), page_size=3, max_steps=6,
                 own_handle=None):
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a nonempty string")
        self._agent_id = agent_id
        if own_handle is None:
            own_handle = agent_id
        if not isinstance(own_handle, str) or not own_handle:
            raise ValueError("own_handle must be a nonempty string")
        self._own_handle = own_handle

        raw_cards = copy.deepcopy(cards) if cards else []
        if not isinstance(raw_cards, list):
            raise ValueError("cards must be a list of public card dicts")
        self._cards = []
        seen_ids = set()
        for i, card in enumerate(raw_cards):
            if not isinstance(card, dict):
                raise ValueError(f"card {i} is not a dict")
            pid = card.get("post_id")
            if not isinstance(pid, str) or not pid:
                raise ValueError(f"card {i} missing post_id")
            if pid in seen_ids:
                raise ValueError(f"duplicate post_id: {pid}")
            seen_ids.add(pid)
            self._cards.append(card)

        state = copy.deepcopy(private_state) if private_state else {}
        if not isinstance(state, dict):
            raise ValueError("private_state must be a dict or None")
        self._private_state = {k: copy.deepcopy(state[k])
                               for k in _PRIVATE_KEYS if k in state}

        self._following = set()
        for h in (following or ()):
            if isinstance(h, str) and h and h != agent_id \
                    and h != own_handle:
                self._following.add(h)

        recs = copy.deepcopy(recommendations) if recommendations else []
        self._recommendations = []
        if isinstance(recs, list):
            seen_handles = set()
            for rec in recs:
                if not isinstance(rec, dict):
                    continue
                handle = rec.get("handle")
                followers = rec.get("followers")
                if isinstance(handle, str) and handle \
                        and handle != agent_id and handle != own_handle \
                        and handle not in seen_handles \
                        and isinstance(followers, int) \
                        and not isinstance(followers, bool) and followers > 0:
                    self._recommendations.append(
                        {"handle": handle, "followers": followers})
                    seen_handles.add(handle)
        self._recommendations.sort(key=lambda r: (-r["followers"], r["handle"]))
        self._recommendations = self._recommendations[:3]

        self._page_size = _pos_int(page_size, "page_size", 3)
        self._max_steps = _pos_int(max_steps, "max_steps", 6)

        self._step = 0
        self._closed = not self._cards
        self._events = []
        self._detail_pid = None
        self._exposed = []
        self._opened = set()
        self._liked = set()
        self._saved = set()
        self._visible_handles = set()
        self._card_by_pid = {c["post_id"]: c for c in self._cards}
        self._expose_page()

    # ---------- exposure ----------

    def _expose_page(self):
        start = len(self._exposed)
        end = min(start + self._page_size, len(self._cards))
        for card in self._cards[start:end]:
            self._exposed.append(card["post_id"])
            self._emit("impression", card["post_id"], "accepted", "preview exposed")

    def _emit(self, kind, post_id, status, reason, **extra):
        evt = {"seq": len(self._events), "agent_id": self._agent_id,
               "kind": kind, "post_id": post_id, "status": status,
               "reason": reason}
        evt.update(extra)
        self._events.append(copy.deepcopy(evt))
        return copy.deepcopy(evt)

    # ---------- properties ----------

    @property
    def done(self) -> bool:
        return self._closed or self._step >= self._max_steps

    @property
    def events(self) -> list:
        return copy.deepcopy(self._events)

    @property
    def following(self) -> set:
        return set(self._following)

    # ---------- shaping ----------

    def _clip(self, text: str, limit: int) -> str:
        text = _safe_text(text)
        return text if len(text) <= limit else text[:limit]

    def _preview(self, card: dict) -> dict:
        out = {"post_id": card["post_id"]}
        for key in ("org", "title", "arm"):
            value = card.get(key)
            if isinstance(value, str):
                out[key] = value
        if "arm" not in out:
            out["arm"] = "T"
        caption = card.get("caption")
        if isinstance(caption, str):
            out["caption"] = self._clip(caption, _MAX_CAPTION)
        if out.get("arm") == "TV" and isinstance(card.get("image_sha"), str):
            out["image_sha"] = card["image_sha"]
        self._source_fields(card, out)
        if 'image_refs' in out:
            out['image_refs'] = out['image_refs'][:1]  # cover preview; gallery requires open
        if out['arm'] == 'TC' and card.get('image_descriptions'):
            out['image_caption_frozen'] = card['image_descriptions'][0]
        return out

    @staticmethod
    def _source_fields(card, out):
        for key in ('market', 'channel', 'time_basis', 'institution_id', 'creative_id',
                    'source_post_id', 'source_published_at', 'publication_kind'):
            if isinstance(card.get(key), str):
                out[key] = card[key]
        # Legacy timed demos used published_at only as a scheduling field.
        # Source disclosure is opt-in through an explicit source channel.
        if isinstance(card.get('channel'), str) and isinstance(card.get('published_at'), str):
            out['published_at'] = card['published_at']
        if out['arm'] == 'TV' and isinstance(card.get('image_refs'), list):
            out['image_refs'] = [r for r in card['image_refs'] if isinstance(r, str)]

    def _comments(self, card: dict) -> list:
        raw = card.get("comments_prev") or []
        if not isinstance(raw, list):
            return []
        items = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            handle = c.get("handle")
            if handle is not None and not isinstance(handle, str):
                continue
            if handle == "":
                continue
            followers = c.get("followers")
            if followers is not None and (
                    not isinstance(followers, int)
                    or isinstance(followers, bool) or followers < 0):
                followers = None
            stance = c.get("stance")
            if stance is not None and not isinstance(stance, str):
                stance = None
            items.append({"handle": handle,
                          "followers": followers,
                          "text": _safe_text(c.get("text")),
                          "stance": stance,
                          "followed": isinstance(handle, str)
                          and handle in self._following})
        followed = [c for c in items if c["followed"]]
        promoted = followed[:2]
        promoted_ids = {id(c) for c in promoted}
        rest = [c for c in items if id(c) not in promoted_ids]
        return (promoted + rest)[:3]

    def _detail(self, card: dict) -> dict:
        arm = card.get("arm")
        arm = arm if isinstance(arm, str) else "T"
        out = {"post_id": card["post_id"]}
        for key in ("org", "title", "caption"):
            value = card.get(key)
            if isinstance(value, str):
                out[key] = value
        out["arm"] = arm
        if arm == "TC":
            for key in ("ocr_text", "image_caption_frozen"):
                value = card.get(key)
                if isinstance(value, str):
                    out[key] = value
            if card.get('image_descriptions'):
                out['image_caption_frozen'] = '\n'.join(
                    f'图片{i + 1}：{text}' for i, text in enumerate(card['image_descriptions']))
        elif arm == "TV":
            value = card.get("image_sha")
            if isinstance(value, str):
                out["image_sha"] = value
        out["comments_prev"] = self._comments(card)
        self._source_fields(card, out)
        return out

    # ---------- view ----------

    def view(self) -> dict:
        page_start = ((len(self._exposed) - 1) // self._page_size) * self._page_size \
            if self._exposed else 0
        exposed_now = self._exposed[page_start:page_start + self._page_size]
        feed = [self._preview(self._card_by_pid[pid]) for pid in exposed_now]
        detail = self._detail(self._card_by_pid[self._detail_pid]) \
            if self._detail_pid else None
        if detail is not None:
            detail = {"is_detail": True, **detail}
            for c in detail.get("comments_prev", ()):
                handle = c.get("handle")
                if isinstance(handle, str) and handle:
                    self._visible_handles.add(handle)
        return copy.deepcopy({
            "agent_id": self._agent_id,
            "step": self._step,
            "remaining_steps": max(0, self._max_steps - self._step),
            "done": self.done,
            "private_state": copy.deepcopy(self._private_state),
            "feed": feed,
            "detail": detail,
            "following": sorted(self._following),
            "recommendations": copy.deepcopy(self._recommendations),
            "visible_handles": sorted(
                self._visible_handles
                | {r["handle"] for r in self._recommendations}),
            "counts": {"exposed": len(set(self._exposed)),
                       "opened": len(self._opened),
                       "following": len(self._following)},
            "policy_note": _POLICY_NOTE,
        })

    # ---------- actions ----------

    def apply_private_action(self, action, executor):
        """Spend a normal microstep on a trusted private-account executor.

        No result from this path is exposed through public_events(). The executor
        is supplied by the world, never by model text or an observer endpoint.
        """
        if self.done or action.get('agent_id', self._agent_id) != self._agent_id:
            return self.apply(action)
        if action.get('kind') not in ('subscribe', 'redeem', 'buy', 'sell', 'cancel_order'):
            return self.apply(action)
        pid = action.get('post_id')
        if pid is not None and (not isinstance(pid, str) or pid not in self._exposed):
            return self._spend(action['kind'], None, 'rejected', 'source_post_not_exposed')
        result = executor(copy.deepcopy(action))
        if result.get('status') not in ('accepted', 'rejected') or not isinstance(result.get('reason'), str):
            raise ValueError('invalid private executor result')
        extras = {'order_id': result['order_id']} if 'order_id' in result else {}
        return self._spend(action['kind'], pid, result['status'], result['reason'], **extras)

    def apply(self, action) -> dict:
        if self.done:
            reason = "session_closed" if self._closed else "max_steps_reached"
            return {
                "seq": -1,
                "agent_id": self._agent_id,
                "kind": None,
                "post_id": None,
                "status": "rejected",
                "reason": reason,
            }
        action = copy.deepcopy(action) if isinstance(action, dict) else {}
        actor = action.get("agent_id", self._agent_id)
        if actor != self._agent_id:
            return self._spend(None, None, "rejected", "forged agent_id")
        kind = action.get("kind")
        if not isinstance(kind, str):
            kind = None
        pid = action.get("post_id")
        if not isinstance(pid, str):
            pid = None
        action["kind"] = kind
        action["post_id"] = pid
        handler = getattr(self, f"_do_{kind}", None) \
            if isinstance(kind, str) else None
        if handler is None:
            return self._spend(kind, pid, "rejected", "unknown_action")
        return handler(action)

    def _spend(self, kind, pid, status, reason, **extra):
        self._step += 1
        if self._step >= self._max_steps:
            self._closed = True
        return self._emit(kind, pid, status, reason, **extra)

    def _do_scroll(self, action):
        if len(self._exposed) >= len(self._cards):
            self._closed = True
            return self._spend("scroll", None, "accepted", "end_of_feed")
        evt = self._spend("scroll", None, "accepted", "page_exposed")
        self._expose_page()
        self._detail_pid = None
        return evt

    def _do_open(self, action, kind="open"):
        pid = action.get("post_id")
        if pid not in self._exposed or pid not in self._card_by_pid:
            return self._spend(kind, pid, "rejected", "post_not_exposed")
        self._detail_pid = pid
        self._opened.add(pid)
        card = self._card_by_pid[pid]
        for c in self._comments(card):
            handle = c.get("handle")
            if isinstance(handle, str) and handle:
                self._visible_handles.add(handle)
        return self._spend(kind, pid, "accepted", "detail_opened")

    def _do_comments(self, action):
        return self._do_open(action, kind="comments")

    def _do_like(self, action):
        pid = action.get("post_id")
        if pid not in self._exposed or pid not in self._card_by_pid:
            return self._spend("like", pid, "rejected", "post_not_exposed")
        if pid in self._liked:
            return self._spend("like", pid, "rejected", "already_liked")
        self._liked.add(pid)
        return self._spend("like", pid, "accepted", "liked")

    def _do_save(self, action):
        pid = action.get("post_id")
        if pid not in self._exposed or pid not in self._card_by_pid:
            return self._spend("save", pid, "rejected", "post_not_exposed")
        if pid in self._saved:
            return self._spend("save", pid, "rejected", "already_saved")
        self._saved.add(pid)
        return self._spend("save", pid, "accepted", "saved")

    def _do_comment(self, action):
        pid = action.get("post_id")
        text = _safe_text(action.get("text"))
        if pid not in self._exposed or pid not in self._card_by_pid:
            return self._spend("comment", pid, "rejected", "post_not_exposed")
        if not text.strip() or len(text) > _MAX_COMMENT:
            return self._spend("comment", pid, "rejected", "invalid_comment_text")
        return self._spend("comment", pid, "accepted", "comment_posted", text=text)

    def _handle_visible(self, handle: str) -> bool:
        return handle in self._visible_handles or any(
            r["handle"] == handle for r in self._recommendations)

    def _do_follow(self, action):
        handle = action.get("handle")
        if not isinstance(handle, str) or not self._handle_visible(handle):
            return self._spend("follow", None, "rejected", "handle_not_visible",
                               handle=handle if isinstance(handle, str) else None)
        if handle == self._agent_id or handle == self._own_handle:
            return self._spend("follow", None, "rejected", "self_follow",
                               handle=handle)
        if handle in self._following:
            return self._spend("follow", None, "rejected", "already_following",
                               handle=handle)
        self._following.add(handle)
        return self._spend("follow", None, "accepted", "followed", handle=handle)

    def _do_unfollow(self, action):
        handle = action.get("handle")
        if not isinstance(handle, str) or handle not in self._following:
            return self._spend("unfollow", None, "rejected", "not_following",
                               handle=handle if isinstance(handle, str) else None)
        self._following.discard(handle)
        return self._spend("unfollow", None, "accepted", "unfollowed",
                           handle=handle)

    def _do_finish(self, action):
        self._closed = True
        return self._spend("finish", None, "accepted", "session_finished")

    # ---------- public surface ----------

    def public_events(self) -> list:
        kinds = {"comment", "follow", "unfollow", "like"}
        out = []
        for evt in self._events:
            if evt["status"] != "accepted" or evt["kind"] not in kinds:
                continue
            out.append({k: copy.deepcopy(v) for k, v in evt.items()
                        if k in ("seq", "agent_id", "kind", "post_id",
                                 "status", "handle", "text")})
        return out

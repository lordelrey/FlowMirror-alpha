"""PublicBoard: coordinator for frozen public phases over real BrowseSession."""

import copy

from flowmirror.platform.browsing import BrowseSession

_SUPPORTED_CARD_FIELDS = (
    "post_id", "org", "title", "caption", "arm",
    "image_sha", "ocr_text", "image_caption_frozen", "comments_prev",
    "image_refs", "market", "channel", "published_at", "time_basis", "image_descriptions",
    "institution_id", "creative_id", "source_post_id", "source_published_at", "publication_kind",
)
_ARMS = ("T", "TC", "TV")
_MAX_COMMENT_LEN = 200


class PublicBoard:
    """Coordinates per-phase browsing sessions and a frozen public snapshot."""

    def __init__(self, cards, handles):
        if not isinstance(handles, dict):
            raise ValueError("handles must be a dict")
        self._handles = {}
        seen = set()
        for raw_id, handle in handles.items():
            if not isinstance(raw_id, str) or not raw_id:
                raise ValueError("raw actor IDs must be nonempty strings")
            if not isinstance(handle, str) or not handle:
                raise ValueError("public handles must be nonempty strings")
            if handle in seen:
                raise ValueError("duplicate public handle: %r" % handle)
            seen.add(handle)
            self._handles[raw_id] = handle
        self._raw_by_handle = {h: r for r, h in self._handles.items()}
        known_handles = set(self._handles.values())

        if not isinstance(cards, list):
            raise ValueError("cards must be a list")
        self._cards = []
        self._comments = {}
        for card in cards:
            public_card = self._sanitize_card(card, known_handles)
            if public_card["post_id"] in self._comments:
                raise ValueError("duplicate post_id: %r" % public_card["post_id"])
            self._comments[public_card["post_id"]] = public_card.pop("comments_prev")
            self._cards.append(public_card)
        self._post_ids = set(self._comments)

        self._following = {raw_id: set() for raw_id in self._handles}
        self._published_following = {raw_id: set() for raw_id in self._handles}
        self._active = {}
        self._committed = set()
        self._pending = []
        self._epoch = 0

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _sanitize_comment(entry, known_handles):
        if not isinstance(entry, dict):
            raise ValueError("comment must be a dict")
        handle = entry.get("handle")
        if handle is not None and (not isinstance(handle, str) or handle not in known_handles):
            raise ValueError("unknown comment handle: %r" % (handle,))
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("comment text must be a nonempty string")
        public = {"handle": handle, "text": text}
        stance = entry.get("stance")
        if stance is not None:
            if not isinstance(stance, str):
                raise ValueError("comment stance must be a string")
            public["stance"] = stance
        return public

    def _sanitize_card(self, card, known_handles):
        if not isinstance(card, dict):
            raise ValueError("card must be a dict")
        post_id = card.get("post_id")
        if not isinstance(post_id, str) or not post_id:
            raise ValueError("card post_id must be a nonempty string")
        comments = card.get("comments_prev")
        if comments is None:
            comments = []
        if not isinstance(comments, list):
            raise ValueError("comments_prev must be a list")
        if card.get('image_descriptions') is not None:
            descriptions, refs = card['image_descriptions'], card.get('image_refs')
            if (not isinstance(descriptions, list) or not isinstance(refs, list)
                    or not descriptions or len(descriptions) != len(refs)
                    or any(not isinstance(d, str) or not d.strip() for d in descriptions)
                    or any(not isinstance(r, str) or not r for r in refs)):
                raise ValueError('image_descriptions must match the ordered image_refs')
        public = {k: copy.deepcopy(card.get(k))
                  for k in _SUPPORTED_CARD_FIELDS if k != "comments_prev"}
        public["post_id"] = post_id
        public["comments_prev"] = [self._sanitize_comment(c, known_handles) for c in comments]
        return public

    def _published_counts(self):
        counts = {h: 0 for h in self._handles.values()}
        for targets in self._published_following.values():
            for handle in targets:
                counts[handle] += 1
        return counts

    def _render_comment(self, comment, counts):
        out = {
            "handle": comment["handle"],
            "text": comment["text"],
            "followers": counts[comment["handle"]] if comment["handle"] is not None else 0,
        }
        if comment.get("stance") is not None:
            out["stance"] = comment["stance"]
        return out

    # ------------------------------------------------------------- open/commit

    def publish_cards(self, cards):
        """Publish a scheduled batch between phases, never inside a frozen phase."""
        if self._active:
            raise ValueError("cannot publish cards during an active phase")
        if not isinstance(cards, list):
            raise ValueError("cards must be a list")
        sanitized = [self._sanitize_card(c, set(self._handles.values())) for c in cards]
        ids = [c["post_id"] for c in sanitized]
        if len(set(ids)) != len(ids) or any(p in self._post_ids for p in ids):
            raise ValueError("duplicate scheduled post_id")
        for card in sanitized:
            self._comments[card["post_id"]] = card.pop("comments_prev")
            self._cards.append(card)
            self._post_ids.add(card["post_id"])

    def open_session(self, agent_id, *, arm="T", private_state=None, max_steps=6, page_size=3, post_ids=None):
        if agent_id not in self._handles:
            raise ValueError("unknown agent id")
        if arm not in _ARMS:
            raise ValueError("invalid arm")
        if agent_id in self._active:
            raise ValueError("session already issued for this actor in this phase")
        from flowmirror.channels.feed import top_comments
        counts = self._published_counts()
        own_handle = self._handles[agent_id]
        recs = [
            {"handle": h, "followers": counts[h]}
            for h in sorted((h for h, n in counts.items() if n > 0 and h != own_handle),
                            key=lambda h: (-counts[h], h))[:3]
        ]
        session_cards = []
        if post_ids is None:
            selected_cards = self._cards
        else:
            by_id = {c['post_id']: c for c in self._cards}
            if len(set(post_ids)) != len(post_ids) or any(p not in by_id for p in post_ids):
                raise ValueError('invalid selected feed IDs')
            selected_cards = [by_id[p] for p in post_ids]
        for card in selected_cards:
            c = copy.deepcopy(card)
            post_id = c["post_id"]
            candidates = []
            for cm in self._comments[post_id]:
                handle = cm.get("handle")
                cand = {
                    "post_id": post_id,
                    "agent_id": handle if handle else "",
                    "handle": handle,
                    "text": cm.get("text", ""),
                    "followers": counts.get(handle, 0) if handle else 0,
                    "fam_level": 0,
                }
                if "stance" in cm and cm["stance"] is not None:
                    cand["stance"] = cm["stance"]
                candidates.append(cand)
            ranked = []
            if candidates:
                ranked = top_comments(
                    post_id,
                    candidates,
                    k=len(candidates),
                    salt=f"public_board:{self._epoch}:{post_id}",
                )
            comments_prev = []
            for item in ranked:
                out = {
                    "handle": item.get("handle"),
                    "text": item.get("text", ""),
                    "followers": item.get("followers", 0),
                }
                if "stance" in item and item["stance"] is not None:
                    out["stance"] = item["stance"]
                comments_prev.append(out)
            c["arm"] = arm
            c["comments_prev"] = comments_prev
            session_cards.append(c)
        session = BrowseSession(
            agent_id,
            session_cards,
            private_state=private_state,
            following=set(self._following[agent_id]),
            recommendations=recs,
            page_size=page_size,
            max_steps=max_steps,
            own_handle=self._handles[agent_id],
        )
        self._active[agent_id] = session
        return session

    def commit_session(self, session):
        registered = [actor for actor, active in self._active.items() if active is session]
        if len(registered) != 1:
            raise ValueError("session is not the registered active session")
        owner = registered[0]
        view = session.view()
        if view.get("agent_id") != owner:
            raise ValueError("session owner mismatch")
        if not session.done:
            raise ValueError("session not finished")
        if owner in self._committed:
            raise ValueError("actor already committed this phase")

        own_handle = self._handles[owner]
        following = session.following
        for target in following:
            if not isinstance(target, str) or target not in self._raw_by_handle or target == own_handle:
                raise ValueError("invalid following target")
        final_following = set(following)

        staged = []
        for event in session.public_events():
            if event.get("status") != "accepted":
                raise ValueError("event not accepted")
            if event.get("agent_id") != owner:
                raise ValueError("event agent mismatch")
            seq = event.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                raise ValueError("invalid event sequence")

            kind = event.get("kind")
            if kind == "comment":
                post_id = event.get("post_id")
                if post_id not in self._post_ids:
                    raise ValueError("unknown post in comment event")
                text = event.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > _MAX_COMMENT_LEN:
                    raise ValueError("invalid comment text")
                staged.append({"raw_id": owner, "seq": seq, "kind": "comment",
                               "post_id": post_id, "handle": own_handle, "text": text})
            elif kind in ("follow", "unfollow"):
                target = event.get("handle")
                if not isinstance(target, str) or target not in self._raw_by_handle or target == own_handle:
                    raise ValueError("invalid follow target")
                staged.append({"raw_id": owner, "seq": seq, "kind": kind, "handle": target})
            elif kind == "like":
                post_id = event.get("post_id")
                if post_id not in self._post_ids:
                    raise ValueError("unknown post in like event")
                staged.append({"raw_id": owner, "seq": seq, "kind": "like", "post_id": post_id})
            else:
                raise ValueError("unsupported public event kind")

        self._following[owner] = set(final_following)
        self._committed.add(owner)
        self._pending.extend(staged)

    # ------------------------------------------------------------------ phases

    def advance(self):
        if any(owner not in self._committed for owner in self._active):
            raise ValueError("cannot advance with uncommitted sessions")
        for item in sorted(self._pending, key=lambda i: (i["raw_id"], i["seq"])):
            if item["kind"] == "comment":
                self._comments[item["post_id"]].append(
                    {"handle": item["handle"], "text": item["text"]})
        self._published_following = {r: set(t) for r, t in self._following.items()}
        self._epoch += 1
        self._active = {}
        self._committed = set()
        self._pending = []

    # ---------------------------------------------------------------- snapshot

    def public_snapshot(self):
        counts = self._published_counts()
        comments = {post_id: [self._render_comment(c, counts) for c in clist]
                    for post_id, clist in self._comments.items()}
        edges = []
        for raw_id in sorted(self._published_following):
            src = self._handles[raw_id]
            for dst in sorted(self._published_following[raw_id]):
                edges.append({"from": src, "to": dst})
        return {
            "epoch": self._epoch,
            "comments": comments,
            "edges": edges,
            "followers": counts,
        }

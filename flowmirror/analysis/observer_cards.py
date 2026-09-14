"""Observer cards: own-impression projections for the mirror (read-only)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _handle_of(agent_id: int) -> Optional[str]:
    # Canonical handle resolution; only meaningful when social graph enabled.
    from flowmirror.engine.loop import handle_of
    return handle_of(agent_id)


def _attachment(arm: str, idx: Any, note: Dict[str, Any]) -> Dict[str, Any]:
    shas = note.get("image_sha256")
    shas = shas if isinstance(shas, list) else []

    att: Dict[str, Any] = {"valid_idx": False, "sha": None,
                           "caption": None, "ocr": None, "reconstruction_note": None}

    if arm == "TV":
        valid = (
            isinstance(idx, int) and not isinstance(idx, bool)
            and 0 <= idx < len(shas)
            and isinstance(shas[idx], str) and bool(shas[idx])
        )
        att["valid_idx"] = valid
        if valid:
            att["sha"] = shas[idx]
        else:
            att["reconstruction_note"] = "TV附件记录不足"
    elif arm == "TC":
        raw_ocr = note.get("ocr_masked")
        if not (isinstance(raw_ocr, str) and raw_ocr):
            raw_ocr = note.get("ocr_text")
        if isinstance(raw_ocr, str) and raw_ocr:
            att["ocr"] = raw_ocr[:200]
        caps = note.get("image_caption_frozen")
        if isinstance(caps, str):
            att["caption"] = caps
        elif isinstance(caps, list):
            att["caption"] = "".join(c for c in caps if isinstance(c, str))
    return att


def _comments_prev(s: Any, t: int, post_id: str, max_rows: int = 3) -> List[Dict[str, Any]]:
    if not s.cfg.get("social", True) or not s.cfg.get("channels", {}).get("social", True):
        return []
    graph_on = bool(s.cfg.get("social_graph", {}).get("enabled", False))
    cl = s.climate.get((t, post_id), {})
    if not graph_on and cl.get("label") in (None, "no_signal"):
        return []
    rows: List[Dict[str, Any]] = []
    for c in cl.get("top") or []:
        if not isinstance(c, dict):
            continue
        i = c.get("i")
        alias = _handle_of(i) if graph_on and isinstance(i, str) and i else None
        text = c.get("text") if isinstance(c.get("text"), str) else ""
        stance = c.get("stance") if isinstance(c.get("stance"), str) else ""
        rows.append({"handle": alias, "text": text, "stance": stance, "followers": None})
        if len(rows) >= max_rows:
            break
    return rows


def cards_for(s: Any, events: List[Dict[str, Any]], agent_id: int) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    graph_enabled = bool(s.cfg.get("social_graph", {}).get("enabled", False))
    for e in events:
        if e.get("ev") != "imp" or e.get("i") != agent_id:
            continue
        post = s.posts.get(str(e.get("p")), {}) or {}
        note = s.notes.get(str(post.get("note")), {}) or {}
        arm = e.get("arm") or s.meta.get("arms", {}).get(agent_id)
        att = _attachment(arm, e.get("img_idx"), note)
        caption = (note.get("caption_masked") or note.get("caption")
                   or note.get("abstract") or note.get("summary") or None)
        # Safe string checks: only keep actual strings/None, never coerce dicts to text.
        org = post.get("org") if isinstance(post.get("org"), str) else None
        title = note.get("title") if isinstance(note.get("title"), str) else None
        caption = caption if isinstance(caption, str) else None
        source = e.get("source") if isinstance(e.get("source"), str) else None

        card: Dict[str, Any] = {
            "post_id": e.get("p"),
            "org": org,
            "title": title,
            "caption": caption,
            "source": source,
            "note_id": post.get("note"),
            "arm": arm,
            "material_has_image": bool(post.get("img")),
            # Distinct from material_has_image: whether this impression
            # actually recorded an attachment (TV arm with valid image index).
            "attachment_recorded": bool(arm == "TV" and att["valid_idx"]),
            "comments_phase": "pre_decision_public_snapshot",
            "comments_prev": _comments_prev(s, e.get("t", 0), str(e.get("p"))),
        }

        # Arm-specific top-level fields (no nested "image" dict anymore).
        if arm == "TV":
            if att["sha"] is not None:
                card["image_sha"] = att["sha"]
        elif arm == "TC":
            card["ocr_text"] = att["ocr"]
            card["image_caption_frozen"] = att["caption"]

        # Reconstruction provenance, if any.
        recon_note = att.get("reconstruction_note")
        if isinstance(recon_note, str) and recon_note:
            note_parts = [recon_note]
            if graph_enabled:
                note_parts.append("历史关注排序未还原")
            card["reconstruction_note"] = "; ".join(note_parts)
        elif graph_enabled:
            card["reconstruction_note"] = "历史关注排序未还原"

        cards.append(card)
    return cards

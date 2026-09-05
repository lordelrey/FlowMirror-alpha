"""Core value types shared by all FlowMirror layers (v7.1 S2).

Additive only: this module imports nothing from the engine, the channels,
or the agents, so importing it cannot perturb any existing run. All
dataclasses are frozen; mutable payloads (``args``, ``fields``) are owned
by the producer and treated as immutable by convention, keeping event rows
deterministic (no timestamps, no counters).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, get_args

#: Modalities a Percept can carry. ``image_as_text`` is an image whose
#: content reached the agent rendered as text (caption / alt-text arm).
Modality = Literal["text", "image", "image_as_text"]

#: Materialised form of :data:`Modality` (order matches the Literal).
MODALITIES: tuple = ("text", "image", "image_as_text")

#: Arm -> modality map. "T" text-only control, "TC" image-as-text (caption),
#: "TV" true image. "TC" is reserved vocabulary: today arm_for_agent only
#: returns "T"/"TV"; nothing in the live pipeline consumes "TC" yet.
ARM_MODALITY: dict = {"T": "text", "TC": "image_as_text", "TV": "image"}


@dataclass(frozen=True)
class Percept:
    """One item an agent perceives on a channel during a single day.

    channel   feed / experience / news / trend / social / direct
    modality  one of MODALITIES
    text      rendered content exactly as shown to the agent
    sha       stable content hash (flowmirror.io.hashing conventions)
    image_ref opaque reference to binary media, if any
    t_source  simulation day the item was produced (may lag the shown day)
    """

    channel: str
    modality: str
    text: str
    sha: str
    image_ref: Optional[str] = None
    t_source: Optional[int] = None


@dataclass(frozen=True)
class MemoryItem:
    """One entry committed to an agent memory store at day close."""

    kind: str  # e.g. "experience", "reflection", "directive"
    t: int     # simulation day of the write
    text: str
    sha: str


@dataclass(frozen=True)
class Action:
    """A proposed (not yet adjudicated) act: post / comment / invest / ..."""

    type: str
    args: dict


@dataclass(frozen=True)
class Event:
    """An adjudicated event; ``fields`` carries the channel payload.

    Field names deliberately mirror the frozen event_log.jsonl vocabulary
    (post/imp/click/co/act/st, dec/cmt/clim/refl): ``ev``/``t``/``d`` sit
    next to a flat ``fields`` dict instead of per-channel dataclasses, so
    no serializer has to change.
    """

    ev: str       # event kind, e.g. "post", "imp", "dec"
    t: int        # tick / day index
    d: str        # day label as written to event_log.jsonl rows
    fields: dict  # channel-specific payload (i, p, arm, slot, ...)


def _self_test() -> int:
    assert set(get_args(Modality)) == set(MODALITIES) == {
        "text",
        "image",
        "image_as_text",
    }
    assert set(ARM_MODALITY) == {"T", "TC", "TV"}
    assert set(ARM_MODALITY.values()) <= set(MODALITIES)

    p = Percept(channel="feed", modality="text", text="hi", sha="ab" * 16)
    assert (p.image_ref, p.t_source) == (None, None)
    q = Percept("feed", "image", "cap", "cd" * 16, image_ref="img/1.png", t_source=3)
    assert q != p and q.t_source == 3 and q.image_ref == "img/1.png"

    m = MemoryItem("experience", 2, "sold", "ef" * 16)
    a = Action("post", {"body": "x"})
    e = Event("post", 2, "d2", {"p": 1})
    assert (m.kind, m.t, a.type, e.ev, e.d) == ("experience", 2, "post", "post", "d2")

    for obj, attr in ((p, "text"), (q, "image_ref"), (m, "t"), (a, "args"), (e, "fields")):
        try:
            setattr(obj, attr, None)
        except AttributeError:
            pass
        else:
            raise AssertionError("%s must be frozen" % type(obj).__name__)

    assert all(ord(ch) < 128 for ch in repr(p) + repr(e))
    print("[ok] flowmirror.core.types self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

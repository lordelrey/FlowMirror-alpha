"""Agent perception facade for the public layer API.

Re-exports the prompt renderers that today implement perception; every
name is the *same object* as in ``flowmirror.agents.prompt`` (no
wrappers, no behaviour change). ``PERCEIVERS`` maps channel names to the
renderer used for that channel. New code should import from here (or
code against ``flowmirror.core.protocols.Perceiver``); this facade is
deleted when perception becomes native. Anti-priming checks stay where
they are: only our own framing blocks are screened, never real creatives.
"""

from flowmirror.agents.prompt import (
    render_card,
    render_direct,
    render_experience,
    render_news,
    render_social,
    render_trend,
)

#: channel -> renderer (feed uses the assembled card).
PERCEIVERS = {
    "feed": render_card,
    "experience": render_experience,
    "news": render_news,
    "trend": render_trend,
    "social": render_social,
    "direct": render_direct,
}

__all__ = [
    "PERCEIVERS",
    "render_card",
    "render_direct",
    "render_experience",
    "render_news",
    "render_social",
    "render_trend",
]


def _self_test() -> int:
    import flowmirror.agents.prompt as prompt

    for name in (
        "render_card",
        "render_direct",
        "render_experience",
        "render_news",
        "render_social",
        "render_trend",
    ):
        assert globals()[name] is getattr(prompt, name), name
    assert set(PERCEIVERS) == {
        "feed",
        "experience",
        "news",
        "trend",
        "social",
        "direct",
    }
    for key, fn in PERCEIVERS.items():
        assert fn is getattr(prompt, fn.__name__), key
    print("[ok] flowmirror.agents.perceive facade self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

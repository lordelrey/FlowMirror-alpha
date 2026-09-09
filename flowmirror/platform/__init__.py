"""Platform layer facade for the public layer API.

Re-exports the channel/feed functions that today implement the platform
layer; every name is the *same object* as in ``flowmirror.channels.feed``
(no wrappers, no behaviour change). New code should import from here (or
code against ``flowmirror.core.protocols.Platform``), never from the
legacy module path; this facade is deleted when a native platform module
lands. The Protocol's lag rule (rank on t-1 tallies, aggregate for t+1)
becomes binding at that point.
"""

from flowmirror.channels.feed import (
    arm_for_agent,
    assign_arms,
    check_arm_balance,
    climate_for,
    hot_score,
    rank_feed,
    top_comments,
)

__all__ = [
    "arm_for_agent",
    "assign_arms",
    "check_arm_balance",
    "climate_for",
    "hot_score",
    "rank_feed",
    "top_comments",
]


def _self_test() -> int:
    from flowmirror.channels import feed

    for name in __all__:
        assert globals()[name] is getattr(feed, name), name
    print("[ok] flowmirror.platform facade self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

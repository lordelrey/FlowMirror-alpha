"""Society layer facade (PROJECT_ARCHITECTURE_v7.1 S2).

Re-exports the world/population functions that today implement the
society layer; every name is the *same object* as in its original module
(no wrappers, no behaviour change). New code should import from here (or
code against ``flowmirror.core.protocols.Society``), never from the
legacy module paths; this facade is deleted once society is native. The
Protocol's lag rule (observe the closed day-t ledger, feed day t+1)
becomes binding at that point.
"""

from flowmirror.engine.world import (
    init_investors,
    intent_probs,
    load_world,
    publish_day,
)
from flowmirror.population.sampler import sample_cohort

__all__ = [
    "init_investors",
    "intent_probs",
    "load_world",
    "publish_day",
    "sample_cohort",
]


def _self_test() -> int:
    import flowmirror.engine.world as world
    import flowmirror.population.sampler as sampler

    src = {
        "load_world": world,
        "init_investors": world,
        "intent_probs": world,
        "publish_day": world,
        "sample_cohort": sampler,
    }
    for name, mod in src.items():
        assert globals()[name] is getattr(mod, name), name
    print("[ok] flowmirror.society facade self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

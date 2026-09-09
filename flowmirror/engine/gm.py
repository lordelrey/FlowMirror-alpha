"""Game-master facade for the public layer API.

Gives layer-stable names to the adjudication entry points that currently
live inside the engine loop, plus the regulator outcome and the log
invariants. Every name is the *same object* as its original (no wrappers,
no behaviour change):

    adjudicate_decision  is flowmirror.engine.loop.apply_decision
    adapt_record         is flowmirror.engine.loop._adapt_record
    cxr_outcome          is flowmirror.regulator.cn_cxr.cxr_outcome
    EventLog             is flowmirror.engine.world.EventLog
    check_invariants     is flowmirror.engine.world.check_invariants

New code should import from here (or code against
``flowmirror.core.protocols.GameMaster``); this facade is deleted when
adjudication becomes a native module honouring the Protocol's lag rule
(adjudicate only after all agents decided; single writer; deterministic
rows: no timestamps, no counters).
"""

from flowmirror.engine.loop import _adapt_record, apply_decision
from flowmirror.engine.world import EventLog, check_invariants
from flowmirror.regulator.cn_cxr import cxr_outcome

#: Adjudicate one agent decision (facade name for loop.apply_decision).
adjudicate_decision = apply_decision

#: Facade name for the loop's adaptation record helper.
adapt_record = _adapt_record

__all__ = [
    "EventLog",
    "adjudicate_decision",
    "adapt_record",
    "check_invariants",
    "cxr_outcome",
]


def _self_test() -> int:
    import flowmirror.engine.loop as loop
    import flowmirror.engine.world as world
    import flowmirror.regulator.cn_cxr as cn_cxr

    assert adjudicate_decision is loop.apply_decision
    assert adapt_record is loop._adapt_record
    assert cxr_outcome is cn_cxr.cxr_outcome
    assert EventLog is world.EventLog
    assert check_invariants is world.check_invariants
    assert apply_decision is loop.apply_decision  # original binding intact
    print("[ok] flowmirror.engine.gm facade self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

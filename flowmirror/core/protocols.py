"""Layer Protocols for FlowMirror (PROJECT_ARCHITECTURE_v7.1 S2).

Structural interfaces only: no behaviour, no imports of engine modules,
so adding this file cannot change any existing run. Each Protocol states
(a) which architecture layer it names and (b) its lag rule: the clock it
runs on and how far behind the engine tick it may lag. Today most layers
are facades (see docs/LAYERS.md); new code must be written against these
Protocols so the facades can be removed as modules are refactored.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol, Sequence, runtime_checkable

from flowmirror.core.types import Action, Event, MemoryItem, Percept


@runtime_checkable
class Perceiver(Protocol):
    """Layer: agent / perception (rendering of the day's view).

    Lag rule: runs once per agent per day t and may render only content
    the platform has already published for day t; it must never read
    pending (un-adjudicated) proposals or another agent's private state.
    """

    def perceive(self, view: dict, agent_state: dict, arm: str) -> list[Percept]:
        ...


@runtime_checkable
class Memory(Protocol):
    """Layer: agent / memory (store plus end-of-day reflection).

    Lag rule: writes for day t commit only after the day has closed, so
    retrieve() during day t sees items up to and including day t-1 (no
    read-your-own-write within a single day); reflect() runs at day close.
    """

    def retrieve(self, ctx: dict) -> dict:
        ...

    def write(self, item: MemoryItem) -> None:
        ...

    def reflect(self, llm: Any) -> dict:
        ...


@runtime_checkable
class Deliberator(Protocol):
    """Layer: agent / cognition (the engine computes; the LLM decides).

    Lag rule: decides once per agent per day t over percepts(t) plus the
    memory snapshot from t-1; it mutates no world state and returns the
    decision payload plus a deterministic scratch record for the log.
    """

    def decide(
        self, working_memory: dict, percepts: Sequence[Percept], instr: str
    ) -> tuple[dict, dict]:
        ...


@runtime_checkable
class Actuator(Protocol):
    """Layer: agent / action (decision turned into proposed Actions).

    Lag rule: proposals are formed in day t but take effect only after
    the GameMaster adjudicates them; the actuator never mutates the
    ledger, the feed, or any other agent's state directly.
    """

    def propose(self, decision: dict) -> list[Action]:
        ...


@runtime_checkable
class GameMaster(Protocol):
    """Layer: engine / adjudication (single writer of the event log).

    Lag rule: adjudicates a day's proposals only after every agent has
    decided, then emits Events stamped with that day t; adjudication is
    deterministic given (state, actions): no timestamps, no counters.
    """

    def adjudicate(self, agent: dict, actions: Sequence[Action]) -> list[Event]:
        ...


@runtime_checkable
class Platform(Protocol):
    """Layer: platform (channels: ranking, climate, arm assignment).

    Lag rule: rank() orders items using tallies frozen at the end of day
    t-1 (no same-day feedback loops); aggregate() folds day-t events into
    the tallies that become visible to ranking on day t+1.
    """

    def rank(self, items: Iterable[dict], ctx: Optional[dict] = None) -> list[dict]:
        ...

    def aggregate(self, events: Iterable[dict]) -> dict:
        ...


@runtime_checkable
class InstitutionPolicy(Protocol):
    """Layer: regulator / institution (e.g. the CN CXR policy).

    Lag rule: publish() runs once per day t after adjudication; whatever
    it publishes becomes visible to agents only from day t+1 onward.
    """

    def publish(self, day: int, state: dict) -> list[Event]:
        ...


@runtime_checkable
class Society(Protocol):
    """Layer: society (population, demographics, world refresh).

    Lag rule: observe() reads only the closed ledger for day t and
    returns aggregates that parameterise the day t+1 cohort / world
    refresh; it never feeds back intra-day.
    """

    def observe(self, ledger: Any) -> dict:
        ...


@runtime_checkable
class Experiment(Protocol):
    """Layer: experiment harness (scenario crossed with run config).

    Lag rule: run() executes a whole scenario over run_cfg end-to-end and
    returns the summary; it streams no partial state back into the live
    loop and is deterministic for a fixed run_tag (seeded RNG streams).
    """

    def run(self, scenario: dict, run_cfg: dict) -> dict:
        ...


def _self_test() -> int:
    protos = (
        Perceiver,
        Memory,
        Deliberator,
        Actuator,
        GameMaster,
        Platform,
        InstitutionPolicy,
        Society,
        Experiment,
    )

    class _Per:
        def perceive(self, view, agent_state, arm):
            return []

    class _Mem:
        def retrieve(self, ctx):
            return {}

        def write(self, item):
            return None

        def reflect(self, llm):
            return {}

    class _Del:
        def decide(self, working_memory, percepts, instr):
            return {}, {}

    class _Act:
        def propose(self, decision):
            return []

    class _GM:
        def adjudicate(self, agent, actions):
            return []

    class _Plat:
        def rank(self, items, ctx=None):
            return list(items)

        def aggregate(self, events):
            return {}

    class _Inst:
        def publish(self, day, state):
            return []

    class _Soc:
        def observe(self, ledger):
            return {}

    class _Exp:
        def run(self, scenario, run_cfg):
            return {}

    pairs = (
        (_Per(), Perceiver),
        (_Mem(), Memory),
        (_Del(), Deliberator),
        (_Act(), Actuator),
        (_GM(), GameMaster),
        (_Plat(), Platform),
        (_Inst(), InstitutionPolicy),
        (_Soc(), Society),
        (_Exp(), Experiment),
    )
    for obj, proto in pairs:
        assert isinstance(obj, proto), proto.__name__
        assert not isinstance(object(), proto), proto.__name__
    for proto in protos:
        assert "Layer:" in proto.__doc__ and "Lag rule:" in proto.__doc__, proto.__name__
    print("[ok] flowmirror.core.protocols self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

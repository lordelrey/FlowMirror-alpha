"""flowmirror.core - shared vocabulary for the five layers (v7.1 S2).

Re-exports the frozen value types and the layer Protocols so that
``from flowmirror.core import Percept, Perceiver`` works. This package is
purely additive: it imports nothing from the engine, channels, or agents at
import time, and it changes no existing behaviour.
"""

from flowmirror.core.protocols import (
    Actuator,
    Deliberator,
    Experiment,
    GameMaster,
    InstitutionPolicy,
    Memory,
    Perceiver,
    Platform,
    Society,
)
from flowmirror.core.types import (
    ARM_MODALITY,
    MODALITIES,
    Action,
    Event,
    MemoryItem,
    Modality,
    Percept,
)

__all__ = [
    "ARM_MODALITY",
    "MODALITIES",
    "Action",
    "Actuator",
    "Deliberator",
    "Event",
    "Experiment",
    "GameMaster",
    "InstitutionPolicy",
    "Memory",
    "MemoryItem",
    "Modality",
    "Percept",
    "Perceiver",
    "Platform",
    "Society",
]


def _self_test() -> int:
    import flowmirror.core.protocols as protocols
    import flowmirror.core.types as types

    assert ARM_MODALITY is types.ARM_MODALITY
    assert MODALITIES is types.MODALITIES
    assert Percept is types.Percept
    assert Event is types.Event
    assert MemoryItem is types.MemoryItem
    assert Action is types.Action
    assert Perceiver is protocols.Perceiver
    assert Experiment is protocols.Experiment
    assert set(ARM_MODALITY) == {"T", "TC", "TV"}
    print("[ok] flowmirror.core self-test")
    return 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    print(__doc__)

"""Frozen-cohort pin for flowmirror.population.sampler.sample_cohort.

sample_cohort(pop, grid, seed=2027, n=400, c2_share=0.0807) must reproduce
the approved proportional cohort: the sha256 over the newline-joined
SORTED agent id list is frozen below.  Input files are looked up under
FLOWMIRROR_RESEARCH_ROOT (default "D:/Desktop/ABM paper/fundmarket-sim"),
probing the two known data layouts (the flowmirror data dir next to the
research repo, and the repo's own data/ dir, with or without a
population/ subdirectory); the test skips with a reason when absent.
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest

# Make the package importable no matter where pytest was started from.
_PKG_PARENT = Path(__file__).resolve().parents[2]
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from flowmirror.population.sampler import sample_cohort  # noqa: E402

RESEARCH_ROOT = Path(os.environ.get("FLOWMIRROR_RESEARCH_ROOT",
                                    "D:/Desktop/ABM paper/fundmarket-sim"))

FROZEN_AGENTS_SHA256 = "ddae7d79e0d835c7c21b88b66c3b1ca574c83f65cd1b908af209d73ca9d5dca3"

_SEARCH_DIRS = (
    RESEARCH_ROOT / ".." / "flowmirror" / "flowmirror" / "data",
    RESEARCH_ROOT / "data",
)
_POP_NAMES = ("population/population_10k_v3.json", "population_10k_v3.json")
_GRID_NAMES = ("persona_grid_v3.json", "population/persona_grid_v3.json")


def _find(name_tuple):
    for base in _SEARCH_DIRS:
        for rel in name_tuple:
            cand = base / rel
            if cand.is_file():
                return cand.resolve()
    return None


def test_sample_cohort_reproduces_frozen_cohort():
    pop_path = _find(_POP_NAMES)
    grid_path = _find(_GRID_NAMES)
    if pop_path is None or grid_path is None:
        pytest.skip(
            "population_10k_v3.json / persona_grid_v3.json not found under "
            "%s/../flowmirror/flowmirror/data or %s/data"
            % (RESEARCH_ROOT, RESEARCH_ROOT))
    payload = sample_cohort(pop_path, grid_path, seed=2027, n=400, c2_share=0.0807)
    ids = [a["id"] for a in payload["agents"]]
    assert len(ids) == 400
    digest = hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
    assert digest == FROZEN_AGENTS_SHA256, digest
    # _meta must carry the same cohort fingerprint.
    assert payload["_meta"]["agents_sha256"] == FROZEN_AGENTS_SHA256

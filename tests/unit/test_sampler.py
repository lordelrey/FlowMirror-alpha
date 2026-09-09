"""Unit test for flowmirror.population.sampler.sample_cohort."""

from hashlib import sha256
from pathlib import Path

from flowmirror.population.sampler import sample_cohort

REPO_ROOT = Path(__file__).resolve().parents[2]
POP_PATH = REPO_ROOT / "data" / "population" / "population_10k_v3.json"
GRID_PATH = REPO_ROOT / "data" / "population" / "persona_grid_v3.json"


def test_sample_cohort_golden_hash():
    assert POP_PATH.is_file()
    assert GRID_PATH.is_file()

    payload = sample_cohort(POP_PATH, GRID_PATH, seed=2027, n=400, c2_share=0.0807)

    ids = [agent["id"] for agent in payload["agents"]]
    assert len(ids) == 400
    assert len(set(ids)) == 400

    digest = sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()
    assert digest == "ddae7d79e0d835c7c21b88b66c3b1ca574c83f65cd1b908af209d73ca9d5dca3"
    assert payload["_meta"]["agents_sha256"] == digest

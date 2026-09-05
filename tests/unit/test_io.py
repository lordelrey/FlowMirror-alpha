"""IO helper tests: jsonl round-trip, sha256 stability, seed determinism."""
from __future__ import annotations

from flowmirror.io.hashing import rng_seed_from, sha256_file, sha256_text
from flowmirror.io.jsonl import iter_jsonl, write_jsonl_atomic


def test_jsonl_round_trip(tmp_path):
    records = [
        {"ev": "imp", "t": 1, "i": "inv_00001"},
        {"ev": "act", "t": 2, "i": "inv_00002", "amount": 1000.5},
        {"ev": "co", "t": 3, "oc": "match"},
    ]
    path = tmp_path / "events.jsonl"
    write_jsonl_atomic(path, records)
    assert list(iter_jsonl(path)) == records


def test_sha256_stability(tmp_path):
    assert (
        sha256_text("hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    p = tmp_path / "note.txt"
    p.write_text("hello", encoding="utf-8")
    assert sha256_file(p) == sha256_text("hello")


def test_rng_seed_from_deterministic():
    a = rng_seed_from("mock_10x3", "day", 1, "inv_00001")
    b = rng_seed_from("mock_10x3", "day", 1, "inv_00001")
    c = rng_seed_from("mock_10x3", "day", 2, "inv_00001")
    assert isinstance(a, int) and 0 <= a < 2 ** 64
    assert a == b
    assert a != c

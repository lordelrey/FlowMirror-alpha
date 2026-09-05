"""Hashing helpers: files, text, deterministic RNG seeds."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_text(text: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file, read in chunks."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rng_seed_from(*parts) -> int:
    """Deterministic 64-bit seed from arbitrary parts.

    Example: rng_seed_from(run_tag, "day", 3, "inv_00001") -> stable int.
    """
    joined = "|".join(str(p) for p in parts)
    return int(sha256_text(joined)[:16], 16)

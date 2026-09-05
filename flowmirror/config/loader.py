"""Configuration loading utilities for FlowMirror.

Loads JSON/YAML config files and resolves data-file references against a
project root so downstream code can always work with absolute paths.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

_PATH_SUFFIXES = (
    "_file",          # covers agents_file, cells_file, flows_file, ...
    "_dir",           # covers out_dir
    "pool",           # covers pool and content_pool
    "cache",          # covers cache and nav_cache
    "guba_signal",
    "content_pool",
    "nav_cache",
    "agents_file",
)


def load_config(path: str | Path) -> dict:
    """Load a JSON or YAML config file and return it as a dict."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config file not found: {p}")
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix == ".json":
        obj = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        obj = yaml.safe_load(text)
    else:
        raise ValueError(
            f"unsupported config format {suffix!r}: {p} (expected .json/.yaml/.yml)"
        )
    if not isinstance(obj, dict):
        raise ValueError(f"config root must be a mapping: {p}")
    return obj


def _resolve(value: Any, key: str, root: Path) -> Any:
    if isinstance(value, dict):
        return {k: _resolve(v, k, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, key, root) for v in value]
    if isinstance(value, str) and key.endswith(_PATH_SUFFIXES):
        if Path(value).is_absolute():
            return value
        return (root / value).as_posix()
    return value


def resolve_paths(cfg: dict, root: str | Path) -> dict:
    """Return a copy of cfg with path-like string values made absolute against root."""
    return _resolve(copy.deepcopy(cfg), "", Path(root))


def deep_merge(defaults: dict, overrides: dict) -> dict:
    """Recursively merge two dicts; overrides win on scalar conflicts."""
    out = copy.deepcopy(defaults)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out

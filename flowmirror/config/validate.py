"""JSON Schema validation for FlowMirror configs and artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .loader import load_config

SCHEMA_NAMES = ("run", "scenario", "persona", "event", "fund_meta")


class ConfigError(Exception):
    """Raised when a config or artifact fails schema validation."""


def schema_dir() -> Path:
    """Locate the directory holding ``<name>.schema.json`` files."""
    env = os.environ.get("FLOWMIRROR_SCHEMA_DIR")
    if env:
        return Path(env)
    try:
        # schemas shipped as package data inside an installed/wheeled flowmirror
        import importlib.resources as _res

        cand = Path(str(_res.files("flowmirror"))) / "schemas"
        if cand.is_dir():
            return cand
    except Exception:
        pass
    # repo checkout: <repo>/config/schemas (this file is <repo>/flowmirror/config/validate.py)
    cand = Path(__file__).resolve().parents[2] / "config" / "schemas"
    if cand.is_dir():
        return cand
    raise ConfigError(
        "config/schemas not found; install with 'pip install -e .' "
        "or set FLOWMIRROR_SCHEMA_DIR"
    )


def load_schema(schema_name: str) -> dict:
    if schema_name not in SCHEMA_NAMES:
        raise ConfigError(
            f"unknown schema {schema_name!r}; expected one of: {', '.join(SCHEMA_NAMES)}"
        )
    path = schema_dir() / f"{schema_name}.schema.json"
    if not path.is_file():
        raise ConfigError(f"schema file missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _pointer(err) -> str:
    parts = [str(p) for p in err.absolute_path]
    return "/" + "/".join(parts) if parts else "/"


def validate(obj: Any, schema_name: str) -> Any:
    """Validate obj against a named schema.

    Collects ALL errors and raises ConfigError listing json-pointer paths.
    Returns obj on success.
    """
    schema = load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(obj), key=lambda e: _pointer(e))
    if errors:
        lines = [f"  {_pointer(e)}: {e.message}" for e in errors]
        raise ConfigError(
            f"{len(errors)} error(s) against {schema_name}.schema.json:\n" + "\n".join(lines)
        )
    return obj


def validate_file(path: str | Path, schema_name: str) -> Any:
    """Load a JSON/YAML file and validate it against a named schema."""
    return validate(load_config(Path(path)), schema_name)

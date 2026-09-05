"""FlowMirror v7 control CLI.

Commands:
    flowmirror validate <path> [--schema run|scenario|persona|fund_meta|event]
    flowmirror schemas
    flowmirror tree

Exit codes: 0 success, 1 validation/usage failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config.loader import load_config
from .config.validate import SCHEMA_NAMES, ConfigError, schema_dir, validate

_TREE = """\
flowmirror/                  package map (engine lands in P3)
    __init__.py  cli.py
    config/       loader.py validate.py  (schema files: config/schemas/)
    population/   cells.py cohort.py beliefs_seed.py
    agents/       investor.py belief.py memory.py decision.py
    channels/     feed.py experience.py trend.py social.py news.py direct.py
    institutions/ org.py policy.py pool.py posting.py
    market/       nav.py funds.py calendar.py settlement.py climate.py
    regulator/    base.py cn_cxr.py us_regbi.py
    engine/       day_loop.py event_log.py arms.py rng.py snapshots.py
    analysis/     metrics.py holdout.py placebo.py report.py
    io/           jsonl.py hashing.py backups.py
"""


def _autodetect_schema(path: Path) -> str:
    if path.name.lower() in ("scenario.yaml", "scenario.yml"):
        return "scenario"
    return "run"


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    schema = args.schema or _autodetect_schema(path)
    try:
        obj = load_config(path)
        validate(obj, schema)
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[flowmirror] FAIL {path} ({schema}): {exc}", file=sys.stderr)
        return 1
    print(f"[flowmirror] OK   {path} <- {schema}.schema.json")
    return 0


def _cmd_schemas(args: argparse.Namespace) -> int:
    try:
        base = schema_dir()
    except ConfigError as exc:
        print(f"[flowmirror] {exc}", file=sys.stderr)
        return 1
    print(f"[flowmirror] schema directory: {base}")
    for name in SCHEMA_NAMES:
        print(f"    {name:<10} {name}.schema.json")
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    print(_TREE.rstrip("\n"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowmirror",
        description="FlowMirror v7 control CLI (scaffold stage; engine lands in P3).",
    )
    parser.add_argument("--version", action="version", version=f"flowmirror {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a JSON/YAML file against a schema")
    p_validate.add_argument("path", help="config file to validate")
    p_validate.add_argument(
        "--schema",
        choices=list(SCHEMA_NAMES),
        default=None,
        help="default: scenario.yaml -> scenario, else run",
    )
    p_validate.set_defaults(func=_cmd_validate)

    p_schemas = sub.add_parser("schemas", help="list bundled schemas")
    p_schemas.set_defaults(func=_cmd_schemas)

    p_tree = sub.add_parser("tree", help="print the package module plan")
    p_tree.set_defaults(func=_cmd_tree)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

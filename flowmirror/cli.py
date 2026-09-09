"""FlowMirror v7 control CLI.

Commands:
    flowmirror run <run.json> [--mock] [--days N] [--agents N] [--seed N]
                   [--out DIR] [--replay-check] [--dump-prompt AGENT@DAY|first]
    flowmirror demo [two-arm|three-arm|null] [--days N] [--agents N]
                    [--out DIR] [--replay-check]
    flowmirror validate <path> [--schema run|scenario|persona|fund_meta|event]
    flowmirror schemas
    flowmirror tree

`run` is a thin wrapper: it validates the config against
config/schemas/run.schema.json first (exit 1 with a readable error on
failure), then hands off to the engine entry point
(flowmirror.engine.loop.main) and propagates its exit code. `tree` is
generated from the installed package at run time, so it cannot rot.

--dump-prompt (like every CLI-only runtime switch) is forwarded verbatim to
the engine, which carries it in its RuntimeOpts object; it is never merged
into the validated run config, whose schema rejects unknown keys by design.

Exit codes: 0 success, 1 validation/usage failure, 2 engine argument error,
3 engine failure or replay-check mismatch (propagated from the engine).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

from . import __version__
from .config.loader import load_config
from .config.validate import SCHEMA_NAMES, ConfigError, schema_dir, validate

# ---------------------------------------------------------------------------
# Shipped offline demos (synthetic NAVs; see docs/RUNBOOK.md section 1).
# name -> (config path inside the repo, force_mock, default days, default agents)
# ---------------------------------------------------------------------------

_DEMO_SPECS: dict[str, tuple[str, bool, int, int]] = {
    "two-arm": ("runs/demo_two_arm.json", True, 5, 40),
    "three-arm": ("runs/demo_three_arm.json", True, 5, 60),
    "null": ("runs/demo_null.json", False, 5, 40),
}

_DEMO_NEXT: dict[str, str] = {
    "two-arm": (
        "open event_log.jsonl and watch how the two arms' creatives shift "
        "agent decisions day by day (rows of type 'dec')"
    ),
    "three-arm": (
        "compare the decision mix across the three arms in event_log.jsonl "
        "(rows of type 'dec'); note the fee treatment"
    ),
    "null": (
        "agent_policy 'null' makes zero LLM calls; only world/market events "
        "appear in event_log.jsonl"
    ),
}

_DEMO_NAV = "data/funds/nav_demo_2025q4.json"
_DEMO_NAV_CMD = (
    "python data_pipeline/cn/make_demo_nav.py "
    "--pool data/creatives/cn/content_pool_v1_masked.jsonl "
    f"--out {_DEMO_NAV}"
)

_TREE_MAX_LINES = 40  # hard line budget for `flowmirror tree`


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


# ---------------------------------------------------------------------------
# engine hand-off (run / demo)
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Repo root as the engine itself resolves it (engine.loop.ROOT)."""
    try:
        from .engine import loop as _loop
    except ImportError:
        _loop = None
    root = getattr(_loop, "ROOT", None)
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[1]


def _run_engine(argv: list[str]) -> int:
    """Hand off to the engine entry point and propagate its exit code.

    Lazy import so `validate`/`schemas`/`tree` stay cheap. No duplicated
    logic, no subprocess: we build an argv and call
    flowmirror.engine.loop.main(argv) directly.
    """
    from .engine.loop import main as engine_main

    try:
        return engine_main(argv)
    except SystemExit as exc:  # engine argparse usage errors
        return exc.code if isinstance(exc.code, int) else (0 if not exc.code else 2)
    except KeyboardInterrupt:
        print("[flowmirror] interrupted", file=sys.stderr)
        return 130


def _validate_run_config(path: Path) -> int:
    """Validate a run config against run.schema.json; 0 ok, 1 failure."""
    try:
        obj = load_config(path)
        validate(obj, "run")
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"[flowmirror] FAIL {path} (run): {exc}", file=sys.stderr)
        print(
            "[flowmirror] the engine was not started; fix the config and retry "
            "(schema: config/schemas/run.schema.json)",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    rc = _validate_run_config(path)
    if rc:
        return rc
    argv = [str(path)]
    if args.mock:
        argv.append("--mock")
    if args.days is not None:
        argv += ["--days", str(args.days)]
    if args.agents is not None:
        argv += ["--agents", str(args.agents)]
    if args.seed is not None:
        argv += ["--seed", str(args.seed)]
    if args.out:
        argv += ["--out", args.out]
    if args.replay_check:
        argv.append("--replay-check")
    if getattr(args, "retry_transport_holes", False):
        argv.append("--retry-transport-holes")
    if getattr(args, "workers", None):
        argv += ["--workers", str(args.workers)]
    if args.dump_prompt:
        # Runtime-only switch: forwarded verbatim; the engine carries it in its
        # RuntimeOpts object (card R2D) -- it must never be merged into the
        # validated run config, whose schema (additionalProperties: false)
        # rejects unknown keys on purpose.
        argv += ["--dump-prompt", args.dump_prompt]
    extras = " ".join(argv[1:]) or "(config defaults)"
    print(f"[flowmirror] run: {path} {extras}")
    return _run_engine(argv)


def _summarise_log(out_dir: Path) -> tuple[int, int]:
    """(rows, dec rows) from <out_dir>/event_log.jsonl; (0, 0) if absent."""
    log = out_dir / "event_log.jsonl"
    rows = decs = 0
    if not log.is_file():
        return rows, decs
    with log.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and any(
                obj.get(k) == "dec" for k in ("type", "event", "kind")
            ):
                decs += 1
    return rows, decs


def _cmd_demo(args: argparse.Namespace) -> int:
    config_rel, force_mock, def_days, def_agents = _DEMO_SPECS[args.name]
    root = _repo_root()
    config_path = root / config_rel

    if not config_path.is_file():
        print(f"[flowmirror] demo config not found: {config_path}")
        expected = ", ".join(
            str(root / spec[0]) for _, spec in sorted(_DEMO_SPECS.items())
        )
        print(f"[flowmirror] expected shipped demo configs: {expected}")
        return 1

    nav_path = root / _DEMO_NAV
    if not nav_path.is_file():
        print(f"[flowmirror] synthetic demo NAV not found: {nav_path}")
        print("[flowmirror] demos run fully offline on synthetic data; generate it once with:")
        print(f"[flowmirror]   {_DEMO_NAV_CMD}")
        return 1

    rc = _validate_run_config(config_path)
    if rc:
        return rc

    days = def_days if args.days is None else args.days
    agents = def_agents if args.agents is None else args.agents
    out = args.out or f"runs/out/demo_{args.name}"
    argv = [str(config_path)]
    if force_mock:
        argv.append("--mock")
    argv += ["--days", str(days), "--agents", str(agents), "--out", out]
    if args.replay_check:
        argv.append("--replay-check")

    mode = "mock LLM (zero API calls)" if force_mock else "null policy (zero API calls)"
    print(f"[flowmirror] demo {args.name}: {config_rel}")
    print(f"[flowmirror]   agents={agents} days={days} llm={mode} out={out}")

    engine_rc = _run_engine(argv)
    out_dir = Path(out) if Path(out).is_absolute() else root / out
    if engine_rc != 0:
        print(
            f"[flowmirror] demo {args.name} failed (engine exit {engine_rc})",
            file=sys.stderr,
        )
        if engine_rc == 3:
            print(
                "[flowmirror] exit 3: engine failure or replay-check mismatch; "
                "see the engine messages above",
                file=sys.stderr,
            )
        return engine_rc

    rows, decs = _summarise_log(out_dir)
    print()
    print(f"[demo] {args.name}: finished OK")
    print(f"[demo]   output dir : {out_dir}")
    print(
        f"[demo]   event log  : {out_dir / 'event_log.jsonl'} "
        f"({rows} rows; schema config/schemas/event.schema.json)"
    )
    if decs:
        print(f"[demo]   decisions  : {decs} rows of type 'dec' (one per agent per trading day)")
    print(f"[demo] next: {_DEMO_NEXT[args.name]}")
    other = sorted(set(_DEMO_SPECS) - {args.name})[0]
    print(f"[demo]       docs/RUNBOOK.md explains every output; also try: flowmirror demo {other}")
    return 0


# ---------------------------------------------------------------------------
# tree: generated from the installed package at run time
# ---------------------------------------------------------------------------


def _doc_first_line(path: Path) -> str:
    """First non-empty line of a module docstring; never imports the module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        doc = ast.get_docstring(tree) or ""
    except (OSError, SyntaxError, ValueError):
        return ""
    for raw in doc.strip().splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def _scan_package(pkg_dir: Path) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Return [(dir label, package doc line, [(module stem, doc line), ...])].

    Deterministic: directories and files are visited in sorted name order.
    """
    groups: list[tuple[str, str, list[tuple[str, str]]]] = []

    def visit(dir_path: Path, label: str) -> None:
        modules: list[Path] = []
        subdirs: list[Path] = []
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name):
            if entry.name == "__pycache__":
                continue
            if entry.is_dir() and (entry / "__init__.py").is_file():
                subdirs.append(entry)
            elif entry.is_file() and entry.suffix == ".py" and entry.name != "__init__.py":
                modules.append(entry)
        pkg_doc = _doc_first_line(dir_path / "__init__.py")
        groups.append((label, pkg_doc, [(m.stem, _doc_first_line(m)) for m in modules]))
        for sub in subdirs:
            visit(sub, label + sub.name + "/")

    visit(pkg_dir, "flowmirror/")
    return groups


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _render_tree(groups: list[tuple[str, str, list[tuple[str, str]]]], pkg_dir: Path) -> str:
    title = [
        f"flowmirror {__version__} -- installed package map (generated at run time)",
        f"source: {pkg_dir}",
    ]
    detailed: list[str] = []
    for label, pkg_doc, mods in groups:
        detailed.append(f"{label:<22} {_clip(pkg_doc, 62)}".rstrip() if pkg_doc else label)
        for stem, purpose in mods:
            detailed.append(f"    {stem:<18} {_clip(purpose, 62)}".rstrip())
    if len(title) + len(detailed) <= _TREE_MAX_LINES:
        return "\n".join(title + detailed)
    # Too many modules for the line budget: degrade to a compact names-only
    # listing so `flowmirror tree` always stays under ~40 lines.
    compact: list[str] = []
    for label, _pkg_doc, mods in groups:
        cur = f"{label:<22}"
        started = False
        for stem, _purpose in mods:
            piece = f" {stem}"
            if started and len(cur) + len(piece) > 88:
                compact.append(cur.rstrip())
                cur = " " * 22
                started = False
            cur += piece
            started = True
        compact.append(cur.rstrip())
    return "\n".join(title + compact)


def _cmd_export_bundle(args: argparse.Namespace) -> int:
    """Turn a finished run directory into the four-file presentation bundle.

    Deferred import: the exporter walks a whole event log, and `flowmirror --help`
    should not pay for that.
    """
    from flowmirror.analysis.export_bundle import export_bundle

    run_dir = Path(args.run_dir)
    if not (run_dir / "event_log.jsonl").is_file():
        print(f"[flowmirror] {run_dir} holds no event_log.jsonl -- point this at a "
              f"finished run output directory", file=sys.stderr)
        return 1
    try:
        info = export_bundle(str(run_dir), out_dir=args.out,
                             anonymise_orgs=bool(args.anonymise_orgs),
                             max_bytes=int(args.max_bytes))
    except (OSError, ValueError) as exc:
        print(f"[flowmirror] export failed: {exc}", file=sys.stderr)
        return 1
    trunc = (info or {}).get("truncation") or {}
    if trunc.get("applied"):
        print(f"[flowmirror] bundle truncated: {trunc.get('rule')}")
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    pkg_dir = Path(__file__).resolve().parent
    try:
        groups = _scan_package(pkg_dir)
    except OSError as exc:
        print(f"[flowmirror] cannot scan package directory: {exc}", file=sys.stderr)
        return 1
    print(_render_tree(groups, pkg_dir))
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowmirror",
        description=(
            "FlowMirror v7 control CLI: validate configs, run simulations, "
            "inspect schemas and the package layout."
        ),
    )
    parser.add_argument("--version", action="version", version=f"flowmirror {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="validate a run config, then execute the simulation")
    p_run.add_argument("path", help="run config (JSON) path")
    p_run.add_argument("--mock", action="store_true", help="force mock_llm (no provider calls)")
    p_run.add_argument("--days", type=int, help="override window.max_trading_days")
    p_run.add_argument("--agents", type=int, help="override n_agents")
    p_run.add_argument("--seed", type=int, help="override the run seed")
    p_run.add_argument(
        "--out",
        help="override out_dir (relative paths resolve against the repo root)",
    )
    p_run.add_argument(
        "--replay-check",
        action="store_true",
        help="run twice; fail (exit 3) unless the event log is byte-identical",
    )
    p_run.add_argument(
        "--retry-transport-holes",
        action="store_true",
        help="forwarded to the engine verbatim: retry cached transport failures (see engine --help)",
    )
    p_run.add_argument(
        "--workers",
        type=int,
        default=None,
        help="forwarded to the engine verbatim: operational override for llm.workers (wall-clock only)",
    )
    p_run.add_argument(
        "--dump-prompt",
        dest="dump_prompt",
        metavar="AGENT@DAY|first",
        help=(
            "export the exact prompt for one agent-day to <out>/prompts/ "
            "(side artifact; does not touch the event log or its hashes; "
            "runtime-only flag, never a run-config key)"
        ),
    )
    p_run.set_defaults(func=_cmd_run)

    p_demo = sub.add_parser("demo", help="run a shipped offline demo (synthetic NAVs)")
    p_demo.add_argument(
        "name",
        choices=sorted(_DEMO_SPECS),
        help="two-arm | three-arm | null (see docs/RUNBOOK.md)",
    )
    p_demo.add_argument("--days", type=int, help="override default demo days")
    p_demo.add_argument("--agents", type=int, help="override default demo agent count")
    p_demo.add_argument("--out", help="override default out dir (runs/out/demo_<name>)")
    p_demo.add_argument(
        "--replay-check", action="store_true", help="prove determinism (runs the demo twice)"
    )
    p_demo.set_defaults(func=_cmd_demo)

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

    p_export = sub.add_parser(
        "export-bundle",
        help="turn a finished run directory into the web viewer's four-file bundle")
    p_export.add_argument("run_dir", help="finished run output directory (holds event_log.jsonl)")
    p_export.add_argument("--out", default=None,
                          help="bundle output directory (default: <run_dir>/bundle)")
    p_export.add_argument(
        "--anonymise-orgs", action="store_true",
        help="replace institution names with stable pseudonyms, for double-blind review")
    p_export.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024,
                          help="total bundle budget; over it, imp then st event rows are "
                               "dropped and the truncation is recorded in bundle.json")
    p_export.set_defaults(func=_cmd_export_bundle)

    p_tree = sub.add_parser("tree", help="print the real package map (generated at run time)")
    p_tree.set_defaults(func=_cmd_tree)
    return parser


def main(argv=None) -> int:
    # The Windows console may default to GBK; force UTF-8 on our own streams
    # so CJK data (config values, docstrings) never crashes a print.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

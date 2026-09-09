"""Dedicated safe driver for the social-probe paid run (OPS2B).

Commands:
    python script/run_social_probe.py --dry-run
    python script/run_social_probe.py
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE_CONFIG_REL = "runs/main_ref_s2027.json"
DERIVED_CONFIG_REL = "runs/probe_social_100x5.json"
OUT_DIR_REL = "runs/out/probe_social_100x5"
LOG_REL = "runs/out/probe_social_100x5_driver.log"
WORKERS = 6

BASE_CONFIG_PATH = ROOT / BASE_CONFIG_REL
DERIVED_CONFIG_PATH = ROOT / DERIVED_CONFIG_REL
OUT_DIR_PATH = ROOT / OUT_DIR_REL
LOG_PATH = ROOT / LOG_REL

WHAT = "Real-model probe of the influencer layer after cards E7/E8A/E8B."
RUN_TAG = "probe_social_100x5"

_PY_PROC_NAMES = ("python.exe", "pythonw.exe", "python3.exe", "python3.11.exe")

_PS_QUERY = (
    "Get-CimInstance Win32_Process | "
    "Where-Object { $_.Name -in @('" + "', '".join(_PY_PROC_NAMES) + "') } | "
    "ForEach-Object { \"$($_.Name)`t$($_.CommandLine)\" }"
)


class SafetyError(RuntimeError):
    """Raised when a fail-closed precondition is not met."""


# ---------------------------------------------------------------------------
# config building
# ---------------------------------------------------------------------------

def build_derived_config(base: dict) -> dict:
    """Deep-copy base and change exactly the seven specified locations."""
    derived = copy.deepcopy(base)

    derived["_what"] = WHAT
    derived["run_tag"] = RUN_TAG
    derived["out_dir"] = OUT_DIR_REL
    derived["n_agents"] = 100
    derived.setdefault("window", {})
    derived["window"]["max_trading_days"] = 5
    derived["social_graph"] = {"enabled": True}
    derived.setdefault("llm", {})
    derived["llm"]["cache"] = OUT_DIR_REL + "/llm_cache.jsonl"

    return derived


def ensure_derived_config(base_path: Path, derived_path: Path, desired: dict) -> str:
    """Write desired config if absent; reuse if equal; reject otherwise.

    Returns "written", "reused", or raises SafetyError. Never overwrites.
    """
    if derived_path.exists():
        try:
            raw = derived_path.read_text(encoding="utf-8")
            existing = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise SafetyError(
                f"Derived config {derived_path} is unreadable/malformed ({exc}); "
                "refusing to overwrite (stale config + partial cache risk)."
            ) from exc
        if not isinstance(existing, dict) or existing != desired:
            raise SafetyError(
                f"Derived config {derived_path} differs from desired config; "
                "refusing to overwrite (stale config + partial cache risk)."
            )
        return "reused"

    derived_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(desired, ensure_ascii=False, indent=2) + "\n"
    derived_path.write_text(text, encoding="utf-8")
    return "written"


# ---------------------------------------------------------------------------
# process inspection (fail closed)
# ---------------------------------------------------------------------------

def inspect_python_collisions(runner=subprocess.run, platform_name=None) -> int:
    """Count Python processes running the engine loop or grid driver.

    Returns the count of colliding processes. Raises SafetyError if the
    inspection itself fails or the platform is unsupported.
    """
    import platform as _platform

    name = platform_name if platform_name is not None else _platform.system()
    if name != "Windows":
        raise SafetyError(
            "Unsupported platform for process inspection "
            f"({name!r}); failing closed: this driver requires Windows."
        )

    try:
        proc = runner(
            ["powershell", "-NoProfile", "-Command", _PS_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise SafetyError(f"Process inspection failed to launch: {exc}") from exc

    if proc.returncode != 0:
        raise SafetyError(
            "Process inspection command failed "
            f"(rc={proc.returncode}); failing closed."
        )

    stdout = proc.stdout or ""
    count = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "\t" not in line:
            raise SafetyError(f"malformed process-inspection line (no tab): {line!r}")
        cmdline = line.split("\t", 1)[1].lower().replace("\\", "/")
        if "flowmirror.engine.loop" in cmdline or "script/run_grid.py" in cmdline:
            count += 1
    return count


def require_no_collisions(runner=subprocess.run, platform_name=None) -> None:
    n = inspect_python_collisions(runner=runner, platform_name=platform_name)
    if n:
        raise SafetyError(
            f"Detected {n} running Python process(es) matching "
            "flowmirror.engine.loop or script/run_grid.py; failing closed."
        )


# ---------------------------------------------------------------------------
# event summary (free, local)
# ---------------------------------------------------------------------------

def summarize_events(lines) -> dict:
    """Summarize flat event rows from an iterable of JSONL text lines."""
    edges = 0
    followers = set()
    decisions = 0
    nonempty_intent = 0
    unknown_handle_occurrences = 0
    malformed = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue

        ev = row.get("ev")
        what = row.get("what")

        if ev == "st" and what == "follow_user":
            edges += 1
            i = row.get("i")
            if i is not None:
                followers.add(i)

        if ev == "dec":
            decisions += 1
            if row.get("p_follow_users"):
                nonempty_intent += 1
            violations = row.get("violations")
            if isinstance(violations, list):
                for v in violations:
                    if v == "unknown_handle":
                        unknown_handle_occurrences += 1

    return {
        "follow_edges": edges,
        "unique_followers": len(followers),
        "decisions": decisions,
        "nonempty_intent": nonempty_intent,
        "unknown_handle_occurrences": unknown_handle_occurrences,
        "malformed_rows": malformed,
    }


def summarize_event_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return summarize_events(fh)


# ---------------------------------------------------------------------------
# engine orchestration
# ---------------------------------------------------------------------------

def is_complete(out_dir: Path) -> bool:
    meta = out_dir / "run_meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("status") == "ok"


def _log_append(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def run_command(cmd, cwd: Path, log_path: Path, runner=subprocess.run,
                stream: bool = False, env=None):
    """Run a command; append output to the log. Stream or capture."""
    header = f"\n$ {' '.join(str(c) for c in cmd)}\n"
    _log_append(log_path, header)
    if stream:
        with log_path.open("a", encoding="utf-8") as fh:
            proc = runner(
                [str(c) for c in cmd], cwd=str(cwd),
                stdout=fh, stderr=fh,
            )
        return proc.returncode, ""
    proc = runner(
        [str(c) for c in cmd], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    _log_append(log_path, out)
    if not out.endswith("\n"):
        _log_append(log_path, "\n")
    return proc.returncode, out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Social-probe driver (OPS2B)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    base_cmd = [sys.executable, "-m", "flowmirror.cli", "validate",
                DERIVED_CONFIG_PATH]
    engine_cmd = [sys.executable, "-m", "flowmirror.engine.loop",
                  DERIVED_CONFIG_PATH, "--workers", str(WORKERS),
                  "--out", OUT_DIR_PATH]
    replay_cmd = list(engine_cmd) + ["--replay-check"]
    analysis_cmd = [sys.executable, "-m", "flowmirror.analysis.influence",
                    OUT_DIR_PATH]

    if args.dry_run:
        # May read the base, but write nothing, launch nothing.
        try:
            base = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
            _ = build_derived_config(base)
        except (OSError, ValueError) as exc:
            print(f"dry-run failed: {exc}", file=sys.stderr)
            return 2
        for cmd in (base_cmd, engine_cmd, replay_cmd, analysis_cmd):
            print(subprocess.list2cmdline([str(c) for c in cmd]))
        return 0

    # --- real mode ---------------------------------------------------------
    try:
        require_no_collisions()

        base = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
        desired = build_derived_config(base)
        action = ensure_derived_config(BASE_CONFIG_PATH, DERIVED_CONFIG_PATH, desired)
    except (SafetyError, OSError, ValueError) as exc:
        print(f"refused before launch: {exc}", file=sys.stderr)
        return 2

    _log_append(LOG_PATH, f"[driver] derived config {action}\n")

    rc, _ = run_command(base_cmd, ROOT, LOG_PATH)
    if rc != 0:
        print("validate failed; see log", file=sys.stderr)
        return 1

    if is_complete(OUT_DIR_PATH):
        _log_append(LOG_PATH, "[driver] run already complete; skipping engine\n")
    else:
        try:
            require_no_collisions()  # re-check immediately before paid launch
        except SafetyError as exc:
            _log_append(LOG_PATH, f"[driver] refused before paid launch: {exc}\n")
            print(f"refused before paid launch: {exc}", file=sys.stderr)
            return 2
        _log_append(LOG_PATH, "[driver] launching paid engine (single attempt)\n")
        rc, _ = run_command(engine_cmd, ROOT, LOG_PATH, stream=True)
        if rc != 0 or not is_complete(OUT_DIR_PATH):
            _log_append(LOG_PATH, f"[driver] engine failed rc={rc}\n")
            print("engine failed; see log", file=sys.stderr)
            return 1

    rc, out = run_command(replay_cmd, ROOT, LOG_PATH)
    if rc != 0 or "replay-check identical=True" not in out:
        _log_append(LOG_PATH, "[driver] replay check failed\n")
        print("replay check failed; see log", file=sys.stderr)
        return 1

    rc, _ = run_command(analysis_cmd, ROOT, LOG_PATH)
    if rc != 0:
        _log_append(LOG_PATH, "[driver] analysis failed\n")
        print("analysis failed; see log", file=sys.stderr)
        return 1

    _log_append(LOG_PATH, "[driver] summarizing events\n")
    totals = summarize_event_file(OUT_DIR_PATH / "event_log.jsonl")

    lines = [
        f"follow_edges={totals['follow_edges']}",
        f"unique_followers={totals['unique_followers']}",
        f"decisions={totals['decisions']}",
        f"nonempty_intent={totals['nonempty_intent']}",
        f"unknown_handle_occurrences={totals['unknown_handle_occurrences']}",
        f"malformed_rows={totals['malformed_rows']}",
    ]
    summary = "\n".join(lines) + "\n"
    print(summary, end="")
    _log_append(LOG_PATH, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

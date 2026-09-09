"""Every public run config must reference only repo-relative, git-tracked inputs."""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"

EXPECTED_PUBLIC_RUNS = {"demo_null.json", "demo_three_arm.json", "demo_two_arm.json"}

TOP_LEVEL_INPUT_KEYS = (
    "agents_file",
    "content_pool",
    "nav_cache",
    "guba_signal",
    "fund_meta_file",
    "family_file",
    "images_root",
)


def _git_available() -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _is_tracked(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode == 0 and proc.stdout.strip() == rel


def _public_run_configs():
    if not RUNS_DIR.is_dir():
        return []
    return sorted(p for p in RUNS_DIR.iterdir() if p.suffix == ".json")


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git unavailable or not inside a worktree"
)


def test_only_expected_public_run_configs():
    actual = {p.name for p in _public_run_configs()}
    assert actual == EXPECTED_PUBLIC_RUNS, (
        f"public runs/*.json set mismatch; expected {sorted(EXPECTED_PUBLIC_RUNS)}, "
        f"got {sorted(actual)}"
    )


@pytest.mark.parametrize("config_path", _public_run_configs(), ids=lambda p: p.name)
def test_config_inputs_are_relative_tracked_files(config_path):
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    offenders = []

    candidates = []
    for key in TOP_LEVEL_INPUT_KEYS:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append((key, value))

    market = config.get("market")
    if isinstance(market, dict):
        bench = market.get("benchmark_path")
        if isinstance(bench, str) and bench.strip():
            candidates.append(("market.benchmark_path", bench))

    for key, value in candidates:
        if Path(value).is_absolute() or value.startswith(("/", "\\")):
            offenders.append(f"{config_path.name}:{key} is not a relative path: {value}")
            continue
        resolved = (REPO_ROOT / value).resolve()
        try:
            resolved.relative_to(REPO_ROOT)
        except ValueError:
            offenders.append(f"{config_path.name}:{key} resolves outside repo: {value}")
            continue
        if not resolved.exists():
            offenders.append(f"{config_path.name}:{key} does not exist: {value}")
            continue
        if not _is_tracked(resolved):
            offenders.append(f"{config_path.name}:{key} is not git-tracked: {value}")

    assert not offenders, "config input violations:\n" + "\n".join(offenders)

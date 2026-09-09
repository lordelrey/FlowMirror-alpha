"""Public-release boundary check for tracked files and leaked internal paths."""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_SCRIPTS = {"script/run_grid.py", "script/run_social_probe.py"}
FORBIDDEN_RUN_PREFIXES = ("main_", "heat_seed_", "emerge_", "live_", "smoke_", "mock_")
FORBIDDEN_DOCS_TOKENS = (
    "hand" + "over",
    "audit",
    "draft",
    "pre" + "reg",
    "plan",
    "status",
    "findings",
    "contract",
    "decision",
)

LOCAL_ABSOLUTE_PATH = re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|Desktop|Documents)[\\/]")
PUBLICATION_DIR = "pa" + "per/"
LOCAL_NOTE_TOKEN = "hand" + "over"


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


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git unavailable or not inside a worktree"
)


def _tracked_files():
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, "git ls-files failed"
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_no_publication_draft_directories():
    offenders = [p for p in _tracked_files()
                 if p.startswith(PUBLICATION_DIR) or p.startswith("docs/research/")]
    assert not offenders, "publication-draft paths must not be tracked:\n" + "\n".join(offenders)


def test_no_internal_doc_names_under_docs():
    offenders = []
    for p in _tracked_files():
        if p.startswith("docs/"):
            stem = Path(p).name.lower()
            if any(token in stem for token in FORBIDDEN_DOCS_TOKENS):
                offenders.append(p)
    assert not offenders, "tracked docs/ files with internal-usage basenames:\n" + "\n".join(offenders)


def test_no_internal_scripts():
    offenders = [p for p in _tracked_files() if p in FORBIDDEN_SCRIPTS]
    assert not offenders, "tracked internal scripts must not exist:\n" + "\n".join(offenders)


def test_no_internal_run_configs():
    offenders = [
        p
        for p in _tracked_files()
        if p.startswith("runs/") and p[len("runs/"):].startswith(FORBIDDEN_RUN_PREFIXES)
    ]
    assert not offenders, "tracked internal run configs must not exist:\n" + "\n".join(offenders)


def test_no_local_absolute_paths_or_local_notes():
    offenders = []
    for p in _tracked_files():
        try:
            text = (REPO_ROOT / p).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if LOCAL_ABSOLUTE_PATH.search(text) or LOCAL_NOTE_TOKEN in text.lower():
            offenders.append(p)
    assert not offenders, "tracked files containing internal leak markers:\n" + "\n".join(offenders)

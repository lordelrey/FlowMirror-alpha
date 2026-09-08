"""Card CI1 guard: every run config the test suite runs on must ship its inputs.

The six CI-only failures happened because two test files built their fixtures
from runs/mock_10x3.json, whose nav_cache is data/funds/nav_cache.json -- the
real 4.4 MB third-party NAV cache, git-ignored on purpose and present only on
the author's machine.  A test that passes only because of a file living
outside the repository is exactly what CI exists to catch, so this module
turns the property itself into a test:

  * every runs/*.json that is NOT on the explicit non-shipping allow-list
    below must resolve each of its input path keys to a path that exists in
    the working tree AND is tracked by git (git ls-files --error-unmatch);
  * every allow-listed config must really have at least one missing or
    untracked input, so the allow-list stays exact: a NEW config whose inputs
    do not ship fails the first half until it is made self-contained or
    consciously added here.

Skipped with a clear reason when git is unavailable or this checkout is not a
git working tree (existence is checkable there, tracked-ness is not)."""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
RUNS_DIR = os.path.join(ROOT, "runs")

# Input path keys of a run config.  family_file and images_root are optional
# and are only checked when the config actually sets them to a path.
# Nested input paths the guard must also follow. market.benchmark_path names a file
# under gitignored data/market/, so a config that sets it has to be allow-listed like
# any other non-shipping config -- without this the guard's stated guarantee ("every
# input path key resolves to a tracked file") would quietly stop covering everything a
# run config can name.
NESTED_INPUT_KEYS = (("market", "benchmark_path"),)

INPUT_KEYS = ("agents_file", "content_pool", "nav_cache", "guba_signal",
              "fund_meta_file", "family_file", "images_root")

# Non-shipping allow-list: research-grade configs that DELIBERATELY depend on
# data that never ships with the repository (the git-ignored third-party NAV
# cache data/funds/nav_cache.json and raw research panels).  The second half
# of the test asserts each of them genuinely has a missing/untracked input, so
# an entry that stops being true is itself a failure.
NONSHIPPING_PATTERNS = (
    "mock_10x3*.json",      # research fixture: real nav_cache.json, never ships
    "smoke_20x10.json",     # research-scale smoke run, same unshipped inputs
    "demo_live_20x5.json",  # live-data demo, same unshipped inputs
    # Local-images demo: images_root points at the 768px creative store, which is
    # deliberately never redistributed (see the data policy in README.md).  The
    # engine runs text-only without it, so this config is opt-in on a machine
    # that holds the images.
    "demo_three_arm_images.json",
    "live_probe_*.json",  # real-model run config; uses local images_root and live net worth; never shipped
    "live_pilot_*.json",  # real-model run config; uses local images_root and live net worth; never shipped
    "main_*.json",  # real-model run config; uses local images_root and live net worth; never shipped
    "emerge_*.json",  # real-model run config; uses local images_root and live net worth; never shipped
    "heat_seed_*.json",  # real-model run config; uses local images_root and live net worth; never shipped
)


def _nonshipping(name):
    return any(fnmatch.fnmatchcase(name, pat) for pat in NONSHIPPING_PATTERNS)


def _load_cfg(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _git_tracked(path):
    """True only for a path inside this repo that git actually tracks.

    A path outside the repository (an absolute images_root on another drive, say)
    is by definition not tracked, and on Windows os.path.relpath raises across
    drive letters -- so answer False instead of blowing up the guard."""
    try:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    except ValueError:                      # different mount / drive letter
        return False
    if rel.startswith("../"):               # outside the working tree
        return False
    proc = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                          cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _input_problems(cfg):
    """[(key, raw_value, why)] for every input that does not ship with the repo."""
    problems = []
    pairs = [(k, cfg.get(k)) for k in INPUT_KEYS]
    # follow nested input paths too, so the guard's promise covers every input a run
    # config can name rather than only the top-level ones
    for outer, inner in NESTED_INPUT_KEYS:
        block = cfg.get(outer)
        if isinstance(block, dict):
            pairs.append((f"{outer}.{inner}", block.get(inner)))
    for key, val in pairs:
        if not val:
            continue
        if not isinstance(val, str):
            problems.append((key, val, "value is not a path string"))
            continue
        path = val if os.path.isabs(val) else os.path.join(ROOT, val)
        if not os.path.exists(path):
            problems.append((key, val, "missing from the working tree"))
        elif not _git_tracked(path):
            problems.append((key, val, "exists but is NOT tracked by git"))
    return problems


def test_runs_configs_resolve_to_tracked_inputs():
    if not os.path.isdir(RUNS_DIR):
        pytest.skip("no runs/ directory in this checkout")
    if shutil.which("git") is None:
        pytest.skip("git is unavailable; input tracked-ness cannot be verified")
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=ROOT, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("not a git working tree, so tracked-ness is undefined "
                    f"(git said: {probe.stderr.strip() or probe.returncode!r})")

    names = sorted(n for n in os.listdir(RUNS_DIR) if n.endswith(".json"))
    shipped = [n for n in names if not _nonshipping(n)]
    excluded = [n for n in names if _nonshipping(n)]
    assert shipped, "every runs/*.json is allow-listed; the guard is vacuous"
    # tests/conftest.py builds all mock fixtures from runs/demo_two_arm.json,
    # so that config must exist and must NOT be on the non-shipping allow-list.
    assert "demo_two_arm.json" in shipped, (
        "the suite's fixture base runs/demo_two_arm.json is missing or is "
        "allow-listed as non-shipping -- the tests would fail on a clean clone")

    offenders = []
    for name in shipped:            # names already sorted: deterministic order
        for key, val, why in _input_problems(_load_cfg(os.path.join(RUNS_DIR, name))):
            offenders.append(f"runs/{name}: {key} -> {val} ({why})")
    assert not offenders, (
        "run configs with inputs that do not ship with the repository -- on a "
        "clean clone these produce exactly the CI failure this test guards "
        "against; make the config self-contained or add it DELIBERATELY to "
        "NONSHIPPING_PATTERNS:\n  " + "\n  ".join(offenders))

    for name in excluded:           # the allow-list must stay exact, not rot
        assert _input_problems(_load_cfg(os.path.join(RUNS_DIR, name))), (
            f"runs/{name} is allow-listed as non-shipping but ALL of its "
            "inputs exist and are git-tracked; delete it from "
            "NONSHIPPING_PATTERNS so the guard covers it")

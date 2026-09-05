"""Schema tests: run fixture, CN scenario, event log, persona cell."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowmirror.config.loader import load_config
from flowmirror.config.validate import ConfigError, validate

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "run_mock_10x3.json"
CN_SCENARIO = ROOT / "scenarios" / "cn_xhs_2025q4" / "scenario.yaml"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_run_fixture_validates():
    validate(_fixture(), "run")


def test_run_seed_string_fails():
    cfg = _fixture()
    cfg["seed"] = "2027"
    with pytest.raises(ConfigError):
        validate(cfg, "run")


def test_run_unknown_top_level_key_fails():
    cfg = _fixture()
    cfg["not_a_real_key"] = 1
    with pytest.raises(ConfigError):
        validate(cfg, "run")


def test_run_underscore_extra_key_passes():
    cfg = _fixture()
    cfg["_generated_from"] = "tools_v7/gen_run_config.py"
    cfg["_what"] = "smoke"
    validate(cfg, "run")


def test_cn_scenario_validates():
    validate(load_config(CN_SCENARIO), "scenario")


def test_imp_event_validates():
    ev = {
        "ev": "imp", "t": 540, "d": "2025-10-09", "i": "inv_00001", "p": "post_0001",
        "arm": "T", "slot": 3, "source": "fit",
    }
    validate(ev, "event")


def test_imp_event_bad_arm_fails():
    ev = {
        "ev": "imp", "t": 540, "d": "2025-10-09", "i": "inv_00001", "p": "post_0001",
        "arm": "X", "slot": 3, "source": "fit",
    }
    with pytest.raises(ConfigError):
        validate(ev, "event")


def test_persona_cell_validates():
    cell = {
        "cell_id": "c2_35|female|new",
        "age": 33,
        "asset": 120000.0,
        "risk_latent": "typical",
        "weight": 0.12,
        "reported_C": "C2",
        "max_R_default": "R3",
        "cpt": {"lambda": 2.25, "alpha": 0.88, "w_plus": 0.61, "w_minus": 0.69},
        "kernel": {"chaser": 0.4, "allocator": 0.35, "social": 0.25},
        "traits": {"fin_literacy": 0.4, "platform_hours": 12.0},
    }
    validate(cell, "persona")

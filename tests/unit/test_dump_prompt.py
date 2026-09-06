"""Card R2D tests: --dump-prompt must be reachable AND stay a pure side artifact.

Two parallel cards collided: one added --dump-prompt and parked its value in
the run-config dict as `dump_prompt`; the other shipped run.schema.json with
additionalProperties:false, so the schema re-validation inside
run_simulation() killed every dump run before day 0
("'dump_prompt' does not match any of the regexes: '^_'" at path /).  The fix
is architectural: the spec travels in flowmirror.engine.loop.RuntimeOpts, a
CLI-only runtime-options object threaded main() -> run_simulation(cfg, rt),
and never enters the validated config.  These tests pin both halves of the
contract: the feature works from every entry point (engine main, control CLI
`run`), and the event log stays byte-identical with vs without the flag."""
from __future__ import annotations

import json
import os

import pytest

from flowmirror.config.validate import ConfigError, validate
from flowmirror.engine.loop import (ROOT, RuntimeOpts, _load_cfg, event_log_sha,
                                    run_simulation)
from flowmirror.io.jsonl import iter_jsonl


def _mock_cfg_path():
    """Same search order as the engine self-test: env overrides, then the repo."""
    for base in (os.environ.get("FLOWMIRROR_DATA_ROOT"),
                 os.environ.get("FLOWMIRROR_RESEARCH_ROOT"),
                 ROOT):
        if base and os.path.isfile(os.path.join(base, "runs", "mock_10x3.json")):
            return os.path.join(base, "runs", "mock_10x3.json")
    return None


needs_mock = pytest.mark.skipif(
    _mock_cfg_path() is None,
    reason="runs/mock_10x3.json not found (set FLOWMIRROR_DATA_ROOT)",
)


def _tiny_cfg(out_dir):
    """Tiny mock run: 2 trading days, 8 agents, every output under out_dir."""
    cfg = _load_cfg(_mock_cfg_path())
    cfg.update({"mock_llm": True, "n_agents": 8, "out_dir": str(out_dir)})
    cfg["window"]["max_trading_days"] = 2
    cfg.setdefault("llm", {})["cache"] = os.path.join(str(out_dir), "llm_cache.jsonl")
    return cfg


def _log_path(out_dir):
    return os.path.join(str(out_dir), "event_log.jsonl")


@needs_mock
def test_dump_prompt_writes_both_files_and_leaves_the_log_byte_identical(tmp_path):
    out_plain, out_dump = tmp_path / "plain", tmp_path / "dump"
    assert run_simulation(_tiny_cfg(out_plain)) == 0
    sha_plain = event_log_sha(_log_path(out_plain))
    assert sha_plain is not None

    cfg = _tiny_cfg(out_dump)
    assert run_simulation(cfg, RuntimeOpts(dump_prompt="first")) == 0
    # the runtime switch never entered the dict that gets schema-validated
    assert "dump_prompt" not in cfg

    pdir = out_dump / "prompts"
    assert pdir.is_dir()
    txts = sorted(f for f in os.listdir(str(pdir)) if f.endswith(".txt"))
    assert len(txts) == 1, "expected exactly one dumped prompt for spec 'first'"
    txt, side = pdir / txts[0], pdir / (txts[0][:-4] + ".json")
    assert txt.is_file() and side.is_file()

    blob = txt.read_text(encoding="utf-8")
    assert "base64," not in blob                  # image parts are sha/size placeholders
    assert blob.count("=====") >= 2               # full system + user blocks rendered

    car = json.loads(side.read_text(encoding="utf-8"))
    assert car.get("prompt_sha")
    assert car.get("arm") in ("T", "TC", "TV")
    assert isinstance(car.get("card_ids"), list) and car["card_ids"]
    assert "channel_shas" in car and "image_shas" in car
    decs = [r for r in iter_jsonl(_log_path(out_dump))
            if r.get("ev") == "dec" and r.get("i") == car.get("agent")
            and r.get("t") == car.get("day")]
    assert decs and decs[0].get("prompt_sha") == car["prompt_sha"]

    # the regression this card fixes: dumping must not change one byte of the log
    assert event_log_sha(_log_path(out_dump)) == sha_plain


@needs_mock
def test_engine_entry_point_accepts_the_dump_prompt_flag(tmp_path):
    from flowmirror.engine.loop import main as engine_main

    out = tmp_path / "eng"
    rc = engine_main([_mock_cfg_path(), "--mock", "--days", "2", "--agents", "8",
                      "--out", str(out), "--dump-prompt", "first"])
    assert rc == 0
    pdir = out / "prompts"
    assert pdir.is_dir()
    names = os.listdir(str(pdir))
    assert any(n.endswith(".txt") for n in names)
    assert any(n.endswith(".json") for n in names)


@needs_mock
def test_control_cli_run_accepts_the_dump_prompt_flag(tmp_path):
    from flowmirror.cli import main as cli_main

    out = tmp_path / "cli"
    rc = cli_main(["run", _mock_cfg_path(), "--mock", "--days", "2", "--agents", "8",
                   "--out", str(out), "--dump-prompt", "first"])
    assert rc == 0
    pdir = out / "prompts"
    assert pdir.is_dir()
    assert any(n.endswith(".txt") for n in os.listdir(str(pdir)))


@needs_mock
def test_run_schema_still_rejects_a_literal_dump_prompt_key():
    from flowmirror.config.loader import load_config

    obj = load_config(_mock_cfg_path())
    validate(obj, "run")              # precondition: the config we hand the engine is clean
    obj["dump_prompt"] = "first"      # the old laundering path must keep failing loudly
    with pytest.raises(ConfigError):
        validate(obj, "run")

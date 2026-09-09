"""Keep engine defaults, the run schema, and bundled configurations aligned.

The tests pin deliberate default values and verify that inert configuration
keys retain the engine's previous behavior when omitted.

The last test re-validates every runs/*.json against the edited schema through
flowmirror.config.validate, the same entry point flowmirror/engine/loop.py's
_load_cfg and `flowmirror validate` use.  run.schema.json sets
additionalProperties: false, so a new key that reaches the defaults without
reaching the schema makes every config that sets it unloadable; and this test
also proves the reverse direction, that widening the schema did not
accidentally invalidate a config that used to pass.
"""
from __future__ import annotations

import os

import pytest

from flowmirror.config.loader import load_config
from flowmirror.config.validate import ConfigError, validate
from flowmirror.engine.world import DEFAULT_CONFIG

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
RUNS_DIR = os.path.join(ROOT, "runs")


# --- fixed public defaults ---------------------------------------------------------------

def test_fees_default_to_the_decided_schedule():
    """The public default is 0.12% on subscriptions and 0.5% on redemptions."""
    fees = DEFAULT_CONFIG["fees"]
    assert fees["subscribe_rate"] == 0.0012
    assert fees["redeem_rate"] == 0.005


def test_fam_decay_defaults_to_the_decided_value():
    # Keep the YAML and engine fallback aligned.
    assert DEFAULT_CONFIG["dynamics"]["fam_decay"] == 0.2


def test_attention_has_its_own_retention():
    # att_t = lambda_attention * att_{t-1} + exposure + beta_guba * z_guba.
    assert DEFAULT_CONFIG["dynamics"]["lambda_attention"] == 0.8


def test_beta_guba_is_exactly_zero():
    """The attention channel is wired but inert by default."""
    assert DEFAULT_CONFIG["dynamics"]["beta_guba"] == 0.0


def test_climate_margin_defaults_to_one_sixth():
    # Compared with a tolerance because YAML carries a decimal expansion.
    assert abs(DEFAULT_CONFIG["feed"]["climate_margin"] - 1.0 / 6.0) < 1e-12


# --- the inert additions: each default must equal the literal it replaces ---------------------

def test_initial_pnl_defaults_to_lookback():
    """The demos use lookback sampling unless target mode is explicit."""
    assert DEFAULT_CONFIG["initial_pnl"]["mode"] == "lookback"


def test_llm_sampling_parameters_equal_the_runtime_constants():
    # Keep engine defaults aligned with the runtime fallbacks.
    assert DEFAULT_CONFIG["llm"]["temperature"] == 0.3
    assert DEFAULT_CONFIG["llm"]["max_provider_attempts"] == 5


def test_benchmark_is_unset_by_default():
    # Benchmark data are opt-in, so the default leaves index_5d absent.
    assert DEFAULT_CONFIG["market"]["benchmark_path"] is None
    assert DEFAULT_CONFIG["market"]["benchmark_label"] is None


def test_qdii_blocked_has_no_default():
    """A QDII suspension calendar is opt-in and has no default."""
    assert "qdii_blocked" not in DEFAULT_CONFIG


@pytest.mark.parametrize(("path", "expected"), [
    # Each of these equals the literal the engine hardcodes today, so a config
    # that omits the key stays byte-identical.  Anchors, for the engine cards
    # that will read them: fam_threshold and lambda_trust in loop.py's lagged
    # update block (the >= 1.0 exposure level and the 0.9 affinity adstock),
    # dca.* in the monthly DCA block (inv.cash * 0.02 and the 100 CNY floor),
    # and the lookback bounds in world.init_investors (randint(60, 250)).
    (("dynamics", "fam_threshold"), 1.0),
    (("dynamics", "lambda_trust"), 0.9),
    (("dca", "pct"), 0.02),
    (("dca", "min_ticket"), 100.0),
    (("initial_pnl", "lookback_days_min"), 60),
    (("initial_pnl", "lookback_days_max"), 250),
    # Target-mode tolerance.
    (("initial_pnl", "tolerance"), 0.02),
    # Attention is inert at the default weight.
    (("feed", "w_att"), 0.0),
    # E13: the fit bands, equal to the literals feed.fit() carried
    (("feed", "fit_band_narrow"), 0.15),
    (("feed", "fit_band_wide"), 0.25),
])
def test_inert_default_equals_todays_literal(path, expected):
    node = DEFAULT_CONFIG
    for key in path:
        assert key in node, f"{'.'.join(path)} missing from DEFAULT_CONFIG"
        node = node[key]
    assert node == expected, (
        f"{'.'.join(path)} = {node!r}, expected {expected!r} -- this key exists to "
        "record the engine's current literal, so changing it silently changes "
        "results in every run that omits the key")


# --- the schema must still accept every config that ships ------------------------------------

def _run_config_names():
    if not os.path.isdir(RUNS_DIR):
        return []
    return sorted(n for n in os.listdir(RUNS_DIR) if n.endswith(".json"))


@pytest.mark.parametrize("name", _run_config_names())
def test_run_config_still_validates(name):
    """Widening run.schema.json must not reject a config that used to load.

    Reuses flowmirror.config.validate -- the single validator loop.py's
    _load_cfg and the CLI already call -- rather than building a second one.
    Only the config file is read, so this passes on a clean clone."""
    validate(load_config(os.path.join(RUNS_DIR, name)), "run")


def test_every_new_key_is_accepted_by_the_schema():
    """The other direction: a config that SETS every new key must validate.

    additionalProperties is false, so a key that reached engine_defaults.yaml
    without reaching the schema would make any config setting it unloadable --
    qdii_blocked is included here through a synthetic fixture."""
    cfg = load_config(os.path.join(RUNS_DIR, "demo_two_arm.json"))
    cfg["dynamics"] = {"fam_decay": 0.2, "fam_threshold": 1.0, "lambda_trust": 0.9,
                       "lambda_attention": 0.8, "beta_guba": 0.0}
    cfg["dca"] = {"pct": 0.02, "min_ticket": 100.0}
    cfg["initial_pnl"] = {"mode": "target", "lookback_days_min": 60,
                          "lookback_days_max": 250,
                          "tolerance": 0.02}
    cfg["market"] = {"benchmark_path": "data/market/benchmark.json",
                     "benchmark_label": "SSE Composite ETF (510760) unit NAV, a proxy"}
    cfg["qdii_blocked"] = {"2025-10-09": {"codes": ["001234", "005678"]}}
    cfg.setdefault("feed", {})["climate_margin"] = 1.0 / 6.0
    cfg.setdefault("llm", {})["temperature"] = 0.3
    cfg["llm"]["max_provider_attempts"] = 5
    validate(cfg, "run")


def test_qdii_blocked_rejects_a_non_date_key():
    """The suspension calendar is keyed by ISO date, so mistyping one must fail.

    The key is normally unset; a key that
    is not a date would otherwise be silently ignored by _qdii_blocked_set."""
    cfg = load_config(os.path.join(RUNS_DIR, "demo_two_arm.json"))
    cfg["qdii_blocked"] = {"2025-10": {"codes": ["001234"]}}
    with pytest.raises(ConfigError):
        validate(cfg, "run")


# --- event.schema.json: decision failure_kind -----------------------------------------------

@pytest.mark.parametrize("kind", ["transport", "model", None])
def test_dec_row_accepts_failure_kind(kind):
    """The field splits diagnosis without changing the halt threshold.

    A dead endpoint or a bad key ("transport") used to be counted as the same
    event as a model returning unparseable JSON ("model"), which made a broken
    credential look like an unstable model in the halt report.  The field is
    optional and null when the decision did not fail."""
    ev = {"ev": "dec", "t": 540, "d": "2025-10-09", "i": "inv_00001",
          "prompt_sha": "0" * 64, "status": "unparsed", "arm": "T",
          "failure_kind": kind}
    validate(ev, "event")


def test_dec_row_rejects_an_unknown_failure_kind():
    # Only the two sides of the call exist; anything else means the writer
    # invented a third category and the transport + model == decision_failures
    # identity would stop holding.
    ev = {"ev": "dec", "t": 540, "d": "2025-10-09", "i": "inv_00001",
          "prompt_sha": "0" * 64, "status": "unparsed", "arm": "T",
          "failure_kind": "timeout"}
    with pytest.raises(ConfigError):
        validate(ev, "event")


def test_dec_row_without_failure_kind_still_validates():
    # Optional for compatibility with older event rows.
    ev = {"ev": "dec", "t": 540, "d": "2025-10-09", "i": "inv_00001",
          "prompt_sha": "0" * 64, "status": "ok", "arm": "TV"}
    validate(ev, "event")


# --------------------------------------------------- mechanism defaults

def test_share_at_loss_has_no_default_on_purpose():
    """Target mode requires callers to state the desired loss share."""
    ipnl = DEFAULT_CONFIG.get("initial_pnl") or {}
    assert "share_at_loss" not in ipnl, (
        "a mode whose purpose is to CONTROL the loss share must not inherit one")
    assert ipnl.get("mode") == "lookback"


def test_target_mode_refuses_to_run_without_its_manipulation(tmp_path):
    """State the share explicitly when using target mode."""
    from flowmirror.engine import world as W
    with pytest.raises(SystemExit):
        W._initial_pnl_cfg({"initial_pnl": {"mode": "target"}})
    got = W._initial_pnl_cfg({"initial_pnl": {"mode": "target", "share_at_loss": 0.5}})
    assert got["share_at_loss"] == pytest.approx(0.5)
    # out of range is refused too
    with pytest.raises(SystemExit):
        W._initial_pnl_cfg({"initial_pnl": {"mode": "target", "share_at_loss": 1.4}})
    # lookback never reads it, so it stays runnable with the key absent
    assert W._initial_pnl_cfg({"initial_pnl": {"mode": "lookback"}})["mode"] == "lookback"


def test_no_unconditional_modality_run_arm_default():
    """Agent-level configurations do not inherit a run-level arm."""
    assert "modality_run_arm" not in DEFAULT_CONFIG


def _modality_cfg(tmp_path, **over):
    """A COMPLETE run config -- validate_config runs the whole run schema -- with only
    the modality keys varied."""
    from tests.conftest import build_demo_cfg
    cfg = build_demo_cfg(tmp_path / "cfg", agents=10, days=3)
    cfg.pop("modality_run_arm", None)
    cfg.update(over)
    return cfg


def test_an_arm_set_without_tv_is_accepted_at_agent_level(tmp_path):
    from flowmirror.engine import world as W
    cfg = _modality_cfg(tmp_path, modality_arms=["T", "TC"], modality_level="agent")
    W.validate_config(cfg)                     # must not raise
    assert "modality_run_arm" not in cfg, "an agent-level run carries no run arm"


def test_run_level_defaults_the_arm_to_the_first_configured_one(tmp_path):
    from flowmirror.engine import world as W
    cfg = _modality_cfg(tmp_path, modality_arms=["TC", "TV"], modality_level="run")
    W.validate_config(cfg)
    assert cfg["modality_run_arm"] == "TC"
    # a run arm outside the configured set is still refused at run level
    with pytest.raises(SystemExit):
        W.validate_config(_modality_cfg(tmp_path, modality_arms=["T", "TC"],
                                        modality_level="run", modality_run_arm="TV"))
    # a misspelling is caught even where the key is inert, so a typo cannot sit in run_meta
    with pytest.raises(SystemExit):
        W.validate_config(_modality_cfg(tmp_path, modality_arms=["T", "TV"],
                                        modality_level="agent", modality_run_arm="TX"))

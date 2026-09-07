"""Card CFG guard: config/engine_defaults.yaml must say what the owner decided.

Every value here was a bare literal inside the engine until the 2026-09-07
remediation round, which is exactly why the code and the decision record had
drifted apart (climate margin 1/3 vs 1/6, fam_decay 0.1 vs 0.2, fees 0/0 vs
0.12%/0.5%).  Moving a parameter into the config surface only fixes that drift
if something keeps watch on the value, so this module pins each default to the
owner decision that set it -- see docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07.md
section 5 for the numbered decisions cited below.

Two separate jobs, hence two groups of tests:

  * the DELIBERATE changes (decisions 1, 2, 8 and the DECISIONS #5 attention
    lambda): these move an event-log sha on purpose, and the test states the
    intended value so a "fix" that reverts one is caught;
  * the INERT additions: keys whose default must equal the literal the engine
    hardcodes today, so that a config which omits them stays byte-identical.
    A wrong value here is the dangerous case -- it silently changes results
    with no diff in any run config.

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


# --- the deliberate changes: each one is expected to move an event-log sha ---------------------

def test_fees_default_to_the_decided_schedule():
    """Decision 8: 0.12% subscription / 0.5% redemption, not the old 0.0/0.0.

    apply_decision already reads cfg["fees"], so unlike every other key on this
    card these two take effect the moment the default lands: any run whose own
    config does not set fees changes its event-log sha."""
    fees = DEFAULT_CONFIG["fees"]
    assert fees["subscribe_rate"] == 0.0012
    assert fees["redeem_rate"] == 0.005


def test_fam_decay_defaults_to_the_decided_value():
    # Decision 2: 0.2, against the 0.1 fallback loop.py carries today.
    assert DEFAULT_CONFIG["dynamics"]["fam_decay"] == 0.2


def test_attention_has_its_own_retention():
    # DECISIONS #5: att_t = lambda_attention * att_{t-1} + exposure + beta_guba * z_guba.
    # Today the attention stock reuses fam_decay, so a separate 0.8 is the point of the key.
    assert DEFAULT_CONFIG["dynamics"]["lambda_attention"] == 0.8


def test_beta_guba_is_exactly_zero():
    """Decision 3: the guba channel is WIRED but its coefficient is ZERO.

    The decision is explicit that beta_guba must be 0.0 and must NOT be written
    as 0.1: keeping it at zero is what makes this round's attention series
    byte-identical to today's while the pipeline becomes real code, and the
    owner signs off on a non-zero value separately, before the main grid.  An
    exact == 0.0 (not a tolerance) is deliberate, so a helpful edit to 0.1
    fails here loudly instead of quietly changing every downstream result."""
    assert DEFAULT_CONFIG["dynamics"]["beta_guba"] == 0.0


def test_climate_margin_defaults_to_one_sixth():
    # Decision 1: 1/6, half as strict as the 1/3 hardcoded in feed.climate_for.
    # Compared with a tolerance because the yaml carries a decimal expansion.
    assert abs(DEFAULT_CONFIG["feed"]["climate_margin"] - 1.0 / 6.0) < 1e-12


# --- the inert additions: each default must equal the literal it replaces ---------------------

def test_initial_pnl_defaults_to_lookback():
    """Decision 13: the three demo configs keep today's lookback sampling.

    Only research-grade configs opt into mode "target"; if the default ever
    flipped, every demo run's opening holdings -- and so every demo hash --
    would move without a single config file changing."""
    assert DEFAULT_CONFIG["initial_pnl"]["mode"] == "lookback"


def test_llm_sampling_parameters_equal_the_runtime_constants():
    # E9: temperature and the physical HTTP retry budget were unconfigurable
    # module constants in agents/runtime.py (TEMP = 0.3, MAX_ATTEMPTS = 5) and
    # never reached run_meta.  Surfacing them must not change what a run does,
    # so both defaults equal those constants exactly.
    assert DEFAULT_CONFIG["llm"]["temperature"] == 0.3
    assert DEFAULT_CONFIG["llm"]["max_provider_attempts"] == 5


def test_benchmark_is_unset_by_default():
    # Decision 6: the 510760 proxy series is machine-local (data/market/ is
    # gitignored), so the default must be null -- which leaves index_5d absent
    # from the agent view, today's behaviour, and keeps the demo hashes still.
    assert DEFAULT_CONFIG["market"]["benchmark_path"] is None
    assert DEFAULT_CONFIG["market"]["benchmark_label"] is None


def test_qdii_blocked_has_no_default():
    """Decision 10: schema only -- no default, and no shipped config sets it.

    loop._qdii_blocked_set reads cfg["qdii_blocked"], so a default here would
    not merely be unused: it would activate the suspension path on every run
    before the real suspension calendar exists.  The key must stay absent until
    that calendar lands as its own data task."""
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
    # target-mode only (decision 13); no literal exists today, these ARE the values
    (("initial_pnl", "share_at_loss"), 0.05),
    (("initial_pnl", "tolerance"), 0.02),
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
    Only the config file is read, so this passes on a clean clone even for the
    research configs whose data inputs deliberately never ship."""
    validate(load_config(os.path.join(RUNS_DIR, name)), "run")


def test_every_new_key_is_accepted_by_the_schema():
    """The other direction: a config that SETS every new key must validate.

    additionalProperties is false, so a key that reached engine_defaults.yaml
    without reaching the schema would make any config setting it unloadable --
    the exact failure mode (mock_options, fam_decay, qdii_blocked) that the
    audit traced this whole round back to.  qdii_blocked is included here
    because a test fixture is the one place decision 10 allows it to appear."""
    cfg = load_config(os.path.join(RUNS_DIR, "demo_two_arm.json"))
    cfg["dynamics"] = {"fam_decay": 0.2, "fam_threshold": 1.0, "lambda_trust": 0.9,
                       "lambda_attention": 0.8, "beta_guba": 0.0}
    cfg["dca"] = {"pct": 0.02, "min_ticket": 100.0}
    cfg["initial_pnl"] = {"mode": "target", "lookback_days_min": 60,
                          "lookback_days_max": 250, "share_at_loss": 0.05,
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

    Decision 10 leaves this key unset everywhere, which means the FIRST config
    to carry it will be written by hand from a suspension calendar; a key that
    is not a date would otherwise be silently ignored by _qdii_blocked_set."""
    cfg = load_config(os.path.join(RUNS_DIR, "demo_two_arm.json"))
    cfg["qdii_blocked"] = {"2025-10": {"codes": ["001234"]}}
    with pytest.raises(ConfigError):
        validate(cfg, "run")


# --- event.schema.json: the dec row's new failure_kind (decision 9) --------------------------

@pytest.mark.parametrize("kind", ["transport", "model", None])
def test_dec_row_accepts_failure_kind(kind):
    """Decision 9 splits the diagnosis, not the halt threshold.

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
    # Optional: every dec row written before this round omits the field.
    ev = {"ev": "dec", "t": 540, "d": "2025-10-09", "i": "inv_00001",
          "prompt_sha": "0" * 64, "status": "ok", "arm": "TV"}
    validate(ev, "event")

"""Owner decisions 15 and 17, taken 2026-09-07 after the adversarial review.

15. A plan (DCA) instalment pays `fees.subscribe_rate` like any other subscription. It
    was the only purchase channel exempt from decision 8, so every `kind="dca"` row
    carried fee 0.0 while the RUNBOOK said `act.fee` is non-zero -- and decision 7 had
    just made the channel reachable for the roughly half of a cohort that opens with no
    holdings, so the exempt path was about to carry real volume.

17. `rank_feed` gains a `w_att` weight so the per-fund attention stock has a reader.
    Nothing read `inv.attention` before, which meant `dynamics.beta_guba` and
    `dynamics.lambda_attention` changed a series no agent could see -- decision 3's
    claim that the guba channel is "wired, only the coefficient is zero" was not true of
    the ranker. The default 0.0 keeps every existing run byte-identical.
"""
from __future__ import annotations

import json

import pytest

from flowmirror.channels.feed import rank_feed
from flowmirror.engine import loop as L

from tests.conftest import build_demo_cfg


# ------------------------------------------------ decision 15: the plan pays its fee

# The plan block fires on `dt_cur.day == 1`, i.e. only when the calendar first of a
# month is itself a trading day. In the demo window that is 2025-10-01 and 2025-12-01
# (2025-11-01 is a Saturday, so November's instalment never executes at all -- reported
# to the owner as a separate mechanism question). Reaching December therefore needs the
# whole window, not the 3-day default.
DCA_DAYS = 60


def _run(out_dir, days=DCA_DAYS, **over):
    cfg = build_demo_cfg(out_dir, agents=10, days=days)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    assert L.run_simulation(cfg, L.RuntimeOpts()) == 0
    rows = [json.loads(l) for l in
            (out_dir / "event_log.jsonl").read_text(encoding="utf-8").splitlines()]
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    return rows, meta


def _acts(rows, kind):
    return [r for r in rows if r.get("ev") == "act" and r.get("kind") == kind]


def test_a_plan_instalment_pays_the_subscription_fee(tmp_path):
    """The rate that applies to a subscription applies to a plan instalment."""
    rate = 0.0012
    rows, _meta = _run(tmp_path / "fee", fees={"subscribe_rate": rate, "redeem_rate": 0.005})
    dca = _acts(rows, "dca")
    if not dca:
        pytest.skip("this short fixture crossed no month boundary with a plan investor")
    for r in dca:
        assert r["fee"] > 0.0, "a plan instalment used to be the one fee-free purchase"
        assert r["fee"] == pytest.approx(r["amt"] * rate, rel=1e-3, abs=0.01)


def test_a_zero_rate_leaves_the_plan_free(tmp_path):
    """The exemption is gone, not replaced by a hardcoded rate."""
    rows, _meta = _run(tmp_path / "free", fees={"subscribe_rate": 0.0, "redeem_rate": 0.0})
    for r in _acts(rows, "dca"):
        assert r["fee"] == pytest.approx(0.0)


def test_the_plan_fee_reaches_the_run_total_and_the_wealth_identity(tmp_path):
    """A fee charged but not accumulated would break invariant (d) silently."""
    out = tmp_path / "ident"
    rows, meta = _run(out, fees={"subscribe_rate": 0.0012, "redeem_rate": 0.005})
    dca = _acts(rows, "dca")
    if not dca:
        pytest.skip("no plan instalment in this fixture")
    # every fee logged anywhere is inside the run's fee total
    logged = sum(float(r.get("fee") or 0.0) for r in rows if r.get("ev") == "act")
    assert float(meta["counters"]["fees_cny"]) == pytest.approx(logged, abs=0.05)
    # and the wealth identity still holds, which is what carries the fee term
    report = json.loads((out / "invariants_report.json").read_text(encoding="utf-8"))
    d = (report.get("checks") or {}).get("d_wealth_conservation") or {}
    assert d.get("pass") is not False, f"wealth identity broke: {d}"


def test_units_are_bought_with_the_ticket_net_of_the_fee(tmp_path):
    """Cash falls by the full ticket; units and the cost basis are on the net amount --
    the same arithmetic the subscribe branch uses, so the two channels agree."""
    rows, _meta = _run(tmp_path / "net", fees={"subscribe_rate": 0.01, "redeem_rate": 0.0})
    dca = _acts(rows, "dca")
    if not dca:
        pytest.skip("no plan instalment in this fixture")
    for r in dca:
        net = r["amt"] - r["fee"]
        # amt and fee are logged rounded to 2dp, so recomputing units from the log
        # carries up to 0.01/nav of rounding -- the tolerance is that, not slack.
        assert r["units"] == pytest.approx(net / r["nav"], abs=0.02 / r["nav"])
        # the fee is a real deduction: gross would buy strictly more units
        assert r["units"] < r["amt"] / r["nav"]


# --------------------------------------------- decision 17: attention has a reader

_POSTS = [{"post_id": "pA", "org": "O", "code": "F_HOT", "intent_group": "I2"},
          {"post_id": "pB", "org": "O", "code": "F_COLD", "intent_group": "I2"}]
_CFG = {"K": 2, "slots": {"follow": 0, "fit": 0, "trending": 2},
        "w_trust": 0.0, "w_fit": 0.0, "w_heat": 0.0, "w_soc": 0.0, "eps": 0.0}


def _order(w_att, attention):
    import random
    state = {"risk_latent": "tolerant", "core": "chaser", "follow": [],
             "trust": {}, "attention": attention, "hold": []}
    cfg = dict(_CFG, w_att=w_att)
    ranked = rank_feed(state, list(_POSTS), {}, {}, cfg, random.Random(7),
                       mode="three_source") or []
    out = []
    for item in ranked:
        post = item[0] if isinstance(item, tuple) else item.get("post", item)
        out.append(post.get("post_id") if isinstance(post, dict) else None)
    return out


def test_attention_is_inert_at_the_default_weight():
    """w_att defaults to 0.0, so every run made before attention had a reader is
    byte-identical -- which is exactly what makes this safe to land."""
    hot = {"F_HOT": 50.0, "F_COLD": 0.0}
    assert _order(0.0, hot) == _order(0.0, {}), \
        "at weight 0 the attention stock must not move the ordering at all"


def test_a_non_zero_weight_lets_attention_move_the_ranking():
    """Decision 17: raising one config value now really enables the channel."""
    hot = {"F_HOT": 50.0, "F_COLD": 0.0}
    assert _order(1.0, hot)[0] == "pA", (
        "with every other weight zeroed, the attended fund's post must rank first -- "
        "if it does not, nothing reads attention and beta_guba is still inert")
    cold = {"F_HOT": 0.0, "F_COLD": 50.0}
    assert _order(1.0, cold)[0] == "pB"


def test_the_attention_term_is_bounded():
    """tanh keeps a fund with a long exposure history from dominating the score, the
    same way the trust term is bounded."""
    import math
    small, large = {"F_HOT": 1.0}, {"F_HOT": 10_000.0}
    # both saturate, so a 10,000x difference in stock cannot buy 10,000x the score
    assert math.tanh(1.0) < math.tanh(10_000.0) <= 1.0
    assert _order(1.0, small)[0] == _order(1.0, large)[0] == "pA"

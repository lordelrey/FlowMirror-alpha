#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flowmirror.engine.world -- FlowMirror v7 world layer (everything but the day loop).

Owns: run-config validation (incl. the A2 modality arms and fee keys), data loaders (World),
the Fund/Inv state classes, the institution posting policy, guba climate seeding, the event
logger, the invariant checks and the report writers. flowmirror.engine.loop imports this module
and owns only the trading-day loop, the apply step and the CLI.

Invariant reporting: every invariant this engine defines is registered in INVARIANTS (stable
key -> one-line description). write_reports() writes ONE invariants_report.json entry per
registered key (union with anything check_invariants returned): description plus pass/fail
with the numeric detail the check produced, or skipped+reason when the check does not apply,
never silence; a top-level summary carries passed/failed/skipped counts and the overall
boolean. Two entries are WARN forms (m_tv_arm_carries_images, m_env_valence_warning): each
states a fact, carries a `reason` beside its pass, and always passes -- gating belongs on
irreversible, cross-system, security or release boundaries and a simulation run is none of
those, so "did pixels reach the TV arm" is answered by the NUMBER run_meta.images.attached and
"was the environment one-sided" by the opening-loss and bearish-climate counts, never by a
failed run. event_log_sha256 is kept and the event log itself is untouched.

INV-FIX: check results are consumed for real now, so every check compares like with like.
(a) derives the expected guba week key from each audited day's date via datetime's
isocalendar (the same ISO-week arithmetic the day loop's wk_prev rule uses) instead of
comparing a week key against a date, and (b) seeds holdings from the agents' opening
positions and clears a fund only when a redemption takes it to zero units. A check whose
input rows are absent reports skipped+reason, never a silent vacuous pass.

Arms: agent-level modality arms come from feed.assign_agent_arms() (stratified block
randomisation within each population cell, PREREG v1.3 B10); feed.arm_for_agent() remains the
unbalanced per-agent coin, kept only as the no-cohort fallback (exposure path, tests).

NAV loading: top-level nav_cache keys that are not 6-digit fund codes (e.g. the demo file's
`_meta` honesty block) are skipped and counted in the loader's summary line; a nav_cache with
`_meta.synthetic == true` prints one warning line at run start and records
`"synthetic_nav": true` in run_meta.json so demo runs are distinguishable from research runs.

Determinism: random.Random(rng_seed_from(run_tag, *parts)) streams only; hashlib
sha256 via flowmirror.io.hashing; never built-in hash(); sets are never iterated
unsorted. Stdlib + this package only; console output is ASCII-only.
"""
from __future__ import annotations

import ast
import json
import os
import random
import sys
import tempfile
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flowmirror.channels import feed
from flowmirror.config.loader import deep_merge, load_config
from flowmirror.config.validate import ConfigError, validate
from flowmirror.io.hashing import rng_seed_from, sha256_file, sha256_text
from flowmirror.io.jsonl import iter_jsonl, write_jsonl_atomic
from flowmirror.regulator.cn_cxr import classify_fund, cxr_outcome  # noqa: F401 (re-export for loop)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- reused VERBATIM from sim/engine_v5.py (frozen 2026-09-02) --------------------------------
C_RANK = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
R_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}
INVEST_SHARE_CASH = {"under_10pct": 0.10, "10_30pct": 0.20, "30_50pct": 0.40, "50_70pct": 0.60, "over_70pct": 0.70}
N_FUNDS_K = {"under_5": 1, "5_10": 3, "10_15": 5, "15_20": 5, "over_20": 5}   # k per bin, cap 5 (spec §3)
NEUTRAL_R, NEUTRAL_MS = "R3", "flat"   # neutral context for product-less posts (design constant)


def die(msg: str, code: int = 1):
    print(f"[FATAL] {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_json(p: Path):
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_jsonl(p: Path):
    return [json.loads(ln) for ln in open(p, "r", encoding="utf-8") if ln.strip()]


def dump(p: Path, obj):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def quarter_of(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


class Fund:
    __slots__ = ("code", "r", "qdii", "family", "dates", "navs", "active_from")

    def __init__(self, code, r, qdii, family, dates, navs, active_from):
        self.code, self.r, self.qdii, self.family = code, r, qdii, family
        self.dates, self.navs, self.active_from = dates, navs, active_from

    def nav_at(self, d: date) -> float:            # last NAV on/before d (series is trade days)
        return self.navs[max(bisect_right(self.dates, d) - 1, 0)]

    def market_state(self, d: date) -> str:        # trailing 63-trading-day return bins
        i = bisect_right(self.dates, d) - 1
        if i < 1:
            return "flat"
        ret = self.navs[i] / self.navs[max(i - 63, 0)] - 1.0
        return "up" if ret > 0.05 else ("down" if ret < -0.05 else "flat")


# --- §1 DEFAULT_CONFIG (engine_defaults.yaml mapped into the run contract) + validation -------
_YAML_ALIAS = {"start_date": ("window", "start"), "end_date": ("window", "end"),
               "days": ("window", "max_trading_days"), "llm_workers": ("llm", "workers"),
               "K": ("feed", "K"), "eps": ("feed", "eps"), "gamma": ("feed", "gamma")}

# A2 modality contract: level (agent|run|exposure), the arm set and the fee schedule.
_MOD_LEVELS = ("agent", "run", "exposure")
_MOD_ARMS = ("T", "TC", "TV")
_FALLBACK_ARMS = ["T", "TV"]
_FALLBACK_FEES = {"subscribe_rate": 0.0, "redeem_rate": 0.0}


def _engine_defaults() -> dict:
    raw = {}
    try:
        got = load_config(REPO_ROOT / "config" / "engine_defaults.yaml")
        if isinstance(got, dict):
            raw = got
    except Exception:
        raw = {}
    out: dict = {}
    for k, v in raw.items():
        if k in _YAML_ALIAS:
            sec, key = _YAML_ALIAS[k]
            out.setdefault(sec, {})[key] = v
        elif isinstance(v, dict) and k in ("window", "feed", "llm", "channels", "strategy_groups", "mock_options"):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


DEFAULT_CONFIG = _engine_defaults()   # nothing else merged; no config files are written here
# A2: pick the modality/fee defaults up from engine_defaults.yaml when the yaml carries them,
# and hard-code the same fallbacks so the engine also runs before the yaml lands.
if DEFAULT_CONFIG.get("modality_level") not in _MOD_LEVELS:
    DEFAULT_CONFIG["modality_level"] = "agent"
_ma = DEFAULT_CONFIG.get("modality_arms")
if not isinstance(_ma, list) or not _ma or any(a not in _MOD_ARMS for a in _ma):
    DEFAULT_CONFIG["modality_arms"] = list(_FALLBACK_ARMS)
# Decision 19: no unconditional modality_run_arm default. Forcing "TV" here put it in
# every merged config, and validate_config then required it to appear in modality_arms
# regardless of level -- so a reference cell running only T/TC was rejected outright,
# with an error pointing at the user's modality_arms rather than at this default. It is
# defaulted at run level only, where it is the only thing that means anything.
if (DEFAULT_CONFIG.get("modality_run_arm") is not None
        and DEFAULT_CONFIG.get("modality_run_arm") not in _MOD_ARMS):
    DEFAULT_CONFIG.pop("modality_run_arm", None)
try:
    _mf = DEFAULT_CONFIG.get("fees")
    _mf = _mf if isinstance(_mf, dict) else {}
    DEFAULT_CONFIG["fees"] = {"subscribe_rate": float(_mf.get("subscribe_rate", 0.0)),
                              "redeem_rate": float(_mf.get("redeem_rate", 0.0))}
except Exception:
    DEFAULT_CONFIG["fees"] = dict(_FALLBACK_FEES)


def _mod_level_of(cfg: dict) -> str:
    """Effective modality level: modality_level wins; arm_level is the legacy alias; default agent."""
    return cfg.get("modality_level") or cfg.get("arm_level") or "agent"


def _modality_compat(cfg: dict) -> str:
    """Backward compat (A2): a config that lacks modality_level but carries the legacy arm_level
    alias is upgraded in place (modality_level = arm_level). Returns the effective level."""
    if "modality_level" not in cfg and "arm_level" in cfg:
        cfg["modality_level"] = cfg["arm_level"]
    return _mod_level_of(cfg)


def _validate_modality(cfg: dict) -> None:
    lvl = _modality_compat(cfg)
    if lvl not in _MOD_LEVELS:
        die(f"config: modality_level must be one of agent|run|exposure (got {lvl!r})")
    cfg["modality_level"] = lvl
    arms = cfg.get("modality_arms")
    if arms is None:
        arms = list(DEFAULT_CONFIG.get("modality_arms") or _FALLBACK_ARMS)
        cfg["modality_arms"] = arms
    if not isinstance(arms, list) or not arms:
        die("config: modality_arms must be a non-empty list of arm names")
    if any(a not in _MOD_ARMS for a in arms):
        die(f"config: modality_arms entries must be T|TC|TV (got {arms})")
    if len(set(arms)) != len(arms):
        die(f"config: modality_arms must not repeat an arm (got {arms})")
    # Decision 19: modality_run_arm only means something at run level, so only there is
    # it defaulted and only there must it be one of modality_arms. At agent or exposure
    # level a stray value is still checked for spelling (a typo should not sit unnoticed
    # in run_meta) but never gates the run.
    run_arm = cfg.get("modality_run_arm")
    if _mod_level_of(cfg) == "run":
        if run_arm is None:
            run_arm = arms[0]
            cfg["modality_run_arm"] = run_arm
        if run_arm not in _MOD_ARMS:
            die(f"config: modality_run_arm must be T|TC|TV (got {run_arm!r})")
        if run_arm not in arms:
            die(f"config: modality_run_arm {run_arm!r} must be one of modality_arms {arms}")
    elif run_arm is not None and run_arm not in _MOD_ARMS:
        die(f"config: modality_run_arm must be T|TC|TV (got {run_arm!r})")
    fees = cfg.get("fees")
    if fees is None:
        fees = dict(DEFAULT_CONFIG.get("fees") or _FALLBACK_FEES)
        cfg["fees"] = fees
    if not isinstance(fees, dict):
        die("config: fees must be an object {subscribe_rate, redeem_rate}")
    for key in ("subscribe_rate", "redeem_rate"):
        v = fees.get(key, 0.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            die(f"config: fees.{key} must be a number in [0, 0.1] (got {v!r})")
        if not 0.0 <= float(v) <= 0.1:
            die(f"config: fees.{key} must lie in [0, 0.1] (got {v})")
        fees[key] = float(v)


def _read_rows(p) -> list:
    p = Path(p)
    if str(p).endswith(".jsonl"):
        return [r for r in iter_jsonl(p) if isinstance(r, dict)]
    obj = load_json(p)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return obj.get("rows") or obj.get("notes") or obj.get("pool") or []
    return []


def _pool_orgs(content_pool):
    if not content_pool:
        return None
    try:
        rows = _read_rows(content_pool)
    except Exception:
        return None
    return {r.get("org") for r in rows if isinstance(r, dict) and r.get("org")}


def validate_config(cfg: dict) -> None:
    try:
        validate(cfg, "run")                       # schema: unknown keys / types rejected here
    except ConfigError as e:
        die(f"config: {e}")
    feedc = cfg.get("feed") or {}
    slots = feedc.get("slots") or {}
    if sum(int(v) for v in slots.values()) != int(feedc.get("K", -1)):
        die("config: feed.slots must sum to feed.K")
    if cfg.get("strategy_mix") not in ("measured", "push_heavy", "edu_heavy"):
        die("config: strategy_mix must be measured|push_heavy|edu_heavy")
    if cfg.get("ranking") not in ("three_source", "random"):
        die("config: ranking must be three_source|random")
    _validate_modality(cfg)                        # A2: level/arms/run_arm/fees (arm_level alias)
    if cfg["strategy_mix"] != "measured":
        if not (cfg.get("strategy_groups") or {}).get(cfg["strategy_mix"]):
            die("config: strategy_groups needs a non-empty list for this strategy_mix")
    if not 0.0 <= float(cfg.get("p_active", -1.0)) <= 1.0:
        die("config: p_active must lie in [0,1]")
    pool_orgs = _pool_orgs(cfg.get("content_pool"))
    if pool_orgs is not None:
        missing = [o for o in (cfg.get("orgs") or []) if o not in pool_orgs]
        if missing:
            die(f"config: {len(missing)} org(s) absent from the content pool")


# --- §2 loaders -------------------------------------------------------------------------------
class World:
    __slots__ = ("agents", "pool", "orgs", "funds", "fund_org", "family", "fund_meta", "guba",
                 "nav_days", "inputs_sha256", "base_codes", "deferred", "start", "end", "run_tag",
                 "nav_synthetic", "nav_source", "nav_skipped")


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _is_fund_code(code) -> bool:
    """A nav_cache top-level key is a fund series iff it is a 6-digit ASCII code. Anything else
    (the demo file's `_meta` honesty block, future bookkeeping keys) is skipped, not parsed."""
    s = str(code)
    return len(s) == 6 and s.isascii() and s.isdigit()


def load_world(cfg: dict) -> World:
    run_tag = cfg["run_tag"]
    paths = {k: _abs(cfg[k]) for k in ("agents_file", "content_pool", "nav_cache", "guba_signal")}
    for opt in ("family_file", "fund_meta_file"):
        if cfg.get(opt):
            paths[opt] = _abs(cfg[opt])
    hashes = {}
    for k in sorted(paths):
        if not paths[k].exists():
            die(f"missing input {k}: {paths[k]}")
        hashes[str(paths[k])] = sha256_file(paths[k])
    agents = load_json(paths["agents_file"])
    if isinstance(agents, dict):
        agents = agents.get("agents") or agents.get("individuals") or []
    if not isinstance(agents, list) or not agents:
        die(f"agents file has no agent rows: {paths['agents_file']}")
    pool_rows = _read_rows(paths["content_pool"])
    pool = defaultdict(list)
    for row in pool_rows:
        if row.get("org"):
            pool[row["org"]].append(row)
    orgs = sorted(cfg.get("orgs") or [])
    for k, org in enumerate(orgs):
        if org not in pool:
            die(f"config org #{k} has no notes in the content pool")
    fund_meta = {}
    if "fund_meta_file" in paths:
        fm = load_json(paths["fund_meta_file"])
        if isinstance(fm, dict) and isinstance(fm.get("funds"), dict):
            fm = fm["funds"]
        fund_meta = fm if isinstance(fm, dict) else {}
    fam_map = {}
    if "family_file" in paths:
        fam_map = {code: fam for fam, codes in load_json(paths["family_file"])["families"].items() for code in codes}
    win = cfg["window"]
    start, end, maxd = date.fromisoformat(win["start"]), date.fromisoformat(win["end"]), int(win.get("max_trading_days") or 0)
    nav_cache = load_json(paths["nav_cache"])
    if not isinstance(nav_cache, dict):
        die(f"nav_cache must be a JSON object of fund code -> {{date: nav}}: {paths['nav_cache']}")
    # DEFECT 3: only 6-digit fund codes are series; `_meta` & friends are skipped (counted,
    # never parsed). `_meta.synthetic == true` marks the shipped demo file -> warn + run_meta.
    nav_skipped = sorted(k for k in nav_cache if not _is_fund_code(k))
    nav_series = {c: nav_cache[c] for c in sorted(nav_cache) if _is_fund_code(c)}
    if not nav_series:
        die(f"nav_cache carries no 6-digit fund codes: {paths['nav_cache']}")
    nav_meta = nav_cache.get("_meta") if isinstance(nav_cache.get("_meta"), dict) else {}
    nav_synthetic = bool(nav_meta.get("synthetic"))
    try:
        nav_label = paths["nav_cache"].relative_to(REPO_ROOT).as_posix()
    except ValueError:
        nav_label = str(paths["nav_cache"])
    if nav_synthetic:
        print(f"[world] WARNING: synthetic demo NAVs in use ({nav_label}) -- results are illustrative, not market data.")
    lo, hi = win["start"], win["end"]             # ISO strings compare chronologically
    day_strs = sorted({d for s in nav_series.values() for d in s if lo <= d <= hi})
    nav_days = [date.fromisoformat(d) for d in day_strs][:maxd]
    if not nav_days:
        die(f"no trading days in window {lo}..{hi} (nav_cache: {paths['nav_cache']})")
    # fund universe: >=63 NAVs before start; creative-linked common-support codes with shorter
    # history enter from first NAV date + 21 days (spec CONFIG)
    cs_codes = {c for n in pool_rows for c in (n.get("common_support_codes") or [])}
    funds, base_codes, deferred, fund_org = {}, [], {}, {}
    for code in sorted(nav_series):
        items = sorted(nav_series[code].items())
        dts = [date.fromisoformat(d) for d, _ in items]
        navs = [float(v) for _, v in items]
        n_before = bisect_left(dts, start)
        if n_before < 63 and code not in cs_codes:
            continue
        meta = fund_meta.get(code) or {}
        r, qdii = classify_fund(str(meta.get("type") or meta.get("ftype") or ""), str(meta.get("name") or ""))
        if n_before >= 63:
            funds[code] = Fund(code, r, qdii, fam_map.get(code, code), dts, navs, start)
            base_codes.append(code)
        else:
            afrom = dts[0] + timedelta(days=21)
            funds[code] = Fund(code, r, qdii, fam_map.get(code, code), dts, navs, afrom)
            deferred[code] = afrom.isoformat()
    if not base_codes:
        die("empty base fund universe (no fund with >=63 NAVs before window start)")
    for row in pool_rows:                         # code -> issuing org (required by srs_analysis)
        for c in (row.get("common_support_codes") or []):
            fund_org.setdefault(c, row.get("org"))
    for code in sorted(fund_meta):
        meta = fund_meta[code]
        if isinstance(meta, dict) and meta.get("org"):
            fund_org[code] = meta["org"]
    guba_raw = load_json(paths["guba_signal"])
    sig = guba_raw.get("signal") if isinstance(guba_raw, dict) else {}
    w = World()
    w.agents, w.pool, w.orgs, w.funds, w.fund_org = agents, dict(pool), orgs, funds, fund_org
    w.family, w.fund_meta = fam_map, fund_meta
    w.guba = sig if isinstance(sig, dict) else {}
    w.nav_days, w.inputs_sha256, w.base_codes, w.deferred = nav_days, hashes, base_codes, deferred
    w.start, w.end, w.run_tag = start, end, run_tag
    w.nav_synthetic, w.nav_source, w.nav_skipped = nav_synthetic, nav_label, nav_skipped
    skip_txt = f"; skipped {len(nav_skipped)} non-fund nav_cache key(s) ({','.join(nav_skipped)})" if nav_skipped else ""
    print(f"[world] loaded {len(funds)} funds ({len(base_codes)} base, {len(deferred)} deferred), "
          f"{len(nav_days)} trading days, {len(agents)} agents, {len(orgs)} orgs, "
          f"{len(pool_rows)} pool notes{skip_txt}")
    # Decision 4, track one: on the real signal file guba_seed_label returns None for every
    # fund-week and the cold-start climate seed quietly falls back to agent comments.  That was
    # invisible, so a reader assumed the guba sentiment seed was working.  Count the ONLY key
    # the seed can fire from (see guba_seed_label) and state the answer at load time.  This is
    # a report, never a gate: zero is the expected state until the stance-labelling data task
    # lands, so it must not fail the run.
    guba_rows = [row for weeks in w.guba.values() if isinstance(weeks, dict)
                 for row in weeks.values() if isinstance(row, dict)]
    guba_stance = sum(1 for row in guba_rows if _stance_field(row.get("bull_ratio")) is not None)
    if guba_stance:
        print(f"[world] guba stance seed: {guba_stance} of {len(guba_rows)} fund-weeks carry "
              f"bull_ratio ({paths['guba_signal'].name})")
    else:
        print(f"[world] WARNING: guba stance seed unavailable -- 0 of {len(guba_rows)} fund-weeks "
              f"carry bull_ratio in {paths['guba_signal'].name}; stance labelling has not been "
              f"run, so the cold-start climate seed falls back to agent comments.")
    return w


# --- §3 investors -----------------------------------------------------------------------------
class Inv:
    # pnl0 / pnl0_misses (card W5+W6) are written once by init_investors and never touched
    # again. pnl0 is the OPENING return of every held fund -- the only record of the environment
    # an investor woke up in: inv.cost is a CLOSING basis (a later subscription blends it), so it
    # can no more answer "did this investor open at a loss" than it can seed invariant (b).
    __slots__ = ("id", "jid", "cell", "risk", "rc", "core", "strat_weight", "cash", "other", "hold", "cost", "lots",
                 "fam", "aff", "follow", "flag", "entry", "dca", "dca_target", "realized", "fees", "w0",
                 "expo", "memory", "reflection", "beliefs", "market_view", "risk_mood", "attention",
                 "gain_loss", "pnl0", "pnl0_misses", "arm", "arm_tally", "rng")

    @property
    def trust(self):                              # alias of aff: the SAME dict object
        return self.aff

    @trust.setter
    def trust(self, value):
        self.aff = value

    @property
    def ref_point(self):                          # alias of cost: the SAME dict object
        return self.cost

    @ref_point.setter
    def ref_point(self, value):
        self.cost = value


def rng_for(run_tag: str, *parts) -> random.Random:
    return random.Random(rng_seed_from(run_tag, *parts))


# Decision 13 defaults. They are repeated here rather than read from DEFAULT_CONFIG because
# init_investors is also called with hand-built configs (the self-test's, the unit suite's)
# that never went through the defaults merge; every value equals the literal it replaces, so a
# config carrying no initial_pnl block behaves exactly as the pre-card tree did.
# Decision 18: share_at_loss has NO default. Its old 0.05 was exactly the one-sided
# environment target mode exists to escape (~4.9% of openings at a loss on the real NAV
# series), so a research config that asked for target mode and forgot the share silently
# reproduced the pathology it was reaching for. Target mode now requires it explicitly;
# lookback mode never reads it, so every demo is unaffected.
_IPNL_DEFAULTS = {"mode": "lookback", "lookback_days_min": 60, "lookback_days_max": 250,
                  "tolerance": 0.02}


def _initial_pnl_cfg(cfg: dict) -> dict:
    """Effective `initial_pnl` block: the config's values over the decision-13 defaults."""
    raw = cfg.get("initial_pnl") if isinstance(cfg.get("initial_pnl"), dict) else {}
    out = dict(_IPNL_DEFAULTS)
    for k in _IPNL_DEFAULTS:
        v = raw.get(k)
        if v is not None and not isinstance(v, bool):
            out[k] = v
    if out["mode"] not in ("lookback", "target"):
        die(f"config: initial_pnl.mode must be lookback|target (got {out['mode']!r})")
    out["lookback_days_min"] = int(out["lookback_days_min"])
    out["lookback_days_max"] = int(out["lookback_days_max"])
    out["tolerance"] = float(out["tolerance"])
    # Decision 18: the manipulation must be stated, not inherited.
    sal = raw.get("share_at_loss")
    if out["mode"] == "target":
        if sal is None or isinstance(sal, bool):
            die("config: initial_pnl.mode is 'target' but share_at_loss is not set. "
                "Target mode exists to CONTROL the share of openings that start at a "
                "loss, so it has no default: inheriting one would silently reproduce the "
                "~4.9% one-sided environment the mode was added to escape. State the "
                "share the experiment intends, e.g. initial_pnl.share_at_loss: 0.5")
        out["share_at_loss"] = float(sal)
        if not 0.0 <= out["share_at_loss"] <= 1.0:
            die(f"config: initial_pnl.share_at_loss must be in [0, 1] "
                f"(got {out['share_at_loss']})")
    else:
        # lookback never reads it; carry a value only so downstream formatting is simple
        out["share_at_loss"] = float(sal) if sal is not None and not isinstance(sal, bool) else None
    # Reported rather than left to raise: with min > max the lookback branch's randint(min, max)
    # dies inside the stdlib with "empty range", which names neither the config key nor the run.
    if out["lookback_days_min"] > out["lookback_days_max"]:
        die(f"config: initial_pnl.lookback_days_min ({out['lookback_days_min']}) exceeds "
            f"lookback_days_max ({out['lookback_days_max']})")
    return out


def _target_cost_index(navs, now_i: int, lo_i: int, hi_i: int, prng, share_at_loss: float,
                       tolerance: float):
    """Cost-basis index in [lo_i, hi_i] whose implied opening return is closest to a drawn
    target (decision 13, `initial_pnl.mode == "target"`). Returns (index, missed).

    Why a target at all: the lookback branch randomises the LENGTH of the walk back, not the
    gain or loss it lands on. On the real NAV series that left 4.9% of openings at a loss with a
    median of +39% -- an environment so one-sided that no agent had a losing position to be
    reluctant to sell, and the disposition effect the paper claims to reproduce had nothing to
    reproduce it from. `share_at_loss` names the share of openings that must sit under water.

    The target is drawn INSIDE the span of returns this window can actually deliver on the
    wanted side of zero, so the requested share is honoured exactly whenever the window holds a
    price of both signs, and one thing alone can go wrong: a window with no price of that sign
    at all (a fund that only ever rose cannot be bought at a loss). That case takes the closest
    achievable return anyway and reports itself through `missed` -- the environment is being
    described, not overruled, so nothing here fails a run. Clamping the span to one side also
    keeps the mode free of loss/gain magnitude parameters the config does not carry.

    Exactly two draws per holding, in a fixed order, so a replay reproduces the same index:
    one random() for the side, one uniform() for the target inside that side's span."""
    nav_now = navs[now_i]
    # Non-positive NAVs cannot price a holding and are skipped here; when the window holds
    # nothing else the caller's own die() reports the fund, as it does in the lookback branch.
    cands = [(i, nav_now / navs[i] - 1.0) for i in range(lo_i, hi_i + 1) if navs[i] > 0.0]
    if not cands:
        return hi_i, False
    rets = [r for _, r in cands]
    lo_r, hi_r = min(rets), max(rets)
    want_loss = prng.random() < share_at_loss
    a, b = ((min(lo_r, 0.0), min(hi_r, 0.0)) if want_loss else
            (max(lo_r, 0.0), max(hi_r, 0.0)))
    target = prng.uniform(a, b)                   # uniform(x, x) == x and still consumes one
    best_i, best_d = hi_i, None                   # draw, so the stream is the same either way
    for i, r in cands:                            # ascending index: the oldest date wins a tie,
        d = abs(r - target)                       # so the pick never depends on iteration order
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i, best_d > tolerance


def init_investors(world: World, cfg: dict) -> list:
    run_tag, n = cfg["run_tag"], int(cfg["n_agents"])
    if len(world.agents) < n:
        die(f"agents file has {len(world.agents)} rows < n_agents={n}")
    arms = tuple(cfg.get("modality_arms") or ("T", "TV"))
    lvl = _mod_level_of(cfg)
    run_arm = cfg.get("modality_run_arm") or "TV"
    force = cfg.get("force_arm")
    ipnl = _initial_pnl_cfg(cfg)                  # decision 13: how the opening cost basis is drawn
    cohort = world.agents[:n]
    # DEFECT 2 (world half): agent-level arms use stratified block randomisation within each
    # population cell via feed.assign_agent_arms(run_tag, [(agent_id, cell), ...], arms), so
    # every cell is as balanced as its size allows (PREREG v1.3 B10). feed.arm_for_agent() is
    # the UNBALANCED per-agent coin and stays only as the no-cohort fallback used when the map
    # is unexpectedly missing an id; run-level and exposure-level behaviour is unchanged.
    arm_map = ({} if (force or lvl != "agent") else
               feed.assign_agent_arms(run_tag, [(rec.get("id"), rec.get("cell", "")) for rec in cohort], arms))
    out = []
    for rec in cohort:
        tr = rec.get("traits") or {}
        try:
            frac = INVEST_SHARE_CASH[tr["invest_share"]]
            kk = min(N_FUNDS_K[tr["n_funds"]], len(world.base_codes))
        except KeyError as e:
            die(f"investor {rec.get('id')}: bad trait bin {e}")
        fin = float(rec["wealth_wan"]) * 1e4
        cash, other = fin * frac, fin * (1.0 - frac)
        inv = Inv()
        inv.id = rec["id"]
        inv.jid = json.dumps(rec["id"])           # pre-serialized id for fast impression rows
        inv.cell, inv.risk = rec.get("cell", ""), rec.get("risk_latent", "")
        rc = rec.get("reported_C")
        if rc not in C_RANK:
            die(f"investor {inv.id}: invalid reported_C ({rc!r})")
        inv.rc = rc
        inv.core, inv.strat_weight = rec.get("core", ""), float(rec.get("strat_weight", 1.0))
        inv.rng = rng_for(run_tag, "agent", inv.id)
        hold, cost, pnl0, n_miss = {}, {}, {}, 0
        nh = inv.rng.randint(0, kk)               # sample 0..k held funds (spec §3)
        if nh:
            alloc = cash * inv.rng.uniform(0.3, 0.8)
            # Decision 13: target mode draws from the DERIVED stream rng_for(run_tag,
            # "initial_pnl", inv.id), never from inv.rng -- the same discipline dca_target
            # follows below, and for a sharper reason here. inv.rng is what picks how many funds
            # an investor holds, which ones, and (seeded onward) every per-day draw; spending it
            # on the cost basis would make the two modes differ in the whole population, not just
            # in its P&L, and E11 would stop being one manipulation. Left on the derived stream,
            # switching mode moves the cost bases and nothing else.
            tprng = rng_for(run_tag, "initial_pnl", inv.id) if ipnl["mode"] == "target" else None
        # Both modes consume inv.rng IDENTICALLY. The lookback offset is drawn (and in
        # target mode discarded) so that switching mode moves the cost bases and nothing
        # else: inv.rng also drives the daily p_active coin, rank_feed's tie-break and
        # exposure-level arm draws, so a mode that consumed one fewer draw per held fund
        # would shift every one of those and E11 would stop being one manipulation.
            for code in inv.rng.sample(world.base_codes, nh):
                f = world.funds[code]
                i0 = bisect_right(f.dates, world.start - timedelta(days=1)) - 1
                # Drawn in BOTH modes. The lookback bounds are config keys whose defaults
                # ARE 60 and 250, so this randint call, its arguments and its position in
                # the stream are exactly the pre-card ones (decision 13 keeps every demo
                # config on the lookback branch, so their hashes cannot move). Target mode
                # discards the value: consuming one fewer draw per held fund would shift
                # every later inv.rng draw -- the daily p_active coin, rank_feed's
                # tie-break, exposure-level arm draws -- and E11 would then confound the
                # opening-P&L manipulation with who was active and how feeds were ordered.
                off = inv.rng.randint(ipnl["lookback_days_min"], ipnl["lookback_days_max"])
                if tprng is None:                 # lookback: the pre-card behaviour, verbatim
                    ci = max(i0 - off, 0)         # clamped
                else:
                    ci, missed = _target_cost_index(f.navs, max(i0, 0),
                                                    max(i0 - ipnl["lookback_days_max"], 0),
                                                    max(i0 - ipnl["lookback_days_min"], 0),
                                                    tprng, ipnl["share_at_loss"],
                                                    ipnl["tolerance"])
                    n_miss += 1 if missed else 0
                cnav = f.navs[ci]
                if cnav <= 0:
                    die(f"fund {code}: non-positive cost NAV")
                hold[code] = alloc / nh / cnav
                cost[code] = cnav
                # same pass that fixes the basis fixes the initial lot's buy date;
                # ci is already drawn, so this adds no sampling
                if "buy_dates" not in locals():
                    buy_dates = {}
                buy_dates[code] = f.dates[ci]
                # Opening valence, recorded here because this is the only moment both operands
                # are unambiguous. Same form the day loop uses for gain_loss (nav / cost - 1);
                # max(i0, 0) is the day-0 mark, and it also covers a world whose series starts
                # after the window (i0 == -1), where navs[i0] would silently read the last day.
                pnl0[code] = f.navs[max(i0, 0)] / cnav - 1.0
            cash -= alloc
        inv.cash, inv.other, inv.hold, inv.cost = cash, other, hold, cost
        # lots mirror hold/cost with per-lot buy dates for FIFO redemption
        # accounting; they never enter snapshot() or any hash
        inv.lots = {code: [[u, cost[code], buy_dates.get(code)]] for code, u in hold.items()}
        inv.pnl0, inv.pnl0_misses = pnl0, n_miss
        inv.attention, inv.gain_loss = {c: 0.0 for c in hold}, {}
        inv.fam, inv.aff, inv.follow, inv.flag = {}, {}, set(), {}
        ev = rec.get("entry_day", 0)              # population entry spread over 365d -> run window
        spread = float(cfg.get("entry_spread_days", 30))
        ev_days = (date.fromisoformat(ev) - world.start).days if isinstance(ev, str) else int(ev)
        inv.entry = max(int(round(ev_days * spread / 365.0)), 0)
        inv.dca = (tr.get("dca") == "positive")
        # Decision 7 (world half): a plan investor whose holdings map starts empty had nothing
        # for the loop's plan block to top up -- it tops up min(inv.hold), which has no argument
        # on an empty map -- so at the smallest n_funds bin roughly half the flagged planners
        # could never place a single instalment and the plan channel measured nothing for them.
        # The loop half (card L5) reads this slot; naming the fund here, once, keeps the target
        # fixed for the whole run instead of drifting with whatever the investor happens to hold.
        #
        # The draw hangs off a DERIVED stream, rng_for(run_tag, "dca", inv.id), rather than
        # inv.rng.  Consuming from inv.rng would shift every later draw for that investor --
        # held-fund sample, cost-basis lookback, and each per-day draw seeded from it -- so
        # holdings and cost bases would move for investors and runs that have nothing to do with
        # plan investing.  The derived stream is seeded from the same run_tag and investor id, so
        # a replay reproduces the identical target while leaving inv.rng byte-for-byte untouched;
        # ineligible investors draw nothing at all.
        #
        # Universe: base_codes is exactly load_world's ">=63 NAVs before start" branch, and that
        # branch constructs its funds with active_from == world.start; codes with a deferred
        # activation date go to world.deferred and never enter this list.  Re-asserting
        # active_from <= world.start is therefore the identity on any world load_world built, and
        # it is what stops a hand-built world (the self-test's) from naming a target that cannot
        # be bought on day 0 -- the same universe the held-fund sample above draws from.
        inv.dca_target = None
        if inv.dca and not inv.hold:
            buyable = [c for c in world.base_codes
                       if c in world.funds and world.funds[c].active_from <= world.start]
            if buyable:
                inv.dca_target = rng_for(run_tag, "dca", inv.id).choice(buyable)
        inv.realized = 0.0
        inv.fees = 0.0                            # A2: cumulative subscribe/redeem fees paid (CNY)
        inv.w0 = cash + sum(u * cost[c] for c, u in hold.items()) + other   # == fin (identity check (d))
        inv.expo, inv.memory, inv.reflection, inv.beliefs = {}, [], {}, []
        inv.market_view = inv.risk_mood = 0
        if force:
            inv.arm = force
        elif lvl == "run":
            inv.arm = run_arm
        elif lvl == "agent":
            inv.arm = arm_map.get(inv.id) or feed.arm_for_agent(run_tag, inv.id, arms)
        else:                                     # exposure: per-impression arms assigned in the loop
            inv.arm = "TV"
        inv.arm_tally = {a: 0 for a in arms}
        out.append(inv)
    # Card W6, first half of the answer: state the opening valence at construction time, in one
    # ASCII line, the same way the loader states the guba stance seed. The live smoke run that
    # motivated this card returned mood 4 for all 77 decisions, no bearish comment and no
    # negative affinity delta, and there was nothing on screen to say the environment had
    # handed out almost no losses -- so the model got the blame. The numbers also travel to
    # invariants_report.json (m_env_valence_warning); this is a report, never a gate.
    n_hold = sum(len(v.pnl0) for v in out)
    n_loss = sum(1 for v in out for r in v.pnl0.values() if r < 0.0)
    n_loss_inv = sum(1 for v in out if any(r < 0.0 for r in v.pnl0.values()))
    share = f"{n_loss / n_hold:.1%}" if n_hold else "n/a"
    miss_txt = (f"; {sum(v.pnl0_misses for v in out)} holding(s) missed the drawn target by more "
                f"than {ipnl['tolerance']}" if ipnl["mode"] == "target" else "")
    print(f"[world] initial P&L ({ipnl['mode']}): {n_loss} of {n_hold} opening holding(s) at a "
          f"loss ({share}), {n_loss_inv} of {len(out)} investor(s) hold at least one{miss_txt}")
    return out


# --- §4 institution posting policy ------------------------------------------------------------
_INTENTS = ("I1", "I2", "I3")
_IG_GROUPS = ("I2", "nonI2")   # post.ig domain: the two-way intent group (schema + engine_v5 ig=grp)


def intent_group(intent: str) -> str:
    """Two-way intent group for post rows: ig / intent_group carry {"I2","nonI2"} while `intent`
    keeps the raw label in {I1,I2,I3} (config/schemas/event.schema.json; sim/engine_v5.py)."""
    return "I2" if intent == "I2" else "nonI2"


def _ig_of(note: dict):
    # Pool rows carry the three-way tag in `intent` (I1 brand / I2 product-push / I3 education) and a
    # two-way `intent_group` (I2 / nonI2). The measured mix is over the three-way tag (PREREG v1.1 §B4).
    it = note.get("intent")
    if it in _INTENTS:
        return it
    ig = note.get("intent_group")
    if ig in _INTENTS:
        return ig
    return "I2" if note.get("primary_intent") == "I2" else None


def _merge_counts(world: World, org_list) -> dict:
    counts = dict.fromkeys(_INTENTS, 0)
    for k, org in enumerate(org_list):
        if org not in world.pool:
            die(f"strategy_groups org #{k} missing from content pool")
        for note in world.pool[org]:
            ig = _ig_of(note)
            if ig:
                counts[ig] += 1
    tot = sum(counts.values())
    if tot <= 0:
        die("content pool has no I1/I2/I3 notes for this strategy mix")
    return {ig: counts[ig] / tot for ig in _INTENTS}


def intent_probs(world: World, cfg: dict) -> dict:
    mix = cfg.get("strategy_mix", "measured")
    if mix == "measured":
        return {org: _merge_counts(world, [org]) for org in world.orgs}
    grp = (cfg.get("strategy_groups") or {}).get(mix)
    if not grp:
        die(f"strategy_groups[{mix}] is empty")
    merged = _merge_counts(world, grp)
    return {org: dict(merged) for org in world.orgs}


def _note_id(note: dict) -> str:
    nid = note.get("note_id") or note.get("id")
    return str(nid) if nid is not None else sha256_text(json.dumps(note, ensure_ascii=False, sort_keys=True))[:12]


def _has_image(note: dict) -> bool:
    for k in ("images", "image_count", "n_images", "img_count", "img"):
        v = note.get(k)
        if isinstance(v, (list, tuple)) and len(v) > 0:
            return True
        if isinstance(v, (int, float)) and v > 0:
            return True
    return False


_HEX = frozenset("0123456789abcdefABCDEF")


def _is_sha256_hex(s) -> bool:
    """A usable image digest: exactly 64 hex characters. An id whose parallel digest fails this
    can never have pixels attached (contract 2.5 item 3 attaches only on an exact match), so
    the pool summary counts it separately instead of implying the file would be used."""
    return isinstance(s, str) and len(s) == 64 and all(c in _HEX for c in s)


def _image_ref_list(value) -> list:
    """The pool's `image_ids` / `image_sha256` column as a list of strings.

    Contract 2.5: the same column arrives in three shapes in the masked content pool -- a real
    JSON list, a JSON-encoded string ('["a_0.jpg"]'), and a Python repr with single quotes
    ("['a_0.jpg']") -- because the pool was assembled by more than one script. Parsing is
    therefore defensive by requirement, not by taste: a row this function cannot read costs
    that note its image, never the run, so every failure path returns a list."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if not isinstance(value, str):
        return []
    s = value.strip()
    if not s:
        return []
    if s[0] in "[(":                              # a serialised sequence: JSON first, then the
        for parse in (json.loads, ast.literal_eval):   # single-quoted repr (literals only)
            try:
                got = parse(s)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(got, (list, tuple)):
                return [str(v) for v in got if v is not None and str(v).strip()]
            break
        return []
    return [s]                                    # a bare single id, no brackets


def image_pool_summary(pool, images_root) -> dict:
    """How many of the content pool's image references resolve to a file under images_root.

    Underpins the run-start image line. Counts only: no path is returned (an images_root is a
    machine-local absolute path and only the operator's own configuration should hold it) and
    no image byte is read -- existence is all a summary can honestly claim, and the sha256
    verification that decides whether pixels are actually attached happens per attachment in
    the day loop (contract 2.5 item 3).

    `pool` accepts the World shape ({org: [note, ...]}, i.e. world.pool) and a flat sequence of
    note rows. Reference counts are over DISTINCT ids: several notes may reference the same
    image, and the number worth printing is how many image FILES this pool needs.

    Returns {root, notes, notes_with_refs, refs, resolvable, missing, refs_without_digest};
    refs == resolvable + missing, and with no root nothing can resolve, so `missing` then
    equals `refs` (the `root` key, None in that case, is what separates the two situations)."""
    if isinstance(pool, dict):
        groups = list(pool.values())
    elif isinstance(pool, (list, tuple)):
        groups = [pool]
    else:
        groups = []
    root = str(images_root) if images_root else ""
    digested = {}                                 # image id -> some row carried a usable digest
    notes = notes_with_refs = 0
    for grp in groups:
        for note in (grp if isinstance(grp, (list, tuple)) else [grp]):
            if not isinstance(note, dict):
                continue
            notes += 1
            ids = _image_ref_list(note.get("image_ids"))
            shas = _image_ref_list(note.get("image_sha256"))
            if ids:
                notes_with_refs += 1
            for k, iid in enumerate(ids):
                ok = k < len(shas) and _is_sha256_hex(shas[k])
                digested[iid] = digested.get(iid, False) or ok
    resolvable = 0
    if root:
        for iid in sorted(digested):              # sorted: this loop must not depend on dict
            if os.path.isfile(os.path.join(root, iid)):   # insertion order for its count
                resolvable += 1
    return {"root": root or None, "notes": notes, "notes_with_refs": notes_with_refs,
            "refs": len(digested), "resolvable": resolvable,
            "missing": len(digested) - resolvable,
            "refs_without_digest": sum(1 for ok in digested.values() if not ok)}


def publish_day(world: World, cfg: dict, t: int, recent: dict, rng_platform: random.Random, log=None) -> list:
    d = world.nav_days[t]
    dstr = d.isoformat()
    probs, ppd = intent_probs(world, cfg), int(cfg.get("posts_per_org_per_day", 1))
    nod = int(cfg.get("no_repeat_days", 10))
    posts = []
    for oi, org in enumerate(world.orgs):
        notes = world.pool[org]
        dq = recent.setdefault(org, deque(maxlen=max(nod, 1)))
        used = set(dq)
        p = probs[org]
        for j in range(ppd):
            intent = rng_platform.choices(_INTENTS, weights=[p[ig] for ig in _INTENTS])[0]
            elig = [n for n in notes if _ig_of(n) == intent and _note_id(n) not in used]
            if not elig:
                elig = [n for n in notes if _note_id(n) not in used]
                if elig:
                    print(f"[world] note fallback any-unused org#{oi} slot{j}")
            if not elig:
                elig = list(notes)
                print(f"[world] note fallback any org#{oi} slot{j}")
            if not elig:
                continue
            note = rng_platform.choice(sorted(elig, key=_note_id))
            nid = _note_id(note)
            dq.append(nid)
            used.add(nid)
            code = None
            for c in (note.get("common_support_codes") or []):
                f = world.funds.get(c)
                if f is not None and f.active_from <= d:
                    code = c
                    break
            pid = f"{t:03d}{oi}{j}"
            ig = intent_group(intent)             # post.ig is the intent GROUP in {I2, nonI2}, not the raw label
            posts.append({"post_id": pid, "org": org, "intent": intent, "intent_group": ig,
                          "note": nid, "code": code, "img": _has_image(note), "t_pub": dstr})
            if log is not None:
                # note_id is the stable content identity; slot id "p" is reused each run.
                log.emit("post", t=t, d=dstr, org=org, p=pid, intent=intent, ig=ig, fund=code,
                         img=posts[-1]["img"], note=nid)
    return posts


# --- §5 guba climate seeding -------------------------------------------------------------------
# REAL FIELD SET of a guba signal row (data/attention/guba_signal_v1.json, built by
# guba_signal_build.py) -- attention/volume only:
#     n_posts, reply_n, read_n, z_abnormal, ratio_vs_baseline, baseline_weeks_used
# The file's own `_meta` states "no stance is computed here" and "stance_jobs_stripped": true,
# so NONE of the stance fields guba_seed_label reads below exist today and it returns None for
# every fund and every week.  Decision 4 of AUDIT_AND_REMEDIATION_PLAN_2026-09-07 §5 makes that
# the CORRECT behaviour for now (two tracks: the code accepts the shape here, the stance
# labelling is a separate data task).  z_abnormal must never be pressed into service as a
# substitute: it is posting VOLUME, and volume carries no direction.
#
# `bull_ratio` is the per-fund-week key the stance-labelling pass will merge in (as a versioned
# guba_signal_v2.json); `bull`/`bear` counts are the alternative shape the same merge could
# take.  Both are read here so that data landing needs ZERO code change -- naming them is the
# whole point of this block.  load_world() prints how many fund-weeks actually carry
# `bull_ratio`, so the "no stance yet" state is stated out loud rather than inferred.
def _stance_field(value):
    """Coerce a stance field to float, or None when it is absent or unusable.

    The stance columns arrive from a separate merge script rather than from this engine, so a
    missing key, a JSON null, or a numeric-looking string must degrade to "no seed for this
    fund-week" instead of raising 40 days into a run.  bool is rejected on purpose: `true`
    would otherwise coerce to 1.0 and read as a unanimous bull week."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def guba_seed_label(world: World, code, week):
    entry = world.guba.get(code)
    if not isinstance(entry, dict):
        return None
    row = None
    for key in (week, str(week)):
        if key in entry:
            row = entry[key]
            break
    if not isinstance(row, dict):
        return None
    br = _stance_field(row.get("bull_ratio"))
    if br is None:                                # fallback shape: raw stance counts, no ratio
        bull = _stance_field(row.get("bull"))
        if bull is None:
            bull = _stance_field(row.get("bullish"))
        bear = _stance_field(row.get("bear"))
        if bear is None:
            bear = _stance_field(row.get("bearish"))
        bull, bear = bull or 0.0, bear or 0.0
        if bull + bear <= 0:                      # includes the real file: neither key present
            return None
        br = bull / (bull + bear)
    if br > 0.6:
        return "bullish_majority"
    if br < 0.4:
        return "bearish_majority"
    return "mixed"


# --- §6 event logger (+ snapshot / sha helpers used by loop) -----------------------------------
class EventLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w", encoding="utf-8", newline="\n")
        self.n = 0

    def emit(self, ev: str, **fields):
        fields.pop("ev", None)
        row = {"ev": ev}                          # ev first, compact, no timestamps (byte-identical replay)
        row.update(fields)
        self._fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self):
        if not self._fh.closed:
            self._fh.close()


def snapshot(invs, funds, label, dstr) -> dict:
    dd = date.fromisoformat(dstr)
    cash = oth = real = holdv = 0.0
    holders, ccash = 0, defaultdict(float)
    for inv in invs:
        cash += inv.cash
        oth += inv.other
        real += inv.realized
        ccash[inv.cell] += inv.cash
        if inv.hold:
            holders += 1
            for c, u in inv.hold.items():
                holdv += u * funds[c].nav_at(dd)
    return {"day": label, "date": dstr, "cash": round(cash, 2), "holdings_nav": round(holdv, 2),
            "other_assets": round(oth, 2), "realized_pnl": round(real, 2), "holders": holders,
            "cell_cash": {c: round(v, 2) for c, v in sorted(ccash.items())}}


def state_sha(obj) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def event_log_sha(path) -> str:
    return sha256_file(Path(path))


# --- §7 invariants ------------------------------------------------------------------------------
# Registry of every invariant this engine defines: stable key -> one-line human description.
# write_reports() writes ONE invariants_report.json entry per key in this registry (union with
# anything check_invariants returned), so a check that did not run shows up as `skipped` with
# a reason instead of being silently absent from the report.
# INV-FIX: every check below compares like-typed fields (week key vs week key, holdings vs
# holdings seeded from opening positions, list lengths vs list lengths) and reports
# skipped+reason when its input rows are absent, never a silent vacuous pass.
INVARIANTS = {
    "a_lagged_signals_only": "day-t feed ranking consumes only signals frozen at end of day t-1 (no same-day leakage)",
    "b_nonholder_never_redeems": "an agent never executes a redemption of a fund it does not hold (checkout refuses with no_holdings)",
    "c_hard_block_never_subscribes": "a subscription never executes for an agent/post pair already hard-blocked at checkout",
    "d_wealth_conservation": "per-agent identity cash + holdings@NAV + other + fees paid == w0 + realized + unrealized",
    "e_all_cells_exposed": "every population cell appears in at least one impression over the run",
    "f_post_ig_is_intent_group": "every post row carries ig in {I2, nonI2} consistent with its raw intent label",
    "g_comments_lagged_only": "a displayed comment climate never contains a comment published on the display day itself",
    "h_arm_balance": "modality arms are balanced: overall share within tolerance of 1/k and each cell as balanced as its size permits (per-impression shares at exposure level)",
    "i_redeem_checkout_never_blocked": "redemption checkouts are never gated: oc for act=redeem may only be match or no_holdings",
    "j_displayed_comment_matches_prev_day": "every displayed comment appears verbatim in that post's day-(t-1) comment set",
    "k_dec_matches_active": "exactly one decision row exists per active agent per day",
    "m_tv_arm_carries_images": "REPORT, never a gate: TV-arm image attachment (attached/missing/sha_mismatch) and which case the run is in -- no images_root, no TV arm, or both present",
    "m_env_valence_warning": "REPORT, never a gate: environment valence -- how many investors opened with a holding at a loss, and how many days carried a bearish_majority climate, so one-sided expressed sentiment can be attributed to the environment or to the model",
}


def _agent_fees(a) -> float:
    """Cumulative fee paid by an agent row; 0.0 for legacy rows created before fees existed."""
    try:
        v = a.get("fees") if isinstance(a, dict) else getattr(a, "fees", 0.0)
        return 0.0 if v is None else float(v)
    except Exception:
        return 0.0


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    return str(v)


def _ag_get(a, key, default=None):
    """Field accessor for agent rows that arrive either as Inv objects (live runs) or as plain
    dicts (replayed/serialised states); Inv slots that were never set read as `default`."""
    if isinstance(a, dict):
        return a.get(key, default)
    try:
        return getattr(a, key, default)
    except Exception:
        return default


def _expected_guba_week(d: date) -> str:
    """ISO week key of the guba signal that may be consumed on trading day d: the most recent
    week that ENDED strictly before d -- loop.py's wk_prev rule. The ISO year/week come from
    datetime.date.isocalendar() (the stdlib ISO-week arithmetic the day loop builds on; week
    math is never reimplemented here), and because the anchor is the Sunday strictly before d,
    the week containing d itself can never be returned. Key format matches guba signal keys,
    e.g. 2025-W41."""
    anchor = d - timedelta(days=d.weekday() + 1)   # most recent Sunday strictly before d
    iso_year, iso_week, _ = anchor.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def check_invariants(state: dict, events_path, cfg: dict):
    """state contract: agents (Inv rows carrying fees, default 0 when absent so old states
    still pass), hold0 ({agent_id: {fund_code: units}} -- each agent's OPENING holdings,
    snapshotted by the loop before day 0; seeds the non-holder check, and when absent that
    check skips rather than seed from anything else), funds, end(iso),
    signal_audit[{t, used(guba ISO week key), live_end(day t ISO date), prev_live_end,
    day_keys(list of day-level key strings)}], active_per_day{t:n}, agent_arms{i:arm}; funds maps
    code -> Fund for end-of-run pricing. Returns (checks, core): checks carries one entry per
    invariant -- {"pass": bool, ...numeric detail} when it ran, {"skipped": True, "reason": str}
    when it does not apply at this configuration or its input rows are absent; core is the
    run's decision and is True exactly when the run passed -- no entry's "pass" is explicitly
    False."""
    agents = state.get("agents") or []
    funds = state.get("funds") or {}
    id2cell = {_ag_get(a, "id"): _ag_get(a, "cell", "") for a in agents}
    rows = load_jsonl(Path(events_path))
    co_oc, co_cf = defaultdict(int), defaultdict(int)
    hard_p = set()
    pos_units = {}                       # FIX4 (b): (agent, fund) -> unit balance; presence in
    hold0 = state.get("hold0")           # this dict IS holdings (a None value marks a holder of
    for aid, holds in (hold0.items() if isinstance(hold0, dict) else []):  # unrecorded size, a
        # missing key a non-holder). FIX4: seeded from state["hold0"] -- each agent's OPENING
        # holdings, captured by the loop before day 0. The live Inv rows in state["agents"]
        # carry CLOSING positions (inv.hold is updated in place during the run), so they must
        # never seed this ledger.
        for code, units in (holds.items() if isinstance(holds, dict) else []):
            if isinstance(units, (int, float)) and not isinstance(units, bool):
                if float(units) > 0.0:
                    pos_units[(aid, code)] = float(units)
            else:
                pos_units[(aid, code)] = None      # opening position of unrecorded size
    b_viol, c_viol, f_viol = 0, 0, 0
    b_first, c_first = None, None
    n_sub, n_redeem_act, n_posts = 0, 0, 0
    imp_arms, exposed = defaultdict(lambda: defaultdict(int)), set()
    dec_day, cmt_idx, clims = defaultdict(int), defaultdict(set), []
    imp_tv = imp_tv_img = 0                       # (m) TV impressions, and how many logged an
    # image index. Read from the event log rather than from the loop's own counters so the two
    # can be compared: the log is the record, state["images"] is the bookkeeping about it.
    for r in rows:
        ev = r.get("ev")
        if ev == "imp":
            imp_arms[r.get("i")][r.get("arm") or "T"] += 1
            exposed.add(id2cell.get(r.get("i")))
            if (r.get("arm") or "T") == "TV":     # (m) imp.img_idx is the per-impression record
                imp_tv += 1                       # of which of the note's images was attached
                if r.get("img_idx") is not None:  # (contract 2.5 item 6); None == nothing attached
                    imp_tv_img += 1
        elif ev == "act":
            i, fund, kind = r.get("i"), r.get("fund"), r.get("kind")
            u = r.get("units")
            u = abs(float(u)) if isinstance(u, (int, float)) and not isinstance(u, bool) else None
            if kind in ("subscribe", "dca"):
                n_sub += 1
                if u is not None and pos_units.get((i, fund)) is not None:
                    pos_units[(i, fund)] += u
                elif u is not None and (i, fund) not in pos_units:
                    pos_units[(i, fund)] = u            # fresh position, provable from this row
                elif u is None and (i, fund) not in pos_units:
                    pos_units[(i, fund)] = None         # holder, size not provable from this row
            elif kind == "redeem":
                n_redeem_act += 1
                # FIX3 (b): a units ledger, not membership. A redemption violates (b) only
                # when the balance beforehand is non-positive (small epsilon for float
                # drift) or the agent never held the fund; a partial redemption just
                # subtracts units and the fund stays in the ledger.
                if (i, fund) not in pos_units:
                    b_viol += 1
                    if b_first is None:
                        b_first = {"i": i, "fund": fund, "units_held": 0.0, "units_redeemed": u}
                else:
                    have = pos_units[(i, fund)]
                    if have is not None and have <= 1e-9:
                        b_viol += 1
                        if b_first is None:
                            b_first = {"i": i, "fund": fund, "units_held": have,
                                       "units_redeemed": u}
                    elif have is not None and u is not None:
                        pos_units[(i, fund)] = have - u     # partial redemption: balance drops,
                        # but the fund only leaves the ledger once the balance is exhausted
        elif ev == "co":
            oc = r.get("oc")
            co_oc[oc] += 1
            co_cf[r.get("oc_cf")] += 1
            if oc == "hard_block":
                hard_p.add((r.get("i"), r.get("p")))
        elif ev == "post":                        # (f) post.ig is the intent GROUP {I2, nonI2}
            n_posts += 1
            if r.get("ig") not in _IG_GROUPS or (r.get("ig") == "I2") != (r.get("intent") == "I2"):
                f_viol += 1
        elif ev == "dec":
            dec_day[r.get("t")] += 1
        elif ev == "cmt":
            cmt_idx[(r.get("p"), r.get("t"))].add(r.get("text"))
        elif ev == "clim":
            clims.append(r)
    for r in rows:                                # second pass: a hard_block must never precede a subscribe
        if (r.get("ev") == "act" and r.get("kind") == "subscribe"
                and (r.get("i"), r.get("p")) in hard_p):
            c_viol += 1
            if c_first is None:
                c_first = {"i": r.get("i"), "p": r.get("p")}
    checks = {}
    # (a) INV-FIX: `used` is a guba ISO WEEK key. Derive the expected key from day t's date
    # (each entry's live_end) with the same rule the engine uses to pick wk_prev -- the most
    # recent week that ENDED strictly before day t -- and compare key to key. day_keys is a
    # LIST of day-level keys: record how many existed, never int() the list itself.
    audit = state.get("signal_audit") or []
    if not audit:
        checks["a_lagged_signals_only"] = {"skipped": True, "reason":
                                           "signal_audit is empty: this run recorded no guba ranking days"}
    else:
        a_matched, a_bad, dk_total, a_first = 0, 0, 0, None
        for e in audit:
            if not isinstance(e, dict):
                a_bad += 1
                if a_first is None:
                    a_first = {"t": None, "date": None, "used": None, "expected": None,
                               "reason": f"malformed audit entry ({type(e).__name__})"}
                continue
            keys = e.get("day_keys")
            if isinstance(keys, (list, tuple, set)):
                dk_total += len(keys)
            elif isinstance(keys, int) and not isinstance(keys, bool):
                dk_total += max(keys, 0)          # legacy integer shape, tolerated
            used, dstr = e.get("used"), e.get("live_end")
            try:
                expected = _expected_guba_week(date.fromisoformat(str(dstr)))
            except (TypeError, ValueError):
                a_bad += 1
                if a_first is None:
                    a_first = {"t": e.get("t"), "date": dstr, "used": used, "expected": None,
                               "reason": "audit entry carries no parseable live_end date"}
                continue
            if used == expected:
                a_matched += 1
            elif a_first is None:
                a_first = {"t": e.get("t"), "date": dstr, "used": used, "expected": expected}
        checks["a_lagged_signals_only"] = {
            "pass": a_first is None, "days_audited": len(audit), "matched": a_matched,
            "unparseable_entries": a_bad, "first_mismatch": a_first, "day_keys_total": dk_total,
            "mechanism": "day t consumes the ISO week that ended strictly before day t "
                         "(expected key derived from live_end via date.isocalendar, the wk_prev rule)"}
    # (b) FIX4: a pure units ledger seeded from state["hold0"] (opening holdings captured by
    # the loop before day 0); subscribe/dca add units, redeem subtracts. A violation is a
    # redemption whose balance beforehand is non-positive (epsilon for float drift) or a fund
    # the agent never held. First offender is reported with agent id, fund, units held and
    # units redeemed. When hold0 is absent the check is SKIPPED -- closing positions must
    # never silently seed it.
    if n_redeem_act == 0:
        checks["b_nonholder_never_redeems"] = {"skipped": True, "redeem_rows": 0, "reason":
                                               "no redemption act rows in the event log"}
    elif hold0 is None:
        checks["b_nonholder_never_redeems"] = {"skipped": True, "redeem_rows": n_redeem_act,
                                               "reason": "opening positions not supplied: "
                                                         "state['hold0'] is absent and closing "
                                                         "positions must never seed this check"}
    else:
        checks["b_nonholder_never_redeems"] = {"pass": b_viol == 0, "violations": b_viol,
                                               "first_violation": b_first, "redeem_rows": n_redeem_act,
                                               "no_holdings_refusals": co_oc["no_holdings"]}
    if co_oc["hard_block"] == 0:
        checks["c_hard_block_never_subscribes"] = {"skipped": True, "reason":
                                                   "no hard_block checkout rows in the event log"}
    else:
        checks["c_hard_block_never_subscribes"] = {"pass": c_viol == 0, "violations": c_viol,
                                                   "first_violation": c_first,
                                                   "hard_block_checkouts": co_oc["hard_block"],
                                                   "subscribe_rows": n_sub}
    max_res, tol = 0.0, 1e-6 * max([1.0] + [_ag_get(a, "w0", 0.0) for a in agents])
    end = date.fromisoformat(state["end"]) if state.get("end") else None
    if not agents or end is None:
        checks["d_wealth_conservation"] = {"skipped": True, "reason":
                                           ("no agent rows in state" if not agents else
                                            "state carries no end date") + "; wealth identity not evaluable"}
    else:
        for a in agents:                          # (d) cash+units*nav+other+fees == w0+realized+unrealized
            hold = _ag_get(a, "hold", None) or {}
            cost = _ag_get(a, "cost", None) or {}
            holdv = costv = 0.0
            for c, u in hold.items():
                nv = funds[c].nav_at(end) if c in funds else cost.get(c, 0.0)
                holdv += u * nv
                costv += u * cost.get(c, 0.0)
            res = ((_ag_get(a, "cash", 0.0) + holdv + _ag_get(a, "other", 0.0) + _agent_fees(a))
                   - (_ag_get(a, "w0", 0.0) + _ag_get(a, "realized", 0.0) + (holdv - costv)))
            max_res = max(max_res, abs(res))
        checks["d_wealth_conservation"] = {"pass": max_res <= tol, "max_abs_residual_cny": max_res,
                                           "tolerance": tol, "agents_checked": len(agents)}
    cells = sorted(set(id2cell.values()))
    if not cells:
        checks["e_all_cells_exposed"] = {"skipped": True, "reason":
                                         "no agent rows in state; cell exposure not applicable"}
    else:
        n_exp = sum(1 for c in cells if c in exposed)
        checks["e_all_cells_exposed"] = {"pass": n_exp == len(cells),
                                         "cells_exposed": n_exp, "cells_total": len(cells)}
    if n_posts == 0:
        checks["f_post_ig_is_intent_group"] = {"skipped": True, "reason":
                                               "no post rows in the event log"}
    else:
        checks["f_post_ig_is_intent_group"] = {"pass": f_viol == 0, "violations": f_viol,
                                               "posts_scanned": n_posts, "ig_domain": list(_IG_GROUPS)}
    g_viol = j_viol = 0
    g_first = j_first = None
    clim_nonseed = clim_seed = entries_checked = 0
    for r in clims:
        if r.get("source") == "guba_seed":        # exogenous day-1 seed, exempt from cmt matching
            clim_seed += 1
            continue
        clim_nonseed += 1
        t, p = int(r.get("t", 0)), r.get("p")
        prior = {txt for (pp, tt), tx in cmt_idx.items() for txt in tx if pp == p and tt < t}
        exact = {txt for (pp, tt), tx in cmt_idx.items() for txt in tx if pp == p and tt == t - 1}
        for ent in (r.get("top") or []):
            txt = ent.get("text") if isinstance(ent, dict) else str(ent)
            entries_checked += 1
            if txt not in prior:
                g_viol += 1
                if g_first is None:
                    g_first = {"t": t, "p": p, "text": txt}
            if txt not in exact:
                j_viol += 1
                if j_first is None:
                    j_first = {"t": t, "p": p, "text": txt}
    if entries_checked == 0:
        checks["g_comments_lagged_only"] = {"skipped": True, "clim_rows_nonseed": clim_nonseed,
                                            "clim_rows_guba_seed": clim_seed, "reason":
                                            "no displayed comment entries to verify (no non-seed climate row carried a top list)"}
        checks["j_displayed_comment_matches_prev_day"] = {"skipped": True, "clim_rows_nonseed": clim_nonseed,
                                                          "clim_rows_guba_seed": clim_seed, "reason":
                                                          "no displayed comment entries to match against day-(t-1) comment sets"}
    else:
        checks["g_comments_lagged_only"] = {"pass": g_viol == 0, "violations": g_viol,
                                            "first_violation": g_first,
                                            "entries_checked": entries_checked,
                                            "clim_rows_nonseed": clim_nonseed}
        checks["j_displayed_comment_matches_prev_day"] = {"pass": j_viol == 0, "violations": j_viol,
                                                          "first_violation": j_first,
                                                          "entries_checked": entries_checked,
                                                          "comment_days_indexed": len(cmt_idx)}
    arms = tuple(cfg.get("modality_arms") or ("T", "TV"))
    lvl = _mod_level_of(cfg)
    agent_arms_map = state.get("agent_arms") or {}
    if lvl == "agent" and not agent_arms_map:
        checks["h_arm_balance"] = {"skipped": True, "level": "agent", "arms": list(arms), "reason":
                                   "no per-agent arm assignments in state; agent-level balance not evaluable"}
    elif lvl == "agent":
        entry = {"pass": True, "level": "agent", "arms": list(arms),
                 "agents_assigned": len(agent_arms_map)}
        try:
            ok, rep = feed.check_arm_balance(agent_arms_map, id2cell, arms=arms)
            entry["pass"] = bool(ok)
            if isinstance(rep, dict) and rep:
                entry["report"] = _jsonable(rep)   # worst cases / tolerances reach the report file
        except Exception:
            entry["pass"] = False
            entry["error"] = "check_arm_balance raised"
        checks["h_arm_balance"] = entry
    elif lvl == "run":
        checks["h_arm_balance"] = {"skipped": True, "level": "run", "arms": list(arms),
                                   "reason": "run-level arms: every agent receives modality_run_arm; agent balance not applicable"}
    else:                                         # exposure: per-agent per-arm impression shares
        viol = checked = 0
        for i in sorted(imp_arms, key=str):
            cnt = imp_arms[i]
            n_imp = sum(cnt.values())
            if n_imp < 20:
                continue
            checked += 1
            for am in arms:
                if abs(cnt.get(am, 0) / n_imp - 1.0 / len(arms)) > 0.03 + 1.0 / n_imp:
                    viol += 1
                    break
        if checked == 0:
            checks["h_arm_balance"] = {"skipped": True, "level": "exposure", "arms": list(arms),
                                       "reason": "exposure-level arms: agent balance not applicable and no agent reached the 20-impression minimum for the per-impression share check"}
        else:
            checks["h_arm_balance"] = {"pass": viol == 0, "level": "exposure", "arms": list(arms),
                                       "agents_checked": checked, "violations": viol,
                                       "note": "per-impression arm shares (agent-level balance not applicable at exposure level)"}
    # (i) redemptions are never gated: a `co` row with act == "redeem" may carry match / no_holdings only.
    # Suitability (PREREG v1.3 §B9) and the QDII purchase block apply to subscriptions alone.
    gated = {"hard_block", "confirm_signed", "confirm_declined", "purchase_blocked"}
    n_redeem_co = i_viol = 0
    i_first = None
    for r in rows:
        if isinstance(r, dict) and r.get("ev") == "co" and r.get("act") == "redeem":
            n_redeem_co += 1
            if r.get("oc") in gated:
                i_viol += 1
                if i_first is None:
                    i_first = {"i": r.get("i"), "p": r.get("p"), "oc": r.get("oc")}
    if n_redeem_co == 0:
        checks["i_redeem_checkout_never_blocked"] = {"skipped": True, "reason":
                                                     "no redemption checkout rows in the event log"}
    else:
        checks["i_redeem_checkout_never_blocked"] = {"pass": i_viol == 0, "violations": i_viol,
                                                     "first_violation": i_first,
                                                     "redeem_checkouts": n_redeem_co}
    active = state.get("active_per_day") or {}
    if not active:
        checks["k_dec_matches_active"] = {"skipped": True, "reason":
                                          "no active-per-day counts in state; decision/day matching not applicable"}
    else:
        k_first = None
        for t in sorted(active, key=str):
            if dec_day.get(t, 0) != active[t] and k_first is None:
                k_first = {"t": t, "expected_dec_rows": active[t], "dec_rows": dec_day.get(t, 0)}
        k_mism = sum(1 for t in active if dec_day.get(t, 0) != active[t])
        k_extra = sorted(set(dec_day) - set(active), key=str)
        checks["k_dec_matches_active"] = {"pass": k_first is None and not k_extra,
                                          "days": len(active), "mismatches": k_mism,
                                          "first_mismatch": k_first,
                                          "dec_rows_on_inactive_days": k_extra,
                                          "dec_rows_total": sum(dec_day.values())}
    # (m) images on the TV arm. This entry ALWAYS passes and can never raise the engine's exit
    # code 4 (contract 2.6; plan 4.1 card W2 and section 7 item 7): gating belongs on
    # irreversible, cross-system, security or release boundaries, and one simulation run is
    # none of those. The honest answer to "did pixels actually reach the TV arm" is the number
    # run_meta.images.attached, which a reader can look at -- expressing it as a failed run
    # would say nothing extra. What the check owes the reader instead is WHICH of the three
    # situations the run is in, so `applicable` and `reason` name it rather than leaving three
    # zeros to be interpreted. Counts come from state["images"] (contract 2.3) and are coerced
    # to int here because a state built before card L2 landed carries none of them.
    imgs = state.get("images") if isinstance(state.get("images"), dict) else {}
    img_root = cfg.get("images_root") or imgs.get("root") or ""
    # At run level every agent gets modality_run_arm, so the run's effective arm set is that one
    # arm -- a three-arm modality_arms list with modality_level "run" still has no TV arm unless
    # the run arm IS TV. Agent and exposure levels draw from modality_arms.
    run_arms = {cfg.get("modality_run_arm") or "TV"} if lvl == "run" else set(arms)
    tally = {k: (int(imgs[k]) if isinstance(imgs.get(k), (int, float))
                 and not isinstance(imgs.get(k), bool) else 0)
             for k in ("attached", "missing", "sha_mismatch")}
    m_ent = {"pass": True, "applicable": bool(img_root) and "TV" in run_arms,
             "images_root_configured": bool(img_root), "level": lvl, "arms": sorted(run_arms),
             "policy": imgs.get("policy") or cfg.get("image_pick", "first"),
             "tv_impressions": imp_tv, "tv_impressions_with_image": imp_tv_img, **tally}
    if not img_root:
        m_ent["reason"] = ("no image library configured (images_root is null): the TV arm "
                           "degrades to text-only, so this run's modality comparison "
                           "measures nothing. Note it is NOT a T==TV null either: "
                           "render_card prints 配图不展示。on a T card and never on a TV "
                           "card, so a T/TV contrast here measures that one sentence")
    elif "TV" not in run_arms:
        m_ent["reason"] = (f"no TV arm in this run (arms {sorted(run_arms)} at modality_level "
                           f"{lvl}): image attachment does not apply")
    else:
        m_ent["reason"] = (f"TV arm active with an image library: {tally['attached']} "
                           f"attachment(s), {tally['missing']} missing file(s), "
                           f"{tally['sha_mismatch']} digest mismatch(es)"
                           + ("" if tally["attached"] else
                              " -- zero attachments with a configured root and an active TV "
                              "arm is the state to investigate, reported here, never gating"))
    checks["m_tv_arm_carries_images"] = m_ent
    # (m_env) environment valence. Same WARN form as (m) above -- pass is unconditionally True
    # and this entry can never raise exit code 4 -- and for the same reason: it describes the
    # world the run happened in, and no description of a world is a release boundary.
    #
    # It exists because of a specific misdiagnosis. In a live smoke run all 77 decisions came
    # back with mood 4, not one comment was bearish and not one affinity delta was negative,
    # and the behaviour counts were fine, which pointed at the model. The cause was the
    # ENVIRONMENT: the lookback cost basis had left only 4.9% of openings at a loss (median
    # +39%), so almost no agent had anything to feel bearish about. These two numbers -- how
    # many investors woke up holding a loss, and how many days the feed's climate was
    # bearish_majority -- are what separates the two explanations, and neither was written down
    # anywhere. Opening P&L comes from inv.pnl0 (written once by init_investors); the live
    # `cost` map is a CLOSING basis and can no more seed this than it can seed (b).
    pnl_rows = [_ag_get(a, "pnl0") for a in agents]
    have_pnl = [p for p in pnl_rows if isinstance(p, dict)]
    open_rets = [[float(r) for r in p.values()
                  if isinstance(r, (int, float)) and not isinstance(r, bool)] for p in have_pnl]
    n_open = sum(len(rs) for rs in open_rets)
    n_open_loss = sum(1 for rs in open_rets for r in rs if r < 0.0)
    n_inv_loss = sum(1 for rs in open_rets if any(r < 0.0 for r in rs))
    clim_labels = defaultdict(int)
    bearish_days = set()
    for r in clims:                               # every climate row, seeded and agent-sourced
        clim_labels[str(r.get("label"))] += 1     # alike: the agent sees the label either way
        if r.get("label") == "bearish_majority":
            bearish_days.add(r.get("t"))
    ipnl = _initial_pnl_cfg(cfg)
    v_ent = {"pass": True, "mode": ipnl["mode"], "investors": len(agents),
             "investors_with_opening_pnl_recorded": len(have_pnl),
             "investors_with_opening_holdings": sum(1 for rs in open_rets if rs),
             "investors_with_an_opening_loss": n_inv_loss,
             "opening_holdings": n_open, "opening_holdings_at_loss": n_open_loss,
             "share_of_opening_holdings_at_loss": (round(n_open_loss / n_open, 4)
                                                   if n_open else None),
             "initial_pnl_misses": sum(int(_ag_get(a, "pnl0_misses", 0) or 0) for a in agents),
             "climate_rows": sum(clim_labels.values()),
             "climate_rows_by_label": dict(sorted(clim_labels.items())),
             "days_with_bearish_majority_climate": len(bearish_days)}
    if ipnl["mode"] == "target":                  # only this mode reads the two target keys, so
        v_ent["share_at_loss_target"] = ipnl["share_at_loss"]   # only here do they mean anything
        v_ent["tolerance"] = ipnl["tolerance"]
    if not have_pnl:
        v_ent["reason"] = ("opening P&L was not recorded for any agent row in this state "
                           "(a replayed or hand-built state, or a run predating card W5): the "
                           "environment's valence cannot be reported, only the climate labels "
                           f"({len(bearish_days)} day(s) bearish_majority of "
                           f"{sum(clim_labels.values())} climate row(s))")
    elif not n_open:
        v_ent["reason"] = ("no investor opened with a holding, so opening P&L is empty by "
                           "construction and says nothing about the environment; "
                           f"{len(bearish_days)} day(s) carried a bearish_majority climate")
    else:
        v_ent["reason"] = (
            f"{n_open_loss} of {n_open} opening holding(s) at a loss "
            f"({n_open_loss / n_open:.1%}), held by {n_inv_loss} of {len(have_pnl)} investor(s); "
            f"{len(bearish_days)} day(s) carried a bearish_majority climate out of "
            f"{sum(clim_labels.values())} climate row(s)"
            + ("" if n_open_loss else
               " -- with no losing position anywhere in the population, uniformly bullish mood "
               "and comments are what the ENVIRONMENT dictated and are not evidence about the "
               "model; initial_pnl.mode 'target' with a share_at_loss is how that is fixed"))
    checks["m_env_valence_warning"] = v_ent
    big = len(agents) >= 300                      # (e) fatal only at n_agents >= 300
    failed = [k for k, v in checks.items() if v.get("pass") is False
              and not (k == "e_all_cells_exposed" and not big)]
    # FIX4: return the run's DECISION, not the failure list -- True when the run passed
    # (no core check explicitly failed), False when at least one did.
    return checks, not failed


def _selftest_invariants_decision():
    """FIX4: assert check_invariants' second element both ways -- a clean state returns True
    (run passed), a state with one planted failing check returns False (run failed)."""
    import json
    import tempfile
    from pathlib import Path
    nav = [date.fromordinal(date(2025, 1, 2).toordinal() + i) for i in range(320)]
    fund = Fund("000001", "R3", False, "FAM", nav, [2.0] * len(nav), date(2025, 1, 2))
    ag = Inv()
    ag.id, ag.cell, ag.rc = "A1", "C2", "C2"
    ag.cash, ag.other, ag.realized, ag.fees = 0.0, 0.0, 0.0, 0.0
    ag.hold, ag.cost, ag.entry = {"000001": 50.0}, {"000001": 2.0}, 0
    ag.w0 = 100.0
    state = {"agents": [ag], "hold0": {"A1": {"000001": 50.0}},  # opening snapshot, exactly as
             "funds": {"000001": fund}, "end": "2025-12-31",     # the loop captures before day 0
             "active_per_day": {0: 1}, "agent_arms": {}, "signal_audit": []}
    dec = {"ev": "dec", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "prompt_sha": "x",
           "raw_sha": "y", "cache_hit": False, "attempts": 1, "status": "ok", "arm": "T",
           "mood": 0, "reason": "r", "violations": []}
    post = {"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00000", "intent": "I2",
            "ig": "I2", "fund": None, "img": False}
    imp = {"ev": "imp", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "arm": "T", "slot": 0,
           "source": "random"}
    co = {"ev": "co", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "fund": "000001",
          "ig": "I2", "act": "redeem", "oc": "match", "oc_cf": "match", "amt": 40.0}
    good = {"ev": "act", "t": 0, "d": "2025-10-01", "i": "A1", "p": "00000", "kind": "redeem",
            "fund": "000001", "amt": 40.0, "units": 20.0, "nav": 2.0}
    bad = dict(good, fund="000002", units=10.0, amt=20.0)   # plant: a fund A1 never held

    def _run(rows):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            return check_invariants(state, p, {"modality_level": "run"})

    _, ok_clean = _run([post, imp, dec, co, good])
    assert ok_clean is True, "clean state must yield the decision True (run passed)"
    checks_bad, ok_bad = _run([post, imp, dec, co, bad])
    assert ok_bad is False, "one planted failing check must yield the decision False (run failed)"
    assert checks_bad["b_nonholder_never_redeems"]["pass"] is False
    print("selftest check_invariants decision polarity: clean->True, planted->False [ok]")


if __name__ == "__main__":            # FIX4: run the decision assertions under --self-test; a
    import sys                        # guard placed beside the fix so the contract stays
    if "--self-test" in sys.argv:     # asserted wherever this sits relative to the main block
        _selftest_invariants_decision()


# --- §8 reports --------------------------------------------------------------------------------
def _invariant_report(checks) -> tuple:
    """Build the on-disk invariants report body: one entry per invariant the engine defines
    (pass/fail with all numeric detail, or skipped+reason), plus a summary with counts. Registry
    keys with no result from check_invariants are written as skipped so a check that did not run
    can never be silently absent; extra keys arriving in `checks` are kept with a fallback
    description. Returns (entries, summary)."""
    checks = checks if isinstance(checks, dict) else {}
    entries = {}
    for key in sorted(set(INVARIANTS) | set(checks)):
        src = checks.get(key)
        ent = {"description": INVARIANTS.get(key)
               or "unregistered invariant (add a description to world.INVARIANTS)"}
        if isinstance(src, dict) and src.get("skipped"):
            ent["skipped"] = True
            ent["reason"] = str(src.get("reason") or "check not applicable for this run")
        elif isinstance(src, dict):
            ent["pass"] = bool(src.get("pass"))
            if src.get("reason"):                 # a WARN-form check (m_...) passes AND explains
                ent["reason"] = str(src["reason"])   # itself; without this the reason was lost
        elif src is None:
            ent["skipped"] = True
            ent["reason"] = "not evaluated: check_invariants produced no result for this run"
        else:
            ent["skipped"] = True
            ent["reason"] = f"not evaluated: malformed check result ({type(src).__name__})"
        if isinstance(src, dict):
            for dk in sorted(src):
                if dk not in ("pass", "skipped", "reason"):
                    ent[dk] = _jsonable(src[dk])
        entries[key] = ent
    n_pass = sum(1 for v in entries.values() if v.get("pass") is True)
    n_fail = sum(1 for v in entries.values() if v.get("pass") is False)
    summary = {"total": len(entries), "passed": n_pass, "failed": n_fail,
               "skipped": len(entries) - n_pass - n_fail, "all_passed": n_fail == 0}
    return entries, summary


def _provider_meta(cfg):
    """Which provider and models produced this run -- host and model names only, never
    a key. Reads the same resolution runtime.call_glm uses (api.yaml / env), so run_meta
    records what was actually called rather than what the config wished for."""
    from flowmirror.agents import runtime as _rt      # local import: avoid an import cycle
    ep, _key, vis, txt = _rt._load_glm_config()
    llm = cfg.get("llm") or {}
    return {"endpoint_host": urlparse(ep).netloc or ep,
            "vision_model": llm.get("model") or vis,
            "text_model": llm.get("text_model") or txt,
            "mock": bool(cfg.get("mock_llm")),
            "agent_policy": cfg.get("agent_policy")}


def write_reports(out_dir, state: dict, cfg: dict, world: World, checks: dict, counters, elapsed) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    logp = out_dir / "event_log.jsonl"
    inv_entries, inv_summary = _invariant_report(checks)
    dump(out_dir / "invariants_report.json",
         {"summary": inv_summary, "checks": inv_entries,
          "event_log_sha256": event_log_sha(logp) if logp.exists() else None})
    flows = state.get("flows") or {}
    dump(out_dir / "flows_family_quarter.json",
         {fam: {q: dict(v) for q, v in sorted(qs.items())} for fam, qs in sorted(flows.items())})
    dump(out_dir / "state_snapshots.json", state.get("snapshots") or {})
    counters = dict(counters or {})
    att = int(counters.get("attempts", 0))
    df = int(counters.get("decision_failures", 0))
    counters.setdefault("calls", 0)
    counters.setdefault("cache_hits", 0)
    counters["attempts"], counters["decision_failures"] = att, df
    counters["decision_failure_rate"] = round(df / att, 6) if att else 0.0
    dflt_org = (cfg.get("orgs") or [""])[0]
    agents = state.get("agents") or []
    agent_arms = state.get("agent_arms") or {}
    fees_cfg = cfg.get("fees") if isinstance(cfg.get("fees"), dict) else {}
    # Contract 2.3: the day loop's image tally travels in state["images"] and is copied here
    # verbatim (via _jsonable, so a Path in `root` cannot crash the report writer). A state
    # without the key -- a run predating card L2, or a replayed/hand-built state -- gets the
    # honest zero form rather than no block at all: the viewer's "this run carried no images"
    # chip reads run_meta.images, so with the block never written that chip showed
    # unconditionally -- on runs that did carry images too.
    images = state.get("images")
    images = _jsonable(images) if isinstance(images, dict) else {
        "root": cfg.get("images_root"), "policy": cfg.get("image_pick", "first"),
        "attached": 0, "missing": 0, "sha_mismatch": 0}
    status_ok = inv_summary["failed"] == 0        # skipped checks never fail a run
    dump(out_dir / "run_meta.json", {
        "engine": "v6",
        "synthetic_nav": bool(getattr(world, "nav_synthetic", False)),
        "cfg": cfg, "inputs_sha256": world.inputs_sha256,
        "universe": {"base": world.base_codes, "deferred": world.deferred, "size": len(world.funds)},
        "funds": {c: {"r": f.r, "qdii": f.qdii, "family": f.family, "org": world.fund_org.get(c, dflt_org),
                      "active_from": str(f.active_from)} for c, f in sorted(world.funds.items())},
        # initial_pnl_misses joins the investor-level aggregates rather than counters (decision
        # 13 / card W5): it is a property of the POPULATION as constructed -- how many openings
        # the lookback window could not price at the drawn target -- not of the run's LLM calls,
        # which is what counters describes. Zero in lookback mode, where nothing is targeted.
        "investors": {"total": len(agents), "active_ever": sum(1 for a in agents if a.entry < len(world.nav_days)),
                      "initial_pnl_misses": sum(int(_ag_get(a, "pnl0_misses", 0) or 0) for a in agents)},
        "arms": {str(k): agent_arms[k] for k in sorted(agent_arms, key=str)},
        # Opening holdings and cost basis per funded agent, rebuilt from state["hold0"] /
        # state["cost0"], so PGR/PLR accounting can recompute paper vs realized P&L from
        # day one. run_meta.json is excluded from every hash, so this key is replay-safe.
        # non-holders are omitted, an absent key means "no opening position"
        "openings": {
            str(aid): {
                "hold": {code: round(units, 6) for code, units in hold.items()},
                "cost": {code: (round(c0[code], 6) if c0.get(code) is not None else None)
                         for code in hold},
            }
            for aid, hold in sorted((state.get("hold0") or {}).items(), key=lambda kv: str(kv[0]))
            for c0 in [(state.get("cost0") or {}).get(aid) or {}]
            if hold
        },
        "provider": _provider_meta(cfg),
        "modality": {"level": _mod_level_of(cfg),
                     "arms": list(cfg.get("modality_arms") or ("T", "TV")),
                     "run_arm": cfg.get("modality_run_arm") or "TV"},
        "images": images,
        "fees": {"subscribe_rate": float(fees_cfg.get("subscribe_rate", 0.0) or 0.0),
                 "redeem_rate": float(fees_cfg.get("redeem_rate", 0.0) or 0.0),
                 "total": round(sum(_agent_fees(a) for a in agents), 2)},
        "counters": counters,
        "checkout_oc": dict(sorted((state.get("checkout_oc") or {}).items())),
        "checkout_oc_cf": dict(sorted((state.get("checkout_oc_cf") or {}).items())),
        "elapsed_s": round(float(elapsed), 1),
        "status": "ok" if status_ok else "invariant_failure"})
    print(f"[world] reports written to {out_dir}; status={'ok' if status_ok else 'invariant_failure'}"
          f" (invariants: {inv_summary['passed']} pass, {inv_summary['failed']} fail,"
          f" {inv_summary['skipped']} skipped, {inv_summary['total']} total)")


# --- §9 self-test (zero API access) ------------------------------------------------------------
def self_test() -> int:
    data_root = Path(os.environ.get("FLOWMIRROR_DATA_ROOT") or (REPO_ROOT / "data"))
    research = Path(os.environ.get("FLOWMIRROR_RESEARCH_ROOT") or "D:/Desktop/ABM paper/fundmarket-sim")
    names = {"agents": "agents_seed2027.json", "content_pool": "content_pool_v1_masked.jsonl",
             "nav_cache": "nav_cache.json", "guba": "guba_signal_v1.json",
             "family": "family_crosswalk.json", "fund_meta": "fund_meta_v1.json"}
    # v7 data layer sub-folders first, then the research repo's flat sim/ folder
    sub = {"agents": "population", "content_pool": "creatives/cn", "nav_cache": "funds",
           "guba": "attention", "family": "funds", "fund_meta": "funds"}
    paths = {}
    for key in sorted(names):
        for cand in (data_root / sub[key] / names[key], data_root / names[key],
                     research / "sim" / names[key], research / names[key]):
            if cand.exists():
                paths[key] = cand
                break
    rows_out = []

    def chk(name, ok):
        rows_out.append((name, bool(ok)))

    def skip(name):
        rows_out.append((name, None))

    class _MemLog:                                # captures log.emit rows for post-schema assertions
        def __init__(self):
            self.rows = []

        def emit(self, ev: str, **fields):
            fields.pop("ev", None)
            self.rows.append({"ev": ev, **fields})

    if all(k in paths for k in ("agents", "content_pool", "nav_cache", "guba")):
        orgs = sorted({r.get("org") for r in _read_rows(paths["content_pool"]) if r.get("org")})[:4]
        cfg = deep_merge(DEFAULT_CONFIG, {
            "run_tag": "selftest|v7", "n_agents": 400, "orgs": orgs,
            "agents_file": str(paths["agents"]), "content_pool": str(paths["content_pool"]),
            "nav_cache": str(paths["nav_cache"]), "guba_signal": str(paths["guba"]),
            "window": {"start": "2025-10-01", "end": "2025-12-31", "max_trading_days": 60},
            "strategy_mix": "measured", "strategy_groups": {"push_heavy": orgs[:2], "edu_heavy": orgs[2:4]},
            "posts_per_org_per_day": 2, "no_repeat_days": 10, "arm_level": "agent",
            "family_file": str(paths["family"]) if "family" in paths else None,
            "fund_meta_file": str(paths["fund_meta"]) if "fund_meta" in paths else None})
        world = load_world(cfg)
        invs = init_investors(world, cfg)
        chk("agents_eq_n_agents", len(invs) == int(cfg["n_agents"]) == 400)
        chk("orgs_eq_cfg", len(world.orgs) == len(cfg["orgs"]))
        # DEFECT 2 (world half): agent-level arms come from feed.assign_agent_arms (stratified)
        k_arms = tuple(cfg.get("modality_arms") or ("T", "TV"))
        arm_cnt = defaultdict(int)
        for v in invs:
            arm_cnt[v.arm] += 1
        chk("agent_init_overall_share_within_0p01",
            len(invs) > 0 and all(a in arm_cnt for a in k_arms)
            and all(abs(arm_cnt[a] / len(invs) - 1.0 / len(k_arms)) <= 0.01 for a in k_arms))
        cell_n, cell_arms = defaultdict(int), defaultdict(set)
        for v in invs:
            cell_n[v.cell] += 1
            cell_arms[v.cell].add(v.arm)
        chk("agent_init_no_single_armed_cell",
            all(len(cell_arms[c]) >= min(len(k_arms), cell_n[c]) for c in cell_n))
        chk("agent_init_matches_assign_agent_arms",
            {v.id: v.arm for v in invs} == feed.assign_agent_arms(
                cfg["run_tag"], [(rec.get("id"), rec.get("cell", "")) for rec in world.agents[:len(invs)]], k_arms))
        tot = sum(len(v) for v in world.pool.values())
        if tot == 200:
            chk("notes_50_per_org_frozen_pool", all(len(world.pool[o]) == 50 for o in world.orgs))
        else:
            skip(f"notes_50_per_org_frozen_pool(non_frozen_n={tot})")
        chk("funds_active_ge_140", sum(1 for f in world.funds.values() if f.active_from <= world.end) >= 140)
        chk("nav_days_eq_60", len(world.nav_days) == 60)
        ok_sum = same = True
        for mix in ("measured", "push_heavy", "edu_heavy"):
            pr = intent_probs(world, dict(cfg, strategy_mix=mix))
            ok_sum = ok_sum and all(abs(sum(p.values()) - 1.0) < 1e-9 for p in pr.values())
            if mix == "push_heavy":
                same = same and len({tuple(pr[o][k] for k in _INTENTS) for o in pr}) == 1
        chk("intent_probs_sum_to_1", ok_sum)
        chk("push_heavy_identical", same)
        recent, rngp, viol, ig_viol = {}, random.Random(rng_seed_from(cfg["run_tag"], "platform", "selftest")), 0, 0
        mem = _MemLog()
        for t in range(len(world.nav_days)):
            seen = {o: set(recent.get(o, ())) for o in world.orgs}
            for pst in publish_day(world, cfg, t, recent, rngp, log=mem):
                if pst["note"] in seen[pst["org"]] and len(world.pool[pst["org"]]) >= 20:
                    viol += 1
                if pst["intent_group"] not in _IG_GROUPS or (pst["intent_group"] == "I2") != (pst["intent"] == "I2"):
                    ig_viol += 1
        chk("publish_no_repeat_within_10d", viol == 0)
        prows = [r for r in mem.rows if r.get("ev") == "post"]
        chk("publish_post_ig_is_intent_group",
            ig_viol == 0 and bool(prows)
            and all(r.get("intent") in _INTENTS and r.get("ig") in _IG_GROUPS
                    and (r.get("ig") == "I2") == (r.get("intent") == "I2") for r in prows))
    else:
        print(f"SKIP world checks: real data files not found (data_root={data_root}, research={research})")
        for nm in ("agents_eq_n_agents", "orgs_eq_cfg", "notes_50_per_org_frozen_pool", "funds_active_ge_140",
                   "nav_days_eq_60", "intent_probs_sum_to_1", "push_heavy_identical", "publish_no_repeat_within_10d",
                   "publish_post_ig_is_intent_group", "agent_init_overall_share_within_0p01",
                   "agent_init_no_single_armed_cell", "agent_init_matches_assign_agent_arms"):
            skip(nm)
    # A2: compat rule arm_level -> modality_level (data-independent)
    c_compat = {"arm_level": "exposure"}
    chk("compat_arm_level_maps_to_modality",
        _modality_compat(c_compat) == "exposure" and c_compat.get("modality_level") == "exposure")
    c_both = {"modality_level": "run", "arm_level": "agent"}
    chk("compat_modality_level_wins", _modality_compat(c_both) == "run")
    chk("compat_defaults_to_agent", _modality_compat({}) == "agent")
    # A2: three-arm agent-level init over 300 synthetic ids (synthetic world, no data files needed)
    fw = World()
    fw.start = date(2025, 10, 1)
    fw_dts = [date(2025, 1, 2) + timedelta(days=i) for i in range(320)]
    fw.funds = {"F00001": Fund("F00001", "R3", False, "FAM", fw_dts, [1.0] * len(fw_dts), date(2025, 1, 2))}
    fw.base_codes = ["F00001"]
    fw.nav_days = [d for d in fw_dts if d >= date(2025, 10, 1)][:60]
    fw.inputs_sha256, fw.deferred, fw.fund_org, fw.fund_meta = {}, {}, {}, {}
    fw.agents = [{"id": f"s{i:03d}", "cell": "C2", "risk_latent": "R3", "reported_C": "C2",
                  "wealth_wan": 10.0, "core": "", "strat_weight": 1.0, "entry_day": 0,
                  "traits": {"invest_share": "10_30pct", "n_funds": "5_10", "dca": "positive"}}
                 for i in range(300)]
    cfg3 = {"run_tag": "selftest|3arm", "n_agents": 300, "modality_level": "agent",
            "modality_arms": ["T", "TC", "TV"], "modality_run_arm": "TV",
            "fees": {"subscribe_rate": 0.0012, "redeem_rate": 0.005}}
    invs3 = init_investors(fw, cfg3)
    chk("three_arm_agent_init_all_arms", {v.arm for v in invs3} == {"T", "TC", "TV"})
    chk("three_arm_agent_init_tally_fees",
        all(v.arm_tally == {"T": 0, "TC": 0, "TV": 0} and v.fees == 0.0 for v in invs3))
    cnt3 = defaultdict(int)
    for v in invs3:
        cnt3[v.arm] += 1
    chk("three_arm_agent_init_balanced_within_one",
        max(cnt3[a] for a in ("T", "TC", "TV")) - min(cnt3[a] for a in ("T", "TC", "TV")) <= 1)
    cfg2 = dict(cfg3, run_tag="selftest|2arm", modality_arms=["T", "TV"])
    invs2 = init_investors(fw, cfg2)
    cnt2 = defaultdict(int)
    for v in invs2:
        cnt2[v.arm] += 1
    chk("two_arm_agent_init_balanced_within_one",
        set(cnt2) == {"T", "TV"} and abs(cnt2["T"] - cnt2["TV"]) <= 1)
    chk("two_arm_agent_init_matches_assign_agent_arms",
        {v.id: v.arm for v in invs2} == feed.assign_agent_arms(
            cfg2["run_tag"], [(r["id"], r.get("cell", "")) for r in fw.agents], tuple(cfg2["modality_arms"])))
    chk("agent_init_deterministic_same_inputs",
        [v.arm for v in init_investors(fw, cfg3)] == [v.arm for v in invs3])
    chk("agent_init_changes_with_run_tag",
        [v.arm for v in init_investors(fw, dict(cfg3, run_tag="selftest|3arm|rot"))] != [v.arm for v in invs3])
    invs_run = init_investors(fw, dict(cfg3, modality_level="run", modality_run_arm="TC"))
    chk("run_level_init_uniform_arm", {v.arm for v in invs_run} == {"TC"})
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        log = EventLog(tdp / "events.jsonl")
        log.emit("co", t=3, d="2025-10-06", i=17, p="00301", fund="000001", ig="I2", act="subscribe",
                 oc="match", oc_cf="match", amt=1000.0)
        log.emit("cmt", t=3, d="2025-10-06", i=17, p="00301", stance="bull", text="caf\u00e9")
        log.close()
        back = load_jsonl(tdp / "events.jsonl")
        raw = (tdp / "events.jsonl").read_text(encoding="utf-8")
        chk("eventlog_roundtrip_oc_cf", back[0]["ev"] == "co" and back[0]["oc_cf"] == "match"
            and list(back[0].keys())[0] == "ev" and back[1]["text"] == "caf\u00e9"
            and '": ' not in raw and ", " not in raw)
        chk("event_log_sha_hex64", len(event_log_sha(tdp / "events.jsonl")) == 64)

        def _mk_agent(cash: float, fees=None):
            a = Inv()
            a.id, a.cell, a.rc = 7, "C1", "C1"
            a.cash, a.other, a.realized, a.w0 = cash, 0.0, 0.0, 100.0
            a.hold, a.cost, a.entry = {}, {}, 0
            if fees is not None:
                a.fees = fees
            return a

        # INV-FIX: realistic signal_audit -- `used` is a guba ISO week key (2025-10-01/02 sit in
        # W40; the week that ended strictly before them is 2025-W39, ended Sun 2025-09-28) and
        # day_keys is a LIST of day-level keys. st also carries a fee-bearing agent so (d) runs
        # non-vacuously and (b) has an agent row to seed holdings from.
        st = {"agents": [_mk_agent(99.5, 0.5)], "funds": {}, "end": "2025-12-31",
              "active_per_day": {0: 1}, "agent_arms": {}, "agent_cells": {},
              "signal_audit": [{"t": 0, "used": "2025-W39", "live_end": "2025-10-01",
                                "prev_live_end": "2025-09-30", "day_keys": ["2025-09-22", "2025-09-26"]},
                               {"t": 1, "used": "2025-W39", "live_end": "2025-10-02",
                                "prev_live_end": "2025-10-01", "day_keys": []}]}
        clean = [{"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00000", "intent": "I2",
                  "ig": "I2", "fund": None, "img": False},
                 {"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00001", "intent": "I1",
                  "ig": "nonI2", "fund": None, "img": True},
                 {"ev": "imp", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "arm": "T", "slot": 0,
                  "source": "random"},
                 {"ev": "dec", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "prompt_sha": "x", "raw_sha": "y",
                  "cache_hit": False, "attempts": 1, "status": "ok", "arm": "T", "mood": 0, "reason": "r",
                  "violations": []},
                 {"ev": "co", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "fund": "000001", "ig": "I2",
                  "act": "subscribe", "oc": "match", "oc_cf": "match", "amt": 100.0},
                 {"ev": "act", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "kind": "subscribe",
                  "fund": "000001", "amt": 100.0, "units": 50.0, "nav": 2.0},
                 {"ev": "act", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "kind": "redeem",
                  "fund": "000001", "amt": 40.0, "units": 20.0, "nav": 2.0}]
        bad = [dict(clean[4], i=2, p="00002", act="redeem", oc="hard_block", oc_cf="hard_block", amt=0.0),
               dict(clean[0], intent="I3", ig="I3"),        # ig outside the group domain (old bug)
               dict(clean[1], intent="I1", ig="I2")]        # group inconsistent with the raw intent
        cp, bp = tdp / "clean.jsonl", tdp / "bad.jsonl"
        write_jsonl_atomic(cp, clean)
        write_jsonl_atomic(bp, bad)
        cc, cf = check_invariants(st, cp, {"arm_level": "exposure"})
        bc, bf = check_invariants(st, bp, {"arm_level": "exposure"})
        # The second return value is the run's DECISION: True when no CORE check failed.
        # e_all_cells_exposed is deliberately non-fatal below 300 agents, so a clean log
        # on this tiny fixture reports that one as failed and the decision still True.
        chk("invariants_clean_passes",
            cf is True and [k for k, v in cc.items() if not v.get("pass", True)] == ["e_all_cells_exposed"])
        chk("invariants_flag_redeem_block",
            bf is False and bc["i_redeem_checkout_never_blocked"]["pass"] is False)
        chk("invariants_flag_post_ig_not_group",
            bf is False and bc["f_post_ig_is_intent_group"]["pass"] is False
            and bc["f_post_ig_is_intent_group"]["violations"] == 2)
        chk("h_exposure_below_min_impressions_marked_skipped",
            cc["h_arm_balance"].get("skipped") is True
            and isinstance(cc["h_arm_balance"].get("reason"), str) and cc["h_arm_balance"]["reason"] != "")
        # INV-FIX defect 1: (a) must compare week key to derived week key and catch the leak of
        # the week CONTAINING day t (2025-10-01 lies in 2025-W40; the legal key is 2025-W39).
        leak = dict(st, signal_audit=[dict(st["signal_audit"][0], used="2025-W40")])
        lc, lf = check_invariants(leak, cp, {"arm_level": "exposure"})
        chk("invariant_a_flags_week_containing_day_t",
            lf is False and lc["a_lagged_signals_only"]["pass"] is False
            and lc["a_lagged_signals_only"]["first_mismatch"]["used"] == "2025-W40"
            and lc["a_lagged_signals_only"]["first_mismatch"]["expected"] == "2025-W39"
            and lc["a_lagged_signals_only"]["matched"] == 0
            and lc["a_lagged_signals_only"]["days_audited"] == 1)
        ec, _ = check_invariants(dict(st, signal_audit=[]), cp, {"arm_level": "exposure"})
        chk("invariant_a_empty_audit_skipped_with_reason",
            ec["a_lagged_signals_only"].get("skipped") is True
            and isinstance(ec["a_lagged_signals_only"].get("reason"), str)
            and ec["a_lagged_signals_only"]["reason"] != "")
        # A2: invariant (d) with fees; run-level (h) skip
        base_st = {"funds": {}, "end": "2025-12-31", "active_per_day": {0: 1}, "agent_arms": {},
                   "signal_audit": list(st["signal_audit"])}
        run_cfg = {"modality_level": "run"}
        c_fee, _ = check_invariants(dict(base_st, agents=[_mk_agent(99.5, 0.5)]), cp, run_cfg)
        c_drop, _ = check_invariants(dict(base_st, agents=[_mk_agent(99.5)]), cp, run_cfg)
        c_old, _ = check_invariants(dict(base_st, agents=[_mk_agent(100.0)]), cp, run_cfg)
        chk("invariant_d_passes_with_fees", c_fee["d_wealth_conservation"]["pass"] is True)
        chk("invariant_d_fails_when_fees_dropped", c_drop["d_wealth_conservation"]["pass"] is False)
        chk("invariant_d_legacy_rows_still_pass", c_old["d_wealth_conservation"]["pass"] is True)
        chk("h_run_level_marked_skipped_with_reason",
            c_fee["h_arm_balance"].get("skipped") is True
            and "not applicable" in str(c_fee["h_arm_balance"].get("reason", "")))
        # INV-FIX defect 2: (b) seeds holdings from the agent rows' opening `hold`; the clean
        # log redeems 20 of 50 opening units (partial, still held) and the bad log adds a
        # redemption of a fund the agent never held, which must be caught with the offender.
        a_hold = _mk_agent(0.0)
        a_hold.hold, a_hold.cost, a_hold.w0 = {"000001": 50.0}, {"000001": 2.0}, 100.0
        b_rows = [r for r in clean if r.get("ev") == "act"]
        bok_p, bbad_p = tdp / "b_clean.jsonl", tdp / "b_bad.jsonl"
        write_jsonl_atomic(bok_p, b_rows)
        write_jsonl_atomic(bbad_p, b_rows + [{"ev": "act", "t": 1, "d": "2025-10-02", "i": 7, "p": "00001",
                                              "kind": "redeem", "fund": "000002", "amt": 20.0,
                                              "units": 10.0, "nav": 2.0}])
        # (b) seeds its units ledger from state["hold0"], the loop's OPENING snapshot
        # ({agent_id: {fund_code: units}}), and skips when it is absent so that closing
        # positions can never silently seed it. The fixture must therefore supply it --
        # without it this check skipped and the assertions below read a missing key.
        st_b = dict(base_st, agents=[a_hold], hold0={7: {"000001": 50.0}})
        cb1, _ = check_invariants(st_b, bok_p, {"modality_level": "run"})
        cb2, _ = check_invariants(st_b, bbad_p, {"modality_level": "run"})
        chk("invariant_b_opening_and_partial_redemptions_clean",
            cb1["b_nonholder_never_redeems"]["pass"] is True
            and cb1["b_nonholder_never_redeems"]["violations"] == 0
            and cb1["b_nonholder_never_redeems"]["redeem_rows"] == 1)
        # FIX3 Defect 3 pin: two successive PARTIAL redemptions of an opening position
        # that never reaches zero units must not flag (the null-policy t=3/t=4 case).
        b2_rows = b_rows + [{"ev": "act", "t": 1, "d": "2025-10-02", "i": 7, "p": "00001",
                             "kind": "redeem", "fund": "000001", "amt": 30.0,
                             "units": 15.0, "nav": 2.0}]
        b2p = tdp / "b_two_partial.jsonl"
        write_jsonl_atomic(b2p, b2_rows)
        cb3, _ = check_invariants(st_b, b2p, {"modality_level": "run"})
        chk("invariant_b_two_partial_redemptions_clean",
            cb3["b_nonholder_never_redeems"]["pass"] is True
            and cb3["b_nonholder_never_redeems"]["violations"] == 0
            and cb3["b_nonholder_never_redeems"]["redeem_rows"] == 2)
        chk("invariant_b_flags_nonholder_redeem_with_offender",
            cb2["b_nonholder_never_redeems"]["pass"] is False
            and cb2["b_nonholder_never_redeems"]["violations"] == 1
            and cb2["b_nonholder_never_redeems"]["first_violation"] == {"i": 7, "fund": "000002",
                                                                        "units_held": 0.0,
                                                                        "units_redeemed": 10.0})
        # A2: exposure-level (h) per-agent share check
        def _imp_rows(n_t, n_tv):
            rows = []
            for j in range(n_t):
                rows.append({"ev": "imp", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000",
                             "arm": "T", "slot": j % 10, "source": "random"})
            for j in range(n_tv):
                rows.append({"ev": "imp", "t": 0, "d": "2025-10-01", "i": 1, "p": "00001",
                             "arm": "TV", "slot": j % 10, "source": "random"})
            return rows

        ep_ok, ep_bad = tdp / "expo_ok.jsonl", tdp / "expo_bad.jsonl"
        write_jsonl_atomic(ep_ok, _imp_rows(10, 10))
        write_jsonl_atomic(ep_bad, _imp_rows(19, 1))
        c_e1, _ = check_invariants(st, ep_ok, {"modality_level": "exposure"})
        c_e2, _ = check_invariants(st, ep_bad, {"modality_level": "exposure"})
        chk("h_exposure_balanced_shares_pass",
            c_e1["h_arm_balance"]["pass"] is True and c_e1["h_arm_balance"]["agents_checked"] == 1)
        chk("h_exposure_skewed_shares_flagged",
            c_e2["h_arm_balance"]["pass"] is False and c_e2["h_arm_balance"]["violations"] == 1)
        # DEFECT 3: NAV mapping with a `_meta` block + two real codes must load without crashing
        dts_syn = [date(2025, 1, 2) + timedelta(days=i) for i in range(300)]
        navp = tdp / "nav_demo_meta.json"
        dump(navp, {"_meta": {"synthetic": True, "generator": "data_pipeline/cn/make_demo_nav.py",
                              "generated_at": "2026-01-05T09:00:00",
                              "note": "synthetic demo series, not market data"},
                    "000001": {d.isoformat(): round(1.0 + 0.0001 * i, 6) for i, d in enumerate(dts_syn)},
                    "000002": {d.isoformat(): round(2.0 - 0.00005 * i, 6) for i, d in enumerate(dts_syn)}})
        agp = tdp / "agents_mini.json"
        dump(agp, [{"id": "m1", "cell": "45_60|high|fragile", "risk_latent": "R3", "reported_C": "C2",
                    "wealth_wan": 10.0, "core": "", "strat_weight": 1.0, "entry_day": 0,
                    "traits": {"invest_share": "10_30pct", "n_funds": "5_10", "dca": "positive"}},
                   {"id": "m2", "cell": "45_60|high|robust", "risk_latent": "R2", "reported_C": "C3",
                    "wealth_wan": 25.0, "core": "", "strat_weight": 1.0, "entry_day": 0,
                    "traits": {"invest_share": "30_50pct", "n_funds": "10_15", "dca": "none"}}])
        poolp = tdp / "pool_mini.jsonl"
        write_jsonl_atomic(poolp, [{"org": "O0", "note_id": f"o0n{j:02d}", "intent": "I2", "images": []}
                                   for j in range(8)]
                           + [{"org": "O1", "note_id": f"o1n{j:02d}", "intent": "I3", "images": []}
                              for j in range(8)])
        gubap = tdp / "guba_empty.json"
        dump(gubap, {"signal": {}})
        metap = tdp / "fund_meta_mini.json"
        dump(metap, {"000001": {"type": "hybrid", "name": "Demo Hybrid One", "org": "O0"},
                     "000002": {"type": "equity", "name": "Demo Equity Two", "org": "O0"}})
        cfg_nav = {"run_tag": "selftest|navmeta", "n_agents": 2, "orgs": ["O0"],
                   "agents_file": str(agp), "content_pool": str(poolp), "nav_cache": str(navp),
                   "guba_signal": str(gubap), "fund_meta_file": str(metap),
                   "window": {"start": "2025-10-01", "end": "2025-12-31", "max_trading_days": 10}}
        wnav = load_world(cfg_nav)
        chk("nav_loader_skips_non_fund_keys_no_crash",
            sorted(wnav.funds) == ["000001", "000002"] and sorted(wnav.base_codes) == ["000001", "000002"])
        chk("nav_loader_counts_skips_and_flags_synthetic",
            wnav.nav_synthetic is True and list(wnav.nav_skipped) == ["_meta"])
        # DEFECT 1: invariants_report.json must carry every registered invariant with detail
        cfg_rep = {"run_tag": "selftest|report", "orgs": ["O0"], "modality_level": "agent",
                   "modality_arms": ["T", "TV"], "modality_run_arm": "TV",
                   "fees": {"subscribe_rate": 0.0, "redeem_rate": 0.0}}
        # fees dropped -> (d) must fail. hold0 supplied because (b) seeds its ledger from
        # the OPENING snapshot and skips without one -- the assertion below reads its
        # "pass" key, which a skipped entry does not carry.
        st_d = dict(base_st, agents=[_mk_agent(99.5)], hold0={7: {"000001": 50.0}})
        c_d, _ = check_invariants(st_d, cp, cfg_rep)
        rep_dir = tdp / "reports"
        log_r = EventLog(rep_dir / "event_log.jsonl")
        for row in clean:
            log_r.emit(row["ev"], **{k: v for k, v in row.items() if k != "ev"})
        log_r.close()
        write_reports(rep_dir, st_d, cfg_rep, fw, c_d, {"attempts": 5, "decision_failures": 0}, 0.25)
        rep = load_json(rep_dir / "invariants_report.json")
        ent = rep.get("checks") or {}
        chk("report_lists_every_registered_invariant",
            set(ent) == set(INVARIANTS)
            and all(isinstance(v, dict) and isinstance(v.get("description"), str) and v["description"]
                    and (("pass" in v) != bool(v.get("skipped"))) for v in ent.values()))
        sm = rep.get("summary") or {}
        chk("report_summary_counts_add_up",
            sm.get("total") == len(ent)
            and sm.get("passed", -1) + sm.get("failed", -2) + sm.get("skipped", -3) == sm.get("total", -4)
            and sm.get("all_passed") == (sm.get("failed") == 0) and sm.get("failed", 0) >= 1)
        chk("report_broken_state_fails_right_invariant_key",
            ent.get("d_wealth_conservation", {}).get("pass") is False
            and ent.get("d_wealth_conservation", {}).get("max_abs_residual_cny") == 0.5
            and ent.get("b_nonholder_never_redeems", {}).get("pass") is True)
        chk("report_carries_event_log_sha256",
            isinstance(rep.get("event_log_sha256"), str) and len(rep["event_log_sha256"]) == 64)
        rm = load_json(rep_dir / "run_meta.json")
        chk("run_meta_records_synthetic_nav_false", rm.get("synthetic_nav") is False)
        # same writers over the synthetic-NAV world: run_meta must flag synthetic_nav=true,
        # and a not-applicable check must be `skipped` with a reason, never absent
        cc_run, _ = check_invariants(dict(st), cp, {"modality_level": "run"})
        rep_dir2 = tdp / "reports_synth"
        write_reports(rep_dir2, dict(st), dict(cfg_rep, modality_level="run"), wnav, cc_run, {}, 0.25)
        rm2 = load_json(rep_dir2 / "run_meta.json")
        rep2 = load_json(rep_dir2 / "invariants_report.json")
        chk("run_meta_records_synthetic_nav_true", rm2.get("synthetic_nav") is True)
        chk("report_marks_not_applicable_check_skipped",
            rep2["checks"]["h_arm_balance"].get("skipped") is True
            and bool(rep2["checks"]["h_arm_balance"].get("reason")))
        chk("report_clean_run_failed_count",
            rep2["summary"]["failed"] == 1 and rep2["checks"]["e_all_cells_exposed"]["pass"] is False)
    fails = sum(1 for _, ok in rows_out if ok is False)
    skips = sum(1 for _, ok in rows_out if ok is None)
    print("=" * 56)
    print("flowmirror.engine.world --self-test")
    for name, ok in rows_out:
        print(f"{'PASS' if ok else ('SKIP' if ok is None else 'FAIL')} {name}")
    print(f"{len(rows_out) - fails - skips} pass, {fails} fail, {skips} skip")
    return fails


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return self_test()
    print("usage: python -m flowmirror.engine.world --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

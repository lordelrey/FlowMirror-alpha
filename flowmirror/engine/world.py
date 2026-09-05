#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flowmirror.engine.world -- FlowMirror v7 world layer (everything but the day loop).

Owns: run-config validation, data loaders (World), the Fund/Inv state classes, the
institution posting policy, guba climate seeding, the event logger, the invariant
checks and the report writers. flowmirror.engine.loop imports this module and owns
only the trading-day loop, the apply step and the CLI.

Determinism: random.Random(rng_seed_from(run_tag, *parts)) streams only; hashlib
sha256 via flowmirror.io.hashing; never built-in hash(); sets are never iterated
unsorted. Stdlib + this package only; console output is ASCII-only.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

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
    if cfg.get("arm_level") not in ("agent", "exposure"):
        die("config: arm_level must be agent|exposure")
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
                 "nav_days", "inputs_sha256", "base_codes", "deferred", "start", "end", "run_tag")


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (REPO_ROOT / p)


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
    lo, hi = win["start"], win["end"]             # ISO strings compare chronologically
    day_strs = sorted({d for s in nav_cache.values() for d in s if lo <= d <= hi})
    nav_days = [date.fromisoformat(d) for d in day_strs][:maxd]
    if not nav_days:
        die(f"no trading days in window {lo}..{hi} (nav_cache: {paths['nav_cache']})")
    # fund universe: >=63 NAVs before start; creative-linked common-support codes with shorter
    # history enter from first NAV date + 21 days (spec CONFIG)
    cs_codes = {c for n in pool_rows for c in (n.get("common_support_codes") or [])}
    funds, base_codes, deferred, fund_org = {}, [], {}, {}
    for code in sorted(nav_cache):
        items = sorted(nav_cache[code].items())
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
    return w


# --- §3 investors -----------------------------------------------------------------------------
class Inv:
    __slots__ = ("id", "jid", "cell", "risk", "rc", "core", "strat_weight", "cash", "other", "hold", "cost",
                 "fam", "aff", "follow", "flag", "entry", "dca", "realized", "w0", "expo", "memory",
                 "reflection", "beliefs", "market_view", "risk_mood", "attention", "gain_loss",
                 "arm", "arm_tally", "rng")

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


def init_investors(world: World, cfg: dict) -> list:
    run_tag, n = cfg["run_tag"], int(cfg["n_agents"])
    if len(world.agents) < n:
        die(f"agents file has {len(world.agents)} rows < n_agents={n}")
    out = []
    for rec in world.agents[:n]:
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
        hold, cost = {}, {}
        nh = inv.rng.randint(0, kk)               # sample 0..k held funds (spec §3)
        if nh:
            alloc = cash * inv.rng.uniform(0.3, 0.8)
            for code in inv.rng.sample(world.base_codes, nh):
                f = world.funds[code]
                i0 = bisect_right(f.dates, world.start - timedelta(days=1)) - 1
                ci = max(i0 - inv.rng.randint(60, 250), 0)   # cost NAV 60..250 trade days back (clamped)
                cnav = f.navs[ci]
                if cnav <= 0:
                    die(f"fund {code}: non-positive cost NAV")
                hold[code] = alloc / nh / cnav
                cost[code] = cnav
            cash -= alloc
        inv.cash, inv.other, inv.hold, inv.cost = cash, other, hold, cost
        inv.attention, inv.gain_loss = {c: 0.0 for c in hold}, {}
        inv.fam, inv.aff, inv.follow, inv.flag = {}, {}, set(), {}
        ev = rec.get("entry_day", 0)              # population entry spread over 365d -> run window
        spread = float(cfg.get("entry_spread_days", 30))
        ev_days = (date.fromisoformat(ev) - world.start).days if isinstance(ev, str) else int(ev)
        inv.entry = max(int(round(ev_days * spread / 365.0)), 0)
        inv.dca = (tr.get("dca") == "positive")
        inv.realized = 0.0
        inv.w0 = cash + sum(u * cost[c] for c, u in hold.items()) + other   # == fin (identity check (d))
        inv.expo, inv.memory, inv.reflection, inv.beliefs = {}, [], {}, []
        inv.market_view = inv.risk_mood = 0
        if cfg.get("force_arm"):
            inv.arm = cfg["force_arm"]
        elif cfg.get("arm_level") == "agent":
            inv.arm = feed.arm_for_agent(run_tag, inv.id)
        else:
            inv.arm = "TV"
        inv.arm_tally = {"T": 0, "TV": 0}
        out.append(inv)
    return out


# --- §4 institution posting policy ------------------------------------------------------------
_INTENTS = ("I1", "I2", "I3")


def _ig_of(note: dict):
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
            posts.append({"post_id": pid, "org": org, "intent": intent, "intent_group": intent,
                          "note": nid, "code": code, "img": _has_image(note), "t_pub": dstr})
            if log is not None:
                log.emit("post", t=t, d=dstr, org=org, p=pid, intent=intent, ig=intent, fund=code,
                         img=posts[-1]["img"])
    return posts


# --- §5 guba climate seeding -------------------------------------------------------------------
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
    br = row.get("bull_ratio")
    if br is None:
        bull = float(row.get("bull", row.get("bullish", 0)) or 0)
        bear = float(row.get("bear", row.get("bearish", 0)) or 0)
        if bull + bear <= 0:
            return None
        br = bull / (bull + bear)
    br = float(br)
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


# --- §7 invariants ----------------------------------------------------------------------------
def check_invariants(state: dict, events_path, cfg: dict):
    """state contract: agents, funds, end(iso), signal_audit[{t,used,live_end,prev_live_end,day_keys}],
    active_per_day{t:n}, agent_arms{i:arm}; funds maps code -> Fund for end-of-run pricing."""
    agents = state.get("agents") or []
    funds = state.get("funds") or {}
    id2cell = {a.id: a.cell for a in agents}
    rows = load_jsonl(Path(events_path))
    co_oc, co_cf = defaultdict(int), defaultdict(int)
    hard_p, holdings = set(), defaultdict(set)
    b_viol = c_viol = i_viol = 0
    imp_cnt, exposed = defaultdict(lambda: [0, 0]), set()
    dec_day, cmt_idx, clims = defaultdict(int), defaultdict(set), []
    for r in rows:
        ev = r.get("ev")
        if ev == "imp":
            imp_cnt[r.get("i")][0 if r.get("arm") == "T" else 1] += 1
            exposed.add(id2cell.get(r.get("i")))
        elif ev == "act":
            i, fund, kind = r.get("i"), r.get("fund"), r.get("kind")
            if kind in ("subscribe", "dca"):
                holdings[i].add(fund)
            elif kind == "redeem" and fund not in holdings[i]:
                b_viol += 1
        elif ev == "co":
            oc = r.get("oc")
            co_oc[oc] += 1
            co_cf[r.get("oc_cf")] += 1
            if oc == "hard_block":
                hard_p.add((r.get("i"), r.get("p")))
            if r.get("act") == "redeem" and oc in ("hard_block", "confirm_signed", "confirm_declined", "purchase_blocked"):
                i_viol += 1
        elif ev == "dec":
            dec_day[r.get("t")] += 1
        elif ev == "cmt":
            cmt_idx[(r.get("p"), r.get("t"))].add(r.get("text"))
        elif ev == "clim":
            clims.append(r)
    for r in rows:                                # second pass: hard_block must never precede a subscribe
        if r.get("ev") == "act" and r.get("kind") == "subscribe" and (r.get("i"), r.get("p")) in hard_p:
            c_viol += 1
    checks = {}
    audit = state.get("signal_audit") or []
    a_ok = len(audit) > 0
    for e in audit[1:]:
        if e.get("used") != e.get("prev_live_end"):
            a_ok = False
    for e in audit:
        if e.get("live_end") == e.get("used") and int(e.get("day_keys", 0)) > 0:
            a_ok = False
    checks["a_lagged_signals_only"] = {"pass": a_ok, "days_audited": len(audit),
                                       "mechanism": "day t ranking input sha == end-of-(t-1) live sha"}
    checks["b_nonholder_never_redeems"] = {"pass": b_viol == 0, "violations": b_viol,
                                           "no_holdings_refusals": co_oc["no_holdings"]}
    checks["c_hard_block_never_subscribes"] = {"pass": c_viol == 0, "violations": c_viol,
                                              "hard_block_checkouts": co_oc["hard_block"]}
    max_res, tol = 0.0, 1e-6 * max([1.0] + [a.w0 for a in agents])
    end = date.fromisoformat(state["end"]) if state.get("end") else None
    if end is not None:
        for a in agents:                          # (d) cash+units*nav+other == w0+realized+unrealized
            holdv = costv = 0.0
            for c, u in a.hold.items():
                nv = funds[c].nav_at(end) if c in funds else a.cost.get(c, 0.0)
                holdv += u * nv
                costv += u * a.cost.get(c, 0.0)
            res = (a.cash + holdv + a.other) - (a.w0 + a.realized + (holdv - costv))
            max_res = max(max_res, abs(res))
    checks["d_wealth_conservation"] = {"pass": max_res <= tol, "max_abs_residual_cny": max_res, "tolerance": tol}
    cells = sorted(set(id2cell.values()))
    n_exp = sum(1 for c in cells if c in exposed)
    checks["e_all_cells_exposed"] = {"pass": len(cells) > 0 and n_exp == len(cells),
                                     "cells_exposed": n_exp, "cells_total": len(cells)}
    g_viol = j_viol = 0
    for r in clims:
        if r.get("source") == "guba_seed":        # exogenous day-1 seed, exempt from cmt matching
            continue
        t, p = int(r.get("t", 0)), r.get("p")
        prior = {txt for (pp, tt), tx in cmt_idx.items() for txt in tx if pp == p and tt < t}
        exact = {txt for (pp, tt), tx in cmt_idx.items() for txt in tx if pp == p and tt == t - 1}
        for ent in (r.get("top") or []):
            txt = ent.get("text") if isinstance(ent, dict) else str(ent)
            if txt not in prior:
                g_viol += 1
            if txt not in exact:
                j_viol += 1
    checks["g_comments_lagged_only"] = {"pass": g_viol == 0, "violations": g_viol}
    checks["j_displayed_comment_matches_prev_day"] = {"pass": j_viol == 0, "violations": j_viol}
    if cfg.get("arm_level") == "agent":
        try:
            res = feed.check_arm_balance(state.get("agent_arms") or {}, id2cell)
            h_ok = all(bool(v) for v in res.values()) if isinstance(res, dict) else bool(res)
        except Exception:
            res, h_ok = None, False
        entry = {"pass": bool(h_ok), "level": "agent"}
        if res is None:
            entry["error"] = "check_arm_balance raised"
        checks["h_arm_balance"] = entry
    else:
        viol = checked = 0
        for i in sorted(imp_cnt, key=str):
            t_cnt, tv_cnt = imp_cnt[i]
            if t_cnt + tv_cnt >= 50:
                checked += 1
                if abs(tv_cnt / (t_cnt + tv_cnt) - 0.5) > 0.02:
                    viol += 1
        checks["h_arm_balance"] = {"pass": viol == 0, "level": "exposure", "agents_checked": checked,
                                  "violations": viol}
    # (i) redemptions are never gated: a `co` row with act == "redeem" may carry match / no_holdings only.
    # Suitability (PREREG v1.3 §B9) and the QDII purchase block apply to subscriptions alone.
    gated = {"hard_block", "confirm_signed", "confirm_declined", "purchase_blocked"}
    i_viol = sum(1 for r in rows if isinstance(r, dict) and r.get("ev") == "co"
                 and r.get("act") == "redeem" and r.get("oc") in gated)
    checks["i_redeem_checkout_never_blocked"] = {"pass": i_viol == 0, "violations": i_viol}
    active = state.get("active_per_day") or {}
    checks["k_dec_matches_active"] = {"pass": bool(active) and all(dec_day.get(t, 0) == n for t, n in active.items()),
                                      "days": len(active)}
    big = len(agents) >= 300                      # (e) fatal only at n_agents >= 300
    core = [k for k, v in checks.items() if not v["pass"] and not (k == "e_all_cells_exposed" and not big)]
    return checks, bool(core)


# --- §8 reports --------------------------------------------------------------------------------
def write_reports(out_dir, state: dict, cfg: dict, world: World, checks: dict, counters, elapsed) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    logp = out_dir / "event_log.jsonl"
    dump(out_dir / "invariants_report.json",
         {"checks": checks, "event_log_sha256": event_log_sha(logp) if logp.exists() else None})
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
    dump(out_dir / "run_meta.json", {
        "engine": "v6", "cfg": cfg, "inputs_sha256": world.inputs_sha256,
        "universe": {"base": world.base_codes, "deferred": world.deferred, "size": len(world.funds)},
        "funds": {c: {"r": f.r, "qdii": f.qdii, "family": f.family, "org": world.fund_org.get(c, dflt_org),
                      "active_from": str(f.active_from)} for c, f in sorted(world.funds.items())},
        "investors": {"total": len(agents), "active_ever": sum(1 for a in agents if a.entry < len(world.nav_days))},
        "counters": counters,
        "checkout_oc": dict(sorted((state.get("checkout_oc") or {}).items())),
        "checkout_oc_cf": dict(sorted((state.get("checkout_oc_cf") or {}).items())),
        "elapsed_s": round(float(elapsed), 1),
        "status": "ok" if all(v.get("pass", True) for v in checks.values()) else "invariant_failure"})
    print(f"[world] reports written to {out_dir}; status="
          f"{'ok' if all(v.get('pass', True) for v in checks.values()) else 'invariant_failure'}")


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
        recent, rngp, viol = {}, random.Random(rng_seed_from(cfg["run_tag"], "platform", "selftest")), 0
        for t in range(len(world.nav_days)):
            seen = {o: set(recent.get(o, ())) for o in world.orgs}
            for pst in publish_day(world, cfg, t, recent, rngp):
                if pst["note"] in seen[pst["org"]] and len(world.pool[pst["org"]]) >= 20:
                    viol += 1
        chk("publish_no_repeat_within_10d", viol == 0)
    else:
        print(f"SKIP world checks: real data files not found (data_root={data_root}, research={research})")
        for nm in ("agents_eq_n_agents", "orgs_eq_cfg", "notes_50_per_org_frozen_pool", "funds_active_ge_140",
                   "nav_days_eq_60", "intent_probs_sum_to_1", "push_heavy_identical", "publish_no_repeat_within_10d"):
            skip(nm)
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
        st = {"agents": [], "funds": {}, "end": "2025-12-31", "active_per_day": {0: 1},
              "agent_arms": {}, "agent_cells": {},
              "signal_audit": [{"t": 0, "used": "aa", "live_end": "bb", "prev_live_end": None, "day_keys": 2},
                               {"t": 1, "used": "bb", "live_end": "cc", "prev_live_end": "bb", "day_keys": 1}]}
        clean = [{"ev": "post", "t": 0, "d": "2025-10-01", "org": "O0", "p": "00000", "intent": "I2",
                  "ig": "I2", "fund": None, "img": False},
                 {"ev": "imp", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "arm": "T", "slot": 0,
                  "source": "random"},
                 {"ev": "dec", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "prompt_sha": "x", "raw_sha": "y",
                  "cache_hit": False, "attempts": 1, "status": "ok", "arm": "T", "mood": 0, "reason": "r",
                  "violations": []},
                 {"ev": "co", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "fund": "000001", "ig": "I2",
                  "act": "subscribe", "oc": "match", "oc_cf": "match", "amt": 100.0},
                 {"ev": "act", "t": 0, "d": "2025-10-01", "i": 1, "p": "00000", "kind": "subscribe",
                  "fund": "000001", "amt": 100.0, "units": 50.0, "nav": 2.0}]
        bad = [dict(clean[3], i=2, p="00001", act="redeem", oc="hard_block", oc_cf="hard_block", amt=0.0)]
        cp, bp = tdp / "clean.jsonl", tdp / "bad.jsonl"
        write_jsonl_atomic(cp, clean)
        write_jsonl_atomic(bp, bad)
        cc, cf = check_invariants(st, cp, {"arm_level": "exposure"})
        bc, bf = check_invariants(st, bp, {"arm_level": "exposure"})
        chk("invariants_clean_passes", cf is False and [k for k, v in cc.items() if not v["pass"]] == ["e_all_cells_exposed"])
        chk("invariants_flag_redeem_block", bf is True and bc["i_redeem_checkout_never_blocked"]["pass"] is False)
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
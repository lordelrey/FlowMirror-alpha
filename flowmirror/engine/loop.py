"""FlowMirror v7 trading-day loop, apply step and CLI (flowmirror.engine.loop).

Day order per card: clock -> publish -> social lag (freeze heat_prev/clim_prev)
-> feed -> prompt assembly (state frozen) -> LLM phase (parallel) -> apply
(serial, sorted agent ids) -> DCA + lagged updates + heat -> reflection ->
snapshots.  `decide`/`reflect` return dicts; consumed keys (all optional except
`parsed`): prompt_sha raw_sha cache_hit attempts status mood reason violations
likes saves follows aff comments trade summary_sha market_view risk_mood beliefs
summary.  World surface used: nav_days funds orgs market_line guba_line
qdii_blocked guba_codes guba_week; funds expose r qdii family org active_from
nav_at(date).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter
from datetime import date
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from flowmirror.agents.runtime import (BudgetGovernor, CapStop, LLMCache, MockLLM,
                                       call_glm, decide, reflect, run_parallel)
from flowmirror.channels.feed import (arm_for_agent, assign_arms, check_arm_balance,
                                      climate_for, hot_score, rank_feed, top_comments)
from flowmirror.config.loader import deep_merge, load_config, resolve_paths
from flowmirror.config.validate import ConfigError, validate
from flowmirror.engine.world import (DEFAULT_CONFIG, EventLog, check_invariants,
                                     event_log_sha, guba_seed_label, init_investors,
                                     intent_probs, load_world, publish_day, quarter_of,
                                     validate_config, write_reports)
from flowmirror.io.backups import backup_existing
from flowmirror.io.hashing import rng_seed_from, sha256_file, sha256_text
from flowmirror.io.jsonl import iter_jsonl, write_jsonl_atomic
from flowmirror.regulator.cn_cxr import cxr_outcome

ROOT = os.environ.get("FLOWMIRROR_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_VALID_OC = ("match", "confirm_signed", "confirm_declined", "hard_block",
             "purchase_blocked", "below_min", "no_holdings")


def _trend_position(navs):
    lo, hi, cur = min(navs), max(navs), navs[-1]
    if hi <= lo:
        return "处于近半年中位"
    q = (cur - lo) / (hi - lo)
    if q >= 2.0 / 3.0:
        return "处于近半年高位"
    return "处于近半年低位" if q < 1.0 / 3.0 else "处于近半年中位"


def _trend_line(code, fund, hist):
    navs = [float(n) for n in (fund.nav_at(dt) for dt in hist) if n is not None and n > 0]
    if len(navs) < 6:
        return None
    cur = navs[-1]
    ret = lambda k: (cur / navs[-k] - 1.0) if len(navs) >= k else None
    win = navs[-min(64, len(navs)):]
    peak = dd = 0.0
    for n in win:
        peak = max(peak, n)
        dd = max(dd, (peak - n) / peak)
    seg = lambda v: "n/a" if v is None else f"{v:+.1%}"
    return (f"{code} 近1周{seg(ret(6))} 近1月{seg(ret(22))} 近3月{seg(ret(64))} "
            f"3月最大回撤{dd:.1%} {_trend_position(navs)}")


def _top_view(top):
    out = []
    for c in top or ():
        if isinstance(c, dict):
            out.append({"i": c.get("i"), "stance": c.get("stance"), "text": c.get("text")})
        else:
            out.append({"i": None, "stance": "neutral", "text": str(c)})
    return out


def _feed_card(post, heat_prev, clim_prev, top_prev, cfg):
    pid = post.get("p")
    card = {"p": pid, "org": post.get("org"), "intent": post.get("intent"),
            "ig": post.get("ig"), "fund": post.get("fund"), "img": bool(post.get("img")),
            "caption": post.get("caption_masked", post.get("caption")),
            "ocr": post.get("ocr_masked", post.get("ocr_text")),
            "heat": round(float(heat_prev.get(pid, 0.0)), 3)}
    if cfg.get("social") and (cfg.get("channels") or {}).get("social"):
        card["climate"] = clim_prev.get(pid)
        card["top"] = _top_view(top_prev.get(pid))
    return card


def _agent_view(inv, shown, W, cfg, navday, hist, trend_cache, top_prev, clim_prev, dt_cur):
    hold = []
    for code in sorted(inv.hold):
        u = inv.hold[code]
        nav = navday.get(code) or u[1]
        hold.append({"fund": code, "units": round(u[0], 2), "cost": round(u[1], 4),
                     "nav": round(nav, 4), "pnl": round(nav / u[1] - 1.0, 4) if u[1] else 0.0})
    orgs = sorted({p.get("org") for p in shown.values() if p.get("org")})
    view = {"persona": inv.persona, "beliefs": inv.beliefs,
            "memory": list(inv.memory[-max(1, int(cfg["memory_days"])):]) if cfg.get("memory") else [],
            "account": {"cash": round(inv.cash, 2), "realized": round(inv.realized, 2),
                        "holdings": hold},
            "experience": {"holdings": [(h["fund"], h["pnl"]) for h in hold],
                           "last_trade": inv.last_trade, "declined": inv.declined_line},
            "familiarity": {org: inv.flag.get(org, 0) for org in orgs},
            "market": W.market_line(dt_cur), "guba": W.guba_line(dt_cur)}
    ch = cfg.get("channels") or {}
    if ch.get("trend"):
        lines = []
        for code in sorted(set(inv.hold) | {p.get("fund") for p in shown.values()
                                            if p.get("fund")}):
            if code not in trend_cache:
                f = W.funds.get(code)
                trend_cache[code] = _trend_line(code, f, hist) if f is not None else None
            if trend_cache[code]:
                lines.append(trend_cache[code])
        view["trend"] = lines
    if cfg.get("social") and ch.get("social"):
        view["social"] = {pid: {"climate": clim_prev.get(pid), "top": _top_view(top_prev.get(pid))}
                          for pid in shown}
    if ch.get("direct"):
        view["direct"] = []
    return view


def _make_llm(cfg, t, agent_id):
    if cfg.get("mock_llm"):
        return MockLLM(cfg.get("mock_options") or {},
                       random.Random(rng_seed_from(cfg["run_tag"], "mock", t, agent_id)))
    llm_cfg = cfg["llm"]
    return lambda prompt, **kw: call_glm(prompt, llm_cfg, **kw)


def _trade_summary(tr):
    if not tr:
        return "未交易"
    if not tr.get("exec"):
        return "申购被拦下：需签确认书，你放弃了" if tr.get("oc") == "confirm_declined" else "未交易"
    verb = "申购" if tr.get("act") == "subscribe" else "赎回"
    return f"{verb}{tr.get('code')} {tr.get('amt', 0.0):.0f}元"


def _memory_line(t, n, tr, cmt, aff, mood, reason):
    cpart = f"｜评{cmt['org']}:{cmt['stance']}\"{cmt['text'][:20]}\"" if cmt else ""
    apart = f"｜好感{aff[0]}{aff[1]:+.1f}" if aff else ""
    return (f"D{t}｜看{n}条｜{_trade_summary(tr)}{cpart}{apart}｜心情{mood or '-'}"
            f"｜{str(reason or '')[:24]}")[:80]


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _inputs_sha(cfg):
    return {k: sha256_file(cfg[k]) for k in ("content_pool", "nav_cache", "guba_signal",
                                             "agents_file", "fund_meta_file", "family_file")
            if cfg.get(k) and os.path.exists(cfg[k])}


def _inv_failures(res):
    if res is None:
        return []
    if isinstance(res, dict):
        return sorted(k for k, v in res.items() if not v)
    if isinstance(res, bool):
        return [] if res else ["core"]
    return [i if isinstance(i, str) else str(i[0]) for i in res
            if isinstance(i, str) or not i[-1]]


def _last_meta(out_dir):
    try:
        for row in iter_jsonl(os.path.join(out_dir, "run_meta.json")):
            return row
    except OSError:
        pass
    return {}


def apply_decision(inv, rec, shown, day):
    """Serial apply of one decision record; returns (comment, affinity, trade) views."""
    cfg, S, logd, row = day.cfg, day.S, day.logd, rec.get("parsed")
    t, dstr = day.t, day.dstr
    w = day.weights.get(inv.id, 1.0) if day.weights else 1.0
    for key, dst in (("likes", day.likes), ("saves", day.saves)):
        for pid in rec.get(key) or ():
            dst[pid] = dst.get(pid, 0.0) + w
    for org in sorted(rec.get("follows") or ()):
        if org not in inv.follow:
            inv.follow.add(org)
            logd(ev="st", t=t, d=dstr, i=inv.id, org=org, what="follow", lv=2,
                 fam=round(inv.fam.get(org, 0.0), 3), aff=round(inv.aff.get(org, 0.0), 3))
    aff_first = None
    for org in sorted(rec.get("aff") or {}):
        dv = float((rec.get("aff") or {}).get(org) or 0.0)
        inv.aff[org] = inv.aff.get(org, 0.0) + 1.0 * dv
        if aff_first is None and dv != 0.0:
            aff_first = (org, dv)
    cmt_out = None
    if cfg.get("social"):
        for c in rec.get("comments") or ():
            pid, stance = c.get("p"), c.get("stance", "neutral")
            text = str(c.get("text") or "")
            day.comments.append({"i": inv.id, "p": pid, "stance": stance, "text": text, "w": w})
            day.cw[pid] = day.cw.get(pid, 0.0) + w
            if cmt_out is None:
                cmt_out = {"org": (shown.get(pid) or {}).get("org"), "stance": stance, "text": text}
            logd(ev="cmt", t=t, d=dstr, i=inv.id, p=pid, stance=stance, text=text)
    tr = None
    trade = rec.get("trade") or None
    if row is not None and trade:
        FUNDS, qdii_blk, navday = day.FUNDS, day.qdii_blk, day.navday
        flows, dt_cur, checkout_oc = day.flows, day.dt_cur, day.checkout_oc
        pid = trade.get("p")
        post = shown.get(pid) or {}
        code, act = post.get("fund"), trade.get("act")
        if code is None:                           # I2 post without in-universe common-support code
            logd(ev="click", t=t, d=dstr, i=inv.id, p=pid, oc="click_no_landing")
            S["click_no_landing"] += 1
            tr = {"act": act, "exec": False, "oc": "click_no_landing", "code": None, "amt": 0.0}
        else:
            logd(ev="click", t=t, d=dstr, i=inv.id, p=pid, oc="to_checkout")
            S["clicks"] += 1
            fund = FUNDS[code]                     # ---- checkout: CxR distribution layer ----
            smc = row.get("sign_mismatch_confirm")
            smc = smc if isinstance(smc, bool) else str(smc).lower() in ("true", "1")
            oc_cf = cxr_outcome(inv.rc, fund.r, smc)
            oc = oc_cf if (act == "subscribe" and cfg.get("suitability")) else "match"
            if act == "subscribe" and oc in ("match", "confirm_signed") and fund.qdii and code in qdii_blk:
                oc = "purchase_blocked"            # QDII restriction replay (step 1/6)
            pct = float(row.get("amount_pct") or 0.0) / 100.0   # amount_pct is a 0-100 percentage
            amt = 0.0
            if oc in ("match", "confirm_signed"):
                if act == "subscribe":
                    amt = pct * inv.cash
                    if amt < 100.0:                # min ticket 100 CNY
                        oc = "below_min"
                    else:
                        nav = navday[code]
                        units = amt / nav
                        u = inv.hold.get(code)
                        if u:                      # average-cost update
                            u[1] = (u[0] * u[1] + amt) / (u[0] + units); u[0] += units
                        else:
                            inv.hold[code] = [units, nav]
                        inv.cash -= amt
                        S["sub_n"] += 1; S["sub_cny"] += amt
                        flows[fund.family][quarter_of(dt_cur)]["sub"] += amt
                        logd(ev="act", t=t, d=dstr, i=inv.id, p=pid, kind="subscribe", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
                else:
                    u = inv.hold.get(code)
                    if not u or u[0] <= 0.0:
                        oc = "no_holdings"         # engine refuses: non-holders never redeem (inv b)
                    else:
                        nav = navday[code]
                        units = u[0] * pct
                        amt = units * nav
                        inv.realized += amt - units * u[1]
                        u[0] -= units
                        if u[0] <= 1e-9:
                            del inv.hold[code]
                        inv.cash += amt
                        S["red_n"] += 1; S["red_cny"] += amt
                        flows[fund.family][quarter_of(dt_cur)]["red"] += amt
                        logd(ev="act", t=t, d=dstr, i=inv.id, p=pid, kind="redeem", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
            checkout_oc[oc] += 1
            day.checkout_oc_cf[oc_cf] += 1
            logd(ev="co", t=t, d=dstr, i=inv.id, p=pid, fund=code, ig=post.get("ig"), act=act,
                 oc=oc, oc_cf=oc_cf, amt=round(amt, 2))
            if oc == "confirm_declined":
                inv.declined_line = "申购被拦下：需签确认书，你放弃了"
            elif amt > 0.0:
                inv.last_trade = f"D{t} {act} {code} {amt:.0f}元"
            tr = {"act": act, "exec": amt > 0.0, "oc": oc, "code": code, "amt": amt}
    return cmt_out, aff_first, tr


def run_simulation(cfg):
    """One simulation pass; returns 0 ok, 2 cap_stopped, 3 halt / invariant failure."""
    t0 = time.time()
    out_dir = cfg["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    for name in ("event_log.jsonl", "run_meta.json"):
        if os.path.exists(os.path.join(out_dir, name)):
            backup_existing(os.path.join(out_dir, name))
    cp = cfg.get("content_pool")
    if cp and os.path.basename(cp) == "content_pool_v1.jsonl":
        masked = os.path.join(os.path.dirname(cp), "content_pool_v1_masked.jsonl")
        if os.path.exists(masked):
            cfg["content_pool"] = masked
    validate_config(cfg)
    W = load_world(cfg)
    rng = random.Random(cfg["seed"])
    invs = sorted(init_investors(cfg, W, rng), key=lambda x: x.id)
    for inv in invs:
        inv.arm = arm_for_agent(cfg["run_tag"], inv.id)
        for attr, val in (("arm_tally", {"T": 0, "TV": 0}), ("memory", []), ("attention", {}),
                          ("gain_loss", {}), ("last_trade", ""), ("declined_line", ""),
                          ("market_view", ""), ("risk_mood", ""), ("reflection", ""),
                          ("beliefs", {}), ("persona", {})):
            if not hasattr(inv, attr):
                setattr(inv, attr, val)
    elog = EventLog(os.path.join(out_dir, "event_log.jsonl"))
    logd = elog if callable(elog) else elog.log
    cache = LLMCache(cfg["llm"]["cache"]) if cfg["llm"].get("cache") else None
    gov = BudgetGovernor(cfg)
    K, gamma = int(cfg["feed"]["K"]), float(cfg["feed"].get("gamma", 1.8))
    delta, p_active = float(cfg.get("fam_decay", 0.1)), float(cfg["p_active"])
    refl_every, mem_days = int(cfg["reflection_every_days"]), max(1, int(cfg["memory_days"]))
    workers = int(cfg["llm"]["workers"])
    wmap = ({inv.id: float(getattr(inv, "w", 1.0) or 1.0) for inv in invs}
            if cfg.get("climate_weighting") else None)
    S = Counter()
    checkout_oc, checkout_oc_cf, imp_cells = Counter(), Counter(), Counter()
    quarters = sorted({quarter_of(dt) for dt in W.nav_days})
    flows = {fa: {q: {"sub": 0.0, "red": 0.0} for q in quarters}
             for fa in sorted({f.family for f in W.funds.values()})}
    comments, likes, saves, cw, heat, birth = {}, {}, {}, {}, {}, {}
    snapshots, recent = {}, []
    guba_codes = set(getattr(W, "guba_codes", ()) or ())
    guba_week = getattr(W, "guba_week", {}) or {}
    n_days, inv_a_ok = len(W.nav_days), True

    def _flush():
        for obj in (elog, cache):
            if obj is not None and hasattr(obj, "flush"):
                obj.flush()

    def snapshot(tag, dstr):
        rows = [{"i": inv.id, "arm": inv.arm, "cash": round(inv.cash, 2),
                 "realized": round(inv.realized, 2),
                 "hold": {c: [round(u[0], 4), round(u[1], 4)] for c, u in sorted(inv.hold.items())},
                 "fam": {o: round(v, 3) for o, v in sorted(inv.fam.items())},
                 "aff": {o: round(v, 3) for o, v in sorted(inv.aff.items())},
                 "flag": dict(sorted(inv.flag.items())), "follow": sorted(inv.follow)}
                for inv in invs]
        path = os.path.join(out_dir, "snapshots", f"snap_{tag}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_jsonl_atomic(path, rows)
        snapshots[tag] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")))
        _flush()

    def _finalize(status, failures):
        _flush()
        n = max(int(S["decisions"]), 1)
        meta = {"funds": {c: {"r": f.r, "qdii": bool(f.qdii), "family": f.family, "org": f.org,
                              "active_from": f.active_from.isoformat()}
                          for c, f in sorted(W.funds.items())},
                "cfg": _jsonable(cfg), "inputs_sha256": _inputs_sha(cfg),
                "counters": {**{k: S[k] for k in sorted(S)},
                             "decision_failure_rate": round(S["decision_failures"] / n, 6)},
                "checkout_oc": dict(sorted(checkout_oc.items())),
                "checkout_oc_cf": dict(sorted(checkout_oc_cf.items())),
                "snapshots": dict(sorted(snapshots.items())),
                "event_log_sha": event_log_sha(os.path.join(out_dir, "event_log.jsonl")),
                "elapsed_s": round(time.time() - t0, 2), "status": status,
                "invariant_failures": list(failures)}
        write_jsonl_atomic(os.path.join(out_dir, "run_meta.json"), [meta])
        return meta

    snapshot("init", W.nav_days[0].isoformat() if W.nav_days else "na")
    t = -1
    try:
        for t, dt_cur in enumerate(W.nav_days):
            dstr = dt_cur.isoformat()
            FUNDS = W.funds
            qb = W.qdii_blocked(dt_cur) if callable(getattr(W, "qdii_blocked", None)) \
                else (getattr(W, "qdii_blocked", None) or ())
            qdii_blk = set(qb)
            navday = {c: f.nav_at(dt_cur) for c, f in sorted(FUNDS.items())
                      if f.active_from <= dt_cur and f.nav_at(dt_cur) is not None}
            for inv in invs:                       # (1) clock: P&L refresh, attention decay
                for code in sorted(inv.hold):
                    u, nav = inv.hold[code], navday.get(code)
                    if nav and u[1] > 0:
                        inv.gain_loss[code] = nav / u[1] - 1.0
                    inv.attention[code] = inv.attention.get(code, 0.0) * (1.0 - delta) \
                        + 1.0 + float(guba_week.get(code, 0.0))
                for code in sorted(set(inv.attention) - set(inv.hold)):
                    inv.attention[code] *= (1.0 - delta)
            today = publish_day(W, t, dt_cur, intent_probs(cfg["strategy_mix"], cfg),
                                rng, cfg) or []    # (2) publish
            for post in today:
                birth[post["p"]] = t
                logd(ev="post", t=t, d=dstr, org=post.get("org"), p=post["p"],
                     intent=post.get("intent"), ig=post.get("ig"), fund=post.get("fund"),
                     img=bool(post.get("img")))
            cand = [p for ps in recent for p in ps] + today
            comments[t] = []
            if t - 2 in comments:
                del comments[t - 2]
            prev_cmts = comments.get(t - 1, [])
            clim_now, top_now = {}, {}
            for post in cand:                      # (3) social lag, one trading day
                pid = post["p"]
                if t == 0 or post.get("fund") in guba_codes:
                    seeded = guba_seed_label(post)
                    label, counts = seeded[0], seeded[1]
                    top = list(seeded[2]) if len(seeded) > 2 else []
                    src = "guba_seed"
                else:
                    label, counts = climate_for(pid, prev_cmts, weights=wmap)
                    top = top_comments(pid, prev_cmts, k=3, weights=wmap)
                    src = "agent"
                clim_now[pid], top_now[pid] = label, top
                logd(ev="clim", t=t, d=dstr, p=pid, label=label, counts=counts,
                     top=_top_view(top), source=src)
            heat_prev, clim_prev, top_prev = dict(heat), dict(clim_now), dict(top_now)
            day = SimpleNamespace(t=t, dstr=dstr, dt_cur=dt_cur, cfg=cfg, S=S, logd=logd,
                                  FUNDS=FUNDS, qdii_blk=qdii_blk, navday=navday, flows=flows,
                                  weights=wmap, comments=comments[t], likes=likes, saves=saves,
                                  cw=cw, checkout_oc=checkout_oc, checkout_oc_cf=checkout_oc_cf)
            jobs, touched, trend_cache = [], {}, {}
            hist = W.nav_days[max(0, t - 125): t + 1]
            for inv in invs:                       # (4)+(5) feed then frozen prompt assembly
                if inv.rng.random() >= p_active:
                    continue
                S["active_days"] += 1
                cards = rank_feed({"id": inv.id, "persona": inv.persona, "beliefs": inv.beliefs,
                                   "follow": sorted(inv.follow), "fam": dict(inv.fam),
                                   "flag": dict(inv.flag), "attention": dict(inv.attention)},
                                  cand, heat_prev, clim_prev, cfg["feed"], inv.rng,
                                  mode=cfg["ranking"])
                arms = [inv.arm] * len(cards) if cfg["arm_level"] == "agent" \
                    else assign_arms(inv.rng, K, inv.arm_tally)
                shown, last_src = {}, None
                for s, item in enumerate(cards):
                    post, source, pid = item[0], item[1], item[0]["p"]
                    shown[pid] = post
                    arm = arms[s] if s < len(arms) else inv.arm
                    imp_cells[(inv.id, arm)] += 1
                    logd(ev="imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)
                    if last_src is not None and source != last_src:
                        S["factor_switches"] += 1
                    last_src = source
                    if post.get("org"):
                        touched.setdefault(inv.id, set()).add(post["org"])
                    if post.get("fund"):
                        inv.attention[post["fund"]] = inv.attention.get(post["fund"], 0.0) \
                            + 1.0 + float(guba_week.get(post["fund"], 0.0))
                jobs.append({"inv": inv, "shown": shown,
                             "cards": [_feed_card(p, heat_prev, clim_prev, top_prev, cfg)
                                       for p in shown.values()],
                             "view": _agent_view(inv, shown, W, cfg, navday, hist, trend_cache,
                                                 top_prev, clim_prev, dt_cur),
                             "rng": random.Random(rng_seed_from(cfg["run_tag"], "dec", t, inv.id)),
                             "llm": _make_llm(cfg, t, inv.id)})

            def _job(job):                          # (6) LLM phase, barrier via run_parallel
                return decide(job["llm"], job["view"], job["cards"], cfg, job["rng"],
                              cache=cache, gov=gov)

            for job, rec in zip(jobs, run_parallel(jobs, _job, workers) or []):  # (7) apply
                inv = job["inv"]
                rec = rec if isinstance(rec, dict) else {}
                S["decisions"] += 1
                S["attempts"] += int(rec.get("attempts") or 0)
                if rec.get("cache_hit"):
                    S["cache_hits"] += 1
                else:
                    S["calls"] += 1
                logd(ev="dec", t=t, d=dstr, i=inv.id, prompt_sha=rec.get("prompt_sha"),
                     raw_sha=rec.get("raw_sha"), cache_hit=bool(rec.get("cache_hit")),
                     attempts=int(rec.get("attempts") or 0), status=rec.get("status"),
                     arm=inv.arm, mood=rec.get("mood"), reason=rec.get("reason"),
                     violations=list(rec.get("violations") or ()))
                if rec.get("parsed") is None:
                    S["decision_failures"] += 1
                    continue
                cmt_out, aff_first, tr = apply_decision(inv, rec, job["shown"], day)
                inv.memory.append(_memory_line(t, len(job["cards"]), tr, cmt_out, aff_first,
                                               rec.get("mood"), rec.get("reason")))
                del inv.memory[:-mem_days]
            halt = cfg["llm"].get("decision_failure_halt", True)
            thr = halt if isinstance(halt, float) and 0.0 < halt < 1.0 else 0.02
            if t >= 3 and halt and S["decisions"] and S["decision_failures"] / S["decisions"] > thr:
                _print_summary(cfg, _finalize("decision_failure_halt",
                                              ["decision_failure_rate"]), t + 1)
                return 3
            if dt_cur.day == 1:                    # DCA (spec §3): not feed-driven, no CxR gate
                for inv in invs:
                    if inv.dca and t >= inv.entry and inv.hold:
                        amt = inv.cash * 0.02
                        if amt < 100.0:
                            S["dca_skipped"] += 1
                            continue
                        code = min(inv.hold)       # deterministic: first held code in sort order
                        f = FUNDS[code]
                        nav = f.nav_at(dt_cur)
                        units = amt / nav
                        u = inv.hold[code]
                        u[1] = (u[0] * u[1] + amt) / (u[0] + units); u[0] += units
                        inv.cash -= amt
                        S["dca_n"] += 1; S["dca_cny"] += amt
                        flows[f.family][quarter_of(dt_cur)]["sub"] += amt
                        logd(ev="act", t=t, d=dstr, i=inv.id, p=None, kind="dca", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
            if heat != heat_prev:                  # would mean mid-day signal mutation (inv a)
                inv_a_ok = False
            for inv in invs:                       # (8) lagged updates, visible from t+1 only
                orgs = touched.get(inv.id) or ()
                if not orgs and not inv.fam and not inv.flag:
                    continue
                for org in sorted(inv.fam):
                    inv.fam[org] = inv.fam[org] * (1.0 - delta) + (1.0 if org in orgs else 0.0)
                for org in sorted(orgs):
                    if org not in inv.fam:
                        inv.fam[org] = 1.0
                for org in sorted(inv.aff):        # adstock decay (spec §6b, lambda_a = 0.9)
                    inv.aff[org] *= 0.9
                for org in sorted(set(inv.fam) | inv.follow | set(inv.aff)):
                    # spec §6: level 2 if follows; level 1 if exposure/affinity stock >= 1.0; else 0
                    if org in inv.follow:
                        lv = 2
                    elif inv.fam.get(org, 0.0) >= 1.0 or inv.aff.get(org, 0.0) >= 1.0:
                        lv = 1
                    else:
                        lv = 0
                    if lv != inv.flag.get(org, 0):
                        inv.flag[org] = lv
                        logd(ev="st", t=t, d=dstr, i=inv.id, org=org, what="level", lv=lv,
                             fam=round(inv.fam.get(org, 0.0), 3),
                             aff=round(inv.aff.get(org, 0.0), 3))
            for pid in sorted(set(heat) | set(likes) | set(saves) | set(cw) | set(birth)):
                heat[pid] = hot_score(likes.get(pid, 0.0), saves.get(pid, 0.0),
                                      cw.get(pid, 0.0), t - birth.get(pid, t), gamma)
            if cfg["memory"] and refl_every > 0 and (t + 1) % refl_every == 0:  # (9)
                rjobs = [{"inv": inv, "llm": _make_llm(cfg, t, inv.id),
                          "view": _agent_view(inv, {}, W, cfg, navday, hist, trend_cache,
                                              {}, {}, dt_cur),
                          "rng": random.Random(rng_seed_from(cfg["run_tag"], "refl", t, inv.id))}
                         for inv in invs]

                def _rjob(job):
                    return reflect(job["llm"], job["view"], cfg, job["rng"])

                for job, res in zip(rjobs, run_parallel(rjobs, _rjob, workers) or []):
                    inv = job["inv"]
                    res = res if isinstance(res, dict) else {}
                    S["attempts"] += int(res.get("attempts") or 0)
                    if res.get("cache_hit"):
                        S["cache_hits"] += 1
                    else:
                        S["calls"] += 1
                    if res.get("market_view") is not None:
                        inv.market_view = res["market_view"]
                    if res.get("risk_mood") is not None:
                        inv.risk_mood = res["risk_mood"]
                    if res.get("beliefs") is not None:
                        inv.beliefs = res["beliefs"]
                    if res.get("summary"):
                        inv.reflection = res["summary"]
                    logd(ev="refl", t=t, d=dstr, i=inv.id, summary_sha=res.get("summary_sha"))
            if (t + 1) % 30 == 0 or t == n_days - 1:  # (10) snapshots
                snapshot(str(t + 1), dstr)
            recent = (recent + [today])[-2:]
    except CapStop:
        _print_summary(cfg, _finalize("cap_stopped", ["cap_stop"]), max(t + 1, 0))
        return 2
    failures = [] if inv_a_ok else ["a_midday_signal_mutation"]
    failures += [f"world:{f}" for f in _inv_failures(check_invariants(cfg))]
    if len(invs) >= 300 and not check_arm_balance({i.id: i.arm for i in invs}, dict(imp_cells)):
        failures.append("e_arm_balance")
    meta = _finalize("ok" if not failures else "invariant_fail", failures)
    write_reports(W, cfg, invs, flows)
    _print_summary(cfg, meta, n_days)
    return 0 if not failures else 3


def _print_summary(cfg, meta, days):
    c = meta.get("counters", {})
    print("=== flowmirror.engine.loop summary ===")
    print(f"run_tag={cfg.get('run_tag')} days={days} agents={cfg.get('n_agents')} "
          f"active_agent_days={c.get('active_days', 0)}")
    print(f"calls={c.get('calls', 0)} cache_hits={c.get('cache_hits', 0)} "
          f"decision_failure_rate={c.get('decision_failure_rate', 0.0):.4f}")
    print(f"checkout_oc={meta.get('checkout_oc')}")
    print(f"checkout_oc_cf={meta.get('checkout_oc_cf')}")
    print(f"factor_switches={c.get('factor_switches', 0)} status={meta.get('status')} "
          f"elapsed_s={meta.get('elapsed_s')}")
    fails = meta.get("invariant_failures") or []
    print("invariants=PASS" if not fails else "invariants=FAIL(" + ",".join(fails) + ")")


def _load_cfg(path):
    obj = load_config(path)
    validate(obj, "run")
    cfg = deep_merge(DEFAULT_CONFIG, obj)
    resolve_paths(cfg, ROOT)
    return cfg


def _fake_inv(**over):
    inv = SimpleNamespace(id="A1", arm="T", rc=3, cash=100000.0, hold={}, realized=0.0,
                          fam={}, aff={}, follow=set(), flag={}, memory=[], w=1.0)
    for k, v in over.items():
        setattr(inv, k, v)
    return inv


def _fake_day(cfg, **over):
    day = SimpleNamespace(t=0, dstr="2024-01-02", dt_cur=date(2024, 1, 2), cfg=cfg, S=Counter(),
                          FUNDS={"F1": SimpleNamespace(r=4, qdii=False, family="famA", org="orgA")},
                          qdii_blk=set(), navday={"F1": 1.5},
                          flows={"famA": {(2024, 1): {"sub": 0.0, "red": 0.0}}}, weights=None,
                          comments=[], likes={}, saves={}, cw={}, checkout_oc=Counter(),
                          checkout_oc_cf=Counter(), events=[])
    day.logd = lambda **kw: day.events.append(kw)
    for k, v in over.items():
        setattr(day, k, v)
    return day


def _self_test():
    ok = True

    def chk(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + name)

    shown = {"P1": {"p": "P1", "org": "orgA", "fund": "F1", "ig": "I2"}}
    base = {"parsed": {"amount_pct": 10, "sign_mismatch_confirm": "false"}, "likes": ["P1"],
            "saves": [], "follows": ["orgA"], "aff": {"orgA": 0.5}, "comments": [],
            "trade": {"p": "P1", "act": "subscribe", "amount_pct": 10}}
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    apply_decision(inv, dict(base), shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("suitability_off_executes_match_cf_logged", co["oc"] == "match" and co["oc_cf"] in _VALID_OC)
    chk("subscribe_and_engagement_applied", bool(inv.hold) and inv.cash < 100000.0
        and "orgA" in inv.follow and inv.aff.get("orgA") == 0.5)
    day, inv = _fake_day({"social": False, "suitability": True}), _fake_inv()
    apply_decision(inv, dict(base), shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("gated_subscribe_oc_equals_cf", co["oc"] == co["oc_cf"] and co["oc"] in _VALID_OC)
    day, inv = _fake_day({"social": False, "suitability": True}), _fake_inv()
    rec = dict(base)
    rec["trade"] = {"p": "P1", "act": "redeem", "amount_pct": 50}
    apply_decision(inv, rec, shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("redeem_ungated_no_holdings", co["oc"] == "no_holdings" and co["oc_cf"] in _VALID_OC)
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    rec = dict(base)
    rec["parsed"] = {"amount_pct": 0.001, "sign_mismatch_confirm": "false"}
    apply_decision(inv, rec, shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("below_min_blocks_ticket", co["oc"] == "below_min"
        and not any(e["ev"] == "act" for e in day.events))
    chk("memory_line_shape", _memory_line(3, 4, None, None, None, "neutral", "r" * 99)
        .startswith("D3｜看4条｜未交易"))
    tr = {"act": "subscribe", "exec": False, "oc": "confirm_declined", "code": "F1", "amt": 0.0}
    chk("declined_memory_text", "申购被拦下：需签确认书，你放弃了"
        in _memory_line(3, 4, tr, None, None, "m", "x"))
    chk("memory_line_len80", len(_memory_line(9, 12, tr, {"org": "o", "stance": "bull",
                                                          "text": "t" * 40}, ("o", 1.5),
                                                 "ok", "y" * 60)) <= 80)
    chk("trend_position_bucket", _trend_position([1.0, 2.0, 3.0]).endswith("高位")
        and _trend_position([3.0, 2.0, 1.0]).endswith("低位")
        and _trend_position([1.0, 3.0, 2.0]).endswith("中位"))
    a = random.Random(rng_seed_from("rt", "dec", 0, "A1"))
    b = random.Random(rng_seed_from("rt", "dec", 0, "A1"))
    chk("rng_streams_deterministic", [a.random() for _ in range(4)] == [b.random() for _ in range(4)])
    root = os.environ.get("FLOWMIRROR_DATA_ROOT") or os.path.join(ROOT, "data")
    alt = os.environ.get("FLOWMIRROR_RESEARCH_ROOT") or "D:/Desktop/ABM paper/fundmarket-sim"
    cfg_path = next((os.path.join(b_, "runs", "mock_10x3.json") for b_ in (root, alt, ROOT)
                     if os.path.exists(os.path.join(b_, "runs", "mock_10x3.json"))), None)
    if cfg_path is None:
        print("SKIP: no runs/mock_10x3.json under FLOWMIRROR_DATA_ROOT/FLOWMIRROR_RESEARCH_ROOT")
        return 0 if ok else 1
    cfg = _load_cfg(cfg_path)
    cfg.update({"mock_llm": True, "n_agents": 8, "out_dir": tempfile.mkdtemp(prefix="fm_loop_st_")})
    cfg["window"]["max_trading_days"] = 2
    rc = run_simulation(cfg)
    chk("mock_end_to_end_rc0", rc == 0)
    if rc != 0:
        return 1
    evs = {row.get("ev") for row in iter_jsonl(os.path.join(cfg["out_dir"], "event_log.jsonl"))}
    chk("required_event_kinds", {"post", "imp", "dec", "clim"} <= evs)
    sha1 = _last_meta(cfg["out_dir"]).get("event_log_sha")
    chk("replay_sha_identical", run_simulation(cfg) == 0 and sha1 is not None
        and sha1 == _last_meta(cfg["out_dir"]).get("event_log_sha"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m flowmirror.engine.loop",
                                 description="FlowMirror v7 trading-day loop")
    ap.add_argument("config", nargs="?", help="run config JSON path (required)")
    ap.add_argument("--mock", action="store_true", help="force mock_llm")
    ap.add_argument("--days", type=int)
    ap.add_argument("--agents", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--out")
    ap.add_argument("--replay-check", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.config:
        ap.error("config path is required")
    try:
        cfg = _load_cfg(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1
    if args.mock:
        cfg["mock_llm"] = True
    if args.days is not None:
        cfg["window"]["max_trading_days"] = args.days
    if args.agents is not None:
        cfg["n_agents"] = args.agents
    if args.seed is not None:
        cfg["seed"] = args.seed
    if args.out:
        cfg["out_dir"] = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    if args.replay_check:
        if run_simulation(cfg) != 0:
            return 3
        m1 = _last_meta(cfg["out_dir"])
        if run_simulation(cfg) != 0:
            return 3
        m2 = _last_meta(cfg["out_dir"])
        same = m1.get("event_log_sha") is not None \
            and m1.get("event_log_sha") == m2.get("event_log_sha")
        print(f"replay-check sha1={m1.get('event_log_sha')}")
        print(f"replay-check sha2={m2.get('event_log_sha')} "
              f"warm_cache_calls={(m2.get('counters') or {}).get('calls')}")
        print(f"replay-check identical={same}")
        return 0 if same else 3
    return run_simulation(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
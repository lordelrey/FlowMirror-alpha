"""FlowMirror v7 trading-day loop, apply step and CLI (flowmirror.engine.loop).

Day order per card: clock -> publish -> social lag (freeze heat_prev/clim_prev)
-> feed -> prompt assembly (state frozen) -> LLM phase (parallel) -> apply
(serial, sorted agent ids) -> DCA + lagged updates + heat -> reflection ->
snapshots.  Verified call surfaces used (no invented signatures):

world:   init_investors(W, cfg) -> [Inv]; publish_day(W, cfg, t, recent,
         rng_platform, log=EventLog) -> [{post_id, org, intent, intent_group,
         note, code, img, t_pub}] (it emits the "post" rows itself);
         guba_seed_label(W, code, week) -> label|None; check_invariants(state,
         events_path, cfg); write_reports(out_dir, state, cfg, W, checks,
         counters, elapsed); quarter_of(date) -> str; event_log_sha(path).
runtime: decide(agent_view, feed_cards, cfg, cache, governor, llm, shown) and
         reflect(agent_view, cfg, cache, governor, llm); records carry parsed,
         violations, parser_status, prompt_sha, raw_sha256, image_shas,
         cache_hit, attempts, notes; run_parallel(jobs, fn, workers);
         BudgetGovernor(hard_cap_attempts); MockLLM(force_c2_r4, malformed_rate).
prompt:  agent_view keys persona_card_zh_rich, memory, last_reflection,
         market_view, risk_mood, c_class, cash, holdings[{code,name,r,units,
         nav,pnl_pct}], last_trade, declined_confirms, familiarity{org:level},
         guba, trend, direct; feed_card keys post_id, org, title, caption,
         landing{code,name,R,ret_3m,ret_1y,min_buy}, likes, arm, image_path,
         image_sha, comments_prev[{stance,text,fam_phrase}], climate_label.
feed:    rank_feed(agent_state, candidates, heat_prev, clim_prev, cfg["feed"],
         rng, mode); climate_for(post_id, comments_prev, min_n=4, weights) ->
         (label, counts); top_comments(post_id, comments_prev, k=3, weights);
         assign_arms(rng, k, tally); check_arm_balance(arms, cells) -> (ok, rep).
Inv:     slots only -- units in inv.hold[code] (float), cost NAV in
         inv.cost[code], familiarity level inv.flag[org], follows inv.follow,
         trust adstock inv.aff, EMA familiarity inv.fam, per-agent inv.rng.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from bisect import bisect_right
from collections import Counter
from datetime import date, timedelta
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from flowmirror.agents.runtime import (BudgetGovernor, CapStop, LLMCache, MockLLM,
                                       call_glm, decide, reflect, run_parallel)
from flowmirror.channels.feed import (assign_arms, check_arm_balance, climate_for,
                                      hot_score, rank_feed, top_comments)
from flowmirror.config.loader import deep_merge, load_config, resolve_paths
from flowmirror.config.validate import ConfigError, validate
from flowmirror.engine.world import (DEFAULT_CONFIG, EventLog, check_invariants,
                                     event_log_sha, guba_seed_label, init_investors,
                                     load_world, publish_day, quarter_of,
                                     validate_config, write_reports)
from flowmirror.io.backups import backup_existing
from flowmirror.io.hashing import sha256_text
from flowmirror.io.jsonl import iter_jsonl
from flowmirror.regulator.cn_cxr import cxr_outcome

ROOT = os.environ.get("FLOWMIRROR_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
ONE_DAY = timedelta(days=1)
_VALID_OC = ("match", "confirm_signed", "confirm_declined", "hard_block",
             "purchase_blocked", "below_min", "no_holdings")
_FAM_PHRASE = {0: "不熟悉该机构", 1: "略有耳闻", 2: "关注已久"}


def _week_key(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _note_index(W):
    """Index pool notes by every plausible id field so posts can find theirs."""
    idx = {}
    for notes in (W.pool or {}).values():
        for n in notes or ():
            if not isinstance(n, dict):
                continue
            for key in ("id", "note_id", "nid", "uuid"):
                nid = n.get(key)
                if nid is not None:
                    idx[str(nid)] = n
    return idx


def _fund_name(W, code):
    fm = (W.fund_meta or {}).get(code)
    if isinstance(fm, dict):
        return fm.get("name") or code
    return fm if isinstance(fm, str) and fm else code


def _hist_ret(f, dt, k):
    j = bisect_right(f.dates, dt) - 1
    if j < 1 or f.navs[j] <= 0:
        return None
    i = max(j - k, 0)
    return round(f.navs[j] / f.navs[i] - 1.0, 4) if f.navs[i] > 0 else None


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


def _rank_item(item):
    if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], dict):
        return item[0], item[1]
    return item, "fit"


def _feed_card(W, post, notes_by_id, arm, heat_prev, clim_prev, top_prev, dt_cur):
    """One impression card with exactly the keys flowmirror.agents.prompt reads."""
    pid = post["post_id"]
    nid = post.get("note")
    note = notes_by_id.get(str(nid)) if nid is not None else None
    note = note or {}
    landing = None
    code = post.get("code")
    if code and code in W.funds:
        f = W.funds[code]
        landing = {"code": code, "name": _fund_name(W, code), "R": f.r,
                   "ret_3m": _hist_ret(f, dt_cur, 63), "ret_1y": _hist_ret(f, dt_cur, 252),
                   "min_buy": 100.0}
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")
    return {"post_id": pid, "org": post.get("org"),
            "title": note.get("title") or note.get("display_title") or "",
            "caption": note.get("caption") or note.get("abstract") or note.get("summary") or "",
            "landing": landing, "likes": round(float(heat_prev.get(pid, 0.0)), 1),
            "arm": arm, "image_path": image_path,
            "image_sha": sha256_text(str(image_path)) if image_path else None,
            "comments_prev": [{"stance": c.get("stance"), "text": c.get("text"),
                               "fam_phrase": c.get("fam_phrase", "")}
                              for c in (top_prev.get(pid) or []) if isinstance(c, dict)],
            "climate_label": clim_prev.get(pid)}


def _agent_view(inv, persona_rec, shown, W, cfg, navday, hist, trend_cache, guba_view,
                last_trade, declined):
    """Frozen pre-LLM agent state with exactly the keys prompt.py reads."""
    hold = []
    for code in sorted(inv.hold):
        units, cst = float(inv.hold[code]), float(inv.cost.get(code, 0.0) or 0.0)
        nav = navday.get(code)
        f = W.funds.get(code)
        hold.append({"code": code, "name": _fund_name(W, code),
                     "r": f.r if f is not None else None,
                     "units": round(units, 2), "nav": round(nav, 4) if nav else None,
                     "pnl_pct": round(nav / cst - 1.0, 4) if (nav and cst > 0) else 0.0})
    orgs = sorted(set(inv.flag) | {p.get("org") for p in shown.values() if p.get("org")})
    codes = {p.get("code") for p in shown.values() if p.get("code")}
    view = {"persona_card_zh_rich": persona_rec,
            "memory": list(inv.memory[-max(1, int(cfg["memory_days"])):])
            if cfg.get("memory") else [],
            "last_reflection": inv.reflection,
            "market_view": inv.market_view, "risk_mood": inv.risk_mood,
            "c_class": inv.rc, "cash": round(inv.cash, 2), "holdings": hold,
            "last_trade": last_trade.get(inv.id, ""),
            "declined_confirms": int(declined.get(inv.id, 0)),
            "familiarity": {org: inv.flag.get(org, 0) for org in orgs},
            "guba": {c: lb for c, lb in sorted(guba_view.items()) if c in inv.hold or c in codes},
            "direct": []}
    if (cfg.get("channels") or {}).get("trend"):
        lines = []
        for code in sorted(set(inv.hold) | codes):
            if code not in trend_cache:
                f = W.funds.get(code)
                trend_cache[code] = _trend_line(code, f, hist) if f is not None else None
            if trend_cache[code]:
                lines.append(trend_cache[code])
        view["trend"] = lines
    return view


def _make_llm(cfg):
    if cfg.get("mock_llm"):
        return MockLLM(bool(cfg.get("mock_force_c2_r4")),
                       float(cfg.get("mock_malformed_rate", 0.05)))
    llm_cfg = cfg["llm"]

    def _call(messages, max_tokens, **kw):
        return call_glm(messages, int(max_tokens), model=llm_cfg.get("model"), **kw)

    return _call


def _qdii_blocked_set(cfg, dstr):
    qb = cfg.get("qdii_blocked")
    if isinstance(qb, dict):
        inner = qb.get(dstr)
        if isinstance(inner, dict):
            return set(inner.get("codes") or ())
        return set(inner or ())
    if isinstance(qb, (list, tuple, set)):
        return set(qb)
    q = cfg.get("qdii")
    if isinstance(q, dict) and isinstance(q.get("blocked"), (list, tuple, set)):
        return set(q["blocked"])
    return set()


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


def _last_meta(out_dir):
    try:
        with open(os.path.join(out_dir, "run_meta.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _adapt_record(rec, shown, inv):
    """Map the parser's decision JSON (flowmirror.agents.prompt schema) onto the flat fields
    apply_decision reads: likes/saves (pids), follows (orgs), aff {org: delta}, comments [{p,stance,text}],
    trade {p, act, fund}. Feasibility beyond this mapping stays in apply_decision (GM)."""
    row = rec.get("parsed") if isinstance(rec.get("parsed"), dict) else None
    out = dict(rec)
    if row is None:
        return out
    likes, saves, follows = [], [], []
    for pid, acts in sorted((row.get("engage") or {}).items()):
        acts = set(acts or ())
        if "like" in acts:
            likes.append(pid)
        if "save" in acts:
            saves.append(pid)
        if "follow" in acts:
            org = (shown.get(pid) or {}).get("org")
            if org:
                follows.append(org)
    out["likes"], out["saves"], out["follows"] = likes, saves, sorted(set(follows))
    out["aff"] = {k: v for k, v in (row.get("org_affinity_delta") or {}).items() if v}
    out["comments"] = [{"p": c.get("post_id"), "stance": c.get("stance"), "text": c.get("text", "")}
                       for c in (row.get("comments") or ()) if c.get("stance") != "no_comment"]
    tr = row.get("trade") or {}
    act = {"buy": "subscribe", "redeem": "redeem"}.get(str(tr.get("action") or "none"))
    if act:
        fund = tr.get("fund")
        pid = next((p for p, post in shown.items() if post.get("code") == fund), None)
        out["trade"] = {"p": pid, "act": act, "fund": fund}
    else:
        out["trade"] = None
    return out


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
            logd("st", t=t, d=dstr, i=inv.id, org=org, what="follow", lv=2,
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
            org = (shown.get(pid) or {}).get("org")
            fam = inv.flag.get(org, 0) if org else 0
            day.comments.append({"i": inv.id, "p": pid, "stance": stance, "text": text,
                                 "w": w, "fam": fam, "fam_phrase": _FAM_PHRASE.get(fam, "")})
            day.cw[pid] = day.cw.get(pid, 0.0) + w
            if cmt_out is None:
                cmt_out = {"org": org, "stance": stance, "text": text}
            logd("cmt", t=t, d=dstr, i=inv.id, p=pid, stance=stance, text=text)
    tr = None
    trade = rec.get("trade") or None
    if row is not None and trade:
        FUNDS, qdii_blk, navday = day.FUNDS, day.qdii_blk, day.navday
        flows, dt_cur, checkout_oc = day.flows, day.dt_cur, day.checkout_oc
        pid = trade.get("p")
        post = shown.get(pid) or {}
        act = trade.get("act")
        # A subscribe must land on a fund shown today; a redeem only needs a held fund (never gated).
        code = post.get("code")
        if code is None and act == "redeem" and trade.get("fund") in inv.hold:
            code = trade.get("fund")
        if code is None:                     # I2 note without in-universe common-support code
            logd("click", t=t, d=dstr, i=inv.id, p=pid, oc="click_no_landing")
            S["click_no_landing"] += 1
            tr = {"act": act, "exec": False, "oc": "click_no_landing", "code": None, "amt": 0.0}
        else:
            logd("click", t=t, d=dstr, i=inv.id, p=pid, oc="to_checkout")
            S["clicks"] += 1
            fund = FUNDS[code]               # ---- checkout: CxR distribution layer ----
            smc = row.get("sign_mismatch_confirm")
            smc = smc if isinstance(smc, bool) else str(smc).lower() in ("true", "1")
            oc_cf = cxr_outcome(inv.rc, fund.r, smc)
            oc = oc_cf if (act == "subscribe" and cfg.get("suitability")) else "match"
            if act == "subscribe" and oc in ("match", "confirm_signed") and fund.qdii \
                    and code in qdii_blk:
                oc = "purchase_blocked"      # QDII quota suspension, subscribe-only
            pct = float(row.get("amount_pct") or 0.0) / 100.0   # amount_pct is 0-100
            amt = 0.0
            if oc in ("match", "confirm_signed"):
                if act == "subscribe":
                    amt = pct * inv.cash
                    if amt < 100.0:          # min ticket 100 CNY
                        oc = "below_min"
                    else:
                        nav = navday[code]
                        units = amt / nav
                        if code in inv.hold:                    # average-cost update
                            tot = inv.hold[code] + units
                            inv.cost[code] = (inv.hold[code] * inv.cost.get(code, nav)
                                              + amt) / tot
                            inv.hold[code] = tot
                        else:
                            inv.hold[code] = units
                            inv.cost[code] = nav
                        inv.cash -= amt
                        S["sub_n"] += 1
                        S["sub_cny"] += amt
                        flows[fund.family][quarter_of(dt_cur)]["sub"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=pid, kind="subscribe", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
                else:
                    u = float(inv.hold.get(code) or 0.0)
                    if u <= 0.0:
                        oc = "no_holdings"   # engine refuses: non-holders never redeem (inv b)
                    else:
                        nav = navday[code]
                        units = u * pct
                        amt = units * nav
                        cst = inv.cost.get(code, nav)
                        inv.realized += units * (nav - cst)
                        inv.hold[code] = u - units
                        if inv.hold[code] <= 1e-9:
                            del inv.hold[code]
                            inv.cost.pop(code, None)
                        inv.cash += amt
                        S["red_n"] += 1
                        S["red_cny"] += amt
                        flows[fund.family][quarter_of(dt_cur)]["red"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=pid, kind="redeem", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
            checkout_oc[oc] += 1
            day.checkout_oc_cf[oc_cf] += 1
            logd("co", t=t, d=dstr, i=inv.id, p=pid, fund=code,
                 ig=post.get("intent_group", post.get("intent")), act=act,
                 oc=oc, oc_cf=oc_cf, amt=round(amt, 2))
            if oc == "confirm_declined":
                day.declined[inv.id] = day.declined.get(inv.id, 0) + 1
            elif amt > 0.0:
                day.last_trade[inv.id] = f"D{t} {act} {code} {amt:.0f}元"
            tr = {"act": act, "exec": amt > 0.0, "oc": oc, "code": code, "amt": amt}
    return cmt_out, aff_first, tr


def run_simulation(cfg):
    """One simulation pass; returns 0 ok, 2 cap_stopped, 3 halt / invariant failure."""
    t0 = time.time()
    out_dir = cfg["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    for name in ("event_log.jsonl", "run_meta.json", "invariants_report.json"):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            backup_existing(p)
    cp = cfg.get("content_pool")
    if cp and os.path.basename(cp) == "content_pool_v1.jsonl":
        masked = os.path.join(os.path.dirname(cp), "content_pool_v1_masked.jsonl")
        if os.path.exists(masked):
            cfg["content_pool"] = masked
    validate_config(cfg)
    W = load_world(cfg)
    rng_platform = random.Random(cfg["seed"])
    invs = sorted(init_investors(W, cfg), key=lambda x: x.id)
    persona = {rec.get("id"): (rec.get("persona_card_zh_rich") or rec.get("persona") or rec)
               for rec in W.agents}
    notes_by_id = _note_index(W)
    elog = EventLog(os.path.join(out_dir, "event_log.jsonl"))
    logd = elog.emit
    llm_cache = LLMCache(cfg["llm"]["cache"]) if cfg["llm"].get("cache") else None
    gov = BudgetGovernor(int(cfg["llm"].get("hard_cap_attempts")
                             or cfg["llm"].get("cap_attempts") or 100000))
    feed_cfg = cfg.get("feed") or {}
    K, gamma = int(feed_cfg.get("K", 5)), float(feed_cfg.get("gamma", 1.8))
    delta, p_active = float(cfg.get("fam_decay", 0.1)), float(cfg["p_active"])
    refl_every, mem_days = int(cfg["reflection_every_days"]), max(1, int(cfg["memory_days"]))
    workers = int(cfg["llm"]["workers"])
    wmap = ({inv.id: float(inv.strat_weight or 1.0) for inv in invs}
            if cfg.get("climate_weighting") else None)
    S = Counter()
    checkout_oc, checkout_oc_cf = Counter(), Counter()
    quarters = sorted({quarter_of(dt) for dt in W.nav_days})
    flows = {fa: {q: {"sub": 0.0, "red": 0.0} for q in quarters}
             for fa in sorted({f.family for f in W.funds.values()})}
    comments, likes, saves, cw, heat, birth = {}, {}, {}, {}, {}, {}
    snapshots, signal_audit, active_per_day = {}, [], {}
    last_trade, declined = {i.id: "" for i in invs}, {i.id: 0 for i in invs}
    guba_codes = {c for c in (W.guba or {}) if c in W.funds}
    guba_day_keys = sorted({k for e in (W.guba or {}).values()
                            if isinstance(e, dict) for k in e})
    no_repeat, recent_list = {}, []
    n_days = len(W.nav_days)
    inv_a_ok = True
    last_d = W.nav_days[0] if W.nav_days else W.start

    def snapshot(tag, dstr):
        rows = [{"i": inv.id, "arm": inv.arm, "cash": round(inv.cash, 2),
                 "realized": round(inv.realized, 2),
                 "hold": {c: round(float(u), 4) for c, u in sorted(inv.hold.items())},
                 "cost": {c: round(float(inv.cost[c]), 4) for c in sorted(inv.cost)},
                 "fam": {o: round(v, 3) for o, v in sorted(inv.fam.items())},
                 "aff": {o: round(v, 3) for o, v in sorted(inv.aff.items())},
                 "flag": dict(sorted(inv.flag.items())), "follow": sorted(inv.follow)}
                for inv in invs]
        snapshots[tag] = {"day": dstr, "agents": len(rows),
                          "cash": round(sum(r["cash"] for r in rows), 2),
                          "sha256": sha256_text(json.dumps(rows, ensure_ascii=False,
                                                           sort_keys=True,
                                                           separators=(",", ":")))}

    def _finish(extra_checks, rc, days):
        elog.close()
        state = {"agents": invs, "funds": W.funds, "end": last_d.isoformat(),
                 "signal_audit": signal_audit, "agent_arms": {i.id: i.arm for i in invs},
                 "active_per_day": dict(active_per_day), "flows": flows,
                 "snapshots": snapshots, "checkout_oc": dict(checkout_oc),
                 "checkout_oc_cf": dict(checkout_oc_cf)}
        checks = check_invariants(state, os.path.join(out_dir, "event_log.jsonl"), cfg)
        if not isinstance(checks, dict):
            checks = {"core": {"pass": bool(checks)}}
        checks = {str(k): (v if isinstance(v, dict) else {"pass": bool(v)})
                  for k, v in checks.items()}
        if len(invs) >= 300:
            ok, rep = check_arm_balance({i.id: i.arm for i in invs},
                                        {i.id: i.cell for i in invs})
            checks["e_arm_balance"] = {"pass": bool(ok), "report": _jsonable(rep)}
        for k, v in (extra_checks or {}).items():
            checks[k] = v
        elapsed = time.time() - t0
        write_reports(out_dir, state, cfg, W, checks, {k: S[k] for k in sorted(S)}, elapsed)
        _print_summary(cfg, dict(S), checks, days, elapsed, checkout_oc, checkout_oc_cf)
        return rc if rc else (0 if all(v.get("pass", True) for v in checks.values()) else 3)

    snapshot("init", last_d.isoformat())
    t = -1
    try:
        for t, dt_cur in enumerate(W.nav_days):
            last_d = dt_cur
            dstr = dt_cur.isoformat()
            FUNDS = W.funds
            qdii_blk = _qdii_blocked_set(cfg, dstr)
            navday = {c: f.nav_at(dt_cur) for c, f in sorted(FUNDS.items())
                      if f.active_from <= dt_cur and f.nav_at(dt_cur) is not None}
            wk_prev = _week_key(dt_cur - ONE_DAY)   # lagged signal week (t=0 -> pre-window day)
            for inv in invs:                       # (1) clock: P&L refresh, attention decay
                for code in sorted(inv.hold):
                    nav, cst = navday.get(code), inv.cost.get(code, 0.0)
                    if nav and cst > 0:
                        inv.gain_loss[code] = nav / cst - 1.0
                    inv.attention[code] = inv.attention.get(code, 0.0) * (1.0 - delta) + 1.0
                for code in sorted(set(inv.attention) - set(inv.hold)):
                    inv.attention[code] *= (1.0 - delta)
            today = publish_day(W, cfg, t, no_repeat, rng_platform, log=elog)  # (2) publish
            for post in today:
                birth[post["post_id"]] = t
            cand = [p for ps in recent_list for p in ps] + today
            comments[t] = []
            if t - 2 in comments:
                del comments[t - 2]
            prev_cmts = comments.get(t - 1, [])
            clim_now, top_now = {}, {}
            for post in cand:                      # (3) social lag, one trading day
                pid, code = post["post_id"], post.get("code")
                label, counts, src = None, {}, "agent"
                if code is not None and (t == 0 or code in guba_codes):
                    seeded = guba_seed_label(W, code, wk_prev)
                    if seeded:
                        label, src = seeded, "guba_seed"
                if label is None:
                    label, counts = climate_for(pid, prev_cmts, weights=wmap)
                top = top_comments(pid, prev_cmts, k=3, weights=wmap) or []
                clim_now[pid], top_now[pid] = label, top
                logd("clim", t=t, d=dstr, p=pid, label=label, counts=_jsonable(counts),
                     top=_top_view(top), source=src)
            signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
                                 "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
                                 "day_keys": guba_day_keys})
            heat_prev, clim_prev, top_prev = dict(heat), dict(clim_now), dict(top_now)
            guba_view = {}
            for post in cand:
                code = post.get("code")
                if code and code in guba_codes and code not in guba_view:
                    lb = guba_seed_label(W, code, wk_prev)
                    if lb:
                        guba_view[code] = lb
            day = SimpleNamespace(t=t, dstr=dstr, dt_cur=dt_cur, cfg=cfg, S=S, logd=logd,
                                  FUNDS=FUNDS, qdii_blk=qdii_blk, navday=navday, flows=flows,
                                  weights=wmap, comments=comments[t], likes=likes, saves=saves,
                                  cw=cw, checkout_oc=checkout_oc, checkout_oc_cf=checkout_oc_cf,
                                  last_trade=last_trade, declined=declined)
            jobs, touched, trend_cache = [], {}, {}
            hist = W.nav_days[max(0, t - 125): t + 1]
            for inv in invs:                       # (4)+(5) feed then frozen prompt assembly
                if inv.rng.random() >= p_active:
                    continue
                S["active_days"] += 1
                astate = {"id": inv.id, "persona": persona.get(inv.id), "risk": inv.risk,
                          "cell": inv.cell, "beliefs": inv.beliefs,
                          "follow": sorted(inv.follow), "fam": dict(inv.fam),
                          "flag": dict(inv.flag), "attention": dict(inv.attention),
                          "hold": sorted(inv.hold)}
                ranked = rank_feed(astate, cand, heat_prev, clim_prev, cfg["feed"],
                                   inv.rng, mode=cfg.get("ranking", "three_source")) or []
                arms = ([inv.arm] * len(ranked) if cfg.get("arm_level") == "agent"
                        else assign_arms(inv.rng, K, inv.arm_tally))
                shown, arm_by_pid, last_src = {}, {}, None
                for s, item in enumerate(ranked):
                    post, source = _rank_item(item)
                    pid = post["post_id"]
                    shown[pid] = post
                    arm = arms[s] if s < len(arms) else inv.arm
                    arm_by_pid[pid] = arm
                    logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)
                    if last_src is not None and source != last_src:
                        S["factor_switches"] += 1
                    last_src = source
                    if post.get("org"):
                        touched.setdefault(inv.id, set()).add(post["org"])
                    if post.get("code"):
                        inv.attention[post["code"]] = \
                            inv.attention.get(post["code"], 0.0) + 1.0
                if not shown:
                    continue
                cards = [_feed_card(W, shown[pid], notes_by_id, arm_by_pid[pid],
                                    heat_prev, clim_prev, top_prev, dt_cur) for pid in shown]
                view = _agent_view(inv, persona.get(inv.id), shown, W, cfg, navday, hist,
                                   trend_cache, guba_view, last_trade, declined)
                jobs.append({"inv": inv, "shown": shown, "cards": cards, "view": view,
                             "llm": _make_llm(cfg)})
            active_per_day[t] = len(jobs)

            def _job(job):                          # (6) LLM phase, barrier via run_parallel
                # decide() takes the parser's feasibility scope, not the pid->post map used by apply_decision
                sh = job["shown"]
                scope = {"pids": list(sh),
                         "codes": sorted({p.get("code") for p in sh.values() if p.get("code")}),
                         "held": sorted(job["inv"].hold),
                         "orgs": sorted({p.get("org") for p in sh.values() if p.get("org")})}
                return decide(job["view"], job["cards"], cfg, llm_cache, gov, job["llm"], scope)

            for job, rec in zip(jobs, run_parallel(jobs, _job, workers) or []):  # (7) apply
                inv = job["inv"]
                rec = rec if isinstance(rec, dict) else {}
                row = rec.get("parsed") if isinstance(rec.get("parsed"), dict) else None
                S["decisions"] += 1
                S["attempts"] += int(rec.get("attempts") or 0)
                if rec.get("cache_hit"):
                    S["cache_hits"] += 1
                else:
                    S["calls"] += 1
                # cache_hit / attempts are provenance, not simulation state: they differ between a cold run
                # and its warm replay, which would break invariant (l) byte-identical logs. They live in
                # llm_cache.jsonl (per call) and run_meta.counters (aggregate) instead.
                logd("dec", t=t, d=dstr, i=inv.id, prompt_sha=rec.get("prompt_sha"),
                     raw_sha=rec.get("raw_sha256"), status=rec.get("parser_status"),
                     arm=inv.arm, mood=(row or {}).get("mood"), reason=(row or {}).get("reason"),
                     violations=list(rec.get("violations") or ()))
                if row is None:
                    S["decision_failures"] += 1
                    continue
                cmt_out, aff_first, tr = apply_decision(inv, _adapt_record(rec, job["shown"], inv),
                                                        job["shown"], day)
                inv.memory.append(_memory_line(t, len(job["cards"]), tr, cmt_out, aff_first,
                                               row.get("mood"), row.get("reason")))
                del inv.memory[:-mem_days]
            halt = cfg["llm"].get("decision_failure_halt", True)
            thr = halt if isinstance(halt, float) and 0.0 < halt < 1.0 else 0.02
            if t >= 3 and halt and S["decisions"] \
                    and S["decision_failures"] / S["decisions"] > thr:
                return _finish({"decision_failure_halt":
                                {"pass": False,
                                 "rate": S["decision_failures"] / max(S["decisions"], 1)}},
                               3, t + 1)
            if dt_cur.day == 1:                    # DCA (spec §3): not feed-driven, no CxR gate
                for inv in invs:
                    if inv.dca and t >= inv.entry and inv.hold:
                        amt = inv.cash * 0.02
                        code = min(inv.hold)       # deterministic: first held code in sort order
                        nav = navday.get(code)
                        if amt < 100.0 or not nav:
                            S["dca_skipped"] += 1
                            continue
                        units = amt / nav
                        inv.cost[code] = (inv.hold[code] * inv.cost.get(code, nav) + amt) \
                            / (inv.hold[code] + units)
                        inv.hold[code] += units
                        inv.cash -= amt
                        S["dca_n"] += 1
                        S["dca_cny"] += amt
                        flows[FUNDS[code].family][quarter_of(dt_cur)]["sub"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=None, kind="dca", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav)
            if heat != heat_prev:                  # would mean mid-day signal mutation (inv a)
                inv_a_ok = False
            for inv in invs:                       # (8) lagged updates, visible from t+1 only
                orgs = touched.get(inv.id) or ()
                for org in sorted(inv.fam):
                    inv.fam[org] = inv.fam[org] * (1.0 - delta) + (1.0 if org in orgs else 0.0)
                for org in sorted(orgs):
                    if org not in inv.fam:
                        inv.fam[org] = 1.0
                for org in sorted(inv.aff):        # adstock decay (spec §6b, lambda_a = 0.9)
                    inv.aff[org] *= 0.9
                for org in sorted(set(inv.fam) | inv.follow | set(inv.aff)):
                    # spec §6: level 2 if follows; level 1 if exposure/affinity stock >= 1.0
                    if org in inv.follow:
                        lv = 2
                    elif inv.fam.get(org, 0.0) >= 1.0 or inv.aff.get(org, 0.0) >= 1.0:
                        lv = 1
                    else:
                        lv = 0
                    if lv != inv.flag.get(org, 0):
                        inv.flag[org] = lv
                        logd("st", t=t, d=dstr, i=inv.id, org=org, what="level", lv=lv,
                             fam=round(inv.fam.get(org, 0.0), 3),
                             aff=round(inv.aff.get(org, 0.0), 3))
            for pid in sorted(set(heat) | set(likes) | set(saves) | set(cw) | set(birth)):
                heat[pid] = hot_score(likes.get(pid, 0.0), saves.get(pid, 0.0),
                                      cw.get(pid, 0.0), t - birth.get(pid, t), gamma)
            if cfg["memory"] and refl_every > 0 and (t + 1) % refl_every == 0:  # (9)
                rjobs = [{"inv": inv, "llm": _make_llm(cfg),
                          "view": _agent_view(inv, persona.get(inv.id), {}, W, cfg, navday,
                                              hist, trend_cache, guba_view, last_trade,
                                              declined)} for inv in invs]

                def _rjob(job):
                    return reflect(job["view"], cfg, llm_cache, gov, job["llm"])

                for job, res in zip(rjobs, run_parallel(rjobs, _rjob, workers) or []):
                    inv = job["inv"]
                    res = res if isinstance(res, dict) else {}
                    rp = res.get("parsed") if isinstance(res.get("parsed"), dict) else {}
                    S["attempts"] += int(res.get("attempts") or 0)
                    if res.get("cache_hit"):
                        S["cache_hits"] += 1
                    else:
                        S["calls"] += 1
                    if rp.get("market_view") is not None:
                        inv.market_view = rp["market_view"]
                    if rp.get("risk_mood") is not None:
                        inv.risk_mood = rp["risk_mood"]
                    if rp.get("beliefs") is not None:
                        inv.beliefs = rp["beliefs"]
                    if rp.get("summary"):
                        inv.reflection = str(rp["summary"])
                    logd("refl", t=t, d=dstr, i=inv.id,
                         summary_sha=sha256_text(str(inv.reflection or "")))
            if (t + 1) % 30 == 0 or t == n_days - 1:  # (10) snapshots
                snapshot(str(t + 1), dstr)
            recent_list = (recent_list + [today])[-2:]
        extra = {} if inv_a_ok else {"a_midday_signal_mutation": {"pass": False}}
        return _finish(extra, 0, n_days)
    except CapStop:
        return _finish({"cap_stop": {"pass": False}}, 2, max(t + 1, 0))
    finally:
        elog.close()


def _print_summary(cfg, counters, checks, days, elapsed, checkout_oc, checkout_oc_cf):
    n = max(int(counters.get("decisions", 0)), 1)
    print("=== flowmirror.engine.loop summary ===")
    print(f"run_tag={cfg.get('run_tag')} days={days} agents={cfg.get('n_agents')} "
          f"active_agent_days={counters.get('active_days', 0)}")
    print(f"calls={counters.get('calls', 0)} cache_hits={counters.get('cache_hits', 0)} "
          f"decision_failure_rate={counters.get('decision_failures', 0) / n:.4f}")
    print(f"checkout_oc={dict(sorted(checkout_oc.items()))}")
    print(f"checkout_oc_cf={dict(sorted(checkout_oc_cf.items()))}")
    print(f"factor_switches={counters.get('factor_switches', 0)} "
          f"elapsed_s={round(elapsed, 1)}")
    fails = [k for k, v in sorted(checks.items()) if not v.get("pass", True)]
    print("invariants=PASS" if not fails else "invariants=FAIL(" + ",".join(fails) + ")")


def _load_cfg(path):
    obj = load_config(path)
    validate(obj, "run")
    cfg = deep_merge(DEFAULT_CONFIG, obj)
    resolve_paths(cfg, ROOT)
    return cfg


def _fake_inv(**over):
    inv = SimpleNamespace(id="A1", arm="T", rc=3, cash=100000.0, hold={}, cost={},
                          realized=0.0, fam={}, aff={}, follow=set(), flag={})
    for k, v in over.items():
        setattr(inv, k, v)
    return inv


def _fake_day(cfg, **over):
    day = SimpleNamespace(t=0, dstr="2024-01-02", dt_cur=date(2024, 1, 2), cfg=cfg, S=Counter(),
                          FUNDS={"F1": SimpleNamespace(r=4, qdii=False, family="famA")},
                          qdii_blk=set(), navday={"F1": 1.5},
                          flows={"famA": {quarter_of(date(2024, 1, 2)): {"sub": 0.0, "red": 0.0}}},
                          weights=None, comments=[], likes={}, saves={}, cw={},
                          checkout_oc=Counter(), checkout_oc_cf=Counter(), events=[],
                          last_trade={}, declined={})
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

    shown = {"P1": {"post_id": "P1", "org": "orgA", "code": "F1", "intent": "I2",
                    "intent_group": "I2"}}
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
    chk("week_key_format", _week_key(date(2024, 1, 1)) == "2024-W01")
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
    logp = os.path.join(cfg["out_dir"], "event_log.jsonl")
    rc = run_simulation(cfg)
    chk("mock_end_to_end_rc0", rc == 0)
    if rc != 0:
        return 1
    evs = {row.get("ev") for row in iter_jsonl(logp)}
    chk("required_event_kinds", {"post", "imp", "dec", "clim"} <= evs)
    sha1 = event_log_sha(logp)
    chk("replay_sha_identical", run_simulation(cfg) == 0 and sha1 is not None
        and sha1 == event_log_sha(logp))
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
        logp = os.path.join(cfg["out_dir"], "event_log.jsonl")
        if run_simulation(cfg) != 0:
            return 3
        sha1 = event_log_sha(logp)
        if run_simulation(cfg) != 0:
            return 3
        sha2 = event_log_sha(logp)
        m2 = _last_meta(cfg["out_dir"])
        same = sha1 is not None and sha1 == sha2
        print(f"replay-check sha1={sha1}")
        print(f"replay-check sha2={sha2} "
              f"warm_cache_calls={(m2.get('counters') or {}).get('calls')}")
        print(f"replay-check identical={bool(same)}")
        return 0 if same else 3
    return run_simulation(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
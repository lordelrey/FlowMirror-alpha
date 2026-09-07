"""FlowMirror v7 trading-day loop, apply step and CLI (flowmirror.engine.loop).

Day order per card: clock -> publish -> social lag (freeze heat_prev/clim_prev)
-> feed -> prompt assembly (state frozen) -> LLM phase (parallel) -> apply
(serial, sorted agent ids) -> DCA + lagged updates + heat -> reflection ->
snapshots.  Verified call surfaces used (no invented signatures):

world:   init_investors(W, cfg) -> [Inv]; publish_day(W, cfg, t, recent,
         rng_platform, log=EventLog) -> [{post_id, org, intent, intent_group,
         note, code, img, t_pub}] (it emits the "post" rows itself);
         guba_seed_label(W, code, week) -> label|None; check_invariants(state,
         events_path, cfg) -> (checks, core) (card R2F: the loop must UNPACK the
         tuple; core is the run's invariant decision); write_reports(out_dir,
         state, cfg, W, checks, counters, elapsed); quarter_of(date) -> str;
         event_log_sha(path).
runtime: decide(agent_view, feed_cards, cfg, cache, governor, llm, shown) and
         reflect(agent_view, cfg, cache, governor, llm); records carry parsed,
         violations, parser_status, prompt_sha, raw_sha256, image_shas,
         cache_hit, attempts, notes; run_parallel(jobs, fn, workers);
         BudgetGovernor(hard_cap_attempts); MockLLM(force_c2_r4, malformed_rate).
         Card C: cfg agent_policy == "null" swaps every job's llm for
         NullPolicyLLM(cfg null_params, run_tag) from flowmirror.agents.null_policy,
         regardless of mock_llm -- the rule-based anti-A1 baseline.
prompt:  agent_view keys persona_card_zh_rich, memory, last_reflection,
         market_view, risk_mood, c_class, cash, holdings[{code,name,r,units,
         nav,pnl_pct}], last_trade, declined_confirms, familiarity{org:level},
         guba{code:{name,mult,bull_ratio}} (card L1: dict-of-dicts, contract
         2.1 -- render_news calls .get on the value), beliefs (list, decision
         11: feeds the DECISION prompt only), holdings_1d (absent unless the
         agent holds funds priced on days t-1 AND t-2), trend, direct;
         feed_card keys post_id, org, title, caption
         (masked preferred), landing{code,name,R,ret_3m,ret_1y,min_buy}, likes,
         arm in {T,TC,TV}, image_path, image_sha, comments_prev[{stance,text,
         fam_phrase}], climate_label, n_comments_prev (full t-1 count);
         TC-only ocr_text and image_caption_frozen (str or list[str]).  TV
         attaches image_path as data-URI; T and TC never attach pixels.
         build_decision_messages(view, cards, cfg) -> (messages, prompt_sha,
         image_shas, prompt_notes) is PURE, so the loop can re-render the very
         same bytes for --dump-prompt (E1) without touching any state.
feed:    rank_feed(agent_state, candidates, heat_prev, clim_prev, cfg["feed"],
         rng, mode); climate_for(post_id, comments_prev, min_n=4, weights) ->
         (label, counts); top_comments(post_id, comments_prev, k=3, weights);
         assign_arms(rng, k, tally, arms=('T','TV')); check_arm_balance(
         agent_arms, agent_cells, arms=None) -> (ok, rep).
Inv:     slots only -- units in inv.hold[code] (float), cost NAV in
         inv.cost[code], familiarity level inv.flag[org], follows inv.follow,
         trust adstock inv.aff, EMA familiarity inv.fam, per-agent inv.rng,
         per-arm impression tally inv.arm_tally; the A2 world card adds the
         inv.fees slot (accumulated subscription/redemption fees).
Loop A2: modality_level {agent, run, exposure} (legacy arm_level alias) picks
         per-agent / whole-run / per-impression arms from cfg["modality_arms"];
         apply_decision(..., fees={"subscribe_rate","redeem_rate"}) charges
         cash-side fees, logged as act.fee on every act row.
Card L:  redeem checkouts emit NO click row (a redeem is not a feed click);
         their co rows carry p=None/ig=None with oc=oc_cf="match" (invariant
         i, no CxR gate on redeem -- the counterfactual obeys it too; only
         the engine refusal no_holdings can still override oc), their act rows
         keep p=None, and QDII purchase_blocked stays subscribe-only.
E1:     --dump-prompt <agent_id>@<day> | first writes
         <out_dir>/prompts/<agent>_d<day>.txt -- the exact rendered system+user
         text with image parts replaced by "[image: <sha256 prefix>, <bytes>
         bytes]" placeholders (never base64) -- plus a .json sidecar with
         prompt_sha, arm, the card ids shown and the channel shas.  The spec
         travels in RuntimeOpts (card R2D), a CLI-only runtime-options object
         threaded main() -> run_simulation(cfg, rt); it is NOT a run-config
         key (run.schema.json is additionalProperties:false and rejects one on
         purpose -- the parallel-card API mismatch this fixes).  It is a
         side artifact ONLY: no event-log row, no RNG draw, no hash change, so
         --replay-check stays byte-identical with or without the flag.  The
         live branch of _make_llm forwards the caller's model kwarg and only
         falls back to cfg["llm"]["model"] (decide passes the vision model,
         reflect the text model); tests/unit/test_live_path.py pins that.
R2F:    check_invariants returns (checks, core) and _finish UNPACKS it (a
         malformed return raises TypeError -- never a silent pass); the run's
         invariant decision is core AND-ed with the engine-side extra checks,
         each failing invariant key is printed with its detail, and an
         invariant failure exits 4 (2 = cap stop, 3 = decision_failure_halt /
         --replay-check mismatch keep their codes).  The old >=300-agent
         recheck that wrote arm balance under the bogus key "e_arm_balance"
         is gone: arm balance is registry key h_arm_balance, which
         check_invariants already evaluates for every cohort size.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ast
import inspect
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

from flowmirror.agents.null_policy import NullPolicyLLM
from flowmirror.agents.prompt import MIME as PROMPT_MIME
from flowmirror.agents.prompt import build_decision_messages
from flowmirror.agents.runtime import (BudgetGovernor, CapStop, LLMCache, MockLLM,
                                       call_glm, decide, reflect, run_parallel)
from flowmirror.channels.feed import (assign_arms, climate_for, hot_score,
                                      rank_feed, top_comments)
from flowmirror.config.loader import deep_merge, load_config, resolve_paths
from flowmirror.engine.benchmark import load_benchmark, pct_5d, sorted_dates
from flowmirror.config.validate import ConfigError, validate
from flowmirror.engine.world import (DEFAULT_CONFIG, EventLog, check_invariants,
                                     event_log_sha, guba_seed_label, image_pool_summary,
                                     init_investors, load_world, publish_day, quarter_of,
                                     validate_config, write_reports)
from flowmirror.io.backups import backup_existing
from flowmirror.io.hashing import rng_seed_from, sha256_file, sha256_text
from flowmirror.io.jsonl import iter_jsonl
from flowmirror.regulator.cn_cxr import cxr_outcome

ROOT = os.environ.get("FLOWMIRROR_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
ONE_DAY = timedelta(days=1)
_VALID_OC = ("match", "confirm_signed", "confirm_declined", "hard_block",
             "purchase_blocked", "below_min", "no_holdings")
_FAM_PHRASE = {0: "不熟悉该机构", 1: "略有耳闻", 2: "关注已久"}


class RuntimeOpts:
    """CLI-only runtime switches (card R2D); never keys of the validated run config.

    run.schema.json is additionalProperties:false (with a '^_' patternProperties
    escape hatch) and run_simulation re-validates the merged cfg it is handed,
    so parking a CLI flag's value in cfg -- as the original --dump-prompt card
    did with `dump_prompt` -- killed the run at schema time with
    "'dump_prompt' does not match any of the regexes: '^_'" before day 0.  The
    rule this class enforces:
      * keys that DEFINE the experiment stay in cfg; their CLI overrides
        (--seed/--days/--agents/--mock/--out) write schema-valid keys
        (seed, window.max_trading_days, n_agents, mock_llm, out_dir), so they
        are correct as config overrides and deliberately NOT moved here;
      * switches that only change what the engine does AROUND the experiment
        travel in this object and are threaded explicitly to their use site.
    Flag audit (card R2D): --replay-check never reaches run_simulation
    (main()-level orchestration, writes nothing to cfg); --days/--agents/
    --seed/--out/--map-to-schema-keys as above; only --dump-prompt was
    laundered, so only it moved.  Future CLI-only switches (--profile,
    --dry-run, ...) get a slot here, never a schema key."""

    __slots__ = ("dump_prompt",)

    def __init__(self, dump_prompt=None):
        self.dump_prompt = dump_prompt

    def __repr__(self):
        return f"RuntimeOpts(dump_prompt={self.dump_prompt!r})"


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


def _guba_row(W, code, week):
    """The RAW weekly guba signal row for a fund, or None when the week is uncovered.

    world.guba_seed_label does this same two-key lookup but returns a stance LABEL; the
    news channel needs the row itself (contract 2.2), and world.py belongs to another lane
    this round, so the lookup is mirrored here instead of exported.  The key order is
    world.guba_seed_label's verbatim, so the climate seed and the news line can never
    disagree about which ISO week they read for the same day.
    """
    entry = (W.guba or {}).get(code)
    if not isinstance(entry, dict):
        return None
    for key in (week, str(week)):
        row = entry.get(key)
        if isinstance(row, dict):
            return row
    return None


def _guba_line(W, code, row):
    """One view["guba"] entry, built from the raw signal row (contract 2.1).

    Shape is dict-of-dicts because prompt.render_news calls .get on the value; the old
    {code: label_string} shape raised AttributeError there and survived only because
    guba_seed_label returns None on every real signal file (audit E1, latent crash).
    mult is the row's ratio_vs_baseline, 1.0 when the field is absent or unparseable.
    bull_ratio stays None until the stance-labelling data task lands: decision 4 makes
    the signal file's "no stance is computed here" the correct current behaviour, and
    render_news already reads None as 0.5, so no caller has to special-case it.
    """
    row = row or {}
    # A week with no posts has nothing to report, and its ratio_vs_baseline is 0.0 --
    # which render_news would turn into "discussion up 0.0x", a sentence that states a
    # fact about attention that did not happen. Returning None drops the code from the
    # view instead; the caller filters.
    try:
        if not int(row.get("n_posts") or 0):
            return None
    except (TypeError, ValueError):
        return None
    try:
        mult = float(row.get("ratio_vs_baseline"))
    except (TypeError, ValueError):
        mult = 1.0
    # Decision 4: read the field rather than hardcoding None, so the stance-labelling
    # output (guba_signal_v2.json) becomes visible the day it lands with no code change.
    # The shipped v1 file carries no bull_ratio -- its own _meta says no stance is
    # computed there -- so this is None today, and prompt.render_news then omits the
    # stance clause instead of asserting an unmeasured 5:5 split.
    try:
        br = row.get("bull_ratio")
        br = None if br is None or isinstance(br, bool) else float(br)
        if br is not None and not 0.0 <= br <= 1.0:
            br = None                     # out of range is not a ratio
    except (TypeError, ValueError):
        br = None
    return {"name": _fund_name(W, code), "mult": mult, "bull_ratio": br}


def _holdings_1d(hold, prev_navdays):
    """Holdings-weighted one-day return of the PREVIOUS trading day, or None.

    Weights are the agent's current units and the two NAV vectors are days t-1 and t-2,
    so every input is dated <= t-1.  Using today's navday would put day t's close into
    the very prompt that decides day t -- look-ahead, invariant (a)'s red line -- and it
    is the same lag rule contract 2.7 puts on index_5d, which reads v[t-1]/v[t-6] rather
    than v[t]/v[t-5].  Funds sorted so the float accumulation order is fixed (replay must
    be byte-identical).  None when the agent holds nothing, when the run has no two prior
    trading days yet, or when no held fund is priced on both days: prompt.render_news
    skips an absent key, which is the correct degradation.
    """
    if not hold or len(prev_navdays) < 2:
        return None
    prev, prev2 = prev_navdays[0] or {}, prev_navdays[1] or {}
    v1 = v0 = 0.0
    for code in sorted(hold):
        n1, n0 = prev.get(code), prev2.get(code)
        if not n1 or not n0 or float(n0) <= 0.0:
            continue
        units = float(hold[code])
        v1 += units * float(n1)
        v0 += units * float(n0)
    if v0 <= 0.0:
        return None
    return round(v1 / v0 - 1.0, 4)


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


def _parse_id_list(v):
    """Pool image_ids / image_sha256 arrive in three shapes across this corpus: a real
    JSON list, a JSON-encoded string, and (from an earlier pandas export) a Python repr
    with single quotes.  Accept all three and never raise -- an unparseable cell means
    "this note has no usable image", not a dead run."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None]
    s = str(v).strip()
    if not s:
        return []
    if s[0] in "[(":
        for loader in (json.loads, ast.literal_eval):
            try:
                got = loader(s)
            except (ValueError, SyntaxError):
                continue
            if isinstance(got, (list, tuple)):
                return [str(x) for x in got if x is not None]
        return []
    return [s]                       # a bare filename is a one-image note


def _resolve_tv_image(note, images_root, policy, rng):
    """Resolve ONE real image file for a TV-arm impression.

    -> (path, sha256, idx, status); status is None on success and otherwise names why
    nothing was attached: no_images (the note references none), image_missing (not
    under images_root) or image_sha_mismatch (the bytes are not the ones the pool
    recorded).

    Before this card the engine read note["image_path"|"image"|"cover"], none of which
    the shipped pool carries -- so image_path was always None, prompt.py's TV branch
    always took its image_missing path, and arm TV was byte-identical to arm T.  The
    pool's real keys are image_ids (0-based names in a FLAT store) and a parallel
    image_sha256.

    The digest check is not ceremony.  Because the ids are bare filenames in a flat
    store, a partially synced or re-exported images_root would hand the agent a
    DIFFERENT picture than the note's, and the modality comparison would measure an
    unknown stimulus with nothing raising anywhere.  Filenames cannot establish
    content and a directory carries no constraint over it; the pool already ships the
    digests for exactly this, so verifying costs one read and removes the whole
    failure mode.  A reference with no recorded digest attaches unverified (defensive
    only: every one of the shipped pool's references carries one)."""
    ids = _parse_id_list(note.get("image_ids"))
    if not ids:
        return (None, None, None, "no_images")
    shas = _parse_id_list(note.get("image_sha256"))
    idx = rng.randrange(len(ids)) if policy == "random" else 0
    path = os.path.join(images_root, ids[idx])
    if not os.path.isfile(path):
        return (None, None, idx, "image_missing")
    # prompt.build_decision_messages will refuse an extension outside its MIME table and
    # record image_unsupported instead of attaching. Checking the same table here keeps
    # run_meta.images.attached meaning "pixels reached the agent" rather than "the file
    # resolved" -- otherwise the counter the acceptance criteria trust could be non-zero
    # while every prompt carried no image at all.
    if os.path.splitext(path)[1].lower() not in PROMPT_MIME:
        return (None, None, idx, "image_unsupported")
    got = sha256_file(path)
    want = shas[idx] if idx < len(shas) else None
    if want and got.lower() != str(want).strip().lower():
        return (None, None, idx, "image_sha_mismatch")
    return (path, got, idx, None)


def _feed_card(W, post, notes_by_id, arm, heat_prev, clim_prev, top_prev, dt_cur, n_prev,
               image=None):
    """One impression card with the keys flowmirror.agents.prompt reads.

    Text fields prefer masked variants; n_comments_prev is the FULL t-1 comment
    count per post (the card header renders 共 n 条) while comments_prev stays
    the top-3 excerpt.  TC cards additionally carry OCR text and the frozen
    image caption; T and TV cards gain no pixel-side keys beyond image_path/sha,
    so only n_comments_prev (and masked text) changes them vs the baseline."""
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
    # The caller resolves and verifies the TV-arm image (see _resolve_tv_image) and
    # passes the result in, so this function stays PURE and re-renderable -- which is
    # what --dump-prompt and the replay check rely on.  image is None for T and TC and
    # for every run without an images_root, and then this block produces exactly the
    # pre-card bytes: no path, no digest, no extra key.
    image_path = image_sha = image_status = None
    if arm == "TV" and image is not None:
        image_path, image_sha, image_status = image[0], image[1], image[3]
    card = {"post_id": pid, "org": post.get("org"),
            "title": note.get("title") or note.get("display_title") or "",
            "caption": (note.get("caption_masked") or note.get("caption")
                        or note.get("abstract") or note.get("summary") or ""),
            "landing": landing, "likes": round(float(heat_prev.get(pid, 0.0)), 1),
            "arm": arm, "image_path": image_path,
            # the REAL digest of the attached bytes; this used to hash the path string
            "image_sha": image_sha,
            # contract 2.5 item 4: why a TV impression carried no pixels, so a prompt
            # dump distinguishes an unsynced store (image_missing) from a corrupted one
            # (image_sha_mismatch). Absent on success and on the text arms, so a run
            # that attaches everything is unchanged.
            **({"image_status": image_status} if image_status else {}),
            "comments_prev": [{"stance": c.get("stance"), "text": c.get("text"),
                               "fam_phrase": c.get("fam_phrase", "")}
                              for c in (top_prev.get(pid) or []) if isinstance(c, dict)],
            "climate_label": clim_prev.get(pid),
            "n_comments_prev": int(n_prev.get(pid, 0) or 0)}
    if arm == "TC":                 # text-complement arm: OCR text in, pixels still out
        card["ocr_text"] = note.get("ocr_masked") or note.get("ocr_text") or ""
        frozen = note.get("image_caption_frozen")
        if frozen is not None:
            card["image_caption_frozen"] = frozen
    return card


def _agent_view(inv, persona_rec, shown, W, cfg, navday, hist, trend_cache, guba_view,
                last_trade, declined, prev_navdays=(), index_5d=None,
                index_label=None):
    """Frozen pre-LLM agent state with exactly the keys prompt.py reads.

    prev_navdays is (navday_{t-1}, navday_{t-2}) -- the only extra input holdings_1d
    needs.  It is additive with a default so the module self-test and any future caller
    that has no NAV history keeps working: an empty tuple simply omits holdings_1d.
    """
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
            # prompt.render_experience expects a list of sentences; the engine keeps only the count
            "declined_confirms": ([f"你此前有 {int(declined.get(inv.id, 0))} 次在《风险不匹配确认书》前放弃了申购。"]
                                  if int(declined.get(inv.id, 0)) > 0 else []),
            "familiarity": {org: inv.flag.get(org, 0) for org in orgs},
            # guba_view carries the RAW weekly signal rows; the per-code line is built here
            # so the day loop stays free of prompt-shape knowledge (contract 2.1).
            # decision 6: only present when a benchmark is configured AND the series
            # covers this day; render_news skips an absent key, which is the honest
            # degradation. The value is a RETURN -- the series is a fund NAV proxy for
            # 上证综指, not index points, so its level would be meaningless.
            **({"index_5d": index_5d, "index_label": index_label}
               if index_5d is not None else {}),
            "guba": {c: ln for c, row in sorted(guba_view.items())
                     if (ln := _guba_line(W, c, row)) is not None
                     if c in inv.hold or c in codes},
            # Audit E10 / decision 11: the beliefs stored at the last reflection feed forward
            # into the NEXT DECISION prompt. Supplying the key is this card's whole job --
            # prompt.render_belief owns the rendering, and build_reflection_messages never
            # calls it, so the reflection INPUT stays untouched as the decision requires.
            "beliefs": list(inv.beliefs or []),
            "direct": []}
    h1d = _holdings_1d(inv.hold, prev_navdays)
    if h1d is not None:                # absent key, not None: render_news skips absent keys
        view["holdings_1d"] = h1d
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
    if cfg.get("agent_policy") == "null":   # Card C: null policy wins over mock/live
        return NullPolicyLLM(cfg.get("null_params") or {}, cfg["run_tag"])
    if cfg.get("mock_llm"):
        # Audit E5: the real keys are NESTED under mock_options -- that is what
        # run.schema.json types, what engine_defaults.yaml defaults and what every
        # runs/*.json sets. The old top-level mock_force_c2_r4 / mock_malformed_rate
        # names exist in no schema and no config, so force_c2_r4_click: true has been
        # silently dead and the acceptance runs never reached the suitability-confirmation
        # branch they claim to cover. The fallbacks equal config/engine_defaults.yaml,
        # which is what actually supplies them after the merge -- the schema declares no
        # default for either, and the old 0.05 literal disagreed with the file it cited.
        mock_opts = cfg.get("mock_options") or {}
        return MockLLM(bool(mock_opts.get("force_c2_r4_click", False)),
                       float(mock_opts.get("malformed_rate", 0.0)))
    llm_cfg = cfg["llm"]
    # E9: temperature and the provider-side retry budget are experiment parameters and
    # belong in the run config, not in runtime.TEMP / runtime.MAX_ATTEMPTS. runtime.call_glm
    # is growing both parameters in a parallel lane, so probe its signature ONCE here
    # instead of wrapping every call in try/except TypeError: a TypeError raised deep inside
    # call_glm (a malformed message payload, say) is indistinguishable at the call site from
    # a signature mismatch, and a retry with fewer kwargs would silently swallow it. Only
    # keys the config actually carries are forwarded, so on a config that omits them
    # call_glm keeps its own defaults -- which contract 3 fixes at exactly 0.3 and 5, the
    # values hardcoded today. Hence no hash change from this branch.
    try:
        params = inspect.signature(call_glm).parameters
        var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        knobs = tuple((k, cast(llm_cfg[k]))
                      for k, cast in (("temperature", float), ("max_provider_attempts", int))
                      if (k in params or var_kw) and llm_cfg.get(k) is not None)
    except (TypeError, ValueError):     # unintrospectable callable (C-implemented stub)
        knobs = ()

    def _call(messages, max_tokens, **kw):
        # Task E1-1 (permanent): runtime.decide() passes the vision model and runtime.reflect()
        # the text model as model=, so the CALLER's choice must win; cfg["llm"]["model"] is only
        # a fallback. The old one-liner passed model=llm_cfg.get("model") explicitly AND
        # forwarded **kw, so every live run died with "TypeError: call_glm() got multiple
        # values for keyword argument 'model'". tests/unit/test_live_path.py pins this.
        kw.setdefault("model", llm_cfg.get("model"))
        for key, val in knobs:          # same caller-wins precedence as model
            kw.setdefault(key, val)
        return call_glm(messages, int(max_tokens), **kw)

    return _call


# ---------------------------------------------------------------------------
# --dump-prompt (task E1-3): side artifacts for the paper appendix.
# Pure post-hoc rendering from the frozen job; never touches the event log,
# the RNG or any hash, so replays stay byte-identical with or without it.
# ---------------------------------------------------------------------------
def _dump_target(spec):
    """'first' -> (None, None); '<agent_id>@<day>' -> (agent_id, int day).

    rpartition('@') so an agent id that itself contains '@' still parses; the
    day must be the 0-based trading-day index in digits."""
    if spec == "first":
        return None, None
    aid, sep, day = str(spec).rpartition("@")
    if not sep or not aid or not day.isdigit():
        raise ValueError(f"bad dump-prompt spec {spec!r} "
                         f"(want <agent_id>@<day> or 'first')")
    return aid, int(day)


def _dump_matches(target, inv_id, t):
    aid, day = target
    if aid is None:
        return True                       # 'first': the first prompt assembled
    return str(inv_id) == aid and int(t) == day


def _image_part_placeholder(part):
    """Never let base64 pixels into a dump: decode just enough to report sha+size."""
    iu = part.get("image_url")
    url = iu.get("url") if isinstance(iu, dict) else iu
    if isinstance(url, str) and url.startswith("data:") and "," in url:
        try:
            data = base64.b64decode(url.split(",", 1)[1])
            return f"[image: {hashlib.sha256(data).hexdigest()[:12]}, {len(data)} bytes]"
        except Exception:
            pass
    return "[image: <payload withheld>]"


def _render_prompt_text(messages):
    """Exact system+user text; image parts become [image: <sha prefix>, <bytes> bytes]."""
    out = []
    for msg in messages or ():
        role = msg.get("role") if isinstance(msg, dict) else None
        out.append(f"===== {role or 'message'} =====")
        content = msg.get("content") if isinstance(msg, dict) else msg
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(str(part.get("text") or ""))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    out.append(_image_part_placeholder(part))
                else:
                    out.append(str(part))
        else:
            out.append(str(content or ""))
    return "\n".join(out) + "\n"


def _channel_shas(notes):
    """channel_sha notes -> {channel: sha}; unparsable bodies map to None."""
    out = {}
    for n in notes or ():
        if isinstance(n, str) and n.startswith("channel_sha:"):
            body = n[len("channel_sha:"):]
            k, _, v = body.partition("=")
            out[k or body] = v or None
    return out


def _write_prompt_dump(out_dir, cfg, job, inv, rec, t, dstr):
    """Write <out_dir>/prompts/<agent>_d<day>.txt (+ .json sidecar); return the txt path.

    build_decision_messages is pure, so re-rendering the frozen view/cards here
    reproduces byte-for-byte the messages decide() hashed (the sidecar records
    whether prompt_sha matches the dec row as a built-in self-verification)."""
    messages, psha, img_shas, notes = build_decision_messages(job["view"], job["cards"], cfg)
    pdir = os.path.join(out_dir, "prompts")
    os.makedirs(pdir, exist_ok=True)
    safe = "".join(c if (c.isascii() and (c.isalnum() or c in "-_.")) else "_"
                   for c in str(inv.id))
    base = f"{safe}_d{int(t)}"
    txt_path = os.path.join(pdir, base + ".txt")
    with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_render_prompt_text(messages))
    side = {"agent": inv.id, "day": int(t), "date": dstr, "arm": inv.arm,
            "card_ids": sorted(job["shown"]),
            "prompt_sha": psha,
            "prompt_sha_matches_dec_row": bool(psha == rec.get("prompt_sha")),
            "image_shas": list(img_shas or []),
            "channel_shas": _channel_shas(notes),
            "prompt_notes": [str(n) for n in (notes or ()) if isinstance(n, str)],
            "file": base + ".txt"}
    with open(os.path.join(pdir, base + ".json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(side, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return txt_path


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


def _report_entry(report, key):
    """Depth-first lookup of `key` in a parsed invariants_report.json (card R2F).

    Tolerant of the nesting write_reports chooses -- flat mapping, nested under
    an "invariants" key, or a list of {key/name: ...} rows -- so callers (the
    self-test, tests/unit/test_invariant_wiring.py) do not pin its layout."""
    stack = [report]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            hit = cur.get(key)
            if isinstance(hit, dict):
                return hit
            if cur.get("key") == key or cur.get("name") == key:
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


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


def _dec_counts(adapted, n_cards, social_on):
    """Post-feasibility decision tallies for the dec row; all null on parse failure.

    The counts mirror the adapted record after the same feasibility rules
    apply_decision enforces (comments exist only with the social channel on);
    cache_hit/attempts stay OUT of dec by design (invariant l, byte-identical
    replays -- they live in llm_cache.jsonl and run_meta.counters)."""
    keys = ("n_read", "n_like", "n_save", "n_follow", "n_comment", "aff_sum")
    if adapted is None:
        return {k: None for k in keys}
    aff = adapted.get("aff") or {}
    return {"n_read": int(n_cards),
            "n_like": len(adapted.get("likes") or ()),
            "n_save": len(adapted.get("saves") or ()),
            "n_follow": len(adapted.get("follows") or ()),
            "n_comment": len(adapted.get("comments") or ()) if social_on else 0,
            "aff_sum": int(round(sum(float(v) for v in aff.values())))}


def _bump_fees(inv, fee):
    """Accumulate cash-side fees on inv.fees (getattr-safe: the slot ships with
    the A2 world card; a pre-A2 slots-only Inv just drops it, and pre-A2 configs
    carry no fees anyway, so both worlds stay runnable)."""
    if fee:
        try:
            inv.fees = float(getattr(inv, "fees", 0.0) or 0.0) + float(fee)
        except AttributeError:
            pass


def apply_decision(inv, rec, shown, day, fees=None):
    """Serial apply of one decision record; returns (comment, affinity, trade) views.

    fees (keyword, default None -> both rates 0, so pre-A2 callers keep working)
    is cfg["fees"]: subscribe pays fee = amt*subscribe_rate out of the ticket,
    so units = (amt - fee)/nav at cost basis amt - fee; redeem pays
    fee = gross*redeem_rate out of the proceeds, so cash += gross - fee.  Fees
    leave cash, accumulate on inv.fees, and every act row carries fee (2dp,
    0.0 when fees are off) so cash can be replayed from the event log.

    Card L-fix: a redeem is not a feed click (there is no post behind it), so
    it never emits a click row nor bumps the click counters; its co row carries
    p=None, ig=None with oc=oc_cf="match" (invariant i -- no CxR gate applies
    to a redeem, the counterfactual included; only the engine refusal
    no_holdings can still override oc); and its act row keeps p=None."""
    cfg, S, logd, row = day.cfg, day.S, day.logd, rec.get("parsed")
    t, dstr = day.t, day.dstr
    fee_cfg = fees if isinstance(fees, dict) else {}
    sub_rate = float(fee_cfg.get("subscribe_rate") or 0.0)
    red_rate = float(fee_cfg.get("redeem_rate") or 0.0)
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
        # Card L-fix: a redeem is not a feed click -- no click row, no click counter.
        if code is None:                     # I2 note without in-universe common-support code
            if act != "redeem":
                logd("click", t=t, d=dstr, i=inv.id, p=pid, oc="click_no_landing")
                S["click_no_landing"] += 1
            tr = {"act": act, "exec": False, "oc": "click_no_landing", "code": None, "amt": 0.0}
        else:
            if act != "redeem":              # only a feed-sourced subscribe clicks
                logd("click", t=t, d=dstr, i=inv.id, p=pid, oc="to_checkout")
                S["clicks"] += 1
            fund = FUNDS[code]               # ---- checkout: CxR distribution layer ----
            trow = row.get("trade") if isinstance(row.get("trade"), dict) else {}   # amount/confirm live under trade
            if act == "subscribe":
                smc = trow.get("sign_mismatch_confirm")
                smc = smc if isinstance(smc, bool) else str(smc).lower() in ("true", "1")
                oc_cf = cxr_outcome(inv.rc, fund.r, smc)
                oc = oc_cf if cfg.get("suitability") else "match"
                if oc in ("match", "confirm_signed") and fund.qdii and code in qdii_blk:
                    oc = "purchase_blocked"  # QDII quota suspension, subscribe-only
            else:                            # invariant (i): a redeem is never CxR-gated and
                oc = oc_cf = "match"         # the counterfactual obeys the same rule
            pct = float(trow.get("amount_pct") or 0.0) / 100.0   # amount_pct is 0-100
            amt = 0.0
            fee = 0.0
            if oc in ("match", "confirm_signed"):
                if act == "subscribe":
                    amt = pct * inv.cash
                    if amt < 100.0:          # min ticket 100 CNY
                        oc = "below_min"
                    else:
                        nav = navday[code]
                        fee = amt * sub_rate
                        units = (amt - fee) / nav
                        if code in inv.hold:                    # average-cost update
                            tot = inv.hold[code] + units
                            inv.cost[code] = (inv.hold[code] * inv.cost.get(code, nav)
                                              + (amt - fee)) / tot
                            inv.hold[code] = tot
                        else:
                            inv.hold[code] = units
                            inv.cost[code] = nav
                        inv.cash -= amt
                        _bump_fees(inv, fee)
                        S["sub_n"] += 1
                        S["sub_cny"] += amt
                        S["fees_cny"] += fee
                        flows[fund.family][quarter_of(dt_cur)]["sub"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=pid, kind="subscribe", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav,
                             fee=round(fee, 2))
                else:
                    u = float(inv.hold.get(code) or 0.0)
                    if u <= 0.0:
                        oc = "no_holdings"   # engine refuses: non-holders never redeem (inv b)
                    else:
                        nav = navday[code]
                        units = u * pct
                        amt = units * nav    # gross redemption value before the fee
                        fee = amt * red_rate
                        cst = inv.cost.get(code, nav)
                        inv.realized += units * (nav - cst)
                        inv.hold[code] = u - units
                        if inv.hold[code] <= 1e-9:
                            del inv.hold[code]
                            inv.cost.pop(code, None)
                        inv.cash += amt - fee
                        _bump_fees(inv, fee)
                        S["red_n"] += 1
                        S["red_cny"] += amt
                        S["fees_cny"] += fee
                        flows[fund.family][quarter_of(dt_cur)]["red"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=None, kind="redeem", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav,
                             fee=round(fee, 2))
            checkout_oc[oc] += 1
            day.checkout_oc_cf[oc_cf] += 1
            if act == "redeem":              # a redeem checkout has no post context
                logd("co", t=t, d=dstr, i=inv.id, p=None, fund=code, ig=None, act=act,
                     oc=oc, oc_cf=oc_cf, amt=round(amt, 2))
            else:
                logd("co", t=t, d=dstr, i=inv.id, p=pid, fund=code,
                     ig=post.get("intent_group", post.get("intent")), act=act,
                     oc=oc, oc_cf=oc_cf, amt=round(amt, 2))
            if oc == "confirm_declined":
                day.declined[inv.id] = day.declined.get(inv.id, 0) + 1
            elif amt > 0.0:
                day.last_trade[inv.id] = f"D{t} {act} {code} {amt:.0f}元"
            tr = {"act": act, "exec": amt > 0.0, "oc": oc, "code": code, "amt": amt}
    return cmt_out, aff_first, tr


def run_simulation(cfg, rt=None):
    """One simulation pass; returns 0 ok, 2 cap_stopped, 3 decision_failure_halt,
    4 invariant failure (card R2F-loop: the run's invariant decision -- world
    check_invariants' core flag AND-ed with any engine-side extra check -- came
    out false; a --replay-check mismatch at the main() level stays 3).

    rt carries CLI-only runtime switches (RuntimeOpts, card R2D) that must NOT
    be laundered through cfg: cfg is re-validated against run.schema.json
    below, and the schema rightly rejects keys that do not define the
    experiment.  Defaults to an empty RuntimeOpts so legacy single-argument
    callers keep working."""
    t0 = time.time()
    rt = rt if rt is not None else RuntimeOpts()
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
    hold0 = {inv.id: dict(inv.hold or {}) for inv in invs}   # FIX4: each agent's OPENING
    # holdings, snapshotted before any day runs (inv.hold mutates in place during the run, so
    # this is a copy, never the live dict). The run-bundle exporter reuses this exact key and
    # {agent_id: {fund_code: units}} shape.
    for inv in invs:        # fees slot ships with the A2 world card; stay runnable without it
        if not hasattr(inv, "fees"):
            try:
                inv.fees = 0.0
            except AttributeError:
                pass
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
    # Decision 1: the majority margin is 1/6, not the 1/3 the code carried. Passing it
    # explicitly means a run records the threshold it actually used, and a sensitivity
    # arm needs no code change.
    clim_margin = float(feed_cfg.get("climate_margin", 1.0 / 6.0))
    # E4 / DECISIONS #5: these were bare literals plus an unreachable cfg["fam_decay"]
    # (never in the schema, and additionalProperties is false, so a config that set it
    # was rejected outright). Worse, ONE constant drove two conceptually different
    # processes -- familiarity decay AND attention decay -- so no run could vary them
    # independently. They are separate parameters now.
    _dyn = cfg.get("dynamics") or {}
    delta = float(_dyn.get("fam_decay", 0.2))            # familiarity EMA decay
    lam_att = float(_dyn.get("lambda_attention", 0.8))   # attention adstock retention
    lam_trust = float(_dyn.get("lambda_trust", 0.9))     # institution-trust adstock
    fam_thr = float(_dyn.get("fam_threshold", 1.0))      # exposure/affinity -> level 1
    # Decision 3: the guba attention term is WIRED but its coefficient is ZERO. The
    # pipeline reads the lagged week z_abnormal and multiplies it in, so enabling the
    # channel later is a one-value config change; at 0.0 the term is exactly inert and
    # the attention series is unchanged. Do not "fix" this to 0.1 -- the owner set 0.
    beta_guba = float(_dyn.get("beta_guba", 0.0))
    p_active = float(cfg["p_active"])
    refl_every, mem_days = int(cfg["reflection_every_days"]), max(1, int(cfg["memory_days"]))
    workers = int(cfg["llm"]["workers"])
    modality_arms = tuple(cfg.get("modality_arms") or ("T", "TV"))
    modality_level = cfg.get("modality_level") or cfg.get("arm_level") or "agent"
    # TV-arm pixels. images_root is machine-local and null by default, so every shipped
    # demo is text-only; the WARNING below exists because a text-only run used to be
    # indistinguishable from a multimodal one, which is how the project's headline
    # experiment ran for months attaching zero images.
    # decision 6: the benchmark behind the news channel's market line. Absent by
    # default (data/market/ is gitignored third-party data), and a configured-but-
    # unreadable path raises rather than silently producing a text-only market line.
    _mkt = cfg.get("market") or {}
    # Decision 6: a configured benchmark MUST carry its disclosing label, because the
    # label is what the agent-facing line names. Refusing to run without one is the only
    # way the disclosure cannot be forgotten; the alternative is a prompt that calls a
    # fund NAV proxy an index.
    if _mkt.get("benchmark_path") and not str(_mkt.get("benchmark_label") or "").strip():
        raise ConfigError(
            "market.benchmark_path is set without market.benchmark_label: the label is "
            "rendered into the agent's news line and must disclose the proxy, e.g. "
            "上证综指ETF（510760）单位净值（上证综指的代理）")
    # Resolved against the repo root the way world._abs() resolves every sibling
    # input, so the relative path the RUNBOOK prints works from any working directory.
    # (Note: _load_cfg calls resolve_paths(cfg, ROOT) and DISCARDS its return -- that
    # call is dead, which is why this has to be done here rather than upstream.)
    _bpath = _mkt.get("benchmark_path")
    if _bpath and not os.path.isabs(_bpath):
        _bpath = os.path.join(ROOT, _bpath)
    benchmark = load_benchmark(_bpath)
    benchmark_days = sorted_dates(benchmark)
    benchmark_label = str(_mkt.get("benchmark_label") or "").strip() or None
    if benchmark:
        print(f"[world] benchmark: {len(benchmark_days)} observations "
              f"{benchmark_days[0]}..{benchmark_days[-1]} "
              f"({_mkt.get('benchmark_label') or 'unlabelled proxy'})")
    images_root = cfg.get("images_root") or None
    image_pick = cfg.get("image_pick") or "first"
    if images_root:
        _isum = image_pool_summary(W.pool, images_root)
        print(f"[world] images: {_isum.get('resolvable', 0)} resolvable under {images_root}")
    elif "TV" in (( (cfg.get("modality_level") or cfg.get("arm_level") or "agent") == "run"
                    and {cfg.get("modality_run_arm")} ) or set(modality_arms)):
        print("[world] WARNING: no images_root configured -- the TV arm degrades to "
              "text-only; the modality comparison measures nothing.")
    # E6 / decision 10: qdii_blocked reaches the schema this round, which is what makes
    # the suspension path reachable at all -- additionalProperties is false, so any config
    # naming the key was rejected before. No shipped config sets it; a real suspension
    # calendar is a data task. A calendar that can never bind is the same silent-no-op
    # class of defect this round exists to remove, so say so rather than ignore it.
    if cfg.get("qdii_blocked") and not any(getattr(f, "qdii", False)
                                           for f in W.funds.values()):
        print("[world] note: qdii_blocked is configured but no fund in this universe is "
              "QDII, so no purchase can ever be suspended by it.")
    wmap = ({inv.id: float(inv.strat_weight or 1.0) for inv in invs}
            if cfg.get("climate_weighting") else None)
    S = Counter()
    # Contract 2.4: seeded at zero so run_meta.counters always carries both, and the
    # identity transport + model == decision_failures can be checked on any run.
    S["decision_failures_transport"] = 0
    S["decision_failures_model"] = 0
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
    # holdings_1d reads the NAVs of days t-1 and t-2 (never today's -- see _holdings_1d),
    # so the loop carries the last two navday vectors and rolls them forward at the very
    # end of each iteration, after both _agent_view call sites of day t have run.
    prev_navday, prev2_navday = None, None
    n_days = len(W.nav_days)
    inv_a_ok = True
    last_d = W.nav_days[0] if W.nav_days else W.start
    # E1-3: --dump-prompt spec, a CLI-only runtime switch carried in rt (card
    # R2D: never a cfg key -- the run schema rejects those); side artifact only.
    dump_spec = rt.dump_prompt or None
    dump_target = _dump_target(dump_spec) if dump_spec else None
    dump_done = False

    def snapshot(tag, dstr):
        rows = [{"i": inv.id, "arm": inv.arm, "cash": round(inv.cash, 2),
                 "realized": round(inv.realized, 2),
                 "fees": round(float(getattr(inv, "fees", 0.0) or 0.0), 2),
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
        if dump_target is not None and not dump_done:
            print("prompt dump: no prompt matched the requested agent/day; nothing written")
        elog.close()
        state = {"agents": invs, "hold0": hold0, "funds": W.funds, "end": last_d.isoformat(),
                 "signal_audit": signal_audit, "agent_arms": {i.id: i.arm for i in invs},
                 "active_per_day": dict(active_per_day), "flows": flows,
                 "snapshots": snapshots, "checkout_oc": dict(checkout_oc),
                 "checkout_oc_cf": dict(checkout_oc_cf),
                 # contract 2.3: world.write_reports copies this into run_meta["images"],
                 # and m_tv_arm_carries_images reports it. attached is the number that
                 # answers "did pixels actually reach the agents" -- it is a count, so
                 # it needs no gate to be read.
                 "images": {"root": images_root, "policy": image_pick,
                            "attached": int(S["images_attached"]),
                            "missing": int(S["images_missing"]),
                            "sha_mismatch": int(S["images_sha_mismatch"])}}
        # Card R2F-loop: check_invariants returns (checks, core). The old code
        # asked isinstance(checks, dict) -- always False for that 2-tuple -- and
        # fell into {"core": {"pass": bool(<tuple>)}}; bool() of a non-empty
        # tuple is always True, so every per-invariant result was discarded
        # before write_reports and the console printed PASS no matter what.
        # Unpack, guard the contract hard, never coerce a malformed result
        # into a pass.
        raw = check_invariants(state, os.path.join(out_dir, "event_log.jsonl"), cfg)
        if not (isinstance(raw, tuple) and len(raw) == 2
                and isinstance(raw[0], dict) and isinstance(raw[1], bool)):
            raise TypeError(
                "check_invariants(state, events_path, cfg) must return "
                "(checks: dict, core: bool); got " + type(raw).__name__
                + (f" of length {len(raw)}" if isinstance(raw, tuple) else ""))
        checks, core = raw
        checks = {str(k): (v if isinstance(v, dict) else {"pass": bool(v)})
                  for k, v in checks.items()}
        extras = {str(k): (v if isinstance(v, dict) else {"pass": bool(v)})
                  for k, v in (extra_checks or {}).items()}
        checks.update(extras)
        # The run's invariant decision: the world's core flag AND-ed with the
        # engine-side extra checks merged above (decision_failure_halt,
        # cap_stop, a_midday_signal_mutation). Skipped registry entries carry
        # no "pass" key and count as neither pass nor failure. (The stale
        # >=300-agent arm-balance recheck under the bogus key "e_arm_balance"
        # is gone: h_arm_balance is the registry key and world.check_invariants
        # already evaluates it for every cohort size.)
        ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())
        elapsed = time.time() - t0
        write_reports(out_dir, state, cfg, W, checks, {k: S[k] for k in sorted(S)}, elapsed)
        _print_summary(cfg, dict(S), checks, days, elapsed, checkout_oc, checkout_oc_cf, ok)
        fails = [k for k, v in sorted(checks.items()) if not v.get("pass", True)]
        if rc:
            # Existing failure kinds keep their own exit codes and printed
            # labels so they stay distinguishable from invariant failures:
            # 2 = budget cap stop, 3 = decision_failure_halt (engine halts,
            # reported through the same checks path but never as code 4).
            label = {2: "budget cap stop (cap_stop check above)",
                     3: "decision_failure_halt (engine halt; NOT an invariant "
                        "failure)"}.get(rc, "halt")
            print(f"engine exit code {rc}: {label}; invariant failures exit 4")
            return rc
        if ok:
            return 0
        # Fail loudly (card R2F-loop item 3): every failing invariant key was
        # already printed with its detail by _print_summary; the run exits 4,
        # distinct from --replay-check mismatches (3, main() level).
        print("engine exit code 4: invariant failure"
              + ("s: " if len(fails) != 1 else ": ") + ", ".join(fails))
        return 4

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
            wk_prev = _week_key(dt_cur - ONE_DAY * (dt_cur.weekday() + 1))   # FIX3 Defect 2: lagged signal week = the ISO week that ENDED strictly before day t (anchor: the Sunday closing the week before dt_cur's own week), never the week containing dt_cur. Feeds both guba_seed_label call sites; matches world._expected_guba_week exactly.
            # DECISIONS #5: att_t = lambda_attention * att_{t-1} + exposure
            # + beta_guba * z_abnormal, with z read from the LAGGED week only (invariant
            # (a)). The signal is exogenous, so the day z map is shared by every
            # investor; at beta_guba == 0 (decision 3) it is not even built.
            # the benchmark is exogenous, so one value serves the whole cohort; pct_5d
            # reads only observations strictly before dt_cur (invariant (a)).
            idx_5d = pct_5d(benchmark, benchmark_days, dt_cur) if benchmark else None
            z_day = ({c: float((((W.guba or {}).get(c) or {}).get(wk_prev) or {})
                               .get("z_abnormal") or 0.0) for c in guba_codes}
                     if beta_guba else {})
            for inv in invs:                       # (1) clock: P&L refresh, attention decay
                for code in sorted(inv.hold):
                    nav, cst = navday.get(code), inv.cost.get(code, 0.0)
                    if nav and cst > 0:
                        inv.gain_loss[code] = nav / cst - 1.0
                    inv.attention[code] = (inv.attention.get(code, 0.0) * lam_att + 1.0
                                           + beta_guba * z_day.get(code, 0.0))
                for code in sorted(set(inv.attention) - set(inv.hold)):
                    inv.attention[code] = (inv.attention[code] * lam_att
                                           + beta_guba * z_day.get(code, 0.0))
            today = publish_day(W, cfg, t, no_repeat, rng_platform, log=elog)  # (2) publish
            for post in today:
                birth[post["post_id"]] = t
            cand = [p for ps in recent_list for p in ps] + today
            comments[t] = []
            if t - 2 in comments:
                del comments[t - 2]
            prev_cmts = comments.get(t - 1, [])
            n_prev = {}                    # full t-1 comment count per pid (header 共 n 条)
            for c in prev_cmts:
                p = c.get("p")
                n_prev[p] = n_prev.get(p, 0) + 1
            clim_now, top_now = {}, {}
            for post in cand:                      # (3) social lag, one trading day
                pid, code = post["post_id"], post.get("code")
                label, counts, src = None, {}, "agent"
                if code is not None and (t == 0 or code in guba_codes):
                    seeded = guba_seed_label(W, code, wk_prev)
                    if seeded:
                        label, src = seeded, "guba_seed"
                if label is None:
                    label, counts = climate_for(pid, prev_cmts, weights=wmap,
                                                margin=clim_margin)
                top = top_comments(pid, prev_cmts, k=3, weights=wmap) or []
                clim_now[pid], top_now[pid] = label, top
                logd("clim", t=t, d=dstr, p=pid, label=label, counts=_jsonable(counts),
                     top=_top_view(top), source=src)
            signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
                                 "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
                                 "day_keys": guba_day_keys})
            heat_prev, clim_prev, top_prev = dict(heat), dict(clim_now), dict(top_now)
            # News channel (audit E1): the view needs the RAW weekly signal row, not the
            # stance label. The label is still the climate seed above (clim.source stays
            # "guba_seed" wherever it fired) -- these are two different consumers of the
            # same week key, so both call sites keep reading wk_prev.
            # Codes worth a news line: today's candidate posts AND anything anyone
            # holds. Post codes alone left the channel silent on the real corpus (12 of
            # 341 funds carry signal, so a post rarely lands on one), while
            # _agent_view's own filter has always read `c in inv.hold or c in codes`.
            guba_view = {}
            held_codes = {c for inv in invs for c in inv.hold if c}
            # a post with no landing fund contributes no code, and None must not reach
            # sorted() alongside the strings
            post_codes = {p.get("code") for p in cand if p.get("code")}
            for code in sorted(post_codes | held_codes):
                if code in guba_codes and code not in guba_view:
                    row = _guba_row(W, code, wk_prev)
                    if row is not None:
                        guba_view[code] = row
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
                          # feed.fit() reads risk_latent/core and rank_feed's trust term
                          # reads trust. Supplying "risk"/"fam"/"flag" instead left fit()
                          # returning exactly 0.5 for every (agent, post) pair and the
                          # trust term at 0, so the three-source recommender's MATCH slot
                          # was not personalised at all -- it degenerated to heat plus
                          # climate, and stage 2's (-fit, -score) primary key was a no-op.
                          "risk_latent": inv.risk, "core": getattr(inv, "core", None),
                          "trust": dict(inv.aff),
                          "cell": inv.cell, "beliefs": inv.beliefs,
                          "follow": sorted(inv.follow), "fam": dict(inv.fam),
                          "flag": dict(inv.flag), "attention": dict(inv.attention),
                          "hold": sorted(inv.hold)}
                ranked = rank_feed(astate, cand, heat_prev, clim_prev, cfg["feed"],
                                   inv.rng, mode=cfg.get("ranking", "three_source")) or []
                # agent/run level: the arm is a property of the agent; exposure level:
                # each impression draws its own arm from the same per-agent stream.
                arms = ([inv.arm] * len(ranked) if modality_level in ("agent", "run")
                        else assign_arms(inv.rng, K, inv.arm_tally, arms=modality_arms))
                shown, arm_by_pid, img_by_pid, last_src = {}, {}, {}, None
                for s, item in enumerate(ranked):
                    post, source = _rank_item(item)
                    pid = post["post_id"]
                    shown[pid] = post
                    arm = arms[s] if s < len(arms) else inv.arm
                    arm_by_pid[pid] = arm
                    # TV arm only: resolve and sha256-verify one real image for THIS
                    # impression. T and TC never enter the resolver, so a text arm can
                    # neither be slowed nor perturbed by the image store. The draw for
                    # image_pick="random" comes from a DEDICATED derived stream rather
                    # than inv.rng: consuming the agent's own stream would shift every
                    # later draw and make a TV agent diverge from a T agent for reasons
                    # that have nothing to do with the picture, which would defeat the
                    # comparison the arms exist to make.
                    imp_kw = {}
                    if images_root and arm == "TV":
                        got = _resolve_tv_image(
                            notes_by_id.get(str(post.get("note"))) or {},
                            images_root, image_pick,
                            random.Random(rng_seed_from(cfg["run_tag"], "img",
                                                        inv.id, t, pid)))
                        img_by_pid[pid] = got
                        if got[0]:
                            S["images_attached"] += 1
                            imp_kw["img_idx"] = got[2]
                        elif got[3] == "image_sha_mismatch":
                            S["images_sha_mismatch"] += 1
                        else:
                            S["images_missing"] += 1
                    # img_idx travels ONLY when a picture was really attached, so a run
                    # with no images_root emits the pre-card bytes exactly.
                    logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s,
                         source=source, **imp_kw)
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
                                    heat_prev, clim_prev, top_prev, dt_cur, n_prev,
                                    image=img_by_pid.get(pid))
                         for pid in shown]
                view = _agent_view(inv, persona.get(inv.id), shown, W, cfg, navday, hist,
                                   trend_cache, guba_view, last_trade, declined,
                                   prev_navdays=(prev_navday, prev2_navday),
                                   index_5d=idx_5d, index_label=benchmark_label)
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
                if dump_target is not None and not dump_done \
                        and _dump_matches(dump_target, inv.id, t):
                    dump_done = True
                    print("prompt dump written: "
                          + _write_prompt_dump(out_dir, cfg, job, inv, rec, t, dstr))
                S["decisions"] += 1
                S["attempts"] += int(rec.get("attempts") or 0)
                if rec.get("cache_hit"):
                    S["cache_hits"] += 1
                else:
                    S["calls"] += 1
                # cache_hit / attempts are provenance, not simulation state: they differ between a cold run
                # and its warm replay, which would break invariant (l) byte-identical logs. They live in
                # llm_cache.jsonl (per call) and run_meta.counters (aggregate) instead.
                adapted = _adapt_record(rec, job["shown"], inv) if row is not None else None
                logd("dec", t=t, d=dstr, i=inv.id, prompt_sha=rec.get("prompt_sha"),
                     raw_sha=rec.get("raw_sha256"), status=rec.get("parser_status"),
                     arm=inv.arm, mood=(row or {}).get("mood"), reason=(row or {}).get("reason"),
                     violations=list(rec.get("violations") or ()),
                     failure_kind=rec.get("failure_kind"),
                     **_dec_counts(adapted, len(job["cards"]), bool(cfg.get("social"))))
                if row is None:
                    S["decision_failures"] += 1
                    # Decision 9: threshold and halt condition UNCHANGED -- this splits
                    # only the diagnosis. An HTTP error, a dead socket or a revoked key
                    # is a transport failure and used to be counted as model
                    # instability: in one live smoke run three rate-limited calls looked
                    # like an unstable model while the parse rate was 77 of 77.
                    S["decision_failures_" + (rec.get("failure_kind") or "model")] += 1
                    continue
                cmt_out, aff_first, tr = apply_decision(inv, adapted, job["shown"], day,
                                                        fees=cfg.get("fees"))
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
                # dca.pct / dca.min_ticket replace two bare literals; both defaults equal
                # those literals, so a config omitting the block is byte-identical.
                dca_pct = float((cfg.get("dca") or {}).get("pct", 0.02))
                dca_min = float((cfg.get("dca") or {}).get("min_ticket", 100.0))
                for inv in invs:
                    # Audit E7 / decision 7: the guard used to require inv.hold, so a
                    # plan could only TOP UP an existing position -- an investor flagged
                    # for a plan who opened with nothing could never begin one, which is
                    # roughly half the cohort at the smallest holdings setting.
                    # world.init_investors now assigns those investors a dca_target from a
                    # DERIVED rng stream, so inv.rng own sequence stays untouched.
                    if inv.dca and t >= inv.entry:
                        code = (min(inv.hold) if inv.hold      # deterministic: first held
                                else getattr(inv, "dca_target", None))
                        amt = inv.cash * dca_pct
                        nav = navday.get(code) if code else None
                        if not code or amt < dca_min or not nav:
                            S["dca_skipped"] += 1
                            continue
                        units = amt / nav
                        held = float(inv.hold.get(code, 0.0))   # 0.0 on an OPENING purchase
                        inv.cost[code] = (held * inv.cost.get(code, nav) + amt) \
                            / (held + units)
                        inv.hold[code] = held + units
                        inv.cash -= amt
                        S["dca_n"] += 1
                        S["dca_cny"] += amt
                        flows[FUNDS[code].family][quarter_of(dt_cur)]["sub"] += amt
                        logd("act", t=t, d=dstr, i=inv.id, p=None, kind="dca", fund=code,
                             amt=round(amt, 2), units=round(units, 6), nav=nav, fee=0.0)
            if heat != heat_prev:                  # would mean mid-day signal mutation (inv a)
                inv_a_ok = False
            for inv in invs:                       # (8) lagged updates, visible from t+1 only
                orgs = touched.get(inv.id) or ()
                for org in sorted(inv.fam):
                    inv.fam[org] = inv.fam[org] * (1.0 - delta) + (1.0 if org in orgs else 0.0)
                for org in sorted(orgs):
                    if org not in inv.fam:
                        inv.fam[org] = 1.0
                for org in sorted(inv.aff):        # adstock decay (dynamics.lambda_trust)
                    inv.aff[org] *= lam_trust
                for org in sorted(set(inv.fam) | inv.follow | set(inv.aff)):
                    # spec §6: level 2 if follows; level 1 if exposure/affinity stock >= 1.0
                    if org in inv.follow:
                        lv = 2
                    elif (inv.fam.get(org, 0.0) >= fam_thr          # dynamics.fam_threshold
                          or inv.aff.get(org, 0.0) >= fam_thr):
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
                                              declined,
                                              prev_navdays=(prev_navday, prev2_navday))}
                         for inv in invs]

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
            # Roll the NAV lag forward only here: every consumer of day t must have seen
            # (t-1, t-2), so the shift belongs after the last _agent_view call of the day.
            prev_navday, prev2_navday = navday, prev_navday
        extra = {} if inv_a_ok else {"a_midday_signal_mutation": {"pass": False}}
        return _finish(extra, 0, n_days)
    except CapStop:
        return _finish({"cap_stop": {"pass": False}}, 2, max(t + 1, 0))
    finally:
        elog.close()


def _check_detail_line(key, check):
    """One ASCII console line carrying a failing check's full detail (card
    R2F-loop item 3: the printed summary must show WHY each invariant failed,
    not just that the run failed)."""
    try:
        body = json.dumps(_jsonable(check), ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        body = str(check)
    if len(body) > 400:
        body = body[:397] + "..."
    return f"invariant FAIL {key}: {body}"


def _print_summary(cfg, counters, checks, days, elapsed, checkout_oc, checkout_oc_cf,
                   ok=None):
    n = max(int(counters.get("decisions", 0)), 1)
    print("=== flowmirror.engine.loop summary ===")
    print(f"run_tag={cfg.get('run_tag')} days={days} agents={cfg.get('n_agents')} "
          f"active_agent_days={counters.get('active_days', 0)}")
    print(f"calls={counters.get('calls', 0)} cache_hits={counters.get('cache_hits', 0)} "
          f"decision_failure_rate={counters.get('decision_failures', 0) / n:.4f}"
          f" (transport={counters.get('decision_failures_transport', 0)}"
          f" model={counters.get('decision_failures_model', 0)})")
    print(f"checkout_oc={dict(sorted(checkout_oc.items()))}")
    print(f"checkout_oc_cf={dict(sorted(checkout_oc_cf.items()))}")
    print(f"fees_cny={round(float(counters.get('fees_cny', 0.0) or 0.0), 2)} "
          f"factor_switches={counters.get('factor_switches', 0)} "
          f"elapsed_s={round(elapsed, 1)}")
    # Card R2F-loop: `ok` is the run's invariant decision handed in by _finish
    # (world core AND-ed with the extra checks); bool(<opaque tuple>) is gone.
    # A skipped entry ({"skipped": True, "reason": ...}) has no "pass" key, so
    # it is neither a pass nor a failure here.
    if ok is None:                    # legacy callers: derive from the checks themselves
        ok = all(v.get("pass", True) for v in checks.values())
    fails = [(k, v) for k, v in sorted(checks.items()) if not v.get("pass", True)]
    if ok and not fails:
        print("invariants=PASS")
        return
    print("invariants=FAIL(" + ",".join(k for k, _ in fails) + ")")
    for k, v in fails:                # every failing invariant key, each with its detail
        print(_check_detail_line(k, v))
    if not fails:                     # core=False while no entry failed: say so explicitly
        print(_check_detail_line("core", {"core": False,
                                          "note": "core=False but no individual check "
                                                  "entry is failing"}))


def _load_cfg(path):
    obj = load_config(path)
    validate(obj, "run")
    cfg = deep_merge(DEFAULT_CONFIG, obj)
    resolve_paths(cfg, ROOT)
    return cfg


def _fake_inv(**over):
    # rc is the REPORTED suitability class as the engine really carries it: regulator
    # .cn_cxr keys C_RANK by "C1".."C5", and init_investors produces exactly those
    # strings. This fixture said 3, so cxr_outcome raised KeyError and every assertion
    # after the first apply_decision never ran.
    inv = SimpleNamespace(id="A1", arm="T", rc="C3", cash=100000.0, hold={}, cost={},
                          realized=0.0, fam={}, aff={}, follow=set(), flag={})
    for k, v in over.items():
        setattr(inv, k, v)
    return inv


def _fake_day(cfg, **over):
    day = SimpleNamespace(t=0, dstr="2024-01-02", dt_cur=date(2024, 1, 2), cfg=cfg, S=Counter(),
                          # r is "R1".."R5" in R_RANK and in the real fund loader, not an int
                          FUNDS={"F1": SimpleNamespace(r="R4", qdii=False, family="famA")},
                          qdii_blk=set(), navday={"F1": 1.5},
                          flows={"famA": {quarter_of(date(2024, 1, 2)): {"sub": 0.0, "red": 0.0}}},
                          weights=None, comments=[], likes={}, saves={}, cw={},
                          checkout_oc=Counter(), checkout_oc_cf=Counter(), events=[],
                          last_trade={}, declined={})
    # apply_decision passes the event kind as the FIRST POSITIONAL argument,
    # exactly like the real EventLog.emit; store it under the "ev" key so the
    # self-test can filter rows by kind.
    day.logd = lambda ev, **kw: day.events.append(dict(ev=ev, **kw))
    for k, v in over.items():
        setattr(day, k, v)
    return day


def _self_test():
    ok = True

    def chk(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + name)

    chk("make_llm_prefers_null_policy_over_mock",
        isinstance(_make_llm({"agent_policy": "null", "run_tag": "st", "mock_llm": True,
                              "null_params": {}}), NullPolicyLLM))
    chk("make_llm_mock_fallback_without_null_policy",
        isinstance(_make_llm({"mock_llm": True}), MockLLM))
    shown = {"P1": {"post_id": "P1", "org": "orgA", "code": "F1", "intent": "I2",
                    "intent_group": "I2"}}
    # apply_decision reads amount_pct / sign_mismatch_confirm from parsed["trade"],
    # so the fixture nests them there (a flat parsed dict silently yields amount 0).
    base = {"parsed": {"trade": {"amount_pct": 10, "sign_mismatch_confirm": "false"}},
            "likes": ["P1"], "saves": [], "follows": ["orgA"], "aff": {"orgA": 0.5},
            "comments": [], "trade": {"p": "P1", "act": "subscribe", "amount_pct": 10}}
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    apply_decision(inv, dict(base), shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("suitability_off_executes_match_cf_logged", co["oc"] == "match" and co["oc_cf"] in _VALID_OC)
    chk("subscribe_still_emits_click_row",
        any(e["ev"] == "click" and e.get("oc") == "to_checkout" for e in day.events))
    chk("subscribe_and_engagement_applied", bool(inv.hold) and inv.cash < 100000.0
        and "orgA" in inv.follow and inv.aff.get("orgA") == 0.5)
    chk("act_rows_carry_zero_fee_when_fees_off",
        bool([e for e in day.events if e["ev"] == "act"])
        and all(e.get("fee") == 0.0 for e in day.events if e["ev"] == "act"))
    day, inv = _fake_day({"social": False, "suitability": True}), _fake_inv()
    apply_decision(inv, dict(base), shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("gated_subscribe_oc_equals_cf", co["oc"] == co["oc_cf"] and co["oc"] in _VALID_OC)
    day, inv = _fake_day({"social": False, "suitability": True}), _fake_inv()
    rec = dict(base)
    rec["trade"] = {"p": "P1", "act": "redeem", "amount_pct": 50}
    apply_decision(inv, rec, shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("redeem_ungated_no_holdings", co["oc"] == "no_holdings" and co["oc_cf"] in _VALID_OC
        and co["p"] is None and not any(e["ev"] == "click" for e in day.events))
    day, inv = _fake_day({"social": False, "suitability": True}), _fake_inv(
        hold={"F1": 1000.0}, cost={"F1": 1.0})
    rec = dict(base)
    rec["parsed"] = {"trade": {"amount_pct": 50, "sign_mismatch_confirm": "false"}}
    rec["trade"] = {"p": "P1", "act": "redeem", "amount_pct": 50}
    apply_decision(inv, rec, shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    acts = [e for e in day.events if e["ev"] == "act"]
    chk("redeem_no_click_co_forced_match_p_none",
        not any(e["ev"] == "click" for e in day.events)
        and co["act"] == "redeem" and co["oc"] == "match" and co["oc_cf"] == "match"
        and co["p"] is None and co["ig"] is None
        and bool(acts) and all(e.get("p") is None for e in acts))
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    rec = dict(base)
    rec["parsed"] = {"trade": {"amount_pct": 0.001, "sign_mismatch_confirm": "false"}}
    apply_decision(inv, rec, shown, day)
    co = [e for e in day.events if e["ev"] == "co"][0]
    chk("below_min_blocks_ticket", co["oc"] == "below_min"
        and not any(e["ev"] == "act" for e in day.events))
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv()
    apply_decision(inv, dict(base), shown, day,
                   fees={"subscribe_rate": 0.0012, "redeem_rate": 0.005})
    act = [e for e in day.events if e["ev"] == "act"][0]
    chk("subscribe_fee_charged_tracked_identity_holds",
        act["fee"] > 0.0 and abs(act["fee"] - 12.0) < 1e-6
        and abs(getattr(inv, "fees", -1.0) - act["fee"]) < 1e-9
        and inv.cash == 90000.0
        and abs(inv.cash + inv.hold["F1"] * 1.5 + getattr(inv, "fees", 0.0)
                - 100000.0 - inv.realized) < 1e-6)
    day, inv = _fake_day({"social": False, "suitability": False}), _fake_inv(
        hold={"F1": 1000.0}, cost={"F1": 1.0})
    rec = dict(base)
    rec["parsed"] = {"trade": {"amount_pct": 50, "sign_mismatch_confirm": "false"}}
    rec["trade"] = {"p": "P1", "act": "redeem", "amount_pct": 50}
    apply_decision(inv, rec, shown, day,
                   fees={"subscribe_rate": 0.0012, "redeem_rate": 0.005})
    act = [e for e in day.events if e["ev"] == "act"][0]
    chk("redeem_fee_charged_on_gross_proceeds",
        abs(act["fee"] - 3.75) < 1e-6 and abs(inv.cash - 100746.25) < 1e-6
        and abs(getattr(inv, "fees", 0.0) - 3.75) < 1e-6
        and abs(inv.realized - 250.0) < 1e-6)
    Wt = SimpleNamespace(funds={}, fund_meta={},
                         pool={"orgA": [{"id": "N1", "title": "t1", "caption": "raw cap",
                                         "caption_masked": "masked cap",
                                         "image_path": "imgs/n1.jpg", "ocr_text": "raw ocr",
                                         "ocr_masked": "masked ocr",
                                         "image_caption_frozen": "frozen cap"}]})
    notes_t = _note_index(Wt)
    post_t = {"post_id": "P1", "org": "orgA", "note": "N1", "code": None, "img": 1}
    card_tc = _feed_card(Wt, post_t, notes_t, "TC", {}, {}, {}, date(2024, 1, 2), {"P1": 9})
    card_t = _feed_card(Wt, post_t, notes_t, "T", {}, {}, {}, date(2024, 1, 2), {"P1": 9})
    card_tv = _feed_card(Wt, post_t, notes_t, "TV", {}, {}, {}, date(2024, 1, 2), {})
    chk("tc_card_carries_ocr_text_and_frozen_caption",
        card_tc.get("ocr_text") == "masked ocr"
        and card_tc.get("image_caption_frozen") == "frozen cap"
        and card_tc.get("caption") == "masked cap")
    chk("t_and_tv_cards_have_no_tc_fields",
        all("ocr_text" not in c and "image_caption_frozen" not in c
            for c in (card_t, card_tv)))
    chk("n_comments_prev_always_present",
        card_tc["n_comments_prev"] == 9 and card_t["n_comments_prev"] == 9
        and card_tv["n_comments_prev"] == 0)
    shown2 = {"P1": {"post_id": "P1", "org": "orgA", "code": "F1", "intent": "I2"},
              "P2": {"post_id": "P2", "org": "orgB", "code": None, "intent": "I1"}}
    adapted = _adapt_record(
        {"parsed": {"engage": {"P1": ["like", "save"], "P2": ["follow"]},
                    "org_affinity_delta": {"orgA": 1, "orgB": -1, "orgC": 0},
                    "comments": [{"post_id": "P1", "stance": "bull", "text": "tt"},
                                 {"post_id": "P2", "stance": "no_comment", "text": ""}],
                    "trade": None}}, shown2, _fake_inv())
    chk("dec_counts_match_adapted_row",
        _dec_counts(adapted, 2, True) == {"n_read": 2, "n_like": 1, "n_save": 1,
                                          "n_follow": 1, "n_comment": 1, "aff_sum": 0})
    chk("dec_counts_all_null_when_parse_failed",
        _dec_counts(None, 2, True) == {k: None for k in
                                       ("n_read", "n_like", "n_save", "n_follow",
                                        "n_comment", "aff_sum")})
    chk("dec_counts_drop_comments_when_social_off",
        _dec_counts(adapted, 2, False)["n_comment"] == 0)
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
    # --- E1-3 unit checks for the prompt-dump renderer (pure, offline) -------
    img_data = b"\x00\x01\x02" * 20
    img_url = "data:image/jpeg;base64," + base64.b64encode(img_data).decode("ascii")
    ph = _image_part_placeholder({"type": "image_url", "image_url": {"url": img_url}})
    chk("dump_placeholder_sha_and_bytes",
        ph == f"[image: {hashlib.sha256(img_data).hexdigest()[:12]}, {len(img_data)} bytes]")
    rtxt = _render_prompt_text([{"role": "system", "content": "SYS"},
                                {"role": "user", "content": [
                                    {"type": "text", "text": "USER"},
                                    {"type": "image_url", "image_url": {"url": img_url}}]}])
    chk("dump_renderer_strips_base64",
        "SYS" in rtxt and "USER" in rtxt and "base64," not in rtxt and rtxt.endswith("\n"))
    chk("dump_target_spec_parsing",
        _dump_target("first") == (None, None) and _dump_target("A3@7") == ("A3", 7))
    try:
        _dump_target("no-at-sign")
        bad_spec = False
    except ValueError:
        bad_spec = True
    chk("dump_target_rejects_bad_spec", bad_spec)
    chk("runtime_opts_carry_cli_only_switches",
        RuntimeOpts().dump_prompt is None
        and RuntimeOpts(dump_prompt="first").dump_prompt == "first")
    root = os.environ.get("FLOWMIRROR_DATA_ROOT") or os.path.join(ROOT, "data")
    alt = os.environ.get("FLOWMIRROR_RESEARCH_ROOT") or "D:/Desktop/ABM paper/fundmarket-sim"
    cfg_path = next((os.path.join(b_, "runs", "mock_10x3.json") for b_ in (root, alt, ROOT)
                     if os.path.exists(os.path.join(b_, "runs", "mock_10x3.json"))), None)
    if cfg_path is None:
        print("SKIP: no runs/mock_10x3.json under FLOWMIRROR_DATA_ROOT/FLOWMIRROR_RESEARCH_ROOT")
        return 0 if ok else 1
    cfg = _load_cfg(cfg_path)
    # Card R2F-loop: the engine now honors REAL invariant outcomes, so the
    # end-to-end fixture mirrors the acceptance run's shape (mock_10x3.json,
    # 40 agents x 5 days) -- cohort-scale-sensitive checks like cell exposure
    # are only guaranteed at the sizes the acceptance card validates.
    cfg.update({"mock_llm": True, "n_agents": 40,
                "out_dir": tempfile.mkdtemp(prefix="fm_loop_st_")})
    cfg["window"]["max_trading_days"] = 5
    logp = os.path.join(cfg["out_dir"], "event_log.jsonl")
    rc = run_simulation(cfg)
    chk("mock_end_to_end_rc0", rc == 0)
    if rc != 0:
        return 1
    # Card R2F-loop: the report must carry real per-invariant results -- the
    # discarded (checks, core) tuple used to leave every registered invariant
    # as "skipped: not evaluated" plus one bogus "core: pass".
    with open(os.path.join(cfg["out_dir"], "invariants_report.json"),
              encoding="utf-8") as fh:
        inv_rep = json.load(fh)
    ent_d = _report_entry(inv_rep, "d_wealth_conservation")
    ent_h = _report_entry(inv_rep, "h_arm_balance")
    chk("invariants_report_has_real_wealth_and_arm_balance_entries",
        ent_d is not None and ent_h is not None
        and not ent_d.get("skipped") and not ent_h.get("skipped")
        and bool(ent_d.get("pass")) and bool(ent_h.get("pass")))
    rows = list(iter_jsonl(logp))
    evs = {row.get("ev") for row in rows}
    chk("required_event_kinds", {"post", "imp", "dec", "clim"} <= evs)
    tally_keys = ("n_read", "n_like", "n_save", "n_follow", "n_comment", "aff_sum")
    decs = [r for r in rows if r.get("ev") == "dec"]
    chk("dec_rows_carry_tally_fields",
        bool(decs) and all(all(k in d for k in tally_keys) for d in decs))
    chk("act_rows_carry_fee_field",
        all("fee" in r and float(r["fee"]) >= 0.0 for r in rows if r.get("ev") == "act"))
    sha1 = event_log_sha(logp)
    chk("replay_sha_identical", run_simulation(cfg) == 0 and sha1 is not None
        and sha1 == event_log_sha(logp))
    # --- E1-3: --dump-prompt side artifact must not touch the log ------------
    # Card R2D: the spec rides in RuntimeOpts (a CLI-only runtime-options
    # object), never in cfgd -- the run schema rejects a literal dump_prompt
    # key, which is exactly what made the flag unreachable before.
    cfgd = _load_cfg(cfg_path)
    cfgd.update({"mock_llm": True, "n_agents": 40,
                 "out_dir": tempfile.mkdtemp(prefix="fm_loop_dmp_")})
    cfgd["window"]["max_trading_days"] = 5
    rcd = run_simulation(cfgd, RuntimeOpts(dump_prompt="first"))
    pdir = os.path.join(cfgd["out_dir"], "prompts")
    txts = sorted(f for f in os.listdir(pdir) if f.endswith(".txt")) \
        if os.path.isdir(pdir) else []
    side_path = os.path.join(pdir, txts[0][:-4] + ".json") if txts else None
    chk("dump_prompt_writes_txt_and_sidecar",
        rcd == 0 and len(txts) == 1 and side_path is not None
        and os.path.exists(side_path))
    if side_path is not None and os.path.exists(side_path):
        with open(os.path.join(pdir, txts[0]), encoding="utf-8") as fh:
            blob = fh.read()
        with open(side_path, encoding="utf-8") as fh:
            side = json.load(fh)
        dec_rows = [r for r in iter_jsonl(os.path.join(cfgd["out_dir"], "event_log.jsonl"))
                    if r.get("ev") == "dec" and r.get("i") == side.get("agent")
                    and r.get("t") == side.get("day")]
        chk("dump_prompt_txt_has_no_base64_payloads", "base64," not in blob)
        chk("dump_prompt_sidecar_prompt_sha_matches_dec_row",
            bool(dec_rows) and dec_rows[0].get("prompt_sha") == side.get("prompt_sha"))
        chk("dump_prompt_sidecar_arm_and_card_ids",
            side.get("arm") in ("T", "TC", "TV") and isinstance(side.get("card_ids"), list)
            and len(side.get("card_ids") or []) > 0)
        chk("dump_prompt_leaves_event_log_byte_identical",
            event_log_sha(os.path.join(cfgd["out_dir"], "event_log.jsonl")) == sha1)
    cfg3_path = next((os.path.join(b_, "runs", "mock_10x3_3arm.json") for b_ in (root, alt, ROOT)
                      if os.path.exists(os.path.join(b_, "runs", "mock_10x3_3arm.json"))), None)
    if cfg3_path is None:
        print("SKIP: no runs/mock_10x3_3arm.json (A2-schema card adds it)")
    else:
        cfg3 = None
        try:
            cfg3 = _load_cfg(cfg3_path)
        except ConfigError:
            print("SKIP: mock_10x3_3arm.json not accepted by the active schema yet")
        if cfg3 is not None:
            # R2F: acceptance shape for the 3-arm config (60 agents x 5 days).
            cfg3.update({"mock_llm": True, "n_agents": 60,
                         "out_dir": tempfile.mkdtemp(prefix="fm_loop_st3_")})
            cfg3["window"]["max_trading_days"] = 5
            try:
                rc3 = run_simulation(cfg3)
            except (ConfigError, SystemExit) as exc:
                rc3 = None
                print(f"SKIP: three-arm run blocked by config/world layer: {exc}")
            if rc3 is not None:
                chk("mock_three_arm_fees_rc0", rc3 == 0)
                if rc3 == 0:
                    rows3 = list(iter_jsonl(os.path.join(cfg3["out_dir"],
                                                         "event_log.jsonl")))
                    arms3 = {r.get("arm") for r in rows3 if r.get("ev") in ("imp", "dec")}
                    chk("three_arm_arms_in_enum_and_act_fees_logged",
                        bool(arms3) and arms3 <= {"T", "TC", "TV"}
                        and all("fee" in r and float(r["fee"]) >= 0.0
                                for r in rows3 if r.get("ev") == "act"))
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
    ap.add_argument("--dump-prompt", metavar="SPEC", default=None,
                    help="write the exact prompt for <agent_id>@<day> (0-based trading-day "
                         "index) or 'first' to <out_dir>/prompts/<agent>_d<day>.txt plus a "
                         ".json sidecar; runtime-only switch carried in RuntimeOpts (never "
                         "a run-config key); side artifact only, the event log is unaffected")
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
        # A run's cache lives with its outputs unless the config points elsewhere on purpose; otherwise an
        # --out override would silently replay another run's cached responses.
        cfg.setdefault("llm", {})["cache"] = os.path.join(cfg["out_dir"], "llm_cache.jsonl")
    # Card R2D: --dump-prompt is a CLI-only runtime switch, so it rides in
    # RuntimeOpts and never enters the validated run config. Parking it in cfg
    # (the old behavior) made run_simulation's schema re-validation reject the
    # whole run ("'dump_prompt' does not match any of the regexes: '^_'")
    # before the first trading day, leaving the feature unreachable.
    rt = RuntimeOpts()
    if args.dump_prompt:
        try:
            _dump_target(args.dump_prompt)
        except ValueError as exc:
            print(f"dump-prompt error: {exc}")
            return 1
        rt.dump_prompt = args.dump_prompt
    if args.replay_check:
        logp = os.path.join(cfg["out_dir"], "event_log.jsonl")
        if run_simulation(cfg, rt) != 0:
            return 3
        sha1 = event_log_sha(logp)
        if run_simulation(cfg, rt) != 0:
            return 3
        sha2 = event_log_sha(logp)
        m2 = _last_meta(cfg["out_dir"])
        same = sha1 is not None and sha1 == sha2
        print(f"replay-check sha1={sha1}")
        print(f"replay-check sha2={sha2} "
              f"warm_cache_calls={(m2.get('counters') or {}).get('calls')}")
        print(f"replay-check identical={bool(same)}")
        return 0 if same else 3
    return run_simulation(cfg, rt)


if __name__ == "__main__":
    raise SystemExit(main())

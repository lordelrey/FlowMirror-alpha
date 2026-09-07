# -*- coding: utf-8 -*-
"""Analysis layer for the modality experiment and channel attribution
(PREREG v1.5 draft A3; seed-level inference per PREREG v1.3 B11).

Agent level (default): per run, each agent's arm comes from run_meta.arms
(fallback: majority imp.arm per agent). Micro outcomes are read from dec fields
(n_read, n_like, n_save, n_follow, n_comment, aff_sum); older logs without those
fields get n_comment derived from cmt rows and the remaining micro metrics null.
Rates per agent: engagement = (n_like+n_save)/n_shown (n_shown = imp count),
comment = n_comment/n_shown, click = I2 clicks / I2 impressions, subscribe
conversion = act.subscribe / I2 clicks. An impression or click counts as I2 when
its post id belongs to the set of `post` rows whose `ig` -- the intent GROUP in
{"I2","nonI2"} written by world.publish_day -- equals "I2"; only logs whose post
rows carry no `ig` at all fall back to the legacy id/source heuristic (_is_i2).
For every ordered pair of arms present
(TV-TC, TV-T, TC-T, ...): difference of agent means, Cohen h (rates) / d
(aff_sum), and an agent-cluster bootstrap CI (1,000 reps, seed 2027) inside the
run. Across runs (seeds) the seed-level Student-t interval (df = n_runs - 1) on
each contrast and on its effect size is the inferential statement; the verdict
uses the effect-scale interval vs SESOI (h = 0.10, d = 0.20): lo > 0 ->
supported, hi < SESOI -> bounded_null, else indeterminate. Inferential guard:
with fewer than 2 finite per-run values (df = 0, e.g. a single run) the
interval is withheld (lo/hi null; mean and per_run kept) and the verdict is
"insufficient_runs" -- one seed is descriptive only and PREREG v1.3 B11 needs
df >= 1 -- and each affected contrast/metric adds a warning
"n_runs=<n>: descriptive only".

Run level (--level run): each run's arm comes from cfg `modality_run_arm`, read
from run_meta.json (top level, or under cfg/config) or from a dumped run config
(run_config.json / config_used.json) next to the log. Runs are PAIRED BY SEED
(the `seed` field of run_meta); the unit is the per-seed difference of run-level
pooled rates between arms, with a seed-level t-interval over the paired seeds
(df = n_common_seeds - 1; fewer than 2 seed pairs triggers the same
insufficient_runs guard with the CI withheld and the same warning). A seed that
carries only one arm of a contrast is excluded from that contrast and reported
in res["warnings"] (as are runs whose arm cannot be detected) instead of being
silently dropped.

Meso per run: heat Gini across posts (imp counts as proxy when no heat log
exists), climate label distribution from clim, median day to familiarity level
1/2 per org from st.

Usage:
  python -m flowmirror.analysis.modality <run_dir> [<run_dir> ...] [--level agent|run] [--out summary.json]
  python -m flowmirror.analysis.modality --self-test
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

from .common import cohen_d, cohen_h, load_events, load_run_meta, seed_t_interval

CANONICAL_ARM_ORDER = ("T", "TC", "TV")
BOOT_REPS = 1000
BOOT_SEED = 2027
SESOI = {"h": 0.10, "d": 0.20}
RATE_METRICS = ("engagement_rate", "comment_rate", "click_rate", "subscribe_conversion")
ALL_METRICS = RATE_METRICS + ("aff_sum",)
EFFECT_KIND = {"engagement_rate": "h", "comment_rate": "h", "click_rate": "h",
               "subscribe_conversion": "h", "aff_sum": "d"}


# ---------------------------------------------------------------- tiny helpers

def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def _n(x):
    if x is None:
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _pooled_sd(v1, v2):
    n1, n2 = len(v1), len(v2)
    dof = n1 + n2 - 2
    if dof <= 0:
        return 0.0
    m1, m2 = _mean(v1), _mean(v2)
    ss = sum((x - m1) ** 2 for x in v1) + sum((x - m2) ** 2 for x in v2)
    return math.sqrt(ss / dof)


def _effect(metric, v1, v2):
    if EFFECT_KIND.get(metric, "h") == "h":
        return cohen_h(_mean(v1), _mean(v2))
    return cohen_d(_mean(v1), _mean(v2), _pooled_sd(v1, v2))


def _is_i2(pid, source=None):
    """LEGACY I2 heuristic: source == 'I2' or a post id carrying the I2 prefix.

    Audit E2: the engine has never emitted either shape -- imp.source is a
    ranking source in {follow, fit, trending, spill, random} and post ids are
    numeric ({t:03d}{org_index}{slot}, e.g. "00031"). The authoritative I2 tag
    is the `post` row's `ig` field, which _aggregate now reads. Kept, not
    deleted, because run directories written by older/external producers exist
    on disk with no `ig` on their post rows; _aggregate falls back to this only
    for those.
    """
    if source == "I2":
        return True
    s = str(pid)
    return s.startswith("I2") or s.startswith("i2")


def _verdict(lo, hi, sesoi):
    if lo is None or hi is None or not _finite(lo) or not _finite(hi):
        return "indeterminate"
    if lo > 0:
        return "supported"
    if hi < sesoi:
        return "bounded_null"
    return "indeterminate"


def _f3(x):
    if x is None or not _finite(x):
        return "--"
    return "%+.3f" % x


def _ci(lo, hi):
    if lo is None or hi is None or not _finite(lo) or not _finite(hi):
        return "[--]"
    return "[%+.3f,%+.3f]" % (lo, hi)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    m = n // 2
    return xs[m] if n % 2 == 1 else 0.5 * (xs[m - 1] + xs[m])


def _gini(xs):
    xs = sorted(xs)
    n = len(xs)
    s = sum(xs)
    if n == 0 or s == 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, 1):
        cum += i * x
    g = (2.0 * cum) / (n * s) - (n + 1.0) / n
    return max(0.0, min(1.0, g))


# ------------------------------------------------------------ per-run loading

def _arm_map(ev, meta):
    """agent -> arm from run_meta.arms, else majority imp.arm per agent."""
    arms = meta.get("arms")
    if isinstance(arms, dict) and arms:
        return {str(k): str(v) for k, v in arms.items()}
    cnt = defaultdict(dict)
    for r in ev.get("imp", []):
        a = r.get("arm")
        if a:
            i = str(r.get("i"))
            cnt[i][str(a)] = cnt[i].get(str(a), 0) + 1
    return {i: max(sorted(c), key=c.get) for i, c in cnt.items()}


def _run_seed(meta):
    for src in (meta, meta.get("cfg") or {}, meta.get("config") or {}):
        if isinstance(src, dict) and src.get("seed") is not None:
            return src["seed"]
    return meta.get("run_tag")


def _run_arm(run_dir, meta, ev):
    """Run-level arm from cfg `modality_run_arm` (persisted metadata only)."""
    for src in (meta, meta.get("cfg") or {}, meta.get("config") or {}):
        if isinstance(src, dict) and src.get("modality_run_arm"):
            return str(src["modality_run_arm"])
    for name in ("run_config.json", "config_used.json"):
        path = os.path.join(run_dir, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(cfg, dict) and cfg.get("modality_run_arm"):
                return str(cfg["modality_run_arm"])
    arms = sorted({str(r.get("arm")) for r in ev.get("imp", []) if r.get("arm")})
    return arms[0] if len(arms) == 1 else None


def _arm_pairs(arms_present):
    """Ordered pairs (hi, lo) in canonical order T < TC < TV; hi - lo contrast."""
    order = [a for a in CANONICAL_ARM_ORDER if a in arms_present]
    order += sorted(a for a in arms_present if a not in CANONICAL_ARM_ORDER)
    return [(order[j], order[i])
            for j in range(len(order) - 1, 0, -1)
            for i in range(j - 1, -1, -1)]


# --------------------------------------------------------- micro aggregation

def _aggregate(ev):
    """Per-agent counters -> (records, modern_micro flag).

    modern = dec rows carry n_read/n_like/n_save/... fields; otherwise (older
    logs) n_comment is derived from cmt rows and the other micro counts stay None.
    """
    dec_rows = ev.get("dec", [])
    modern = any(("n_like" in r) or ("n_save" in r) or ("n_read" in r) for r in dec_rows)
    imp_rows = ev.get("imp", [])
    # Audit E2: I2 membership is a property of the POST, not of the impression.
    # The engine tags it on the `post` row's `ig` field (the intent GROUP in
    # {"I2","nonI2"}, world.intent_group; invariant (f) ties ig == "I2" to
    # intent == "I2"), while imp.source only says how the card was ranked
    # (follow/fit/trending/spill/random) and post ids are numeric. Reading the
    # impression instead of the post is why click_rate and subscribe_conversion
    # came back empty on every real event log.
    post_rows = ev.get("post", [])
    i2_posts = {r.get("p") for r in post_rows if r.get("ig") == "I2"}
    # LEGACY fallback, kept for run directories whose post rows predate `ig`
    # (or which have no post block at all): only then trust the id/source
    # heuristic. A log WITH ig-bearing post rows and no I2 among them genuinely
    # has no I2 content, so it must not silently fall back.
    legacy_i2 = not any("ig" in r for r in post_rows)
    if legacy_i2:
        i2_posts = {r.get("p") for r in imp_rows
                    if _is_i2(r.get("p"), r.get("source"))}
    rec = {}

    def R(i):
        if i not in rec:
            rec[i] = {"n_shown": 0, "i2_shown": 0, "i2_clicks": 0, "subs": 0,
                      "n_read": None, "n_like": None, "n_save": None,
                      "n_follow": None, "n_comment": None, "aff_vals": []}
        return rec[i]

    for r in imp_rows:
        rr = R(r.get("i"))
        rr["n_shown"] += 1
        if r.get("p") in i2_posts:
            rr["i2_shown"] += 1
    for r in ev.get("click", []):
        # membership in the post-row I2 set is authoritative; the heuristic
        # second arm only fires on legacy logs (a click on a post that was
        # never impressed), never on a log whose post rows carry `ig`
        if r.get("p") in i2_posts or (legacy_i2 and _is_i2(r.get("p"), r.get("source"))):
            R(r.get("i"))["i2_clicks"] += 1
    for r in ev.get("act", []):
        if r.get("kind") == "subscribe":
            R(r.get("i"))["subs"] += 1
    for r in dec_rows:
        rr = R(r.get("i"))
        if modern:
            for k in ("n_read", "n_like", "n_save", "n_follow", "n_comment"):
                v = r.get(k)
                if isinstance(v, (int, float)):
                    rr[k] = (rr[k] or 0) + v
        a = r.get("aff_sum")
        if isinstance(a, (int, float)):
            rr["aff_vals"].append(float(a))
    if not modern:
        cmt = Counter(r.get("i") for r in ev.get("cmt", []))
        for i in list(rec):
            rec[i]["n_comment"] = cmt.get(i, 0)
    return rec, modern


def _agent_metrics(rec):
    out = {}
    for i in sorted(rec):
        r = rec[i]
        m = {}
        if r["n_shown"] > 0:
            if r["n_like"] is not None and r["n_save"] is not None:
                m["engagement_rate"] = (r["n_like"] + r["n_save"]) / r["n_shown"]
            if r["n_comment"] is not None:
                m["comment_rate"] = r["n_comment"] / r["n_shown"]
        if r["i2_shown"] > 0:
            m["click_rate"] = r["i2_clicks"] / r["i2_shown"]
        if r["i2_clicks"] > 0:
            m["subscribe_conversion"] = r["subs"] / r["i2_clicks"]
        if r["aff_vals"]:
            m["aff_sum"] = sum(r["aff_vals"]) / len(r["aff_vals"])
        out[i] = m
    return out


def _pooled_metrics(rec):
    n_shown = i2_shown = i2_clicks = subs = like_save = n_comment = 0
    any_ls = any_cm = False
    aff_all = []
    for r in rec.values():
        n_shown += r["n_shown"]
        i2_shown += r["i2_shown"]
        i2_clicks += r["i2_clicks"]
        subs += r["subs"]
        if r["n_like"] is not None and r["n_save"] is not None:
            like_save += r["n_like"] + r["n_save"]
            any_ls = True
        if r["n_comment"] is not None:
            n_comment += r["n_comment"]
            any_cm = True
        aff_all.extend(r["aff_vals"])
    m = {}
    if any_ls and n_shown > 0:
        m["engagement_rate"] = like_save / n_shown
    if any_cm and n_shown > 0:
        m["comment_rate"] = n_comment / n_shown
    if i2_shown > 0:
        m["click_rate"] = i2_clicks / i2_shown
    if i2_clicks > 0:
        m["subscribe_conversion"] = subs / i2_clicks
    if aff_all:
        m["aff_sum"] = sum(aff_all) / len(aff_all)
    return m


# ------------------------------------------------------------------- meso

def _meso(ev):
    out = {"heat_gini_posts": None, "heat_source": None,
           "climate_label_distribution": {}, "familiarity": {}}
    heat_rows = ev.get("heat") or ev.get("hl") or []
    if heat_rows:
        final = {}
        for r in sorted(heat_rows, key=lambda r: (r.get("t", 0), str(r.get("p")))):
            final[r.get("p")] = r.get("heat", r.get("v"))
        vals = [float(v) for v in final.values() if isinstance(v, (int, float))]
        if vals:
            out["heat_gini_posts"] = _gini(vals)
            out["heat_source"] = "heat_log"
    if out["heat_gini_posts"] is None:
        counts = defaultdict(int)
        for r in ev.get("imp", []):
            counts[r.get("p")] += 1
        if counts:
            out["heat_gini_posts"] = _gini(list(counts.values()))
            out["heat_source"] = "imp_counts"
    dist = defaultdict(int)
    for r in ev.get("clim", []):
        lab = None
        for k in ("label", "mood", "state", "climate"):
            if r.get(k) is not None:
                lab = str(r[k])
                break
        dist[lab if lab is not None else "unknown"] += 1
    out["climate_label_distribution"] = dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))
    per = defaultdict(list)  # (i, org) -> [(t, lv)]
    for r in ev.get("st", []):
        what = r.get("what")
        if what == "level":
            per[(r.get("i"), r.get("org"))].append((r.get("t", r.get("d", 0)), int(r.get("lv", 0))))
        elif what == "follow":
            per[(r.get("i"), r.get("org"))].append((r.get("t", r.get("d", 0)), 2))
    f1 = defaultdict(list)
    f2 = defaultdict(list)
    for (i, org), hist in sorted(per.items()):
        hist.sort()
        for t, lv in hist:
            if lv >= 1:
                f1[org].append(t)
                break
        for t, lv in hist:
            if lv >= 2:
                f2[org].append(t)
                break
    fam = {}
    for org in sorted(set(f1) | set(f2)):
        fam[org] = {"median_day_lv1": _median(f1.get(org, [])),
                    "median_day_lv2": _median(f2.get(org, [])),
                    "n_agents_lv1": len(f1.get(org, [])),
                    "n_agents_lv2": len(f2.get(org, []))}
    out["familiarity"] = fam
    return out


# ------------------------------------------------------------- contrasts

def _boot_diff_ci(v1, v2, reps=BOOT_REPS, seed=BOOT_SEED):
    """Agent-cluster bootstrap CI of mean(v1) - mean(v2), inside one run."""
    n1, n2 = len(v1), len(v2)
    if n1 == 0 or n2 == 0:
        return (None, None)
    rng = random.Random(seed)
    diffs = []
    for _ in range(reps):
        b1 = rng.choices(v1, k=n1)
        b2 = rng.choices(v2, k=n2)
        diffs.append(sum(b1) / n1 - sum(b2) / n2)
    diffs.sort()
    return (diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1])


def _run_contrasts_agent(am, agent_metrics):
    by_arm = defaultdict(list)
    for i in sorted(agent_metrics):
        a = am.get(i)
        if a is None:
            continue
        for metric, v in sorted(agent_metrics[i].items()):
            if v is not None and _finite(v):
                by_arm[(a, metric)].append(v)
    out = {}
    for hi, lo in _arm_pairs(set(am.values())):
        rec = {}
        for metric in ALL_METRICS:
            v1 = by_arm.get((hi, metric))
            v2 = by_arm.get((lo, metric))
            if not v1 or not v2:
                rec[metric] = None
                continue
            blo, bhi = _boot_diff_ci(v1, v2)
            rec[metric] = {"diff": _mean(v1) - _mean(v2),
                           "effect": _n(_effect(metric, v1, v2)),
                           "boot_ci": [blo, bhi],
                           "mean_hi": _mean(v1), "mean_lo": _mean(v2),
                           "n_hi": len(v1), "n_lo": len(v2)}
        out["%s-%s" % (hi, lo)] = rec
    return out


def _cross_agent(run_states):
    """Seed-level agent aggregation -> (contrasts, warnings).

    With fewer than 2 finite per-run values for a contrast/metric the
    t-interval is withheld: lo/hi are null, mean and per_run are kept, df = 0
    (verdict "insufficient_runs") and a "n_runs=<n>: descriptive only"
    warning is recorded (PREREG v1.3 B11 needs df >= 1).
    """
    all_arms = set()
    for rs in run_states:
        all_arms.update(rs.get("arms_present") or [])
    contrasts = {}
    warnings = []
    for hi, lo in _arm_pairs(all_arms):
        pname = "%s-%s" % (hi, lo)
        mrec = {}
        for metric in ALL_METRICS:
            diffs, effs = [], []
            for rs in run_states:
                c = ((rs.get("contrasts") or {}).get(pname) or {}).get(metric)
                diffs.append(c.get("diff") if c else None)
                effs.append(c.get("effect") if c else None)
            vals = [x for x in diffs if _finite(x)]
            evals = [x for x in effs if _finite(x)]
            if len(vals) >= 2:
                mean, _sd, lo_, hi_, df = seed_t_interval(vals)
            else:
                mean, lo_, hi_, df = _mean(vals), None, None, 0
                warnings.append("agent-level: %s %s n_runs=%d: descriptive only"
                                % (pname, metric, len(vals)))
            if len(evals) >= 2:
                emean, _esd, elo, ehi, edf = seed_t_interval(evals)
            else:
                emean, elo, ehi, edf = _mean(evals), None, None, 0
            mrec[metric] = {"per_run": diffs,
                            "mean": _n(mean), "lo": _n(lo_), "hi": _n(hi_),
                            "df": df,
                            "effect": {"type": EFFECT_KIND[metric],
                                       "per_run": [_n(x) for x in effs],
                                       "mean": _n(emean), "lo": _n(elo), "hi": _n(ehi),
                                       "df": edf}}
        contrasts[pname] = mrec
    return contrasts, warnings


def _cross_runlevel(run_states):
    """Run-level contrasts -> (contrasts, warnings).

    Runs are paired by the seed field of run_meta; the unit is the per-seed
    difference of run-level pooled rates (hi arm - lo arm) and the inferential
    statement is the seed-level t-interval over the paired seeds
    (df = n_common_seeds - 1). Seeds that have one arm of a pair but not the
    other, and runs with no detectable arm, are excluded and reported via
    warnings instead of being silently dropped. With fewer than 2 seed pairs
    the interval is withheld: lo/hi null, mean and per_run kept, df = 0
    (verdict "insufficient_runs") and a "n_runs=<n>: descriptive only"
    warning is recorded (PREREG v1.3 B11 needs df >= 1).
    """
    armed = [rs for rs in run_states if rs.get("arm")]
    warnings = ["run-level: run %s has no detectable arm (cfg modality_run_arm); excluded"
                % rs["name"]
                for rs in run_states if not rs.get("arm")]
    arms_present = {rs["arm"] for rs in armed}
    seeds = sorted({str(rs.get("seed")) for rs in armed}, key=lambda s: (len(s), s))
    by = {}
    seed_arms = defaultdict(set)
    for rs in armed:
        by[(str(rs.get("seed")), rs["arm"])] = rs.get("pooled") or {}
        seed_arms[str(rs.get("seed"))].add(rs["arm"])
    for s in seeds:
        have = seed_arms[s]
        for hi, lo in _arm_pairs(arms_present):
            if hi in have and lo not in have:
                warnings.append("run-level: seed %s has arm %s but no %s partner; "
                                "excluded from %s-%s" % (s, hi, lo, hi, lo))
            elif lo in have and hi not in have:
                warnings.append("run-level: seed %s has arm %s but no %s partner; "
                                "excluded from %s-%s" % (s, lo, hi, hi, lo))
    contrasts = {}
    for hi, lo in _arm_pairs(arms_present):
        rec = {}
        for metric in ALL_METRICS:
            diffs, effs, per_seed = [], [], {}
            for s in seeds:
                m1 = by.get((s, hi), {}).get(metric)
                m2 = by.get((s, lo), {}).get(metric)
                if m1 is None or m2 is None:
                    continue
                diffs.append(m1 - m2)
                per_seed[s] = m1 - m2
                if EFFECT_KIND[metric] == "h":
                    effs.append(cohen_h(m1, m2))
            if metric == "aff_sum" and diffs:
                # d uses the pooled sd across the paired seeds only
                vh = [by[(s, hi)][metric] for s in per_seed]
                vl = [by[(s, lo)][metric] for s in per_seed]
                sp = _pooled_sd(vh, vl)
                if sp > 0:
                    effs = [(by[(s, hi)][metric] - by[(s, lo)][metric]) / sp
                            for s in per_seed]
            if len(diffs) >= 2:
                mean, _sd, lo_, hi_, df = seed_t_interval(diffs)
            else:
                mean, lo_, hi_, df = _mean(diffs), None, None, 0
                warnings.append("run-level: %s-%s %s n_runs=%d: descriptive only"
                                % (hi, lo, metric, len(diffs)))
            if len(effs) >= 2:
                emean, _esd, elo, ehi, edf = seed_t_interval(effs)
            else:
                emean, elo, ehi, edf = _mean(effs), None, None, 0
            rec[metric] = {"per_run": diffs, "per_seed": per_seed,
                           "mean": _n(mean), "lo": _n(lo_), "hi": _n(hi_),
                           "df": df,
                           "n_common_seeds": len(diffs),
                           "effect": {"type": EFFECT_KIND[metric],
                                      "per_run": [_n(x) for x in effs],
                                      "mean": _n(emean), "lo": _n(elo), "hi": _n(ehi),
                                      "df": edf}}
        contrasts["%s-%s" % (hi, lo)] = rec
    return contrasts, warnings


# ------------------------------------------------------------------ driver

def analyze(run_dirs, level="agent"):
    if level not in ("agent", "run"):
        raise ValueError("level must be 'agent' or 'run'")
    runs = []
    seen = {}
    for rd in run_dirs:
        ev = load_events(rd)
        meta = load_run_meta(rd)
        am = _arm_map(ev, meta)
        rec, modern = _aggregate(ev)
        name = os.path.basename(os.path.normpath(rd)) or rd
        if name in seen:
            seen[name] += 1
            name = "%s#%d" % (name, seen[name])
        else:
            seen[name] = 0
        rs = {"name": name, "run_dir": rd, "seed": _run_seed(meta),
              "n_agents": len(rec), "n_events": sum(len(v) for v in ev.values()),
              "arm_counts": dict(sorted(Counter(am.values()).items())),
              "modern_micro": bool(modern),
              "pooled": _pooled_metrics(rec), "meso": _meso(ev)}
        if level == "agent":
            rs["arms_present"] = sorted(set(am.values()))
            rs["contrasts"] = _run_contrasts_agent(am, _agent_metrics(rec))
        else:
            rs["arm"] = _run_arm(rd, meta, ev)
        runs.append(rs)
    meso = {}
    for rs in runs:
        meso[rs["name"]] = rs.pop("meso")
    if level == "agent":
        contrasts, warnings = _cross_agent(runs)
    else:
        contrasts, warnings = _cross_runlevel(runs)
    verdicts = {}
    for pair, metrics in contrasts.items():
        verdicts[pair] = {}
        for metric, e in metrics.items():
            eff = e.get("effect") or {}
            if e.get("df") == 0:
                # inferential guard: fewer than 2 finite per-run values
                # (n_runs < 2 agent-level / < 2 seed pairs run-level)
                verdicts[pair][metric] = "insufficient_runs"
            else:
                verdicts[pair][metric] = _verdict(eff.get("lo"), eff.get("hi"),
                                                  SESOI[EFFECT_KIND[metric]])
    return {"level": level, "runs": runs, "contrasts": contrasts, "meso": meso,
            "sesoi": {"h": SESOI["h"], "d": SESOI["d"]}, "verdicts": verdicts,
            "warnings": warnings}


def _print_report(res):
    print("== flowmirror modality analysis (PREREG v1.5 draft A3) ==")
    print("level=%s runs=%d bootstrap=%d reps seed=%d sesoi h=%.2f d=%.2f"
          % (res["level"], len(res["runs"]), BOOT_REPS, BOOT_SEED,
             SESOI["h"], SESOI["d"]))
    for rs in res["runs"]:
        counts = ",".join("%s:%d" % (k, v) for k, v in sorted((rs.get("arm_counts") or {}).items())) or "-"
        gini = (res["meso"].get(rs["name"]) or {}).get("heat_gini_posts")
        gtxt = "n/a" if gini is None else "%.3f" % gini
        micro = "modern" if rs["modern_micro"] else "legacy"
        if rs.get("arm"):
            print("run %-16s seed=%s arm=%s agents=%d micro=%s gini(posts)=%s"
                  % (rs["name"], rs["seed"], rs["arm"], rs["n_agents"], micro, gtxt))
        else:
            print("run %-16s seed=%s arms={%s} agents=%d micro=%s gini(posts)=%s"
                  % (rs["name"], rs["seed"], counts, rs["n_agents"], micro, gtxt))
    print("-- contrasts: seed-level Student-t across runs (df = n_runs - 1) --")
    if not res["contrasts"]:
        print("no contrasts (need at least two arms with data)")
        return
    hdr = "%-8s %-21s %8s %-21s %8s %-21s %4s %s"
    print(hdr % ("pair", "metric", "diff", "CI95(diff)", "eff", "CI95(eff)", "df", "verdict"))
    for pair, metrics in res["contrasts"].items():
        for metric, e in metrics.items():
            eff = e.get("effect") or {}
            df = e.get("df")
            print(hdr % (pair, metric, _f3(e.get("mean")), _ci(e.get("lo"), e.get("hi")),
                         _f3(eff.get("mean")), _ci(eff.get("lo"), eff.get("hi")),
                         "-" if df is None else str(df),
                         res["verdicts"].get(pair, {}).get(metric, "indeterminate")))
    print("rule: eff CI lo > 0 -> supported; hi < SESOI -> bounded_null; else indeterminate; "
          "df=0 (n_runs<2) -> insufficient_runs")
    for w in (res.get("warnings") or []):
        print("warning: %s" % w)
    for name in sorted(res["meso"]):
        m = res["meso"][name]
        g = m.get("heat_gini_posts")
        gtxt = "n/a" if g is None else "%.3f" % g
        print("meso %-16s heat_gini=%s(%s) clim=%s fam=%s"
              % (name, gtxt, m.get("heat_source") or "-",
                 json.dumps(m.get("climate_label_distribution") or {}, sort_keys=True),
                 json.dumps(m.get("familiarity") or {}, sort_keys=True)))


# --------------------------------------------------------------- self-test

def _synthetic_run(run_dir, seed, like_tv=None, run_arm=None, legacy=False,
                   with_meta_arms=True):
    """Write a tiny synthetic run for self-tests.

    Audit E2: the fixture must exercise the REAL I2 path, so its rows carry what
    world.publish_day / loop.py actually write -- `post` rows with NUMERIC ids
    shaped {t:03d}{org_index}{slot} and the intent GROUP on `ig` ("I2"/"nonI2",
    half and half as in the demo runs' 20/20 split), impressions with an integer
    `slot` and a ranking `source` from the engine's real domain, and clicks
    landing on I2 post ids. The old fixture hand-planted "I2-..." ids and
    source="I2", shapes the engine has never emitted, which let the module's
    self-test pass while every real event log produced an empty click_rate.

    Agent-level fixture (run_arm=None): T vs TV, 10 agents per arm, 10 days,
    20 impressions/day over that day's 20 posts of which 10 are I2, 2 I2 clicks
    per day (click_rate 0.2), dec micro fields (unless legacy), constant
    aff_sum, uniform post popularity, and (legacy only) one comment per day
    from a single TV-arm agent. Planted TV engagement effect:
    TV agents like like_tv/day vs 4/day for T over 200 imps -> per-agent
    contrast (like_tv - 4)/20. like_tv defaults to 7 for seed 2 and 6 for every
    other seed (+0.15 / +0.10), so per-seed diffs are genuinely different, the
    seed-level t-interval is non-degenerate, and the planted effect stays
    ~+0.10 TV over T.

    Run-level fixture (run_arm="T" or "TV"): the run holds ONLY that arm's 10
    agents -- imp.arm and run_meta.arms are all set to run_arm, the planted
    like rate is the arm's rate (T baseline 4/day, TV = baseline + planted,
    i.e. like_tv/day) -- and cfg modality_run_arm is written into run_meta.json
    so run-level mode can label the run.
    """
    if like_tv is None:
        like_tv = 7 if seed == 2 else 6
    os.makedirs(run_dir, exist_ok=True)
    # engine's real imp.source domain (channels.feed.rank_feed) -- never "I2"
    sources = ("follow", "fit", "trending", "spill", "random")
    days = range(1, 11)
    dstr = {d: "2025-10-%02d" % d for d in days}      # d is an ISO date, not an int
    # each day publishes 20 posts from 2 orgs x 10 slots; ig alternates so half
    # are I2, and `intent` agrees with `ig` exactly as invariant (f) demands
    day_posts = {}
    for d in days:
        items = []
        for oi in (0, 1):
            for j in range(10):
                idx = oi * 10 + j
                ig = "I2" if idx % 2 == 0 else "nonI2"
                intent = "I2" if ig == "I2" else ("I1" if idx % 4 == 1 else "I3")
                items.append(("%03d%d%d" % (d, oi, j), ig, intent, "ORG%d" % (oi + 1)))
        day_posts[d] = items
    i2_of_day = {d: [p for p, ig, _int, _org in items if ig == "I2"]
                 for d, items in day_posts.items()}
    rows = []
    for d in days:                       # the platform publishes before it shows
        for pid, ig, intent, org in day_posts[d]:
            rows.append({"ev": "post", "t": d, "d": dstr[d], "org": org, "p": pid,
                         "intent": intent, "ig": ig, "fund": None, "img": True})
    arms = {}
    for arm in (("T", "TV") if run_arm is None else (run_arm,)):
        pref = "A" if arm == "T" else "B"
        for k in range(10):
            i = "%s%03d" % (pref, k)
            arms[i] = arm
            for d in days:
                for s, (pid, _ig, _int, _org) in enumerate(day_posts[d]):
                    rows.append({"ev": "imp", "t": d, "d": dstr[d], "i": i,
                                 "p": pid, "arm": arm,
                                 "slot": s, "source": sources[s % len(sources)]})
                for pid in i2_of_day[d][:2]:   # 2 of 10 I2 imps clicked -> 0.2
                    rows.append({"ev": "click", "t": d, "d": dstr[d], "i": i,
                                 "p": pid, "oc": "to_checkout"})
                dec = {"ev": "dec", "t": d, "d": dstr[d], "i": i, "status": "ok",
                       "arm": arm, "aff_sum": 5.0}
                if not legacy:
                    dec.update({"n_read": 20, "n_like": 4 if arm == "T" else like_tv,
                                "n_save": 0, "n_follow": 0, "n_comment": 1})
                rows.append(dec)
                if legacy and arm == "TV" and k == 0:
                    # legacy-only commenting agent is a TV agent, so the
                    # TV-T comment contrast is POSITIVE under hi - lo
                    rows.append({"ev": "cmt", "t": d, "d": dstr[d], "i": i,
                                 "p": i2_of_day[d][0], "stance": "neutral",
                                 "text": "c%d" % d})
    for d in days:
        rows.append({"ev": "clim", "t": d, "d": dstr[d], "label": "neutral"})
    rows.append({"ev": "st", "t": 3, "d": dstr[3], "i": "A000", "org": "ORG1",
                 "what": "level", "lv": 1})
    rows.append({"ev": "st", "t": 6, "d": dstr[6], "i": "B000", "org": "ORG1",
                 "what": "follow"})
    meta = {"seed": seed, "run_tag": "fake%s" % seed}
    if run_arm is not None:
        meta["modality_run_arm"] = run_arm
    if with_meta_arms:
        meta["arms"] = arms
    with open(os.path.join(run_dir, "event_log.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(run_dir, "run_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # agent level: like_tv varies by seed (6, 7, 6) -> per-run diffs
        # +0.10, +0.15, +0.10 and a non-degenerate seed-level t-interval
        rds = []
        for s in (1, 2, 3):
            rd = os.path.join(td, "s%d" % s)
            _synthetic_run(rd, s)
            rds.append(rd)
        res = analyze(rds, level="agent")
        v = res["verdicts"]["TV-T"]
        assert v["engagement_rate"] == "supported", v
        assert v["subscribe_conversion"] in ("bounded_null", "indeterminate"), v
        assert v["aff_sum"] == "bounded_null", v
        e = res["contrasts"]["TV-T"]["engagement_rate"]
        # fp-safe coverage check (tolerance 1e-9): planted diffs are
        # 0.0999999... / 0.1499999..., so compare the interval with slack
        assert e["lo"] is not None and e["lo"] - 1e-9 <= 0.10 <= e["hi"] + 1e-9, e
        assert abs(e["per_run"][0] - 0.10) <= 1e-9, e
        assert abs(e["mean"] - (0.10 + 0.15 + 0.10) / 3.0) <= 1e-9, e
        json.dumps(res, allow_nan=False)  # strict-JSON safe
        # older logs: micro fields absent -> n_comment from cmt rows, others null
        rd2 = os.path.join(td, "legacy")
        _synthetic_run(rd2, 9, legacy=True)
        res2 = analyze([rd2], level="agent")
        assert res2["runs"][0]["modern_micro"] is False
        c2 = res2["contrasts"]["TV-T"]["comment_rate"]
        # the legacy commenter is a TV agent and the contrast is hi - lo
        # (T < TC < TV), so TV-T = mean(TV) - mean(T) = +0.005
        assert c2["mean"] is not None and abs(c2["mean"] - 0.005) <= 1e-9, c2
        assert res2["contrasts"]["TV-T"]["engagement_rate"]["mean"] is None
        # run-level mode: homogeneous single-arm runs paired by seed
        rds3 = []
        for s in (1, 2, 3):
            for arm in ("T", "TV"):
                rd = os.path.join(td, "%s%d" % (arm, s))
                _synthetic_run(rd, s, run_arm=arm)
                rds3.append(rd)
        res3 = analyze(rds3, level="run")
        e3 = res3["contrasts"]["TV-T"]["engagement_rate"]
        # T run: 4 likes/day / 20 imps -> 0.20; TV run: like_tv/20 ->
        # 0.30 (seeds 1,3) / 0.35 (seed 2); per-seed diffs +0.10, +0.15, +0.10
        assert e3["mean"] is not None and \
            abs(e3["mean"] - (0.10 + 0.15 + 0.10) / 3.0) <= 1e-9, e3
        assert e3["df"] == 2 and e3["n_common_seeds"] == 3, e3
        assert res3["verdicts"]["TV-T"]["engagement_rate"] == "supported"
        # unpaired seed: a T run whose seed has no TV partner is warned
        # about and excluded from the contrast, not a crash
        rd4 = os.path.join(td, "T99")
        _synthetic_run(rd4, 99, run_arm="T")
        res4 = analyze(rds3 + [rd4], level="run")
        e4 = res4["contrasts"]["TV-T"]["engagement_rate"]
        assert len(e4["per_run"]) == 3 and e4["df"] == 2, e4
        assert any("99" in w for w in res4.get("warnings", [])), res4.get("warnings")
        json.dumps(res3, allow_nan=False)
        json.dumps(res4, allow_nan=False)
        # single run (n_runs = 1): the seed-level interval degenerates, so
        # the inferential guard must withhold both CIs, pin df = 0, keep the
        # mean, and stamp every verdict insufficient_runs
        rd5 = os.path.join(td, "solo")
        _synthetic_run(rd5, 1)
        res5 = analyze([rd5], level="agent")
        for pair, mv in res5["verdicts"].items():
            for metric, verd in mv.items():
                assert verd == "insufficient_runs", (pair, metric, verd)
        e5 = res5["contrasts"]["TV-T"]["engagement_rate"]
        assert e5["lo"] is None and e5["hi"] is None and e5["df"] == 0, e5
        assert e5["effect"]["lo"] is None and e5["effect"]["hi"] is None, e5
        assert e5["mean"] is not None and abs(e5["mean"] - 0.10) <= 1e-9, e5
        assert any("n_runs=1" in w and "descriptive only" in w
                   for w in res5["warnings"]), res5["warnings"]
        json.dumps(res5, allow_nan=False)
        # run-level: a single seed pair is equally descriptive only
        res6 = analyze([os.path.join(td, "T1"), os.path.join(td, "TV1")], level="run")
        for pair, mv in res6["verdicts"].items():
            for metric, verd in mv.items():
                assert verd == "insufficient_runs", (pair, metric, verd)
        e6 = res6["contrasts"]["TV-T"]["engagement_rate"]
        assert e6["lo"] is None and e6["hi"] is None and e6["df"] == 0, e6
        assert abs(e6["mean"] - 0.10) <= 1e-9, e6
        assert any("n_runs=1" in w and "descriptive only" in w
                   for w in res6["warnings"]), res6["warnings"]
        json.dumps(res6, allow_nan=False)
    print("modality self-test ... passed")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m flowmirror.analysis.modality",
        description="Modality experiment + channel attribution analysis "
                    "(PREREG v1.5 draft A3; seed-level t per PREREG v1.3 B11).")
    ap.add_argument("run_dirs", nargs="*", metavar="run_dir",
                    help="one run directory per seed (each with event_log.jsonl)")
    ap.add_argument("--level", choices=("agent", "run"), default="agent",
                    help="agent-level arm contrast (default) or run-level arm "
                         "from cfg modality_run_arm")
    ap.add_argument("--out", default=None, help="write the full result JSON here")
    ap.add_argument("--self-test", action="store_true", help="run built-in checks and exit")
    a = ap.parse_args(argv)
    if a.self_test:
        _self_test()
        return 0
    if not a.run_dirs:
        ap.error("at least one run_dir is required (or --self-test)")
    res = analyze(a.run_dirs, level=a.level)
    _print_report(res)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=True, indent=1)
        print("saved %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

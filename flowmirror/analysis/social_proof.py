"""Seed-heat (social proof) analysis: Muchnik-Aral-Taylor style plus/ctrl contrast."""
import argparse, json, os, random, statistics

from flowmirror.analysis import common

DAYS = (0, 1, 2)
FIELDS = ("imps", "likes", "saves", "comments", "clicks", "matches")
# Template returned when the heat-seed experiment is off for this run.
_OFF = {"enabled": False, "plus": None, "ctrl": None, "diff_first_day": None,
        "diff_cum2": None, "multiplier": None, "multiplier_path": None, "verdict": None,
        "na_n": 0, "rows": [], "plus_rows": [], "ctrl_rows": [],
        "note": "本次运行种子热度实验关闭"}

def per_post_rows(posts, evs, arm_map=None):
    """Pure: per-(pid, arm) daily rows for d in {0,1,2}; posts define t0/label/intent."""
    t0s, info = {}, {}
    for p in posts:
        pid = p.get("p")
        if pid is not None:
            t0s[pid] = p.get("t", 0)
            info[pid] = (p.get("heat_seed", "na"), p.get("intent") or p.get("ig") or "?")
    cnt = {}

    def arm_of(r):
        return arm_map.get(r.get("i"), "all") if arm_map else "all"

    def add(pid, arm, field, t):
        d = t - t0s[pid] if pid in t0s else None
        if d in DAYS:
            slot = cnt.setdefault((pid, arm), {f: {} for f in FIELDS})
            slot[field][d] = slot[field].get(d, 0) + 1

    for key, field, mo in (("imp", "imps", None), ("cmt", "comments", None),
                           ("click", "clicks", None), ("co", "matches", "match")):
        for r in evs.get(key, []):
            if mo is None or r.get("oc") == mo:
                add(r.get("p"), arm_of(r), field, r.get("t", 0))
    for r in evs.get("dec", []):
        arm = arm_of(r)
        for p in (r.get("p_like") or []):
            add(p, arm, "likes", r.get("t", 0))
        for p in (r.get("p_save") or []):
            add(p, arm, "saves", r.get("t", 0))
    if arm_map is None:  # arm-agnostic pass: guarantee one row per post
        for pid in t0s:
            cnt.setdefault((pid, "all"), {f: {} for f in FIELDS})
    out = {}
    for (pid, arm), c in cnt.items():
        row = _finalize(pid, t0s[pid], c, info[pid])
        row["arm"] = arm
        out[(pid, arm)] = row
    return out

def _finalize(pid, t0, c, info):
    """Pure: flatten day counters into one row with derived rates."""
    row = {"pid": pid, "t0": t0, "label": info[0], "intent": info[1]}
    for d in DAYS:
        for f in FIELDS:
            row[f"{f}_{d}"] = c[f].get(d, 0)
        row[f"inter_{d}"] = row[f"likes_{d}"] + row[f"saves_{d}"] + row[f"comments_{d}"]
    row["like_rate_0"] = row["likes_0"] / row["imps_0"] if row["imps_0"] else None
    row["inter_cum2"] = sum(row[f"inter_{d}"] for d in DAYS)
    row["imps_cum2"] = sum(row[f"imps_{d}"] for d in DAYS)
    return row

def group_stats(rows):
    """Pure: group-level first-day rates and cumulative interaction stats."""
    out = {"n_posts": len(rows), "imps_0": sum(r["imps_0"] for r in rows)}
    rates = [r["like_rate_0"] for r in rows if r["like_rate_0"] is not None]
    out["like_rate_0_mean"] = statistics.fmean(rates) if rates else None
    out["like_rate_0_pooled"] = sum(r["likes_0"] for r in rows) / out["imps_0"] if out["imps_0"] else None
    imps2 = sum(r["imps_cum2"] for r in rows)
    out["inter_per_imp_cum2"] = sum(r["inter_cum2"] for r in rows) / imps2 if imps2 else None
    out["mean_inter_cum2"] = statistics.fmean([r["inter_cum2"] for r in rows]) if rows else None
    out["mean_inter_d"] = [statistics.fmean([r[f"inter_{d}"] for r in rows]) if rows else None
                           for d in DAYS]
    return out

def compare(plus_rows, ctrl_rows, k):
    """Pure: plus vs ctrl contrast incl. cascade multiplier (k<=0 disables it)."""
    sp, sc = group_stats(plus_rows), group_stats(ctrl_rows)
    dif = lambda a, b: None if a is None or b is None else a - b
    out = {"plus": sp, "ctrl": sc, "k": k,
           "diff_first_day": dif(sp["like_rate_0_pooled"], sc["like_rate_0_pooled"]),
           "diff_cum2": dif(sp["inter_per_imp_cum2"], sc["inter_per_imp_cum2"])}
    m = path = v = None
    if k and k > 0 and sp["mean_inter_cum2"] is not None and sc["mean_inter_cum2"] is not None:
        m = (sp["mean_inter_cum2"] - sc["mean_inter_cum2"]) / k
        path = [None if a is None or b is None else (a - b) / k
                for a, b in zip(sp["mean_inter_d"], sc["mean_inter_d"])]
        if None not in path:
            v = "none_or_negative" if m <= 0 else (
                "amplifying" if m > 1 and path[2] > path[1] > path[0] else "damped")
    out["multiplier"], out["multiplier_path"], out["verdict"] = m, path, v
    return out

def bootstrap_ci(plus_rows, ctrl_rows, k, n_boot=200, seed=2027):
    """Pure: post-level bootstrap 95% CI for diff_first_day and multiplier."""
    if len(plus_rows) < 3 or len(ctrl_rows) < 3:
        return None, None, "plus或ctrl帖数不足3，自助区间不可算"
    rng, diffs, mults = random.Random(seed), [], []
    for _ in range(n_boot):
        ps = [plus_rows[rng.randrange(len(plus_rows))] for _ in plus_rows]
        cs = [ctrl_rows[rng.randrange(len(ctrl_rows))] for _ in ctrl_rows]
        c = compare(ps, cs, k)
        diffs += [c["diff_first_day"]] * (c["diff_first_day"] is not None)
        mults += [c["multiplier"]] * (c["multiplier"] is not None)
    return _pct(diffs), _pct(mults), None

def _pct(vals):
    if len(vals) < 2:
        return None
    q = statistics.quantiles(vals, n=40)  # 2.5% and 97.5% cut points
    return {"lo": q[0], "hi": q[-1], "n_boot": len(vals)}

def summarize(posts, evs, k):
    """Pure core: group contrast; note set when the heat-seed experiment is off."""
    if not any("heat_seed" in p for p in posts):
        return dict(_OFF)
    rows = list(per_post_rows(posts, evs).values())
    plus = [r for r in rows if r["label"] == "plus"]
    ctrl = [r for r in rows if r["label"] == "ctrl"]
    out = compare(plus, ctrl, k)
    out.update(enabled=True, rows=rows, plus_rows=plus, ctrl_rows=ctrl,
               na_n=sum(1 for r in rows if r["label"] == "na"), note="")
    return out

def _strata(rows, key_of):
    buckets = {}
    for r in rows:
        buckets.setdefault(key_of(r), []).append(r)
    out = {}
    for key in sorted(buckets, key=str):
        rs = buckets[key]
        plus = [r for r in rs if r["label"] == "plus"]
        ctrl = [r for r in rs if r["label"] == "ctrl"]
        out[str(key)] = {"n_posts": len(rs), "n_plus": len(plus), "n_ctrl": len(ctrl),
                         "diff_first_day": compare(plus, ctrl, 0)["diff_first_day"]}
    return out

def _focus(ev, posts, hs, notes):
    """Focus-fund block: match path, matched CNY, other-fund totals, redemption check."""
    fund = hs.get("focus_fund")
    if not fund:
        notes.append("存在na帖但未配置focus_fund，focus分析跳过")
        return None
    t0s = {p.get("p"): p.get("t", 0) for p in posts if p.get("fund") == fund}
    path, cny, oth = [0, 0, 0], 0.0, {"n_co": 0, "n_match": 0, "cny_matched": 0.0}
    for r in ev.get("co", []):
        amt, hit = r.get("amt") or 0.0, r.get("fund") == fund
        d = r.get("t", 0) - t0s.get(r.get("p"), -99)
        if hit and r.get("oc") == "match" and d in DAYS:
            path[d] += 1
            cny += amt
        elif not hit:
            oth["n_co"] += 1
            if r.get("oc") == "match":
                oth["n_match"] += 1
                oth["cny_matched"] += amt
    corr = {"focus_redeems": 0, "other_redeems": 0, "focus_cny": 0.0, "other_cny": 0.0}
    for r in ev.get("act", []):
        if r.get("kind") == "redeem":
            hit = r.get("fund") == fund
            corr["focus_redeems" if hit else "other_redeems"] += 1
            corr["focus_cny" if hit else "other_cny"] += r.get("amt") or 0.0
    if not corr["focus_redeems"] + corr["other_redeems"]:
        notes.append("无赎回记录，修正阶段不可算")
        corr = None
    lab = {x: sum(1 for p in posts if p.get("fund") == fund and p.get("heat_seed") == x)
           for x in ("plus", "ctrl", "na")}
    return {"fund": fund, "n_posts": lab, "matches_path_d012": path, "cny_matched": cny,
            "other_funds": oth, "correction": corr}

def _analyze_one(run_dir):
    tag = os.path.basename(os.path.normpath(run_dir)) or run_dir
    res = dict.fromkeys(("k", "plus", "ctrl", "na", "diff_first_day", "diff_cum2",
                         "multiplier", "multiplier_path", "verdict", "bootstrap",
                         "by_arm", "by_intent", "focus", "note"), None)
    res.update(run_dir=run_dir, tag=tag, enabled=False, note="")
    try:
        ev, meta = common.load_events(run_dir), common.load_run_meta(run_dir)
        hs = ((meta.get("cfg") or {}).get("heat_seed")) or {}
        k, posts = hs.get("k", 10), list(ev.get("post", []))
        s = summarize(posts, ev, k)
        res["k"], res["enabled"] = k, s["enabled"]
        notes = [s["note"]] if s["note"] else []
        if s["enabled"]:
            for key in ("plus", "ctrl", "diff_first_day", "diff_cum2", "multiplier",
                        "multiplier_path", "verdict"):
                res[key] = s[key]
            res["na"] = {"n_posts": s["na_n"]}
            if not s["plus"]["n_posts"] or not s["ctrl"]["n_posts"]:
                notes.append("plus或ctrl帖数为0，组间对比不可算")
            lo, hi, bnote = bootstrap_ci(s["plus_rows"], s["ctrl_rows"], k)
            res["bootstrap"] = {"diff_first_day": lo, "multiplier": hi, "note": bnote}
            notes += [bnote] if bnote else []
            res["by_intent"] = _strata(s["rows"], lambda r: r["intent"])
            arm_rows = list(per_post_rows(posts, ev, meta.get("arms") or {}).values())
            res["by_arm"] = _strata(arm_rows, lambda r: r["arm"])
            if s["na_n"]:
                res["focus"] = _focus(ev, posts, hs, notes)
        res["note"] = "；".join(x for x in notes if x)
    except Exception as exc:  # never propagate: CLI must stay exit-code 0
        res["note"] = "分析失败：%r" % (exc,)
    return res

def _cross(per_run):
    out = {}
    for key in ("diff_first_day", "multiplier"):
        vals = [r.get(key) for r in per_run if r.get("enabled") and r.get(key) is not None]
        if len(vals) >= 2:
            mean, sd, lo, hi, df = common.seed_t_interval(vals)
            out[key] = {"n": len(vals), "mean": mean, "sd": sd, "lo": lo, "hi": hi, "df": df}
        else:
            out[key] = None
            out.setdefault("warnings", []).append(key + "：有效运行数不足2，跨运行区间不可算")
    return out

def analyze(run_dirs):
    """Entry point: per-run summaries plus cross-run seed-t intervals."""
    per = [_analyze_one(rd) for rd in run_dirs]
    return {"per_run": per, "cross_run": _cross(per)}

def _dump(path, obj):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=True, indent=1)
    except Exception as exc:
        print("[social_proof] 写入失败 %s：%r" % (path, exc))

def _print_line(r):
    tag = r.get("tag") or "?"
    if not r.get("enabled"):
        print("[%s] %s" % (tag, r.get("note") or "种子热度实验未开启"))
        return
    p, c = r.get("plus") or {}, r.get("ctrl") or {}
    pct = lambda x: "%.1f%%" % (100 * x) if isinstance(x, (int, float)) else "NA"
    d, m = r.get("diff_first_day"), r.get("multiplier")
    path = r.get("multiplier_path") or []
    pt = "→".join("%.3f" % x if isinstance(x, (int, float)) else "NA" for x in path) or "NA"
    print("[%s] plus %d / ctrl %d | 首日点赞率 plus %s vs ctrl %s（差 %s）| 级联乘数 %s（路径 %s，判定 %s）"
          % (tag, p.get("n_posts", 0), c.get("n_posts", 0), pct(p.get("like_rate_0_pooled")),
             pct(c.get("like_rate_0_pooled")),
             "%.1fpp" % (100 * d) if isinstance(d, (int, float)) else "NA",
             "%.3f" % m if isinstance(m, (int, float)) else "NA", pt, r.get("verdict")))

def main(argv=None):
    ap = argparse.ArgumentParser(description="种子热度（社会证明）实验分析")
    ap.add_argument("run_dirs", nargs="*", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 0
    if args.self_test:
        _self_test()
    elif not args.run_dirs:
        print("[social_proof] 未提供 run_dir")
    else:
        res = analyze(args.run_dirs)
        for r in res["per_run"]:
            _print_line(r)
            if not args.no_write and not args.out:
                _dump(os.path.join(r["run_dir"], "analysis", "social_proof.json"), r)
        if args.out:
            _dump(args.out, res)
    return 0

def _self_test():
    mk = lambda pid, lb: {"ev": "post", "t": 1, "org": "o", "p": pid, "intent": "I2",
                          "fund": "003670", "heat_seed": lb}
    posts = [mk("p1", "plus"), mk("p2", "plus"), mk("p3", "ctrl"), mk("p4", "ctrl")]
    imps, decs = [], []
    for n, (pid, likes) in enumerate((("p1", 4), ("p2", 4), ("p3", 2), ("p4", 2))):
        imps += [{"ev": "imp", "t": 1, "i": "i%d_%d" % (n, j), "p": pid} for j in range(10)]
        decs += [{"ev": "dec", "t": 1, "i": "i%d_%d" % (n, j), "p_like": [pid],
                  "p_save": []} for j in range(likes)]
    evs = {"imp": imps, "dec": decs}
    s = summarize(posts, evs, 10)  # case 1: contrast, multiplier, path
    assert abs(s["diff_first_day"] - 0.2) < 1e-9 and abs(s["multiplier"] - 0.2) < 1e-9
    assert all(abs(a - b) < 1e-9 for a, b in zip(s["multiplier_path"], [0.2, 0.0, 0.0]))
    off = [{k: v for k, v in p.items() if k != "heat_seed"} for p in posts]
    s2 = summarize(off, evs, 10)  # case 2: experiment off
    assert s2["multiplier"] is None and s2["note"]
    s3 = summarize(posts + [mk("p5", "plus")], evs, 10)  # case 3: no day-0 impressions
    assert [r for r in s3["rows"] if r["pid"] == "p5"][0]["like_rate_0"] is None
    assert abs(group_stats(s3["plus_rows"])["like_rate_0_mean"] - 0.4) < 1e-9
    assert compare(s["plus_rows"], s["ctrl_rows"], 0)["multiplier"] is None  # case 4: k=0
    lo, hi, bnote = bootstrap_ci(s["plus_rows"], s["ctrl_rows"], 10)  # case 5: n<3
    assert lo is None and hi is None and bnote
    print("[social_proof] self-test 全部通过")


if __name__ == "__main__":
    raise SystemExit(main())

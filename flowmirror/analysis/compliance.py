"""Compliant vs non-compliant note performance on the platform (observational, descriptive)."""
import argparse
import json
import os
from collections import defaultdict

from flowmirror.analysis import common

DEFAULT_LABELS = "data/creatives/cn/compliance_labels_v1.jsonl"
MIN_IMPS = 20  # a rate is reported only when the impression denominator >= MIN_IMPS
RULES = ["R0%d" % i for i in range(1, 8)]
SEVS = ["none", "low", "medium", "high"]
TOTAL_KEYS = ("imps", "likes", "saves", "comments", "clicks", "checkouts", "matches", "cny")
NUM = {"like_rate": "likes", "save_rate": "saves", "comment_rate": "comments", "click_rate": "clicks", "match_rate": "matches", "cny_per_imp": "cny"}
RATE_KEYS = ["like_rate", "save_rate", "comment_rate", "click_rate", "match_rate", "cny_per_imp"]
CORE_RATES = ["like_rate", "click_rate", "match_rate"]
CN = {"compliant": "合规", "noncompliant": "不合规"}
EMPTY = {"imps": 0, "likes": 0, "saves": 0, "comments": 0, "clicks": 0, "checkouts": 0, "matches": 0, "cny": 0.0}

def _rates(t, keys=RATE_KEYS):
    imps = t.get("imps", 0)
    return {k: (round(t.get(NUM[k], 0) / imps, 4) if imps >= MIN_IMPS else None) for k in keys}

def load_labels(labels_path):
    # sidecar jsonl -> ({note_id: row}, [notes]); the {"_meta": ...} header line is skipped
    if not labels_path or not os.path.isfile(labels_path):
        return {}, ["标签文件不可读：%s" % (labels_path or "")]
    rows, notes = {}, []
    try:
        with open(labels_path, "r", encoding="utf-8") as fh:
            for ln in fh:
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(row, dict) and "_meta" not in row and row.get("note_id"):
                    rows[row["note_id"]] = row
    except OSError:
        return {}, ["标签文件不可读：%s" % labels_path]
    if not rows:
        notes.append("标签文件可读但无有效标签行：%s" % labels_path)
    return rows, notes

def _classify(post, labels):
    # group from hits recomputed as `hit is True`; hit=None means "unknown", never a hit
    row = labels.get(post.get("note") or "")
    if not row:
        return "unlabeled", "none", set()
    labs = row.get("labels") or {}
    hits = {r for r in RULES if (labs.get(r) or {}).get("hit") is True}
    sev = row.get("severity_max")
    return ("compliant" if not hits else "noncompliant"), (sev if sev in SEVS[1:] else "none"), hits

def _funnel(ev, arms):
    per = defaultdict(lambda: defaultdict(lambda: dict(EMPTY)))
    arm_of = lambda i: arms.get(i, "all")
    for row in ev.get("imp", []):
        if row.get("p"):
            per[row["p"]][arm_of(row.get("i"))]["imps"] += 1
    for row in ev.get("dec", []):
        a = arm_of(row.get("i"))
        for pk, kk in (("p_like", "likes"), ("p_save", "saves")):
            for p in (row.get(pk) or []):
                if p in per:
                    per[p][a][kk] += 1
    for key, evk in (("comments", "cmt"), ("clicks", "click")):
        for row in ev.get(evk, []):
            if row.get("p") in per:
                per[row["p"]][arm_of(row.get("i"))][key] += 1
    for row in ev.get("co", []):
        p = row.get("p")
        if p in per:
            t = per[p][arm_of(row.get("i"))]
            t["checkouts"] += 1
            if row.get("oc") == "match":
                t["matches"] += 1
                amt = row.get("amt")
                t["cny"] += amt if isinstance(amt, (int, float)) else 0
    return per

def _run_metrics(ev, arms, labels, label_notes):
    # core metrics from an in-memory event mapping + {note_id: label_row}; no disk access
    notes = list(label_notes)
    counts = {"compliant": 0, "noncompliant": 0, "unlabeled": 0}
    info = {}  # pid -> [group, severity bucket, hit rules]
    for post in ev.get("post", []):
        pid = post.get("p")
        if pid and pid not in info:
            info[pid] = list(_classify(post, labels))
            counts[info[pid][0]] += 1
    per = _funnel(ev, arms)
    agg, seen = {}, {}

    def _add(key, pid, t):
        a = agg.setdefault(key, dict(EMPTY))
        seen.setdefault(key, set()).add(pid)
        for k in TOTAL_KEYS:
            a[k] += t[k]

    def _merge(pids):
        t = dict(EMPTY)
        for pid in pids:
            for at in per.get(pid, {}).values():
                for k in TOTAL_KEYS:
                    t[k] += at[k]
        return t
    for pid, (g, _s, _h) in info.items():
        if g != "unlabeled":
            for arm, t in per.get(pid, {}).items():
                _add((g, arm), pid, t)  # the same post counts separately in each arm
                _add((g, "all"), pid, t)
    arm_keys = ["all"] + sorted({a for (_g, a) in agg if a != "all"})
    groups = {}
    for arm in arm_keys:
        lbl = "全体" if arm == "all" else "臂%s" % arm
        entry = {}
        for g in ("compliant", "noncompliant"):
            t = agg.get((g, arm))
            if t is None:
                entry[g] = dict(n_posts=0, imps=0, **dict.fromkeys(RATE_KEYS, None))
            else:
                entry[g] = {"n_posts": len(seen[(g, arm)]), "imps": t["imps"]}
                entry[g].update({k: t[k] for k in TOTAL_KEYS})
                entry[g]["cny"] = round(t["cny"], 2)
                entry[g].update(_rates(t))
            if entry[g]["n_posts"] == 0:
                notes.append("%s：%s组帖数为 0，该组指标不可用" % (lbl, CN[g]))
            elif entry[g]["imps"] < MIN_IMPS:
                notes.append("%s：%s组展示数 %d < %d，对应率记为 None" % (lbl, CN[g], entry[g]["imps"], MIN_IMPS))
        cv, nv = entry["compliant"], entry["noncompliant"]
        entry["diff"] = {k: (round(nv[k] - cv[k], 4) if None not in (cv[k], nv[k]) else None) for k in RATE_KEYS}
        groups[arm] = entry
    by_rule, by_sev = {}, {}
    for r in RULES:  # posts hitting each rule, merged across all arms
        pids = [pid for pid, (_g, _s, hs) in info.items() if r in hs]
        t = _merge(pids)
        by_rule[r] = dict(n_posts=len(pids), imps=t["imps"], **_rates(t, CORE_RATES))
    for sev in SEVS:  # severity_max None / unknown falls into the "none" bucket
        pids = [pid for pid, (g, s, _h) in info.items() if g != "unlabeled" and s == sev]
        t = _merge(pids)
        by_sev[sev] = dict(n_posts=len(pids), imps=t["imps"], **_rates(t, CORE_RATES))
    if counts["compliant"] + counts["noncompliant"] == 0:
        notes.append("无已标注帖（未标 %d 帖不进对比），两组对比不可用，各组率均为 None" % counts["unlabeled"])
    return {"n_posts": counts, "arms": arm_keys, "groups": groups,
            "by_rule": by_rule, "by_severity": by_sev, "notes": notes}

def analyze(run_dirs, labels_path=None):
    labels_path = labels_path or DEFAULT_LABELS
    labels, label_notes = load_labels(labels_path)
    runs, diffs = [], {k: [] for k in CORE_RATES}
    for rd in run_dirs:
        res = {"run_dir": rd}
        try:
            ev = common.load_events(rd)
            arms = (common.load_run_meta(rd) or {}).get("arms") or {}
            res.update(_run_metrics(ev, arms, labels, label_notes))
        except Exception as ex:  # a broken run must not crash the CLI
            res["notes"] = list(label_notes) + ["运行数据读取失败：%s" % ex]
        runs.append(res)
        d = (res.get("groups", {}).get("all") or {}).get("diff") or {}
        diffs = {k: v + [d.get(k)] for k, v in diffs.items()}
    cross = {}
    for k in CORE_RATES:
        vals = [v for v in diffs[k] if v is not None]
        if len(vals) >= 2:
            mean, sd, lo, hi, df = common.seed_t_interval(vals)
            cross[k] = {"n_runs": len(vals), "mean": mean, "sd": sd, "lo": lo, "hi": hi, "df": df}
        else:
            cross[k] = None
            label_notes.append("cross_run.%s：非 None 差值不足 2 个，区间未计算" % k)
    return {"metric": "compliance", "design": "observational", "wording": "descriptive",
            "labels_path": labels_path, "n_labels": len(labels), "min_imps": MIN_IMPS,
            "notes": label_notes, "runs": runs, "cross_run": cross}

def _pct(v):
    return "NA" if v is None else "%.1f%%" % (100.0 * v)

def _print_line(res):
    cnt = res.get("n_posts", {})
    g = res.get("groups", {}).get("all") or {}
    c, n = g.get("compliant", {}), g.get("noncompliant", {})
    tag = os.path.basename(os.path.normpath(res.get("run_dir") or "")) or "?"
    print("[%s] 帖 %d（合规 %d / 不合规 %d / 未标 %d）| 点赞率 合规 %s vs 不合规 %s | 点击率 合规 %s vs 不合规 %s | 成交率 合规 %s vs 不合规 %s"
          % (tag, sum(cnt.values()), cnt.get("compliant", 0), cnt.get("noncompliant", 0), cnt.get("unlabeled", 0),
             _pct(c.get("like_rate")), _pct(n.get("like_rate")), _pct(c.get("click_rate")), _pct(n.get("click_rate")),
             _pct(c.get("match_rate")), _pct(n.get("match_rate"))))

def _write(path, obj):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=True, indent=1)
        return True
    except (OSError, TypeError, ValueError):
        return False

def main(argv=None):
    ap = argparse.ArgumentParser(description="合规 vs 不合规笔记表现对比（观察性描述）")
    ap.add_argument("run_dirs", nargs="*")
    ap.add_argument("--labels", default=DEFAULT_LABELS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        try:
            _self_test()
            print("[self-test] 全部断言通过")
        except AssertionError as ex:
            print("[self-test] 失败：%s" % ex)
        return 0
    if not args.run_dirs:
        print("未提供 run_dirs（可先运行 --self-test 自检）")
        return 0
    res = analyze(args.run_dirs, labels_path=args.labels)
    for r in res["runs"]:
        _print_line(r)
    if not args.no_write:
        for pth in ([args.out] if args.out else [os.path.join(rd, "analysis", "compliance.json") for rd in args.run_dirs]):
            print("已写入 %s" % pth if _write(pth, res) else "写入失败 %s" % pth)
    return 0

def _mk_events(i1, i2, l1, l2):
    return {"post": [{"ev": "post", "p": "p1", "note": "n1"}, {"ev": "post", "p": "p2", "note": "n2"}],
            "imp": [{"ev": "imp", "i": "inv1", "p": "p1"}] * i1 + [{"ev": "imp", "i": "inv1", "p": "p2"}] * i2,
            "dec": [{"ev": "dec", "i": "inv1", "p_like": ["p1"]}] * l1 + [{"ev": "dec", "i": "inv1", "p_like": ["p2"]}] * l2,
            "cmt": [], "click": [], "co": []}

def _label(hits, sev):
    return {"note_id": "x", "severity_max": sev, "n_hits": 99,  # n_hits in the file is distrusted
            "labels": {r: {"hit": hits.get(r, False)} for r in RULES}}

def _self_test():
    labs = {"n1": _label({}, None), "n2": _label({"R01": True}, "high")}
    a = _run_metrics(_mk_events(30, 30, 3, 9), {}, labs, [])["groups"]["all"]
    assert abs(a["compliant"]["like_rate"] - 0.1) < 1e-9 and abs(a["noncompliant"]["like_rate"] - 0.3) < 1e-9
    assert abs(a["diff"]["like_rate"] - 0.2) < 1e-9  # diff = noncompliant - compliant
    assert _run_metrics(_mk_events(5, 30, 2, 9), {}, labs, [])["groups"]["all"]["compliant"]["like_rate"] is None
    r = _run_metrics(_mk_events(20, 20, 2, 2), {}, {"n1": _label({"R02": None}, None), "n2": _label({"R01": True}, "low")}, [])
    assert r["n_posts"]["compliant"] == 1 and r["n_posts"]["noncompliant"] == 1  # hit=None is not a hit
    r = _run_metrics(_mk_events(30, 30, 3, 9), {}, {}, ["标签文件不可读：fake"])
    assert r["n_posts"]["unlabeled"] == 2 and r["notes"] and r["notes"][0]
    assert r["groups"]["all"]["compliant"]["like_rate"] is None and r["groups"]["all"]["noncompliant"]["click_rate"] is None
    r = _run_metrics(_mk_events(20, 20, 2, 2), {}, {"n1": _label({"R01": True}, None), "n2": _label({"R01": True}, "high")}, [])
    assert r["by_severity"]["none"]["n_posts"] == 1 and r["by_severity"]["high"]["n_posts"] == 1

if __name__ == "__main__":
    raise SystemExit(main())

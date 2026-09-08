"""Institutional-layer learning: platform I2 push-share path, per-org intent
weight convergence, and intent-mix entropy. Stdlib only; never raises; exit 0."""
import argparse
import collections
import json
import math
import os

from flowmirror.analysis import common

INTENTS = ("I1", "I2", "I3")
ADAPTIVE_OFF = "本次运行机构适应关闭（无 inst 行）"

def _tag(rd):
    return os.path.basename(os.path.normpath(os.path.abspath(rd))) or str(rd)

def _inst_cfg(meta):
    # institutions config: period_days with default 5 when missing or invalid
    v = (((meta or {}).get("cfg") or {}).get("institutions") or {}).get("period_days", 5)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 5

def _entropy_bits(cnt):
    """Shannon entropy in bits over the three intent shares; zeros skipped."""
    tot = sum(cnt.get(k, 0) for k in INTENTS)
    if not tot:
        return None
    return -sum(p * math.log2(p) for p in
                (cnt.get(k, 0) / tot for k in INTENTS if cnt.get(k, 0)))

def _period_rows(cnt, period_days):
    # rows for k = 0..max_k counted from t=0; empty middle buckets keep None
    out = []
    for k in range((max(cnt) + 1) if cnt else 0):
        c, n = cnt.get(k, {}), sum(cnt.get(k, {}).values())
        out.append({"k": k, "t0": k * period_days, "t1": (k + 1) * period_days - 1, "n_posts": n,
                    "i2_share": (c.get("I2", 0) / n) if n else None, "entropy_bits": _entropy_bits(c)})
    return out

def _pairwise_l1(wmap):
    # mean over org pairs of sum|w_a-w_b| across intents; None if fewer than 2 orgs
    orgs = sorted(wmap)
    if len(orgs) < 2:
        return None
    ds = [sum(abs(wmap[a].get(k, 0.0) - wmap[b].get(k, 0.0)) for k in INTENTS)
          for i, a in enumerate(orgs) for b in orgs[i + 1:]]
    return sum(ds) / len(ds)

def _weights_block(rows, notes):
    """Weight path, final weights, pairwise-L1 ends, convergence, conv totals."""
    if not rows:
        return {"path": None, "final_w": None, "pairwise_l1_first": None, "pairwise_l1_final": None,
                "convergence": None, "conv_total": None, "note": ADAPTIVE_OFF}
    by_t, conv_tot = {}, collections.defaultdict(lambda: {k: 0 for k in INTENTS})
    for r in rows:
        org, w, c = str(r.get("org", "?")), r.get("w") or {}, r.get("conv") or {}
        by_t.setdefault(int(r.get("t", 0) or 0), {})[org] = {
            k: float(w.get(k, 0.0) or 0.0) for k in INTENTS}
        for k in INTENTS:
            conv_tot[org][k] += int(c.get(k, 0) or 0)
    path = [{"t": t, "w": by_t[t], "pairwise_l1_mean": _pairwise_l1(by_t[t])}
            for t in sorted(by_t)]
    l1a, l1b = path[0]["pairwise_l1_mean"], path[-1]["pairwise_l1_mean"]
    if l1a is None or l1b is None:
        notes.append("机构数不足 2，两两 L1 与收敛度不可计算（仅保留权重路径与响应合计）")
    return {"path": path, "final_w": path[-1]["w"], "pairwise_l1_first": l1a, "pairwise_l1_final": l1b,
            "convergence": (l1a - l1b) if (l1a is not None and l1b is not None) else None,
            "conv_total": {o: dict(v) for o, v in conv_tot.items()}, "note": None}

def _analyze_run(ev, meta):
    """Core per-run computation; ev groups events by `ev` key; meta may be {}."""
    notes = []
    pdays = _inst_cfg(meta)
    pc = collections.defaultdict(collections.Counter)
    oc = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for r in (ev or {}).get("post") or []:
        try:
            k = int(r.get("t", 0) or 0) // pdays
        except (TypeError, ValueError):
            k = 0
        pc[k][r.get("intent", "?")] += 1
        oc[str(r.get("org", "?"))][k][r.get("intent", "?")] += 1
    periods = _period_rows(pc, pdays)
    drift = None
    if len(periods) < 2:
        notes.append("仅一个周期（或无帖子），drift_to_i2 不可计算")
    elif periods[0]["i2_share"] is None or periods[-1]["i2_share"] is None:
        notes.append("首段或末段无帖子，drift_to_i2 不可计算")
    else:
        drift = periods[-1]["i2_share"] - periods[0]["i2_share"]
    by_org = {}
    for org in sorted(oc):
        rows = _period_rows(oc[org], pdays)
        by_org[org] = {"periods": rows, "final_i2_share": rows[-1]["i2_share"] if rows else None}
    weights = _weights_block((ev or {}).get("inst") or [], notes)
    notes += [weights["note"]] if weights["note"] else []
    return {"period_days": pdays, "adaptive": bool(weights["path"]),
            "platform": {"period_days": pdays, "periods": periods, "drift_to_i2": drift},
            "by_org": by_org, "weights": weights, "notes": notes}

def _interval(vals, label, warnings):
    if len(vals) < 2:
        warnings.append("%s：有效样本 %d 个（不足 2），跳过跨运行区间" % (label, len(vals)))
        return None
    try:
        mean, sd, lo, hi, df = common.seed_t_interval(vals)
        return {"n": len(vals), "mean": mean, "sd": sd, "lo": lo, "hi": hi, "df": df}
    except Exception as exc:  # defensive: interval helper failed
        warnings.append("%s：跨运行区间计算失败（%s）" % (label, exc))
        return None

def analyze(run_dirs):
    """Per-run results plus cross-run intervals; never raises."""
    runs, warnings, dv, cv = {}, [], [], []
    for rd in run_dirs or []:
        tag = _tag(rd)
        try:
            res = _analyze_run(common.load_events(rd), common.load_run_meta(rd))
        except Exception as exc:  # keep exit code 0 even on broken inputs
            res = _analyze_run({}, {})  # all-None stub for this run
            res["notes"] = ["本运行读取或分析失败：%s" % exc]
            warnings.append("[%s] 读取或分析失败：%s" % (tag, exc))
        runs[tag] = res
        if (res.get("platform") or {}).get("drift_to_i2") is not None:
            dv.append(res["platform"]["drift_to_i2"])
        if (res.get("weights") or {}).get("convergence") is not None:
            cv.append(res["weights"]["convergence"])
    cross = ({"drift_to_i2": _interval(dv, "平台 I2 漂移(drift_to_i2)", warnings),
              "convergence": _interval(cv, "机构权重收敛(convergence)", warnings)}
             if run_dirs else None)
    return {"runs": runs, "cross_run": cross, "warnings": warnings}

def _print_line(tag, res):
    per = (res.get("platform") or {}).get("periods") or []
    w = res.get("weights") or {}
    vals = [per[0]["i2_share"] if per else None, per[-1]["i2_share"] if per else None,
            w.get("pairwise_l1_first"), w.get("pairwise_l1_final")]
    vals = ["%.3f" % v if isinstance(v, (int, float)) else "NA" for v in vals]
    print("[%s] 周期 %d | 平台 I2 占比 首段 %s → 末段 %s | 机构权重两两 L1 首 %s → 末 %s | 适应 %s"
          % ((tag, len(per)) + tuple(vals) + ("开" if res.get("adaptive") else "关",)))

def _write_json(path, payload):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=1)

def main(argv=None):
    ap = argparse.ArgumentParser(description="机构层学习分析：平台推品(I2)占比路径与机构意图权重收敛")
    ap.add_argument("run_dirs", nargs="*", help="运行目录，可多个")
    ap.add_argument("--out", default=None, help="合并结果 JSON 输出路径")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写 JSON")
    ap.add_argument("--self-test", action="store_true", help="内置自测（不读磁盘）")
    args = ap.parse_args(argv)
    if args.self_test:
        ok, msgs = _self_test()
        print("\n".join(msgs) + "\nself-test：%s（%d 项）"
              % ("全部通过" if ok else "存在失败", len(msgs)))
        return 0
    result = analyze(args.run_dirs)
    for tag, res in result["runs"].items():
        _print_line(tag, res)
    if not args.no_write:
        pairs = ([(args.out, result)] if args.out else
                 [(os.path.join(rd, "analysis", "institutions.json"),
                   dict(result["runs"][_tag(rd)], tag=_tag(rd)))
                  for rd in args.run_dirs if _tag(rd) in result["runs"]])
        for path, payload in pairs:
            _write_json(path, payload)
    print("".join("警告：%s\n" % m for m in result["warnings"]), end="")
    return 0

def _self_test():
    """Hand-written event tables; no disk access."""
    try:
        post = lambda t, it: {"ev": "post", "t": t, "org": "甲", "intent": it}
        m = lambda d: {"cfg": {"institutions": {"adaptive": True, "period_days": d}}}
        p1 = [post(t, it) for t, it in ((0, "I2"), (0, "I1"), (1, "I2"), (1, "I3"),
                                        (2, "I2"), (2, "I2"), (3, "I2"), (3, "I1"))]
        r1 = _analyze_run({"post": p1, "inst": []}, m(2))
        r2 = _analyze_run({"post": [post(0, "I1"), post(1, "I2"), post(2, "I3")]}, m(10))
        inst = [{"ev": "inst", "t": 5, "org": o, "w": w, "conv": c} for o, w, c in (
            ("甲", {"I1": 1, "I2": 0, "I3": 0}, {"I1": 1}), ("乙", {"I1": 0, "I2": 1, "I3": 0}, {"I2": 2}))]
        r3 = _analyze_run({"post": p1, "inst": inst}, m(2))
        pp, d1 = r1["platform"]["periods"], r1["platform"]["drift_to_i2"]
        checks = (
            ("两段周期 i2_share 0.5/0.75，drift_to_i2 0.25", len(pp) == 2 and d1 is not None
             and abs(pp[0]["i2_share"] - 0.5) < 1e-9 and abs(pp[1]["i2_share"] - 0.75) < 1e-9
             and abs(d1 - 0.25) < 1e-9),
            ("三意图各一帖熵≈log2(3)", abs(r2["platform"]["periods"][0]["entropy_bits"] - math.log2(3)) < 1e-9),
            ("两机构两两 L1=2.0", abs(r3["weights"]["path"][0]["pairwise_l1_mean"] - 2.0) < 1e-12),
            ("无 inst 行 final_w=None 且 note 非空", r1["weights"]["final_w"] is None and bool(r1["weights"]["note"])),
            ("仅一段周期 drift=None", r2["platform"]["drift_to_i2"] is None),
            ("conv_total 全程合计", r3["weights"]["conv_total"]["乙"]["I2"] == 2),
        )
        msgs = [("PASS " if c else "FAIL ") + n for n, c in checks]
        return all(c for _, c in checks), msgs
    except Exception as exc:  # self-test must never crash the CLI
        return False, ["FAIL 自测内部异常：%s" % exc]

if __name__ == "__main__":
    raise SystemExit(main())

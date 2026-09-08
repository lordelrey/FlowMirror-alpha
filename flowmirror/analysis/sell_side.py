"""Sell-side analytics: redemption overview, holding-period distribution, and
exposure to the 7-day short-term redemption-fee rule (counterfactual fee).

Reads only `act` / `co` event rows via flowmirror.analysis.common; no NAV, no
snapshots, no data/ access. Disposition effect (PGR/PLR) is a later patch.
"""
import argparse
import json
import os
import statistics

from flowmirror.analysis import common

EMPTY_NOTE = "本次运行无赎回，处置效应与持有期不可算"
_BUCKETS = ((0, 7, "[0,7)"), (7, 30, "[7,30)"), (30, 90, "[30,90)"),
            (90, 180, "[90,180)"), (180, float("inf"), "[180,+)"))

def _num(v, default=0.0):
    return default if v is None else v

def _percentile(sorted_vals, q):
    return sorted_vals[int(q * (len(sorted_vals) - 1))] if sorted_vals else None

def _st_share(redeems):
    """(share, method): st_units ratio when present, else hold_days<7 proxy."""
    denom = sum(_num(r.get("units")) for r in redeems)
    if any("st_units" in r for r in redeems):
        num = sum(_num(r.get("st_units")) for r in redeems)
        method = "st_units"
    elif any(r.get("hold_days") is not None for r in redeems):
        num = sum(_num(r.get("units")) for r in redeems
                  if r.get("hold_days") is not None and r.get("hold_days") < 7)
        method = "hold_days<7"
    else:
        return None, None
    return (num / denom if denom else None), method

def _holding(redeems):
    raw = [r.get("hold_days") for r in redeems]
    vals = sorted(v for v in raw if v is not None)
    buckets = {key: 0 for _, _, key in _BUCKETS}
    for v in vals:
        for lo, hi, key in _BUCKETS:
            if lo <= v < hi:
                buckets[key] += 1
                break
    share, method = _st_share(redeems)
    return {"n": len(vals), "n_missing_hold_days": len(raw) - len(vals),
            "median": statistics.median(vals) if vals else None,
            "p10": _percentile(vals, 0.1), "p90": _percentile(vals, 0.9),
            "buckets": buckets, "short_term_units_share": share,
            "short_term_share_method": method}

def _st_rule(redeems, co_redeems):
    cf = [c.get("st_fee_cf") for c in co_redeems]
    nz = sum(1 for v in cf if v is not None and v != 0)
    return {"cf_fee_total": sum(_num(v) for v in cf), "cf_nonzero_rows": nz,
            "cf_share_of_redeems": (nz / len(cf)) if cf else None,
            "charged_fee_total": sum(_num(r.get("st_fee")) for r in redeems),
            "n_redeem_rows_with_cf": len(cf)}

def _by_arm(redeems, meta):
    arms = meta.get("arms") if isinstance(meta.get("arms"), dict) else {}
    groups = {}
    for r in redeems:
        groups.setdefault(arms.get(r.get("i"), "all"), []).append(r)
    out = {}
    for arm in sorted(groups):
        vals = sorted(v for v in (r.get("hold_days") for r in groups[arm]) if v is not None)
        share, _ = _st_share(groups[arm])
        out[arm] = {"n_redeem": len(groups[arm]),
                    "median_hold_days": statistics.median(vals) if vals else None,
                    "short_term_units_share": share}
    return out

def _analyze_events(events, meta, run_dir):
    acts = list(events.get("act") or [])
    cos = list(events.get("co") or [])
    redeems = [r for r in acts if r.get("kind") == "redeem"]
    overview = {"n_subscribe": 0, "n_redeem": 0, "n_dca": 0,
                "cny_subscribe": 0.0, "cny_redeem": 0.0, "cny_dca": 0.0,
                "n_agents_redeeming": 0, "note": None}
    agents = set()
    for r in acts:
        kind = r.get("kind")
        if kind in ("subscribe", "redeem", "dca"):
            overview["n_" + kind] += 1
            overview["cny_" + kind] += _num(r.get("amt"))
            if kind == "redeem":
                agents.add(r.get("i"))
    overview["n_agents_redeeming"] = len(agents)
    if overview["n_redeem"] == 0:
        overview["note"] = EMPTY_NOTE
    cfg = meta.get("cfg")
    return {"run_dir": run_dir,
            "agent_policy": cfg.get("agent_policy") if isinstance(cfg, dict) else None,
            "overview": overview, "holding": _holding(redeems),
            "short_term_rule": _st_rule(redeems, [c for c in cos if c.get("act") == "redeem"]),
            "by_arm": _by_arm(redeems, meta)}

def analyze_run(run_dir):
    meta = common.load_run_meta(run_dir) or {}
    return _analyze_events(common.load_events(run_dir), meta, run_dir)

def analyze(run_dirs):
    runs, warnings = {}, []
    for rd in run_dirs:
        tag = os.path.basename(os.path.normpath(rd))
        try:
            runs[tag] = analyze_run(rd)
        except Exception as exc:  # never abort the batch; keep exit code 0
            warnings.append(f"[{tag}] 分析失败：{exc!r}")
            runs[tag] = {"run_dir": rd, "agent_policy": None, "error": repr(exc)}
    cross = None
    if len(runs) >= 2:
        cross = {}
        for key in ("median_hold_days", "short_term_units_share"):
            vals = [v for v in ((r.get("holding") or {}).get(key) for r in runs.values())
                    if v is not None]
            if len(vals) >= 2:
                mean, sd, lo, hi, df = common.seed_t_interval(vals)
                cross[key] = {"mean": mean, "sd": sd, "lo": lo, "hi": hi, "df": df}
            else:
                cross[key] = None
                warnings.append(f"cross_run：{key} 有效运行数不足 2，跳过")
        if all(v is None for v in cross.values()):
            cross = None
    return {"runs": runs, "cross_run": cross, "warnings": warnings}

def _self_test():
    def act(i, kind, amt, units, hold=None, st=None, fee=None):
        return {"ev": "act", "t": 0, "d": "2025-10-01", "i": i, "p": None, "kind": kind,
                "fund": "000001", "amt": amt, "units": units, "nav": 1.0, "fee": fee,
                **({"hold_days": hold} if hold is not None else {}),
                **({"st_units": st} if st is not None else {})}
    def co(cf):
        return {"ev": "co", "t": 0, "d": "2025-10-01", "i": "inv_x", "p": None,
                "fund": "000001", "act": "redeem", "oc": "match", "oc_cf": "match",
                "amt": 1.0, "st_fee_cf": cf}
    def chk(name, exp, got):
        print(f"  {name}: 期望 {exp!r} / 实际 {got!r}")
        assert exp == got, name
    def run(acts, cos=()):
        return _analyze_events({"act": list(acts), "co": list(cos)}, {}, "selftest")
    # 1) no redemption -> note set, median None
    r = run([act("i1", "subscribe", 100.0, 100.0)])
    chk("1 无赎回note非空", True, bool(r["overview"]["note"]))
    chk("1 median为None", None, r["holding"]["median"])
    # 2) bucket edges and median
    hd = [3, 7, 29, 30, 200]
    r = run([act(f"i{k}", "redeem", 10.0, 10.0, hold=d) for k, d in enumerate(hd)])
    chk("2 分桶", {"[0,7)": 1, "[7,30)": 2, "[30,90)": 1, "[90,180)": 0, "[180,+)": 1},
        r["holding"]["buckets"])
    chk("2 中位数29", 29, r["holding"]["median"])
    # 3) share method selection
    with_st = [act(f"i{k}", "redeem", 10.0, 10.0, hold=d, st=2.0) for k, d in enumerate(hd)]
    chk("3 method=st_units", "st_units", run(with_st)["holding"]["short_term_share_method"])
    chk("3 method=hold_days<7", "hold_days<7", r["holding"]["short_term_share_method"])
    # 4) counterfactual fee stats
    sr = run([act("i1", "redeem", 5.0, 5.0, hold=3, fee=1.0)], [co(0.0), co(3.0)])["short_term_rule"]
    chk("4 cf费合计3.0", 3.0, sr["cf_fee_total"])
    chk("4 cf非零1笔", 1, sr["cf_nonzero_rows"])
    chk("4 cf占比0.5", 0.5, sr["cf_share_of_redeems"])
    # 5) zero denominator -> None, not 0
    chk("5 分母0份额为None", None,
        run([act("i1", "redeem", None, None, hold=3)])["holding"]["short_term_units_share"])

def main(argv=None):
    ap = argparse.ArgumentParser(description="卖出侧分析：赎回概况、持有期分布、七日费规则暴露面")
    ap.add_argument("run_dirs", nargs="*", help="运行目录列表")
    ap.add_argument("--out", help="合并 JSON 输出路径")
    ap.add_argument("--no-write", action="store_true", help="不写每运行 JSON")
    ap.add_argument("--self-test", action="store_true", help="运行内置自检")
    args = ap.parse_args(argv)
    if args.self_test:
        try:
            _self_test()
        except AssertionError as exc:
            print(f"自检未通过：{exc}")
        else:
            print("自检通过（5 组断言全部通过）")
        return 0
    if not args.run_dirs:
        print("未提供运行目录；用法：python -m flowmirror.analysis.sell_side <run_dir> ...")
        return 0
    result = analyze(args.run_dirs)
    for tag, r in result["runs"].items():
        if "error" in r:
            print(f"[{tag}] 分析失败：{r['error']}")
            continue
        if not args.no_write:
            path = os.path.join(r["run_dir"], "analysis", "sell_side.json")
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(r, f, ensure_ascii=True, indent=1)
            except OSError as exc:
                result["warnings"].append(f"[{tag}] 写出失败：{exc!r}")
        h, st, ov = r["holding"], r["short_term_rule"], r["overview"]
        med = "NA" if h["median"] is None else f"{h['median']:.0f}"
        shr = "NA" if h["short_term_units_share"] is None else f"{h['short_term_units_share'] * 100:.1f}%"
        print(f"[{tag}] 申购 {ov['n_subscribe']} | 赎回 {ov['n_redeem']} | 持有期中位 {med} 日 | "
              f"7日内份额占比 {shr} | 规则反事实费用 {st['cf_fee_total']:.2f} 元（{st['cf_nonzero_rows']} 笔）")
    if args.out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=True, indent=1)
        except OSError as exc:
            result["warnings"].append(f"合并输出失败：{exc!r}")
    for w in result["warnings"]:
        print(f"警告：{w}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

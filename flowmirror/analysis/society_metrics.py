# -*- coding: utf-8 -*-
"""Society-level metrics from a run's event log (analysis only, no gating).

Inputs: common.load_events(run_dir) + common.load_run_meta(run_dir) only.
census: per_post imp/click/cmt/sub/sub_amt over ALL published posts (never
shown -> 0; redeems carry no post id and never enter per-post shares);
verdict gives non-zero post counts for click/cmt/sub (<10 -> too few).
concentration(values, support): Gini (sorted linear formula, zeros in
the support count), HHI, top1/top5 share; None -- not 0 -- when
total<=0 or n_support<2.  Levels: by_post (all posts), by_fund /
by_family (subscribed funds; family from meta["funds"]), by_org (orgs
with >=1 post), by_post_by_arm (arm from meta["arms"][row i]; support
= all posts).  lsv: cell = (fund, day), direction = investor's FIRST
act that day, N = B+S, qualifies for the mean iff N >= min_traders; p_bar(t) =
sumB/sumN over ALL cells traded that day (LSV full-market base); AF(N,p) = E|X/N - p|,
X ~ Binomial(N,p), exact via math.comb; mean_lsv = mean |B/N-p_bar|-AF.
stance_entropy: stance bits, label set fixed run-wide, normalized =
bits/log2(k), per-day normalized None when day n < 5.  comment_burst:
sorted comment-count vector + gini/top shares, no tail fitting.
cross_run (>=2 runs with identical post-id sets AND identical per-post
identity org/intent/ig/fund): mean over posts of the across-run population
variance of shares; mean pairwise Kendall tau-b. Post ids are slot numbers
reused in every run, so the same slot under a different seed holds a
different creative; without matching identities cross-run shares are not
comparable.
"""

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations

from flowmirror.analysis import common

_ROW_NAMES = ("imp", "click", "cmt", "act", "co", "post", "dec", "st")
_POST_METRICS = ("imp", "click", "cmt", "sub", "sub_amt")


def concentration(values, support):
    """Gini/HHI/top-k shares of `values` over `support`; zeros in the
    support count toward n.  total<=0 or n_support<2 -> stats are None."""
    support = list(support)
    n = len(support)
    xs = [float(values.get(k, 0.0)) for k in support]
    total = sum(xs)
    out = {"n_support": n, "n_nonzero": sum(1 for x in xs if x > 0),
           "total": total, "gini": None, "hhi": None,
           "top1_share": None, "top5_share": None}
    if n >= 2 and total > 0:
        asc = sorted(xs)
        out["gini"] = (2.0 * sum(i * x for i, x in enumerate(asc, 1))
                       / (n * total) - (n + 1.0) / n)
        out["hhi"] = sum((x / total) ** 2 for x in xs)
        out["top1_share"] = asc[-1] / total
        out["top5_share"] = sum(asc[-5:]) / total
    return out


def _af(n_traders, p_bar):
    """AF(N,p) = E|X/N - p| with X ~ Binomial(N,p); exact sum over X = 0..N.
    (N=2, p=0.5 -> 0.25; the task card's "AF == 0.5" mis-sums its terms.)"""
    af = 0.0
    for x in range(n_traders + 1):
        pr = math.comb(n_traders, x) * p_bar ** x * (1.0 - p_bar) ** (n_traders - x)
        af += pr * abs(x / n_traders - p_bar)
    return af


def _kendall_tau_b(xs, ys):
    """Kendall tau-b, exact O(n^2) double loop; ties counted on both axes."""
    n = len(xs)
    if n < 2: return None  # need at least one pair
    conc = disc = ties_x = ties_y = 0
    for a in range(n - 1):
        for b in range(a + 1, n):
            dx = (xs[a] > xs[b]) - (xs[a] < xs[b])
            dy = (ys[a] > ys[b]) - (ys[a] < ys[b])
            prod = dx * dy
            conc += prod > 0
            disc += prod < 0
            ties_x += dx == 0
            ties_y += dy == 0
    n0 = n * (n - 1) // 2
    denom = math.sqrt(max(0.0, (n0 - ties_x) * (n0 - ties_y)))
    return None if denom == 0 else (conc - disc) / denom


def _stance_summary(stances, k, min_n=0):
    """counts / entropy_bits / normalized (= bits / log2(k); None when k < 2
    or n < min_n) / n.  k = size of the run-wide label set."""
    counts = Counter(stances)
    h = 0.0
    for c in counts.values():
        p = c / len(stances)
        h -= p * math.log2(p)
    norm = h / math.log2(k) if k >= 2 and len(stances) >= min_n else None
    return {"counts": dict(sorted(counts.items())), "entropy_bits": h,
            "normalized": norm, "n": len(stances)}


def _lsv_block(act_rows, min_traders):
    """LSV herding per (fund, day) cell; see the module docstring."""
    first = {}
    for r in act_rows:
        if r.get("kind") in ("subscribe", "redeem"):
            first.setdefault((r.get("t"), r.get("i"), r.get("fund")),
                             1 if r["kind"] == "subscribe" else -1)
    cells = defaultdict(lambda: [0, 0])  # (fund, day) -> [buyers, sellers]
    for (t, _i, fund), d in first.items():
        cells[(fund, t)][0 if d > 0 else 1] += 1
    by_day = defaultdict(list)
    for (fund, t), (b, s) in cells.items():
        if b + s >= min_traders:
            by_day[t].append((b, s))
    # p_bar is LSV's whole-market benchmark: it must aggregate over every cell
    # traded that day, however small. Computing it on the qualified subset alone
    # makes the benchmark track that subset's own mean, so on a fully one-sided
    # day |p - p_bar| is identically 0 and herding goes undetected.
    all_by_day = defaultdict(list)
    for (fund, t), (b, s) in cells.items():
        all_by_day[t].append((b, s))
    lsv_vals = []
    for t in sorted(by_day):
        cs = by_day[t]
        day = all_by_day[t]
        tot = sum(b + s for b, s in day)
        p_bar = sum(b for b, s in day) / tot if tot else 0.0
        for b, s in cs:
            lsv_vals.append(abs(b / (b + s) - p_bar) - _af(b + s, p_bar))
    cells_at = {str(m): sum(1 for b, s in cells.values() if b + s >= m)
                for m in (2, 3, 5)}
    sellers = sum(s for _, s in cells.values())
    if sellers == 0:
        mean_lsv, note = None, ("本次运行赎回数为 0：所有格子只有买方，p ≡ 1，"
                                "LSV 在本次运行上没有信息量（实测常态，非异常）。")
    elif not lsv_vals:
        mean_lsv, note = None, "无合格格子（min_traders=%d），mean_lsv 记 None。" % min_traders
    else:
        mean_lsv = sum(lsv_vals) / len(lsv_vals)
        note = "合格格子 %d 个；p_bar 按日以当日全部格子的 ΣB/Σ(B+S) 计（全市场基准），LSV 仅在合格格子上取均值。" % len(lsv_vals)
    return {"mean_lsv": mean_lsv, "n_cells": len(lsv_vals),
            "min_traders": min_traders, "cells_at": cells_at, "note": note}


def _analyze_one(run_dir, tag, min_traders, warnings):
    ev = common.load_events(run_dir)
    meta = common.load_run_meta(run_dir)
    post_rows = ev.get("post", [])
    post_order = list(dict.fromkeys(r["p"] for r in post_rows if r.get("p") is not None))
    arms_map = meta.get("arms") or {}
    if not arms_map:
        warnings.append("%s: run_meta 缺 arms，按单臂 all 处理" % tag)
    arm_of = lambda i: arms_map.get(i, "all")
    blank = lambda: {"imp": 0, "click": 0, "cmt": 0, "sub": 0, "sub_amt": 0.0}
    rows = {name: 0 for name in _ROW_NAMES}
    rows.update({name: len(lst) for name, lst in ev.items()})
    act_rows = ev.get("act", [])
    n_sub = sum(1 for r in act_rows if r.get("kind") == "subscribe")
    n_red = sum(1 for r in act_rows if r.get("kind") == "redeem")
    per_post = {pid: blank() for pid in post_order}
    per_post_arm = defaultdict(lambda: {pid: blank() for pid in post_order})
    fund_n, fund_amt = Counter(), Counter()
    org_posts = Counter(r.get("org") for r in post_rows if r.get("org"))
    for name in ("imp", "click", "cmt"):
        for r in ev.get(name, []):
            pid = r.get("p")
            if pid in per_post:
                per_post[pid][name] += 1
                per_post_arm[arm_of(r.get("i"))][pid][name] += 1
    for r in act_rows:
        if r.get("kind") != "subscribe":
            continue  # redeems carry no post id; never enter per-post shares
        amt = float(r.get("amt") or 0.0)
        if r.get("fund") is not None:
            fund_n[r["fund"]] += 1
            fund_amt[r["fund"]] += amt
        pid = r.get("p")
        if pid in per_post:
            pp, pa = per_post[pid], per_post_arm[arm_of(r.get("i"))][pid]
            for acc in (pp, pa):
                acc["sub"] += 1
                acc["sub_amt"] += amt
    summary, verdict = {}, []
    for m in ("imp", "click", "cmt", "sub"):
        vals = [per_post[pid][m] for pid in post_order]
        nz = sum(1 for v in vals if v > 0)
        summary[m] = {"total": sum(vals), "n_posts_nonzero": nz,
                      "max": max(vals) if vals else 0,
                      "median": statistics.median(vals) if vals else 0.0,
                      "mean": sum(vals) / len(vals) if vals else 0.0}
    for m in ("click", "cmt", "sub"):
        nz = summary[m]["n_posts_nonzero"]
        verdict.append("%s：非零帖子数 = %d%s"
                       % (m, nz, "，样本不足以做份额集中度推断" if nz < 10 else ""))
    census = {"rows": rows, "act_subscribe": n_sub, "act_redeem": n_red,
              "per_post": per_post, "summary": summary, "verdict": verdict}
    sup_p = "all published posts (n=%d)" % len(post_order)

    def conc_over(mapping):
        return {m: dict(concentration({pid: mapping[pid][m] for pid in post_order},
                                      post_order), support=sup_p)
                for m in _POST_METRICS}

    conc_out = {"by_post": conc_over(per_post),
                "by_post_by_arm": {a: conc_over(per_post_arm[a])
                                   for a in sorted(per_post_arm)},
                "by_fund": None, "by_family": None, "by_org": None}
    funds_meta = meta.get("funds") or {}
    funds_used = sorted(fund_n)
    sup_f = "funds subscribed at least once"
    conc_out["by_fund"] = {
        "sub_n": dict(concentration(fund_n, funds_used), support=sup_f),
        "sub_amt": dict(concentration(fund_amt, funds_used), support=sup_f)}
    fam_n, fam_amt, org_n, org_amt = Counter(), Counter(), Counter(), Counter()
    for fund in funds_used:
        fd = funds_meta.get(fund) or {}
        fam = fd.get("family") or fund
        fam_n[fam] += fund_n[fund]
        fam_amt[fam] += fund_amt[fund]
        if fd.get("org") in org_posts:
            org_n[fd["org"]] += fund_n[fund]
            org_amt[fd["org"]] += fund_amt[fund]
    sup_o = "orgs with >=1 post (subscribe side restricted to them)"
    conc_out["by_org"] = {
        "post_n": dict(concentration(org_posts, sorted(org_posts)), support=sup_o),
        "sub_n": None, "sub_amt": None}
    if funds_meta:
        sup_fam = "families of subscribed funds (meta funds)"
        conc_out["by_family"] = {
            "sub_n": dict(concentration(fam_n, sorted(fam_n)), support=sup_fam),
            "sub_amt": dict(concentration(fam_amt, sorted(fam_amt)),
                            support=sup_fam)}
        conc_out["by_org"]["sub_n"] = dict(
            concentration(org_n, sorted(org_n)), support=sup_o)
        conc_out["by_org"]["sub_amt"] = dict(
            concentration(org_amt, sorted(org_amt)), support=sup_o)
    else:
        warnings.append("%s: run_meta 缺 funds：by_family 与 by_org 申购侧跳过" % tag)
    cmt_rows = ev.get("cmt", [])
    stances = [r.get("stance") for r in cmt_rows if r.get("stance") is not None]
    labels = sorted(set(stances))
    k = len(labels)
    per_day, per_arm = defaultdict(list), defaultdict(list)
    for r in cmt_rows:
        st = r.get("stance")
        if st is not None:
            per_day[r.get("t")].append(st)
            per_arm[arm_of(r.get("i"))].append(st)
    stance_out = {"labels": labels, "overall": _stance_summary(stances, k),
                  "by_day": {str(t): _stance_summary(per_day[t], k, min_n=5)
                             for t in sorted(per_day)},
                  "by_arm": {a: _stance_summary(ss, k)
                             for a, ss in sorted(per_arm.items())}}
    cvals = [per_post[pid]["cmt"] for pid in post_order]
    cb = concentration(dict(zip(post_order, cvals)), post_order)
    burst = {"counts_sorted": sorted(cvals, reverse=True), "n_posts": len(post_order),
             "n_with_comment": sum(1 for v in cvals if v > 0),
             "max": max(cvals) if cvals else 0,
             "mean": sum(cvals) / len(cvals) if cvals else 0.0,
             "gini": cb["gini"], "top1_share": cb["top1_share"], "top5_share": cb["top5_share"],
             "note": "帖子数与评论数都不足以估计尾指数，这里只报分布本身"}
    # Slot ids repeat across runs; record what each slot really published (first
    # post row wins, missing fields -> None) for cross_run identity checks.
    post_identity = {}
    for row in post_rows:
        pid = row.get("p", row.get("pid"))
        if pid is not None and pid not in post_identity:
            post_identity[pid] = [row.get("org"), row.get("intent"),
                                  row.get("ig"), row.get("fund")]
    return {"run_dir": run_dir, "n_posts": len(post_order), "census": census,
            "concentration": conc_out, "lsv": _lsv_block(act_rows, min_traders),
            "stance_entropy": stance_out, "comment_burst": burst,
            "post_identity": post_identity}


def _fe(v):
    # share_var (the main path-dependence measure) is ~1e-5 for ~100 posts;
    # "%.3f" would always render 0.000 and hide the magnitude entirely.
    return "NA" if v is None else "%.3e" % v


def _cross_run(runs, warnings):
    """Cross-run share dispersion; only for runs with identical post sets."""
    tags = sorted(runs)
    if len(tags) < 2:
        return None
    sets = [set(runs[tg]["census"]["per_post"]) for tg in tags]
    if any(s != sets[0] for s in sets[1:]):
        warnings.append("cross_run = None：各运行发布的帖子 id 不是同一批，无法比较份额")
        return None
    # Equal id sets prove little: post ids are slot numbers reused in every
    # run, so under a different seed the same slot holds a different creative.
    # Only runs with matching per-post identity are truly comparable.
    idents = [runs[tg].get("post_identity") for tg in tags]
    if any(d is None for d in idents):
        warnings.append("cross_run：至少一个运行缺少 post_identity（旧结果），无法校验逐帖身份，退回仅按帖子 id 比较")
    else:
        for pid in sorted(sets[0]):
            vals = [d.get(pid) for d in idents]
            if any(v != vals[0] for v in vals[1:]):
                j = next(k for k in range(1, len(vals)) if vals[k] != vals[0])
                warnings.append(
                    "cross_run = None：帖子 id 只是各运行复用的槽位号，同一槽位放的是不同创意"
                    "（pid=%s：%s 为 %r，%s 为 %r），跨运行份额不可比" % (
                        pid, tags[0], vals[0], tags[j], vals[j]))
                return None
    pids = list(runs[tags[0]]["census"]["per_post"])
    metrics = {}
    for m in ("imp", "click", "cmt", "sub"):
        shares = {}
        for tg in tags:
            pp = runs[tg]["census"]["per_post"]
            total = sum(pp[pid][m] for pid in pp)
            shares[tg] = ({pid: pp[pid][m] / total for pid in pp}
                          if total > 0 else None)
        if all(v is not None for v in shares.values()):
            share_var = sum(statistics.pvariance([shares[tg][pid] for tg in tags])
                            for pid in pids) / len(pids)
        else:
            share_var = None
        taus, pairs = [], []
        for tg_a, tg_b in combinations(tags, 2):
            tau = None
            if shares[tg_a] is not None and shares[tg_b] is not None:
                tau = _kendall_tau_b([shares[tg_a][p] for p in pids],
                                     [shares[tg_b][p] for p in pids])
                if tau is not None:
                    taus.append(tau)
            pairs.append([tg_a, tg_b, tau])
        metrics[m] = {"share_var": share_var,
                      "kendall_tau_mean": (sum(taus) / len(taus)) if taus else None,
                      "n_pairs": len(taus), "pair_taus": pairs}
    return {"tags": tags, "n_runs": len(tags), "metrics": metrics,
            "note": "share_var 为逐帖跨运行份额总体方差的均值；tau 为 tau-b"}


def analyze(run_dirs, min_traders=5):
    """Analyze run directories; tag = basename of the normalized path.
    -> {"runs": {tag: per-run dict}, "cross_run": dict|None, "warnings": [...]}"""
    warnings, runs = [], {}
    for run_dir in run_dirs:
        tag = os.path.basename(os.path.normpath(run_dir))
        runs[tag] = _analyze_one(run_dir, tag, min_traders, warnings)
    cross = _cross_run(runs, warnings) if len(runs) >= 2 else None
    return {"runs": runs, "cross_run": cross, "warnings": warnings}


def _f3(v):
    return "NA" if v is None else "%.3f" % v


def _print_summary(tag, res):
    """One-screen Chinese summary of a single run."""
    cen, rows = res["census"], res["census"]["rows"]
    print("[%s] 帖子 %d | imp %d | click %d | cmt %d | act %d（申购 %d / 赎回 %d）" % (
          tag, res["n_posts"], rows.get("imp", 0), rows.get("click", 0), rows.get("cmt", 0),
          rows.get("act", 0), cen["act_subscribe"], cen["act_redeem"]))
    for line in cen["verdict"]:
        print("  - " + line)
    bp = res["concentration"]["by_post"]
    print("  逐帖集中度 click：gini=%s hhi=%s | sub：gini=%s top1=%s"
          % (_f3(bp["click"]["gini"]), _f3(bp["click"]["hhi"]),
             _f3(bp["sub"]["gini"]), _f3(bp["sub"]["top1_share"])))
    lsv, at = res["lsv"], res["lsv"]["cells_at"]
    print("  LSV：mean=%s（合格格子 %d；阈值2/3/5 各 %s/%s/%s 个）"
          % (_f3(lsv["mean_lsv"]), lsv["n_cells"], at["2"], at["3"], at["5"]))
    se = res["stance_entropy"]["overall"]
    print("  立场熵：%s bit（归一 %s，n=%d）"
          % (_f3(se["entropy_bits"]), _f3(se["normalized"]), se["n"]))
    cb = res["comment_burst"]
    print("  评论爆发：max=%s mean=%s gini=%s（%d/%d 有评论）" % (
          cb["max"], _f3(cb["mean"]), _f3(cb["gini"]), cb["n_with_comment"], cb["n_posts"]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m flowmirror.analysis.society_metrics")
    ap.add_argument("run_dirs", nargs="*")
    ap.add_argument("--min-traders", type=int, default=5)
    ap.add_argument("--out", help="also write the merged result JSON here")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if not args.run_dirs:
        ap.error("需要至少一个 run_dir（或使用 --self-test）")
    result = analyze(args.run_dirs, min_traders=args.min_traders)
    for run_dir in args.run_dirs:
        tag = os.path.basename(os.path.normpath(run_dir))
        if not args.no_write:
            path = os.path.join(run_dir, "analysis", "society.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(result["runs"][tag], fh, ensure_ascii=True, indent=1)
        _print_summary(tag, result["runs"][tag])
    if result["cross_run"] is not None:
        for m, mm in result["cross_run"]["metrics"].items():
            print("[cross_run] %s：share_var=%s tau均值=%s（有效配对 %d）" % (
                  m, _fe(mm["share_var"]), _f3(mm["kendall_tau_mean"]), mm["n_pairs"]))
    for w in result["warnings"]:
        print("警告：%s" % w)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=True, indent=1)
        print("合并结果已写入 %s" % args.out)
    return 0


def _expect(label, expected, actual):
    """assert with expected/actual dump on failure; float-aware to 1e-9."""
    ok = (abs(expected - actual) <= 1e-9 if isinstance(expected, float)
          and isinstance(actual, float) else expected == actual)
    if not ok:
        print("SELF-TEST FAIL %s: expected=%r actual=%r" % (label, expected, actual))
    assert ok, label


def _self_test():
    """In-memory checks only; no disk access."""
    even = concentration({"a": 1.0, "b": 1.0, "c": 1.0}, "abc")
    _expect("even gini == 0", 0.0, even["gini"])
    _expect("even hhi == 1/3", 1.0 / 3.0, even["hhi"])
    one = concentration({"a": 1.0, "b": 0.0, "c": 0.0}, "abc")
    _expect("one gini == 2/3", 2.0 / 3.0, one["gini"])
    _expect("one hhi == 1.0", 1.0, one["hhi"])
    _expect("one top1_share == 1.0", 1.0, one["top1_share"])
    zero = concentration({"a": 0.0, "b": 0.0, "c": 0.0}, "abc")
    for key in ("gini", "hhi", "top1_share", "top5_share"):
        _expect("zero %s is None" % key, None, zero[key])
    _expect("af(2, 0.5) == 0.25", 0.25, _af(2, 0.5))
    st = _stance_summary(["a", "b", "c"], 3)
    _expect("uniform entropy == log2(3)", math.log2(3.0), st["entropy_bits"])
    _expect("uniform normalized == 1", 1.0, st["normalized"])
    print("自检通过：10 项断言全部通过")


if __name__ == "__main__":
    sys.exit(main())

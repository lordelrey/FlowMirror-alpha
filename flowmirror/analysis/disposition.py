"""Odean (1998) disposition effect: PGR / PLR / DE per run.

Positive control: runs with agent_policy == "null" have a built-in
sell-gain probability higher than the sell-loss probability, so DE
(PGR - PLR) should be > 0 for rule-based (null) runs.

Standard library only.
"""

import argparse
import bisect
import json
import os
from collections import defaultdict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _blank():
    return {"RG": 0, "RL": 0, "PG": 0, "PL": 0}


def _bump(tot, per, arm, key):
    tot[key] += 1
    per.setdefault(arm, _blank())[key] += 1


def _pick_nav(series, day):
    """Nav at `day`; else nearest strictly-earlier date key; else None."""
    if not series:
        return None
    keys = sorted(series)
    i = bisect.bisect_right(keys, day) - 1
    return series[keys[i]] if i >= 0 else None


def _load_nav_cache(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _apply_buy(h, code, amt, fee, units):
    """Weighted-average cost: cost = (held*cost + amt - fee) / (held + units)."""
    paid = amt - fee
    cur = h.get(code)
    if cur and cur[0] > 0 and units > 0:
        cur[1] = (cur[0] * cur[1] + paid) / (cur[0] + units)
        cur[0] += units
    elif units > 0:
        h[code] = [units, paid / units]
    else:
        h.setdefault(code, [0.0, 0.0])


def _rates(c):
    pgr = c["RG"] / (c["RG"] + c["PG"]) if (c["RG"] + c["PG"]) > 0 else None
    plr = c["RL"] / (c["RL"] + c["PL"]) if (c["RL"] + c["PL"]) > 0 else None
    de = (pgr - plr) if (pgr is not None and plr is not None) else None
    return {"RG": c["RG"], "RL": c["RL"], "PG": c["PG"], "PL": c["PL"],
            "PGR": pgr, "PLR": plr, "DE": de}


def _core(acts, openings, arms, nav_cache, agent_policy=None, nav_source=None,
          nav_unreadable=None, run_dir="."):
    # holdings: agent -> {code: [units, cost]}
    holdings = {}
    for aid, o in (openings or {}).items():
        hold = (o or {}).get("hold") or {}
        cost = (o or {}).get("cost") or {}
        holdings[aid] = {c: [float(hold.get(c, 0.0)), float(cost.get(c, 0.0))]
                         for c in hold}
    by_day = defaultdict(list)
    for r in acts:
        if r.get("ev", "act") == "act":
            by_day[r.get("d", "")].append(r)
    tot, per = _blank(), {}
    sell_days, nav_missing, sellers = 0, 0, set()
    for day in sorted(by_day):
        per_agent = defaultdict(list)
        for r in by_day[day]:
            per_agent[r.get("i")].append(r)
        sold, day_sell = {}, False
        for aid, rows in per_agent.items():
            h = holdings.setdefault(aid, {})
            s = set()
            for r in rows:
                code, kind = r.get("fund"), r.get("kind")
                if not code:
                    continue
                if kind in ("subscribe", "dca"):
                    _apply_buy(h, code, float(r.get("amt") or 0.0),
                               float(r.get("fee") or 0.0),
                               float(r.get("units") or 0.0))
                elif kind == "redeem":
                    day_sell = True
                    sellers.add(aid)
                    units = float(r.get("units") or 0.0)
                    cur = h.get(code)
                    cost = cur[1] if cur else 0.0
                    pnl = r.get("pnl")
                    if pnl is None:
                        pnl = units * (float(r.get("nav") or 0.0) - cost)
                    key = "RG" if pnl > 0 else ("RL" if pnl < 0 else None)
                    if key:
                        _bump(tot, per, (arms or {}).get(aid, "all"), key)
                    s.add(code)
                    if cur:
                        cur[0] -= units
                        if cur[0] <= 1e-9:
                            del h[code]
            if s:
                sold[aid] = s
        if day_sell:
            sell_days += 1
        # unrealized gains/losses on held-but-not-sold funds, only on sell days
        for aid, s in sold.items():
            arm = (arms or {}).get(aid, "all")
            for code, cell in holdings.get(aid, {}).items():
                if code in s:
                    continue
                series = (nav_cache or {}).get(code)
                nav = _pick_nav(series, day) if series else None
                if nav is None:
                    nav_missing += 1
                    continue
                unrealized = cell[0] * (nav - cell[1])
                key = "PG" if unrealized > 0 else ("PL" if unrealized < 0 else None)
                if key:
                    _bump(tot, per, arm, key)
    note = None
    if tot["RG"] + tot["RL"] == 0:
        note = "本次运行无赎回，处置效应不可算"
    elif nav_unreadable:
        note = "净值缓存不可读：%s" % nav_unreadable
    return {
        "run_dir": str(run_dir),
        "agent_policy": agent_policy,
        "nav_source": nav_source,
        "counts": {"RG": tot["RG"], "RL": tot["RL"], "PG": tot["PG"], "PL": tot["PL"],
                   "sell_days": sell_days, "agents_with_sells": len(sellers),
                   "nav_missing": nav_missing},
        "overall": _rates(tot),
        "by_arm": {arm: _rates(per[arm]) for arm in sorted(per)},
        "note": note,
    }


def analyze_run(run_dir):
    from flowmirror.analysis import common
    ev = common.load_events(run_dir)
    meta = common.load_run_meta(run_dir)
    cfg = meta.get("cfg") or {}
    rel = cfg.get("nav_cache")
    if rel and os.path.isabs(rel):
        nav_path = rel
    elif rel:
        nav_path = os.path.join(_REPO_ROOT, rel)
    else:
        nav_path = None
    cache = _load_nav_cache(nav_path)
    return _core(list(ev.get("act") or []), meta.get("openings") or {},
                 meta.get("arms") or {}, cache,
                 agent_policy=cfg.get("agent_policy"),
                 nav_source=nav_path if cache is not None else None,
                 nav_unreadable=None if cache is not None else nav_path,
                 run_dir=run_dir)


def _fmt(x):
    return "None" if x is None else "%.4f" % x


def _report(res):
    c, o = res["counts"], res["overall"]
    tag = os.path.basename(os.path.normpath(res["run_dir"]))
    line = ("[%s] 卖出日 %d | RG/RL/PG/PL = %d/%d/%d/%d | PGR %s PLR %s DE %s"
            % (tag, c["sell_days"], c["RG"], c["RL"], c["PG"], c["PL"],
               _fmt(o["PGR"]), _fmt(o["PLR"]), _fmt(o["DE"])))
    if res["note"]:
        line += " | %s" % res["note"]
    print(line)


def analyze(run_dirs):
    from flowmirror.analysis import common
    results = []
    for rd in run_dirs:
        try:
            res = analyze_run(rd)
        except Exception as exc:  # never raise, keep exit code 0
            res = {"run_dir": str(rd), "agent_policy": None, "nav_source": None,
                   "counts": {"RG": 0, "RL": 0, "PG": 0, "PL": 0, "sell_days": 0,
                              "agents_with_sells": 0, "nav_missing": 0},
                   "overall": {"PGR": None, "PLR": None, "DE": None},
                   "by_arm": {}, "note": "分析失败：%s" % exc}
        _report(res)
        results.append(res)
    if len(results) >= 2:
        des = [r["overall"]["DE"] for r in results if r["overall"]["DE"] is not None]
        if len(des) >= 2:
            mean, sd, lo, hi, df = common.seed_t_interval(des)
            cross = {"mean": mean, "sd": sd, "lo": lo, "hi": hi, "df": df}
        else:
            cross = None
            print("[warn] 有效 DE 不足 2 个，无法计算种子级 t 区间")
        for r in results:
            r["cross_run"] = cross
    return results


def _self_test():
    # nearest-earlier nav lookup
    s = {"2025-10-01": 1.0, "2025-10-03": 1.2}
    assert _pick_nav(s, "2025-10-02") == 1.0
    assert _pick_nav(s, "2025-09-30") is None
    # weighted average cost: 100 @1.0 + 100 @2.0 (fee 0) -> 1.5
    h = {"X": [100.0, 1.0]}
    _apply_buy(h, "X", 200.0, 0.0, 100.0)
    assert abs(h["X"][1] - 1.5) < 1e-12
    # zero denominators -> None, not 0 (RG+PG == 0 and RL+PL == 0)
    r = _rates({"RG": 0, "RL": 0, "PG": 0, "PL": 0})
    assert r["PGR"] is None and r["PLR"] is None and r["DE"] is None
    # non-zero denominators with zero numerators -> 0.0, a real rate, not None
    r = _rates({"RG": 0, "RL": 0, "PG": 5, "PL": 3})
    assert r["PGR"] == 0.0 and r["PLR"] == 0.0 and r["DE"] == 0.0
    r = _rates({"RG": 1, "RL": 1, "PG": 0, "PL": 0})
    assert r["PGR"] == 1.0 and r["PLR"] == 1.0 and r["DE"] == 0.0
    # one realized gain sold plus one unrealized loss held -> PGR 1, PLR 0, DE 1
    openings = {"a1": {"hold": {"FG": 100.0, "FL": 100.0},
                       "cost": {"FG": 1.0, "FL": 2.0}}}
    nav = {"FL": {"2025-10-01": 1.5}}
    acts = [{"ev": "act", "t": 0, "d": "2025-10-01", "i": "a1", "kind": "redeem",
             "fund": "FG", "units": 100.0, "nav": 1.2, "pnl": 20.0}]
    res = _core(acts, openings, {"a1": "ctrl"}, nav)
    o = res["overall"]
    assert (o["RG"], o["RL"], o["PG"], o["PL"]) == (1, 0, 0, 1)
    assert abs(o["PGR"] - 1.0) < 1e-12 and o["PLR"] == 0.0
    assert abs(o["DE"] - 1.0) < 1e-12 and res["by_arm"]["ctrl"]["DE"] == 1.0
    # no redemption -> all None + non-empty note
    res2 = _core([], openings, {}, nav)
    v = res2["overall"]
    assert v["PGR"] is None and v["PLR"] is None and v["DE"] is None
    assert res2["note"]
    print("[self-test] 全部通过")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Odean (1998) disposition effect")
    ap.add_argument("run_dirs", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if not args.run_dirs:
        print("未提供 run_dir，无操作")
        return 0
    results = analyze(args.run_dirs)
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=True, indent=1)
        print("[write] %s" % args.out)
    elif not args.no_write:
        for rd, res in zip(args.run_dirs, results):
            out = os.path.join(rd, "analysis", "disposition.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=True, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

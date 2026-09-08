"""FlowMirror influence analysis: follower concentration & follow-graph homophily.
Copying / influence-vs-homophily are PATCH-card placeholders; handle = "@u"+sha256(id)[:5]."""
import argparse, hashlib, json, os, random, statistics
from collections import Counter, defaultdict

from flowmirror.analysis import common

_STANCES = ("bullish", "bearish", "watching")
_NULL_REPS, _NULL_SEED, _OUT_NAME = 200, 2027, "influence.json"

def _handle(aid):
    return "@u" + hashlib.sha256(aid.encode("utf-8")).hexdigest()[:5]

def _handle_table(agent_ids):
    # handle -> agent_id; a handle owned by 2+ agents is ambiguous: drop & count
    owners = defaultdict(list)
    for aid in agent_ids:
        owners[_handle(aid)].append(aid)
    table = {h: v[0] for h, v in owners.items() if len(v) == 1}
    return table, sum(1 for v in owners.values() if len(v) > 1)

def _stances(events):
    # majority stance per agent over cmt rows; tie -> watching; no cmt -> absent
    cnt = defaultdict(Counter)
    for e in events:
        if e.get("ev") == "cmt" and e.get("i") and e.get("stance") in _STANCES:
            cnt[e["i"]][e["stance"]] += 1
    out = {}
    for aid, c in cnt.items():
        winners = [s for s in _STANCES if c.get(s) == max(c.values())]
        out[aid] = winners[0] if len(winners) == 1 else "watching"
    return out

def _gini(xs):
    # Gini over the full vector, zeros included; all-zero or trivial -> None
    n, tot = len(xs), sum(xs)
    if n < 2 or tot <= 0:
        return None
    cum = sum((k + 1) * v for k, v in enumerate(sorted(xs)))
    return (2.0 * cum) / (n * tot) - (n + 1.0) / n

def _follow_edges(events, table):
    # (follower, followee-or-None) per follow_user event; None = unresolved handle
    for e in events:
        if e.get("ev") == "st" and e.get("what") == "follow_user":
            yield e.get("i"), table.get(e.get("handle"))

def _followers(edges, agent_ids, arms, stances, ambiguous):
    if not edges:  # zero result: big-V layer off, or on but nothing formed
        zero = {k: None for k in ("n_agents", "n_with_followers", "max", "mean", "gini",
                                  "top5", "top1_share", "top5_share", "counts_sorted")}
        zero["note"] = "本次运行无人关注任何人（零结果）：可能为大 V 层关闭，或开启但关注结构未形成。"
        return zero
    cnt, unresolved = Counter(), 0
    for _u, v in edges:
        if v is None:
            unresolved += 1
        else:
            cnt[v] += 1
    counts = [cnt.get(a, 0) for a in agent_ids]
    tot = sum(counts)
    order = sorted(agent_ids, key=lambda a: (-cnt.get(a, 0), a))
    top5 = [{"handle": _handle(a), "followers": cnt.get(a, 0),
             "stance": stances.get(a), "arm": arms.get(a, "all")} for a in order[:5]]
    block = {"n_agents": len(agent_ids), "n_with_followers": sum(c > 0 for c in counts),
             "max": max(counts) if counts else 0,
             "mean": (tot / len(counts)) if counts else 0.0,
             "gini": _gini(counts), "top5": top5,
             "top1_share": (max(counts) / tot) if tot else None,
             "top5_share": (sum(x["followers"] for x in top5) / tot) if tot else None,
             "counts_sorted": sorted(counts, reverse=True),
             "n_unresolved_handles": unresolved}
    if tot == 0:
        block["note"] = "有关注事件但没有任何句柄能反查到 agent。"
    return block

def _homophily(edges, stances):
    both = [(u, v) for u, v in edges if u in stances and v in stances]
    n = len(both)
    block = {"p_same_obs": None, "p_same_null_mean": None, "p_same_null_p95": None,
             "excess": None, "n_edges_with_stance": n}
    if n == 0:
        block["note"] = "没有双方均有主立场的关注边，无法评估回音室。"
        return block
    pool, p_obs = sorted(stances), sum(1 for u, v in both
                                       if stances[u] == stances[v]) / float(n)
    # null: keep out-degrees, rewire targets uniformly among stance-holders
    rng = random.Random(_NULL_SEED)
    reps = sorted(sum(stances[u] == stances[rng.choice(pool)] for u, _ in both)
                  / float(n) for _ in range(_NULL_REPS))
    null_mean = statistics.mean(reps)
    block["p_same_obs"], block["p_same_null_mean"] = p_obs, null_mean
    block["p_same_null_p95"] = reps[min(_NULL_REPS - 1, -(-95 * _NULL_REPS // 100) - 1)]
    if n >= 10:
        block["excess"] = p_obs - null_mean
    else:
        block["note"] = "双方均有主立场的关注边仅 %d 条（<10），excess 不报告。" % n
    return block

def _run_metrics(events, meta, run_dir=None):
    # common.load_events returns {ev: [rows]} grouped by event kind; the self-test hands in a
    # flat list. Flatten here so every consumer below iterates rows, never dict keys.
    if isinstance(events, dict):
        events = [row for rows in events.values() for row in (rows or [])]
    arms = (meta or {}).get("arms") or {}
    agent_ids = sorted(arms) if arms else sorted({e.get("i") for e in events if e.get("i")})
    table, ambiguous = _handle_table(agent_ids)
    stances, edges = _stances(events), list(_follow_edges(events, table))
    return {"run_dir": run_dir, "n_events": len(events),
            "n_follow_events": len(edges), "n_ambiguous_handles": ambiguous,
            "followers": _followers(edges, agent_ids, arms, stances, ambiguous),
            "homophily": _homophily(edges, stances),
            "copying": None,  # PATCH card: fans buying what the star just bought
            "influence_vs_homophily": None}  # PATCH card: pre-follow matching

def _cross_run(runs, warnings):
    def t_entry(key, sub):
        vals = [v for m in runs.values() if isinstance(m, dict)
                for v in [(m.get(key) or {}).get(sub)] if isinstance(v, (int, float))]
        e = {"n": len(vals), "mean": statistics.mean(vals) if vals else None,
             "t_interval": None}
        if len(vals) >= 2 and len(runs) >= 2:
            try:
                e["t_interval"] = common.seed_t_interval(vals)
            except Exception as exc:
                warnings.append("seed_t_interval(%s.%s) 失败：%s" % (key, sub, exc))
        return e
    return {"n_runs": len(runs), "followers_gini": t_entry("followers", "gini"),
            "homophily_excess": t_entry("homophily", "excess")}

def analyze(run_dirs):
    """Analyze run dirs -> {"runs": {tag: metrics}, "cross_run": {...}, "warnings": []}."""
    runs, warnings = {}, []
    for rd in run_dirs:
        tag = os.path.basename(os.path.normpath(rd)) or str(rd)
        while tag in runs: tag += "#"  # rare basename collision: force uniqueness
        try:
            events = list(common.load_events(rd) or [])
            meta = common.load_run_meta(rd) or {}
            runs[tag] = _run_metrics(events, meta, run_dir=rd)
        except Exception as exc:  # per-run failures never propagate
            runs[tag] = {"run_dir": rd, "error": "analysis failed: %r" % exc}
            warnings.append("%s：分析失败：%r" % (tag, exc))
    if len(runs) < 2:
        warnings.append("n_runs < 2，无法给出种子级 t 区间。")
    return {"runs": runs, "cross_run": _cross_run(runs, warnings),
            "warnings": warnings}

def _fmt(x):
    return ("%.3f" % x) if isinstance(x, (int, float)) else "NA"

def _write(path, obj, warnings):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=True, indent=1)
        print("已写入 %s" % path)
    except Exception as exc:
        warnings.append("写入 %s 失败：%s" % (path, exc))

def _print_summary(tag, m):
    if "error" in m: print("[%s] 分析失败：%s" % (tag, m["error"])); return
    f, h = m.get("followers") or {}, m.get("homophily") or {}
    print("[%s] 粉丝分布：agent=%s 有粉=%s max=%s Gini=%s top1=%s(share=%s)%s" % (
        tag, f.get("n_agents"), f.get("n_with_followers"), f.get("max"),
        _fmt(f.get("gini")), ((f.get("top5") or [{}])[0]).get("handle"),
        _fmt(f.get("top1_share")), ("；" + f["note"]) if f.get("note") else ""))
    print("[%s] 同质性：边=%s p_same=%s null均值=%s p95=%s excess=%s%s" % (
        tag, h.get("n_edges_with_stance"), _fmt(h.get("p_same_obs")),
        _fmt(h.get("p_same_null_mean")), _fmt(h.get("p_same_null_p95")),
        _fmt(h.get("excess")), ("；" + h["note"]) if h.get("note") else ""))

def _self_test():
    # 1) handle reverse lookup: a known id's handle must map back to that id
    table, amb = _handle_table(["inv_00012"])
    assert table[_handle("inv_00012")] == "inv_00012" and amb == 0
    # 2) three same-stance follow edges -> p_same_obs == 1.0
    meta = {"arms": {"a1": "star", "a2": "plain", "a3": "plain", "w1": "plain"}}
    events = [{"ev": "cmt", "t": 1, "i": a, "stance": s} for a, s in
              (("a1", "bullish"), ("a2", "bullish"), ("a3", "bullish"), ("w1", "watching"))]
    events += [{"ev": "st", "t": 2, "i": u, "what": "follow_user", "handle": _handle(v)}
               for u, v in (("a2", "a1"), ("a3", "a1"), ("a3", "a2"))]
    m = _run_metrics(events, meta)
    assert m["homophily"]["p_same_obs"] == 1.0
    assert m["followers"]["counts_sorted"] == [2, 1, 0, 0]
    # 3) no follow events -> followers.gini is None and the note is non-empty
    m0 = _run_metrics(events[:4], meta)
    assert m0["followers"]["gini"] is None and m0["followers"].get("note")
    # 5) Gini of an all-zero vector is None (not 0); sanity check on [0, 1]
    assert _gini([0, 0, 0, 0]) is None and abs(_gini([0, 1]) - 0.5) < 1e-9
    print("self-test 通过：句柄反查 / 同质性=1.0 / 零关注 / 全零Gini=None")

def main(argv=None):
    ap = argparse.ArgumentParser(description="FlowMirror influence analysis")
    ap.add_argument("run_dirs", nargs="*", help="run directories to analyze")
    ap.add_argument("--out", help="write the combined json to this path")
    ap.add_argument("--no-write", action="store_true", help="do not write json")
    ap.add_argument("--self-test", action="store_true", help="run built-in checks")
    ns = ap.parse_args(argv)
    if ns.self_test:
        _self_test()
        return 0
    if not ns.run_dirs:
        print("未给定 run 目录（用法：python -m flowmirror.analysis.influence RUN_DIR …）")
        return 0
    res = analyze(ns.run_dirs)
    single = ns.out and len(ns.run_dirs) == 1
    for tag, m in res["runs"].items():
        _print_summary(tag, m)
        if not ns.no_write and not single and m.get("run_dir") and "error" not in m:
            _write(os.path.join(m["run_dir"], "analysis", _OUT_NAME), m,
                   res["warnings"])
    if ns.out:
        _write(ns.out, res, res["warnings"])
    cross = res["cross_run"] or {}
    for name in ("followers_gini", "homophily_excess"):
        print("跨运行 %s：%s" % (name, cross.get(name) or {}))
    for w in res["warnings"]:
        print("警告：%s" % w)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

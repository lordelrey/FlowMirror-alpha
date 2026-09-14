"""Observer-only macro aggregation for FlowMirror.

Pure, deterministic, JSON-safe. No writes, no model calls, no live runs.
"""

import copy
from collections import defaultdict

from flowmirror.analysis.macro_market import market_phase
from flowmirror.analysis.macro_finance import finance_phase
from flowmirror.platform.behavior import BehaviorLedger

TRADE_KINDS = {"subscribe", "redeem", "buy", "sell", "cancel_order"}
POST_META_WHITELIST = ("post_id", "title", "market", "institution_id")
FEED_FIELDS = ("accepted", "rejected", "exposures", "opened_exposed")


def _available_phases(spec, snapshots, status):
    completed = max(0, min(len(snapshots) - 1, spec["phases"]))
    phases = list(range(completed))
    if completed < spec["phases"] and status != "completed" and snapshots:
        phases.append(completed)
    return phases, completed


def _pass_frames(state, spec):
    """Single pass over frames: ledger records, counts, trades, latest accounts."""
    from flowmirror.analysis.browse_observer import graph_metrics  # noqa: F401

    arms = {a["id"]: a["arm"] for a in spec["agents"]}
    ledger = BehaviorLedger()
    counts = defaultdict(int)
    trades = defaultdict(list)
    latest = defaultdict(dict)
    posts = {}
    for frame in state["frames"]:
        p = frame["phase"]
        counts[p] += 1
        actor = frame["agent_id"]
        ledger.record(frame, arms.get(actor))
        after = frame.get("after") or {}
        account = after.get("account")
        if account is not None:
            latest[p][actor] = account
        action = frame.get("action") or {}
        if isinstance(action.get("kind"), str) and action.get("kind") in TRADE_KINDS:
            if p not in trades:
                trades[p] = []
            trades[p].append({
                "index": frame["index"],
                "phase": p,
                "agent_id": frame["agent_id"],
                "step": frame["step"],
                "action": action,
                "result": frame.get("result") or {},
            })
        feed = (frame.get("before") or {}).get("feed") or []
        detail = (frame.get("before") or {}).get("detail")
        delivered = list(feed) + ([detail] if isinstance(detail, dict) else [])
        for item in delivered:
            pid = item.get("post_id")
            if pid is None or (p, pid) in posts:
                continue
            posts[(p, pid)] = {k: item.get(k) for k in POST_META_WHITELIST}
            posts[(p, pid)]["sample"] = {
                "agent_id": frame["agent_id"], "phase": p,
                "step": frame["step"], "index": frame["index"],
            }
    return ledger, counts, trades, latest, posts


def _social(report_rows, posts, phase):
    by_post = defaultdict(list)
    phase_acc = defaultdict(int)
    phase_rej = defaultdict(int)
    for row in report_rows:
        if row["phase"] != phase:
            continue
        pid = row["post_id"]
        for k in ("accepted", "rejected"):
            src = row.get(k) or {}
            dst = phase_acc if k == "accepted" else phase_rej
            for kk, vv in src.items():
                dst[kk] += vv
        if pid is not None:
            by_post[pid].append(row)
    exposures = sum(r.get("exposures", 0) for r in report_rows
                    if r["phase"] == phase and r["post_id"] is not None)
    opened = sum(r.get("opened_exposed", 0) for r in report_rows
                 if r["phase"] == phase and r["post_id"] is not None)
    post_rows = []
    order = sorted(by_post, key=lambda x: (-sum(r.get("exposures", 0)
                                                for r in by_post[x]), x))
    for pid in order:
        rows = by_post[pid]
        acc = defaultdict(int)
        rej = defaultdict(int)
        exp = sum(r.get("exposures", 0) for r in rows)
        op = sum(r.get("opened_exposed", 0) for r in rows)
        for r in rows:
            for k, v in (r.get("accepted") or {}).items():
                acc[k] += v
            for k, v in (r.get("rejected") or {}).items():
                rej[k] += v
        post_rows.append({
            "post_id": pid,
            "title": posts.get((phase, pid), {}).get("title"),
            "market": posts.get((phase, pid), {}).get("market"),
            "institution_id": posts.get((phase, pid), {}).get("institution_id"),
            "sample": posts.get((phase, pid), {}).get("sample"),
            "arms": [r["arm"] for r in rows],
            "exposures": exp,
            "opened_exposed": op,
            "open_rate": (op / exp) if exp else None,
            "accepted": dict(acc),
            "rejected": dict(rej),
        })
    return {
        "accepted": dict(phase_acc),
        "rejected": dict(phase_rej),
        "exposures": exposures,
        "opened_exposed": opened,
        "open_rate": (opened / exposures) if exposures else None,
        "posts": post_rows,
    }


def _institutions(state, phase, enabled):
    decisions = []
    for snap in state.get("institution_snapshots") or []:
        if snap.get("phase") != phase:
            continue
        for d in snap.get("decisions") or []:
            before = d.get("before") or {}
            action = d.get("action") or {}
            result = d.get("result") or {}
            decisions.append({
                "institution_id": d.get("institution_id"),
                "org": before.get("org"),
                "market": before.get("market"),
                "strategy": before.get("strategy"),
                "policy_kind": before.get("policy_kind"),
                "kind": action.get("kind"),
                "status": result.get("status"),
                "post_id": result.get("post_id"),
                "creative_id": action.get("creative_id"),
                "feedback_through_phase": before.get("feedback_through_phase"),
                "remaining_publications_before": before.get("remaining_publications"),
                "feedback": before.get("feedback"),
            })
    return {"enabled": enabled, "decisions": decisions}


def _graph(public):
    from flowmirror.analysis.browse_observer import graph_metrics

    g = graph_metrics(public)
    edges = public.get("edges") or []
    followers = public.get("followers") or {}
    if not edges:
        g["concentration"] = None
    leaders = sorted(followers.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    g["leaders"] = [{"handle": h, "followers": c} for h, c in leaders]
    return g


def build_macro(state):
    spec = state["spec"]
    snapshots = state.get("snapshots") or []
    times = spec.get("phase_times") or []
    status = state.get("status") or ""
    phases_avail, completed = _available_phases(spec, snapshots, status)

    ledger, counts, trades, latest, posts = _pass_frames(state, spec)
    report_rows = ledger.report()

    rows = []
    previous_ending = {}
    refs = {}
    for p in phases_avail:
        begin = dict((state.get("account_snapshots") or [])[p]) \
            if p < len(state.get("account_snapshots") or []) else {}
        ending = dict(begin)
        ending.update(latest.get(p, {}))
        for f in trades.get(p, []):
            if f["result"].get("status") == "accepted" \
                    and f["action"].get("kind") != "cancel_order":
                oid = f["result"].get("order_id")
                if oid is not None:
                    refs[(f["agent_id"], str(oid))] = {
                        "phase": p,
                        "step": f["step"],
                        "index": f["index"],
                    }
        financial = finance_phase(spec, ending, previous_ending,
                                  trades.get(p, []), refs, p)
        previous_ending = ending
        market = market_phase(spec, p)
        public = snapshots[p + 1] if p < completed and p + 1 < len(snapshots) \
            else snapshots[p]
        social = _social(report_rows, posts, p)
        social["graph"] = _graph(public)
        social["graph_basis"] = "phase_end_public" \
            if p < completed else "phase_begin_public"
        rows.append({
            "phase": p,
            "as_of": times[p] if p < len(times) else None,
            "phase_status": "complete" if p < completed else "partial",
            "committed_frame_count": counts.get(p, 0),
            "market": market,
            "financial": financial,
            "social": social,
            "institutions": _institutions(
                state, p, bool(spec.get("marketing"))),
        })

    overview = {
        "name": spec.get("name"),
        "status": status or None,
        "policy_mode": spec.get("policy_mode"),
        "label": {
            "scripted": "脚本策略",
            "null": "零互动策略 · 非真模型结果",
            "external": "来源未核验",
        }.get(spec.get("policy_mode")),
        "agent_count": len(spec.get("agents") or []),
        "total_phases": spec["phases"],
        "completed_phases": completed,
        "available_phases": [r["phase"] for r in rows],
        "pending_excluded": state.get("pending") is not None,
        "notice": "仅已提交记录；未完成阶段为部分状态；行情外生、资金属模拟样本",
    }
    return {"overview": overview, "phases": rows}


def select_macro(report, phase=None):
    phases = report.get("phases") or []
    if phase is None:
        if not phases:
            return copy.deepcopy({"overview": report.get("overview"),
                                  "selected": None, "timeline": []})
        phase = phases[-1]["phase"]
    if isinstance(phase, bool) or not isinstance(phase, int):
        raise ValueError("phase must be an int")
    if phase not in {r["phase"] for r in phases}:
        raise ValueError("phase not available")

    timeline = []
    for row in phases:
        if row["phase"] > phase:
            break
        fin = row.get("financial") or {}
        timeline.append({
            "phase": row["phase"],
            "as_of": row.get("as_of"),
            "phase_status": row.get("phase_status"),
            "committed_frame_count": row.get("committed_frame_count", 0),
            "market_groups": (row.get("market") or {}).get("groups", []),
            "financial_currencies": fin.get("currencies", []),
            "financial_flows": fin.get("flows", []),
            "exposures": row["social"].get("exposures", 0),
            "opened_exposed": row["social"].get("opened_exposed", 0),
            "follow_count": row["social"].get("accepted", {}).get("follow", 0),
            "graph_edges": row["social"]["graph"].get("edges"),
        })
    selected = next((r for r in phases if r["phase"] == phase), None)
    return copy.deepcopy({
        "overview": report.get("overview"),
        "selected": selected,
        "timeline": timeline,
    })

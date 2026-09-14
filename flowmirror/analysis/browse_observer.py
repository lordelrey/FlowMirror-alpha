"""Read-only observer projections of persisted browsing runs, not policy inputs."""
from __future__ import annotations

import copy
import threading
from collections import Counter

from flowmirror.platform.browse_store import read_checkpoint


def financial_summary(state):
    snapshots = state.get('account_snapshots')
    if not snapshots:
        return {'enabled': False}
    latest = copy.deepcopy(snapshots[-1])
    phase = len(state['snapshots']) - 1
    for frame in state['frames']:
        if frame['phase'] == phase and 'account' in frame['after']:
            latest[frame['agent_id']] = frame['after']['account']
    flows = {}
    products = {(p['market'], p['instrument_id']): p for p in state['spec']['trading']['instruments']}
    for actor, account in latest.items():
        for order in account['orders']:
            key = (order['market'], order['instrument_id'])
            row = flows.setdefault(key, {'market': key[0], 'instrument_id': key[1],
                                        'currency': order['currency'], 'kind': products[key]['kind'],
                                        'buy_gross': 0, 'sell_gross': 0, 'fees': 0,
                                        'pending_orders': 0, 'settled_orders': 0})
            row['pending_orders'] += order['status'] in ('submitted', 'filled')
            row['settled_orders'] += order['status'] == 'settled'
            if order['status'] in ('filled', 'settled'):
                row[order['side'] + '_gross'] += order['gross']
                row['fees'] += order['fee']
    for row in flows.values():
        row['buy_gross'] = round(row['buy_gross'], 2)
        row['sell_gross'] = round(row['sell_gross'], 2)
        row['fees'] = round(row['fees'], 2)
        row['net_executed_gross'] = round(row['buy_gross'] - row['sell_gross'], 2)
    return {'enabled': True, 'accounts': [
        {'agent_id': actor, **{k: a[k] for k in ('currency', 'equity', 'return_pct', 'fees',
                                               'available_cash', 'receivable')}}
        for actor, a in latest.items()], 'flows': [flows[k] for k in sorted(flows)],
        'notice': '成交净额不是因果营销效果；不合并不同币种，不改变外生市场价格。'}


def graph_metrics(snapshot):
    counts = sorted(snapshot["followers"].values())
    total, n = sum(counts), len(counts)
    return {
        "epoch": snapshot["epoch"], "edges": len(snapshot["edges"]),
        "agents": n, "followed_authors": sum(c > 0 for c in counts),
        "top1_share": max(counts) / total if total else None,
        "hhi": sum((c / total) ** 2 for c in counts) if total else None,
        "gini_all_agents": (sum((2 * i - n - 1) * c for i, c in enumerate(counts, 1))
                            / (n * total)) if total and n else None,
    }


def summarize_browse(state):
    frames = state["frames"]
    accepted, rejected = Counter(), Counter()
    exposures, opened, follow_opportunities = set(), set(), set()
    for frame in frames:
        before, action, result = frame["before"], frame["action"], frame["result"]
        unit = (frame["phase"], frame["agent_id"])
        for post in before["feed"]:
            exposures.add((*unit, post["post_id"]))
        visible = set(before["visible_handles"]) - {before["own_handle"]}
        if visible - set(before["following"]):
            follow_opportunities.add(unit)
        kind = action.get("kind", "invalid")
        # Invalid JSON kinds can be any JSON type; make aggregation total.
        kind = kind if isinstance(kind, str) else "invalid"
        (accepted if result["status"] == "accepted" else rejected)[kind] += 1
        if result["status"] == "accepted" and kind in ("open", "comments"):
            opened.add((*unit, action["post_id"]))
    spec = state["spec"]
    mode = spec["policy_mode"]
    summary = {
        "mode": "recorded_microsteps", "name": spec.get("name", "browsing run"),
        "policy_mode": mode, "policy_name": spec.get("policy_name"),
        "label": {"scripted": "已保存脚本演练 · 非真模型结果",
                  "null": "已保存零互动策略 · 非真模型结果",
                  "external": "外部策略轨迹 · 模型来源须另行核实"}[mode],
        "status": state["status"], "phases": spec["phases"],
        "completed_phases": len(state["snapshots"]) - 1,
        "phase_times": spec.get("phase_times"),
        'corpus_info': spec.get('corpus_info'),
        "agents": [{k: a[k] for k in ("id", "handle", "arm")} for a in spec["agents"]],
        "frames": len(frames), "policy_calls": state["policy_calls"],
        "model_calls": 0 if mode != "external" else None,
        "accepted": dict(accepted), "rejected": dict(rejected),
        "exposure_units": len(exposures), "opened_units": len(opened),
        "open_rate": len(opened) / len(exposures) if exposures else None,
        "follow_opportunity_sessions": len(follow_opportunities),
        "unit_note": "曝光和打开按 agent×阶段×帖子去重；关注机会按 agent×阶段去重，非因果转化率",
        "graph_timeline": [graph_metrics(s) for s in state["snapshots"]],
        "pending": state.get("pending") is not None,
        "financial": financial_summary(state),
        "account_note": ("模拟账户：预留、成交、交收分别记录；价格外生，不是真实券商交易。"
                         if 'trading' in spec else "本浏览协议未执行交易；现金和记忆为配置初值，不是收益或学习结果"),
    }
    if 'marketing' in spec:
        from flowmirror.analysis.marketing_behavior import summarize_marketing
        summary['marketing'] = summarize_marketing(state)
    return summary


class BrowseObserver:
    def __init__(self, out, index_path=None):
        self.out = out
        from flowmirror.analysis.browse_index import load_index
        cached = load_index(out, index_path) if index_path is not None else None
        self.state, self._summary = cached if cached is not None else (read_checkpoint(out), None)
        self.indexed = cached is not None
        self._macro = None
        self._macro_lock = threading.Lock()

    def macro(self, phase=None):
        from flowmirror.analysis.macro_observer import build_macro, select_macro
        from flowmirror.analysis.browse_index import IndexUnavailable
        with self._macro_lock:
            if self.indexed:
                try:
                    self.state['frames'].source.check()
                except IndexUnavailable:
                    self.state = read_checkpoint(self.out)
                    self._summary = None
                    self._macro = None
                    self.indexed = False
            if self._macro is None:
                try:
                    self._macro = build_macro(read_checkpoint(self.out) if self.indexed else self.state)
                except IndexUnavailable:
                    self.state = read_checkpoint(self.out)
                    self._summary = None
                    self.indexed = False
                    self._macro = build_macro(self.state)
            return copy.deepcopy(select_macro(self._macro, phase))

    def summary(self):
        from flowmirror.analysis.browse_index import IndexUnavailable
        if self.indexed:
            try:
                self.state['frames'].source.check()
            except IndexUnavailable:
                self.state, self._summary, self.indexed = read_checkpoint(self.out), None, False
        if self._summary is None:
            self._summary = summarize_browse(self.state)
        return copy.deepcopy(self._summary)

    def agent_trace(self, agent_id, phase):
        from flowmirror.analysis.browse_index import IndexUnavailable
        try:
            return self._agent_trace(agent_id, phase)
        except IndexUnavailable:
            self.state, self._summary, self.indexed = read_checkpoint(self.out), None, False
            return self._agent_trace(agent_id, phase)

    def _agent_trace(self, agent_id, phase):
        spec = self.state["spec"]
        if agent_id not in {a["id"] for a in spec["agents"]}:
            raise ValueError("unknown actor")
        if type(phase) is not int or not 0 <= phase < spec["phases"]:
            raise ValueError("unknown phase")
        saved = self.state['frames']
        frames = (list(saved.select(agent_id, phase)) if hasattr(saved, 'select') else
                  [f for f in saved if f['agent_id'] == agent_id and f['phase'] == phase])
        snapshots = self.state["snapshots"]
        public = snapshots[phase] if phase < len(snapshots) else None
        institutions = self.state.get('institution_snapshots', [])
        institution_before = ({'phase': phase, 'decisions': institutions[phase]['decisions']}
                              if phase < len(institutions) else None)
        from flowmirror.analysis.session_behavior import summarize_session
        behavior = summarize_session(frames, policy_mode=spec['policy_mode'])
        if not frames:
            behavior['source'].update(agent_id=agent_id, phase=phase)
        # Slider position must not show the rest of this session as already acted.
        behavior_timeline = [summarize_session(frames[:i + 1], policy_mode=spec['policy_mode'])
                             for i in range(len(frames))]
        return copy.deepcopy({
            "mode": "recorded_microsteps", "agent_id": agent_id, "phase": phase,
            "frames": frames, "public_before": public,
            # Observer-only. Exclude same-phase final feedback from a decision-time panel.
            'institution_before': institution_before,
            'behavior': behavior, 'behavior_timeline': behavior_timeline,
            'account_history': [{'phase': i, **{k: accounts[agent_id][k] for k in
                                               ('as_of', 'equity', 'return_pct', 'currency')}}
                                for i, accounts in enumerate(self.state.get('account_snapshots', [])[:phase + 1])],
            "public_metrics": graph_metrics(public) if public is not None else None,
            "notice": "公开图为本阶段起点；本轮自己的关注即时有效，其他人下阶段才获知。",
        })

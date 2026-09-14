"""Independent, resumable multi-phase browsing runner (not engine.loop).

The injected policy receives ONLY the selected actor's view. The checkpoint is
an observer artifact containing private data; never deliver it to a policy.
"""
from __future__ import annotations

import copy
import json
from datetime import timedelta

from flowmirror.platform.browse_store import (
    checkpoint_path, read_checkpoint, save_checkpoint, writer_lock,
)
from flowmirror.platform.public_board import PublicBoard
from flowmirror.platform.browse_journal import JournalFrames, start_journal
from flowmirror.market.tape import MarketTape, instant
from flowmirror.market.broker import SimBroker, TRADE_KINDS
from flowmirror.platform.marketing import MarketingDesk, validate_marketing


def _positive(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(name + " must be an integer >= " + str(minimum))
    return value


def normalize_spec(spec):
    spec = json.loads(json.dumps(spec, ensure_ascii=False, allow_nan=False))
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    for name, default in (("phases", 3), ("max_steps", 6), ("page_size", 3)):
        spec[name] = _positive(spec.get(name, default), name)
    if spec.get("policy_mode") not in ("scripted", "null", "external"):
        raise ValueError("policy_mode must be scripted, null, or external")
    interval = _positive(spec.get('offline_checkpoint_interval', 1), 'offline_checkpoint_interval')
    if interval > 1 and spec['policy_mode'] == 'external':
        raise ValueError('external policies require per-request persistence')
    if 'feed_policy' in spec:
        feed = spec['feed_policy']
        if not isinstance(feed, dict) or feed.get('kind') != 'recent_rotating' or 'phase_times' not in spec:
            raise ValueError('recent_rotating feed requires phase_times')
        for key in ('limit', 'lookback_days'):
            _positive(feed.get(key), 'feed_' + key)
        if type(feed.get('seed')) is not int:
            raise ValueError('feed seed must be an integer')
    agents = spec.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("agents must be a nonempty list")
    ids = set()
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError("invalid agent")
        actor = agent.get("id")
        if not isinstance(actor, str) or not actor or actor in ids:
            raise ValueError("agent IDs must be unique nonempty strings")
        ids.add(actor)
        if agent.get("arm") not in ("T", "TC", "TV"):
            raise ValueError("invalid arm")
        if not isinstance(agent.get("private_state", {}), dict):
            raise ValueError("invalid private state")
        if 'feed_group' in agent:
            _positive(agent['feed_group'], 'feed_group', minimum=0)
    # Reuse the actual platform validation, rather than a second card schema.
    PublicBoard(spec.get("cards"), {a["id"]: a.get("handle") for a in agents})
    for card in spec["cards"]:
        phase = _positive(card.get("publish_phase", 0), "publish_phase", minimum=0)
        if phase >= spec["phases"]:
            raise ValueError("publish_phase is outside the run")
    if "phase_times" in spec:
        times = spec["phase_times"]
        if not isinstance(times, list) or len(times) != spec["phases"]:
            raise ValueError("phase_times must supply one timestamp per phase")
        clocks = [instant(t) for t in times]
        if any(a >= b for a, b in zip(clocks, clocks[1:])):
            raise ValueError("phase_times must strictly increase")
    if "market_data" in spec:
        if "phase_times" not in spec:
            raise ValueError("market_data requires phase_times")
        MarketTape(spec["market_data"])
    for agent in agents:
        if agent.get("market") not in (None, "CN", "US"):
            raise ValueError("invalid agent market")
        if 'market_instruments' in agent and (not isinstance(agent['market_instruments'], list)
                or any(not isinstance(i, str) for i in agent['market_instruments'])):
            raise ValueError('market_instruments must be a list of IDs')
    for card in spec["cards"]:
        if "published_at" in card:
            if "phase_times" not in spec or "publish_phase" in card:
                raise ValueError("published_at requires phase_times and no publish_phase")
            instant(card["published_at"])
    if 'trading' in spec:
        if 'phase_times' not in spec or 'market_data' not in spec:
            raise ValueError('trading requires a clock and market data')
        SimBroker(spec['trading'], agents, spec['market_data'], spec['phase_times'][0])
    if 'marketing' in spec:
        validate_marketing(spec['marketing'], phases=spec['phases'],
                           phase_times=spec.get('phase_times'),
                           handles={a['id']: a.get('handle') for a in agents})
        # Campaign IDs occupy their own namespace; ordinary primary-key checks
        # catch collisions before a local run starts producing any actions.
        prefixes = tuple('campaign_' + o['id'] + '_' for o in spec['marketing'])
        if any(c['post_id'].startswith(prefixes) for c in spec['cards']):
            raise ValueError('organic post ID collides with campaign namespace')
    return spec


class _World:
    def __init__(self, spec):
        self.spec = normalize_spec(spec)
        self.board = PublicBoard([c for c in self.spec["cards"] if self._release_phase(c) == 0],
                                 {a["id"]: a["handle"] for a in self.spec["agents"]})
        self.market = MarketTape(self.spec.get("market_data", []))
        self.broker = (SimBroker(self.spec['trading'], self.spec['agents'], self.spec['market_data'],
                                 self.spec['phase_times'][0]) if 'trading' in self.spec else None)
        self.account_snapshots = ([self.broker.snapshot(self.spec['phase_times'][0])] if self.broker else [])
        self.marketing = (MarketingDesk(self.spec['marketing'], self.spec['phases'], self.spec.get('phase_times'))
                          if 'marketing' in self.spec else None)
        self.histories = {a["id"]: [] for a in self.spec["agents"]}
        self._market_views = {}
        self._releases = {}
        for card in self.spec['cards']:
            self._releases.setdefault(self._release_phase(card), []).append(card)
        self.snapshots = [self.board.public_snapshot()]
        self._iterator = self._steps()
        self.cursor = next(self._iterator, None)

    def _release_phase(self, card):
        if "published_at" not in card:
            return card.get("publish_phase", 0)
        published = instant(card["published_at"])
        return next((i for i, t in enumerate(self.spec["phase_times"]) if instant(t) >= published), None)

    def _steps(self):
        for phase in range(self.spec["phases"]):
            if self.broker:
                self.broker.advance(self.spec['phase_times'][phase])
                self.account_snapshots[-1] = self.broker.snapshot(self.spec['phase_times'][phase])
            if phase > 0:
                self.board.publish_cards(self._releases.get(phase, []))
                # The epoch's public snapshot includes posts released at its start.
                self.snapshots[-1] = self.board.public_snapshot()
            if self.marketing:
                self.board.publish_cards(self.marketing.begin_phase(phase))
                self.snapshots[-1] = self.board.public_snapshot()
            self._market_views = {}
            feed_policy = self.spec.get('feed_policy')
            candidates = {}
            library = self.spec['cards'] + (self.marketing.published if self.marketing else [])
            if feed_policy:
                clock = instant(self.spec['phase_times'][phase])
                earliest = clock - timedelta(days=feed_policy['lookback_days'])
                for card in library:
                    at = instant(card['published_at']) if 'published_at' in card else None
                    if at is not None and earliest <= at <= clock:
                        candidates.setdefault(card.get('market'), []).append(card)
                for cards in candidates.values():
                    cards.sort(key=lambda c: (instant(c['published_at']), c['post_id']), reverse=True)
            for agent_index, agent in enumerate(self.spec["agents"]):
                selected = None
                if feed_policy:
                    cards = candidates.get(agent.get('market'), [])
                    # Matched T/TC/TV triplets start with the same stimulus window.
                    group = agent.get('feed_group', agent_index // 3)
                    offset = (feed_policy['seed'] + phase * 7 + group * 13) % max(1, len(cards))
                    selected = [cards[(offset + i) % len(cards)]['post_id']
                                for i in range(min(feed_policy['limit'], len(cards)))]
                elif self.marketing:
                    selected = [c['post_id'] for c in library
                                if self._release_phase(c) is not None and self._release_phase(c) <= phase
                                and c.get('market') in (None, agent.get('market'))]
                session = self.board.open_session(
                    agent["id"], arm=agent["arm"],
                    private_state=agent.get("private_state", {}),
                    page_size=self.spec["page_size"], max_steps=self.spec["max_steps"],
                    post_ids=selected,
                )
                while not session.done:
                    yield phase, agent, session
                self.board.commit_session(session)
            if self.marketing:
                self.marketing.finish_phase(phase)
            self.board.advance()
            self.snapshots.append(self.board.public_snapshot())
            if self.broker:
                self.account_snapshots.append(self.broker.snapshot(self.spec['phase_times'][phase]))

    def view(self):
        phase, agent, session = self.cursor
        view = {**session.view(), "phase": phase, "own_handle": agent["handle"],
                "recent_actions": copy.deepcopy(self.histories[agent["id"]][-8:])}
        if "phase_times" in self.spec:
            view["sim_time"] = self.spec["phase_times"][phase]
        if "market_data" in self.spec:
            key = agent.get('market')
            if key not in self._market_views:
                self._market_views[key] = self.market.snapshot(view['sim_time'], market=key)
            snapshot = self._market_views[key]
            if 'market_instruments' in agent:
                watch = set(agent['market_instruments'])
                snapshot = {**snapshot, 'quotes': [q for q in snapshot['quotes'] if q['instrument_id'] in watch]}
            view['market_snapshot'] = copy.deepcopy(snapshot)
        if self.broker:
            account = self.broker.view(agent['id'], view['sim_time'])
            view['account'] = account
            view['private_state']['cash'] = account['available_cash']
            view['private_state']['holdings'] = copy.deepcopy(account['positions'])
            view['tradable_instruments'] = self.broker.catalog(agent['id'])
        return view

    def apply(self, action, index):
        phase, agent, session = self.cursor
        before = self.view()
        if self.broker and action.get('kind') in TRADE_KINDS:
            result = session.apply_private_action(action, lambda a: self.broker.submit(agent['id'], a, before['sim_time']))
        else:
            result = session.apply(copy.deepcopy(action))
        self.histories[agent["id"]].append({
            "phase": phase, "step": before["step"],
            "action": copy.deepcopy(action), "result": copy.deepcopy(result),
        })
        # Only the last eight actions are part of the policy observation.
        self.histories[agent['id']] = self.histories[agent['id']][-8:]
        frame = {"index": index, "phase": phase, "agent_id": agent["id"],
                 "step": before["step"], "before": before,
                 "action": copy.deepcopy(action), "result": result, "after": self.view()}
        if self.marketing:
            # Record the delivered observation before advancing to the next phase.
            self.marketing.ledger.record(frame, agent['arm'])
        self.cursor = next(self._iterator, None)
        return frame


def _rebuild(state):
    world = _World(state["spec"])
    frames = state.get("frames")
    if not isinstance(frames, (list, JournalFrames)):
        raise ValueError("invalid frame list")
    for index, saved in enumerate(frames):
        if world.cursor is None or not isinstance(saved, dict):
            raise ValueError("unexpected cached frame")
        if saved.get("before") != world.view():
            raise ValueError("cached observation differs at step " + str(index))
        if not isinstance(saved.get("action"), dict):
            raise ValueError("invalid cached action")
        actual = world.apply(saved["action"], index)
        if any(saved.get(k) != v for k, v in actual.items()):
            raise ValueError("cached transition differs at step " + str(index))
    if state.get("snapshots") != world.snapshots:
        raise ValueError("cached public snapshots differ")
    if world.broker and state.get('account_snapshots') != world.account_snapshots:
        raise ValueError('cached account snapshots differ')
    if world.marketing and state.get('institution_snapshots') != world.marketing.snapshots:
        raise ValueError('cached institution snapshots differ')
    pending = state.get("pending")
    if pending is not None:
        if not isinstance(pending, dict) or world.cursor is None:
            raise ValueError("unexpected pending request")
        if pending.get("before") != world.view() or pending.get("index") != len(frames):
            raise ValueError("pending observation differs")
        if pending.get("action") is not None and not isinstance(pending["action"], dict):
            raise ValueError("invalid pending action")
    expected_calls = len(frames) + int(pending is not None)
    if state.get("policy_calls") != expected_calls:
        raise ValueError("policy call count differs")
    if state.get("status") == "completed" and world.cursor is not None:
        raise ValueError("incomplete run marked completed")
    return world


def replay_run(out):
    """Read-only deterministic replay; no policy construction or network access."""
    state = read_checkpoint(out)
    world = _rebuild(state)
    pending = state.get("pending")
    result = {"identical": True, "complete": world.cursor is None,
            "cached_steps": len(state["frames"]), "model_calls": 0,
            "pending": pending is not None,
            "note": "valid cached prefix; pending request is not a completed action"
            if pending is not None else "recorded actions and public snapshots reproduced"}
    if world.broker:
        result['pending_orders'] = sum(o['status'] in ('submitted', 'filled') for o in world.broker.orders)
        result['financially_settled'] = result['pending_orders'] == 0
    if world.marketing:
        result['institution_decisions'] = sum(len(s['decisions']) for s in world.marketing.snapshots)
        result['simulated_publications'] = len(world.marketing.published)
    return result


def _outcome(state, new_calls, recovered_tail=None):
    result = {"status": state["status"], "cached_steps": len(state["frames"]),
            "new_policy_calls": new_calls, "policy_calls": state["policy_calls"],
            "completed_phases": len(state["snapshots"]) - 1,
            "policy_mode": state["spec"]["policy_mode"],
            "model_calls": 0 if state["spec"]["policy_mode"] != "external" else None}
    if recovered_tail is not None:
        result['recovered_uncommitted_tail'] = recovered_tail.name
    if state.get('account_snapshots'):
        # A paused partial phase may have newer own orders than its opening snapshot.
        latest = dict(state['account_snapshots'][-1])
        for frame in state['frames']:
            if frame['phase'] == len(state['snapshots']) - 1 and 'account' in frame['after']:
                latest[frame['agent_id']] = frame['after']['account']
        orders = [o for a in latest.values() for o in a['orders']]
        result['pending_orders'] = sum(o['status'] in ('submitted', 'filled') for o in orders)
        result['settled_orders'] = sum(o['status'] == 'settled' for o in orders)
    if 'marketing' in state['spec']:
        decisions = [d for s in state['institution_snapshots'] for d in s['decisions']]
        result['institution_rule_decisions'] = len(decisions)
        result['simulated_publications'] = sum(d['result'].get('post_id') is not None for d in decisions)
    return result


def run_browsing(spec, out, policy, *, max_new_calls=0, storage='snapshot'):
    """Resume saved actions, then make at most max_new_calls policy invocations.

    An unanswered pending call never retries, including on a later invocation.
    A persisted response can be applied without calling the provider again.
    Caller owns explicit API authorization and provider configuration.
    """
    spec = normalize_spec(spec)
    _positive(max_new_calls, "max_new_calls", minimum=0)
    if storage not in ('snapshot', 'journal'):
        raise ValueError('storage must be snapshot or journal')
    with writer_lock(out):
        recovered_tail = None
        if checkpoint_path(out).exists():
            state = read_checkpoint(out)
            if state["spec"] != spec:
                raise ValueError("spec differs; use a separate output directory")
            world = _rebuild(state)
            if isinstance(state['frames'], JournalFrames):
                recovered_tail = state['frames'].repair_uncommitted_tail()
        else:
            world = _World(spec)
            state = {"format": "flowmirror-browse-v1", "spec": spec,
                     "status": "paused", "frames": [], "pending": None,
                     "snapshots": copy.deepcopy(world.snapshots), "policy_calls": 0}
            if world.broker:
                state['account_snapshots'] = copy.deepcopy(world.account_snapshots)
            if world.marketing:
                state['institution_snapshots'] = copy.deepcopy(world.marketing.snapshots)
            if storage == 'journal':
                start_journal(out, state, save_checkpoint)
            save_checkpoint(out, state)
        calls = 0
        interval = spec.get('offline_checkpoint_interval', 1)
        while world.cursor is not None:
            pending = state["pending"]
            if pending is not None and pending.get("action") is None:
                state["status"] = "uncertain"
                save_checkpoint(out, state)
                return _outcome(state, calls, recovered_tail)
            if pending is None:
                if calls >= max_new_calls:
                    state["status"] = "paused"
                    save_checkpoint(out, state)
                    return _outcome(state, calls, recovered_tail)
                before = world.view()
                state["pending"] = pending = {
                    "index": len(state["frames"]), "before": before, "action": None,
                }
                state["policy_calls"] += 1
                state["status"] = "calling"
                if interval == 1:
                    save_checkpoint(out, state)  # precedes every external side effect
                calls += 1
                try:
                    action = policy(copy.deepcopy(before))
                    if not isinstance(action, dict):
                        raise TypeError("policy must return an action object")
                    # Reject non-JSON responses before partially modifying pending.
                    action = json.loads(json.dumps(action, allow_nan=False))
                except (Exception, KeyboardInterrupt) as exc:
                    state["status"] = "uncertain"
                    pending["error_type"] = type(exc).__name__  # no exception secrets
                    save_checkpoint(out, state)
                    return _outcome(state, calls, recovered_tail)
                pending["action"] = action
                delivery = getattr(policy, "last_delivery", None)
                # Metadata only: never copy raw prompts, base64 bytes, or auth objects.
                if isinstance(delivery, dict):
                    pending["delivery"] = copy.deepcopy({
                        k: delivery[k] for k in ("visual_delivery", "attached_shas",
                                                "missing_shas", "missing_post_ids", 'attached_refs', 'missing_refs',
                                                'image_attachments')
                        if k in delivery
                    })
                state["status"] = "responded"
                if interval == 1:
                    save_checkpoint(out, state)
            frame = world.apply(pending["action"], len(state["frames"]))
            frame["delivery"] = pending.get("delivery")
            state["frames"].append(frame)
            state["pending"] = None
            state["snapshots"] = world.snapshots
            if world.broker:
                state['account_snapshots'] = copy.deepcopy(world.account_snapshots)
            if world.marketing:
                state['institution_snapshots'] = world.marketing.snapshots
            state["status"] = "completed" if world.cursor is None else "paused"
            if calls % interval == 0 or world.cursor is None:
                save_checkpoint(out, state)
        # Empty feeds can finish every phase without a single policy invocation.
        if state['status'] != 'completed':
            state['status'] = 'completed'
            save_checkpoint(out, state)
        return _outcome(state, calls, recovered_tail)

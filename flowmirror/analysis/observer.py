"""Read-only facade over ObserverStore for historical batch observation."""
import copy

from .observer_store import ObserverStore
from .observer_account import reconstruct_account
from .observer_cards import cards_for


class RunObserver:
    def __init__(self, root, tag):
        self.tag = tag
        self.s = ObserverStore(root, tag)

    def _provider_mode(self):
        """Classify provider from saved config.

        This mirrors the engine's decision order: agent_policy == "null"
        wins outright; then mock flags (cfg.mock_llm, legacy cfg.mock, or a
        model named 'mock'); else a supplied llm.model means a real model;
        absence of a model means unknown. This is classification of saved
        configuration, not independent proof of provider responses.

        When a null policy or mock flag overrides, the *displayed* model is
        "null_policy"/"mock" rather than the (unused) real model name.
        """
        cfg = self.s.cfg
        model = cfg.get('llm', {}).get('model')
        null_policy = cfg.get('agent_policy') == 'null'
        mock_flag = bool(cfg.get('mock_llm')) or bool(cfg.get('mock'))
        if null_policy:
            return 'null_policy', 'mock'
        if mock_flag or model in ('mock', 'null'):
            return 'mock', 'mock'
        if model:
            return model, 'real_model'
        return None, 'unknown'

    def summary(self):
        s = self.s
        a = s.meta.get('arms', {})
        agent_ids = sorted(set(a.keys()) | {k[0] for k in s.by_agent_day if k})
        days = [{'t': t, 'date': s.days[t]} for t in sorted(s.days.keys())]
        cfg = s.cfg
        social = cfg.get('social_graph') or {}
        enabled = bool(social.get('enabled', False))
        model, provider_mode = self._provider_mode()
        return {
            'mode': 'historical_batch',
            'tag': self.tag,
            'model': model,
            'provider_mode': provider_mode,
            'agents': [{'id': i, 'arm': a.get(i)} for i in agent_ids],
            'days': days,
            'social_graph_enabled': enabled,
            'warnings': list(s.warnings),
        }

    def agent_frame(self, agent_id, day):
        s = self.s
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError('agent_id must be str')
        if isinstance(day, bool) or not isinstance(day, int):
            raise ValueError('day must be int')
        if agent_id not in {x['id'] for x in self.summary()['agents']}:
            raise ValueError('unknown agent_id')
        if day not in s.days:
            raise ValueError('unknown day')
        events = copy.deepcopy(s.by_agent_day.get((agent_id, day), []))
        profile = s.agents.get(agent_id, {}) or {}
        persona = (profile.get('persona_card_zh_rich')
                   or profile.get('persona_card_zh'))
        account = None
        notice = None
        # write_run_meta contract: non-holders are omitted from the top-level
        # openings dict, so an absent key on a KNOWN agent means "no opening
        # position" -> replay from an empty opening. An absent/non-dict
        # openings field, or an explicitly null/bad entry, stays unknown.
        openings = s.meta.get('openings')
        if isinstance(openings, dict):
            if agent_id in openings:
                opening = openings.get(agent_id)
            else:
                opening = {}
        else:
            opening = None
        if not isinstance(opening, dict):
            account = None
        else:
            own = [r for r in s.rows
                   if r.get('ev') == 'act' and r.get('i') == agent_id
                   and r.get('t') <= day]
            try:
                account = reconstruct_account(opening, own, s.nav, s.days[day])
            except ValueError:
                account = None
                notice = '账户重建失败，估值缺失'
        feed = cards_for(s, events, agent_id)
        frame = {
            'mode': 'historical_batch',
            'tag': self.tag,
            'agent_id': agent_id,
            'arm': (s.meta.get('arms') or {}).get(agent_id),
            't': day,
            'date': s.days[day],
            'persona': persona,
            'feed': feed,
            'events': events,
            'account': account,
            'notice': notice or '历史批量曝光，不含逐次滚动；账户为日终估值',
            'following': None,
            'recommendations': [],
        }
        return copy.deepcopy(frame)

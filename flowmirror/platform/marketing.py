"""Institution-owned marketing actions with lagged, aggregate platform feedback.

Built-in policies are transparent rules, not LLM agents or learned dynamics.
They can publish or wait; they cannot mutate investor choices or market prices.
"""
from __future__ import annotations

import copy
import math
import re

from flowmirror.market.tape import instant
from flowmirror.platform.behavior import BehaviorLedger
from flowmirror.platform.public_board import PublicBoard


_CREATIVE_CARD_FIELDS = (
    'post_id', 'org', 'title', 'caption', 'arm', 'image_sha', 'ocr_text',
    'image_caption_frozen', 'image_refs', 'image_descriptions', 'market',
    'channel', 'published_at', 'time_basis',
)


def _public_creative(card):
    # 素材库仅提供公开内容；旧评论、读者身份和任意附加元数据不随转发传播。
    return {**{k: copy.deepcopy(card[k]) for k in _CREATIVE_CARD_FIELDS if k in card},
            'comments_prev': []}


def validate_marketing(config, *, phases, phase_times, handles):
    if type(phases) is not int or phases <= 0:
        raise ValueError('marketing phases must be a positive integer')
    if phase_times is not None:
        if not isinstance(phase_times, list) or len(phase_times) != phases:
            raise ValueError('phase_times must supply one timestamp per phase')
        clocks = [instant(t) for t in phase_times]
        if any(a >= b for a, b in zip(clocks, clocks[1:])):
            raise ValueError('phase_times must strictly increase')
    if not isinstance(config, list) or not config:
        raise ValueError('marketing must contain institutions')
    seen = set()
    for owner in config:
        if not isinstance(owner, dict):
            raise ValueError('marketing institution must be an object')
        identity = owner.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity) or identity in seen:
            raise ValueError('marketing institution IDs must be unique')
        seen.add(identity)
        if not isinstance(owner.get('org'), str) or not owner['org'].strip() or owner.get('market') not in ('CN', 'US'):
            raise ValueError('institution requires org and market')
        if owner.get('strategy') not in ('rotate', 'feedback_select'):
            raise ValueError('only named offline marketing strategies are implemented')
        if type(owner.get('publication_budget')) is not int or owner['publication_budget'] < 0:
            raise ValueError('publication_budget is a nonnegative count, not a currency amount')
        schedule = owner.get('publish_phases', list(range(phases)))
        if (not isinstance(schedule, list)
                or any(type(p) is not int or not 0 <= p < phases for p in schedule)
                or len(set(schedule)) != len(schedule)):
            raise ValueError('invalid marketing publish_phases')
        threshold = owner.get('min_open_rate', 0)
        if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not 0 <= threshold <= 1 or not math.isfinite(threshold)):
            raise ValueError('min_open_rate must be between zero and one')
        creatives = owner.get('creatives')
        if not isinstance(creatives, list) or not creatives:
            raise ValueError('institution requires a creative library')
        names = set()
        for item in creatives:
            if not isinstance(item, dict):
                raise ValueError('creative must be an object')
            name = item.get('id')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) or name in names:
                raise ValueError('creative IDs must be unique within an institution')
            names.add(name)
            at = item.get('available_phase', 0)
            if type(at) is not int or not 0 <= at < phases:
                raise ValueError('invalid creative available_phase')
            card = item.get('card')
            if not isinstance(card, dict):
                raise ValueError('creative card must be an object')
            if not isinstance(card.get('title'), str) or not card['title'].strip():
                raise ValueError('creative card requires a title')
            for key in ('caption', 'ocr_text', 'image_caption_frozen', 'image_sha', 'channel', 'time_basis'):
                if key in card and not isinstance(card[key], str):
                    raise ValueError('creative card ' + key + ' must be a string')
            if 'arm' in card and card['arm'] not in ('T', 'TC', 'TV'):
                raise ValueError('invalid creative arm')
            if 'image_refs' in card and (not isinstance(card['image_refs'], list)
                    or any(not isinstance(ref, str) or not ref for ref in card['image_refs'])):
                raise ValueError('creative image_refs must contain strings')
            PublicBoard([_public_creative(card)], handles)
            if card.get('org') != owner['org'] or card.get('market', owner['market']) != owner['market']:
                raise ValueError('institution cannot publish another organization or market creative')
            if 'publish_phase' in card:
                release = card['publish_phase']
                if type(release) is not int or not 0 <= release < phases:
                    raise ValueError('invalid creative publish_phase')
            if 'published_at' in card:
                if phase_times is None:
                    raise ValueError('dated creative requires phase_times')
                if 'publish_phase' in card:
                    raise ValueError('creative cannot combine published_at and publish_phase')
                instant(card['published_at'])


def choose_marketing_action(view):
    """Deterministic rule over ONLY the provided institution view."""
    if not view['scheduled']:
        return {'kind': 'wait', 'reason': 'outside_publish_schedule'}
    if view['remaining_publications'] <= 0:
        return {'kind': 'wait', 'reason': 'publication_budget_exhausted'}
    if not view.get('publication_slot_available', True):
        return {'kind': 'wait', 'reason': 'phase_publication_slot_exhausted'}
    choices = view['available_creatives']
    if not choices:
        return {'kind': 'wait', 'reason': 'no_available_creative'}
    counts, feedback = view['published_counts'], view['feedback']
    unseen = [c for c in choices if counts.get(c['id'], 0) == 0]
    if view['strategy'] == 'rotate' or unseen:
        selected = min(unseen or choices, key=lambda c: (counts.get(c['id'], 0), c['id']))
        return {'kind': 'publish', 'creative_id': selected['id'], 'reason': 'rotation' if not unseen else 'first_publication'}
    observed = [c for c in choices if feedback.get(c['id'], {}).get('open_rate') is not None]
    if not observed:
        return {'kind': 'wait', 'reason': 'no_observed_feedback'}
    selected = min(observed, key=lambda c: (-feedback[c['id']]['open_rate'], counts.get(c['id'], 0), c['id']))
    if feedback[selected['id']]['open_rate'] == 0:
        return {'kind': 'wait', 'reason': 'zero_observed_openings'}
    if feedback[selected['id']]['open_rate'] < view['min_open_rate']:
        return {'kind': 'wait', 'reason': 'observed_open_rate_below_threshold'}
    return {'kind': 'publish', 'creative_id': selected['id'], 'reason': 'past_open_rate'}


class MarketingDesk:
    def __init__(self, config, phases, phase_times):
        validate_marketing(config, phases=phases, phase_times=phase_times, handles={})
        self.config = copy.deepcopy(config)
        self.phases, self.times = phases, copy.deepcopy(phase_times)
        self.ledger = BehaviorLedger()
        self.published = []
        self.publications = {o['id']: {} for o in config}
        self.snapshots = []

    def view(self, identity, phase):
        if type(phase) is not int or not 0 <= phase < self.phases:
            raise ValueError('marketing phase is outside the run')
        owner = next((o for o in self.config if o['id'] == identity), None)
        if owner is None:
            raise ValueError('unknown marketing institution')
        own = self.publications[identity]
        available = []
        for item in owner['creatives']:
            available_phase = max(item.get('available_phase', 0), item['card'].get('publish_phase', 0))
            if available_phase > phase:
                continue
            at = item['card'].get('published_at')
            if at and instant(at) > instant(self.times[phase]):
                continue
            available.append({'id': item['id'], 'available_phase': available_phase,
                              'card': _public_creative(item['card'])})
        eligible = {c['id'] for c in available}
        feedback_posts = {post: p for post, p in own.items()
                          if p['phase'] < phase and p['creative_id'] in eligible}
        return {'institution_id': identity, 'org': owner['org'], 'market': owner['market'],
                'phase': phase, 'as_of': self.times[phase] if self.times else None,
                'feedback_through_phase': phase - 1,
                'strategy': owner['strategy'], 'min_open_rate': owner.get('min_open_rate', 0),
                'policy_kind': 'RULE',
                'scheduled': phase in owner.get('publish_phases', list(range(self.phases))),
                'publication_slot_available': not any(p['phase'] == phase for p in own.values()),
                'remaining_publications': owner['publication_budget'] - len(own),
                'published_counts': {c['id']: sum(p['creative_id'] == c['id'] for p in own.values()) for c in available},
                'available_creatives': available,
                'feedback': self.ledger.creative_feedback(feedback_posts, through_phase=phase - 1),
                'notice': 'Own aggregate platform feedback only; no investor identities, wallets or future outcomes.'}

    def apply(self, view, action):
        if not isinstance(view, dict):
            raise ValueError('institution observation must be an object')
        identity, phase = view.get('institution_id'), view.get('phase')
        current = self.view(identity, phase)
        if current != view:
            raise ValueError('institution observation changed before its action')
        if not isinstance(action, dict):
            return {'status': 'rejected', 'reason': 'unsupported_institution_action'}, None
        if action.get('institution_id', identity) != identity:
            return {'status': 'rejected', 'reason': 'forged_institution_id'}, None
        kind = action.get('kind')
        if kind == 'wait':
            return {'status': 'accepted', 'reason': 'no_publication'}, None
        if kind != 'publish':
            return {'status': 'rejected', 'reason': 'unsupported_institution_action'}, None
        item = next((c for c in current['available_creatives'] if c['id'] == action.get('creative_id')), None)
        if (not current['scheduled'] or not current['publication_slot_available']
                or current['remaining_publications'] <= 0 or item is None):
            return {'status': 'rejected', 'reason': 'unavailable_creative_or_publication_slot'}, None
        own = self.publications[identity]
        source = item['card']
        card = copy.deepcopy(source)
        card.update(post_id=f'campaign_{identity}_{len(own) + 1:06d}', source_post_id=source['post_id'],
                    source_published_at=source.get('published_at'), institution_id=identity,
                    creative_id=item['id'], publication_kind='simulated_campaign', market=current['market'])
        if self.times:
            card['published_at'] = self.times[phase]
            card['channel'] = card.get('channel') or 'simulated_campaign'
        else:
            card.pop('published_at', None)
        # A repost does not inherit comments from a different public thread.
        card['comments_prev'] = []
        own[card['post_id']] = {'creative_id': item['id'], 'phase': phase}
        self.published.append(copy.deepcopy(card))
        return {'status': 'accepted', 'reason': 'simulated_post_published', 'post_id': card['post_id']}, card

    def begin_phase(self, phase):
        if type(phase) is not int or phase != len(self.snapshots) or not 0 <= phase < self.phases:
            raise ValueError('marketing phases must begin in order, once each')
        if self.snapshots and self.snapshots[-1]['feedback_after'] is None:
            raise ValueError('finish the previous marketing phase first')
        decisions, cards = [], []
        for owner in sorted(self.config, key=lambda o: o['id']):
            view = self.view(owner['id'], phase)
            action = choose_marketing_action(copy.deepcopy(view))
            result, card = self.apply(view, action)
            decisions.append({'institution_id': owner['id'], 'before': view, 'action': action, 'result': result})
            if card is not None:
                cards.append(card)
        self.snapshots.append({'phase': phase, 'decisions': decisions, 'feedback_after': None})
        return cards

    def finish_phase(self, phase):
        if (type(phase) is not int or not self.snapshots
                or self.snapshots[-1]['phase'] != phase):
            raise ValueError('finish_phase must match the active marketing phase')
        self.snapshots[-1]['feedback_after'] = {
            o['id']: self.ledger.creative_feedback(self.publications[o['id']], through_phase=phase)
            for o in self.config}

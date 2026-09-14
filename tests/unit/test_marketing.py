"""机构规则、观察隐私与合成读者策略的离线测试，无数据库或运行目录写入。"""
import copy
import json

import pytest

from flowmirror.market.broker import SimBroker, TRADE_KINDS
from flowmirror.platform.behavior import BehaviorLedger
from flowmirror.platform.browse_cli import main
from flowmirror.platform.marketing import MarketingDesk, choose_marketing_action, validate_marketing
from flowmirror.platform.marketing_demo import marketing_demo_spec, marketing_policy
from flowmirror.platform.public_board import PublicBoard


TIMES = [f'2026-09-{day:02d}T10:00:00+08:00' for day in range(8, 14)]


def owner(identity='own', *, strategy='feedback_select', market='CN'):
    org = '虚构机构' + identity
    return {'id': identity, 'org': org, 'market': market, 'strategy': strategy,
            'publication_budget': 6, 'creatives': [
                {'id': variant, 'card': {'post_id': 'library_' + identity + '_' + variant,
                                         'org': org, 'title': '[' + variant + '] 合成素材',
                                         'market': market, 'comments_prev': []}}
                for variant in ('B', 'A')]}


def desk(config=None, times=TIMES):
    return MarketingDesk(config if config is not None else [owner()], 6, times)


def record(marketing, phase, post, *, actor='private_reader', kind='finish', accepted=True, arm='T'):
    marketing.ledger.record({'phase': phase, 'agent_id': actor,
                             'before': {'feed': [{'post_id': post}], 'detail': None},
                             'action': {'kind': kind, **({'post_id': post} if kind != 'finish' else {})},
                             'result': {'status': 'accepted' if accepted else 'rejected'}}, arm)


def test_rotate_is_deterministic_with_reordered_libraries_and_no_feedback():
    one, two = [owner(strategy='rotate')], [owner(strategy='rotate')]
    two[0]['creatives'].reverse()
    runs = []
    for config in (one, two):
        marketing = desk(config)
        assert isinstance(marketing.ledger, BehaviorLedger)
        sequence = []
        for phase in range(6):
            sequence.append(marketing.begin_phase(phase)[0]['creative_id'])
            marketing.finish_phase(phase)
        runs.append((sequence, marketing.published))
    assert runs[0] == runs[1]
    assert runs[0][0] == ['A', 'B', 'A', 'B', 'A', 'B']


def test_feedback_select_explores_then_uses_observed_open_rate_with_phase_lag():
    marketing = desk()
    first = marketing.begin_phase(0)[0]
    record(marketing, 0, first['post_id'])
    marketing.finish_phase(0)
    second = marketing.begin_phase(1)[0]
    assert [first['creative_id'], second['creative_id']] == ['A', 'B']
    for kind in ('open', 'like', 'save', 'comment'):
        record(marketing, 1, second['post_id'], kind=kind)
    assert 'B' not in marketing.view('own', 1)['feedback']
    marketing.finish_phase(1)
    third = marketing.begin_phase(2)[0]
    before = marketing.snapshots[2]['decisions'][0]['before']
    assert before['feedback_through_phase'] == 1
    assert before['feedback']['B'] == {'exposures': 1, 'opened': 1, 'opened_exposed': 1, 'like': 1,
                                        'save': 1, 'comment': 1, 'open_rate': 1}
    assert third['creative_id'] == 'B'
    assert marketing.snapshots[2]['decisions'][0]['action']['reason'] == 'past_open_rate'


def test_feedback_rank_uses_rate_not_total_openings():
    marketing = desk()
    for phase, exposures, openings in [(0, 10, 3), (1, 2, 1)]:
        card = marketing.begin_phase(phase)[0]
        for index in range(exposures):
            record(marketing, phase, card['post_id'], actor=str(index),
                   kind='open' if index < openings else 'finish')
        marketing.finish_phase(phase)
    assert marketing.begin_phase(2)[0]['creative_id'] == 'B'


def test_equal_feedback_breaks_ties_by_own_count_then_creative_id():
    marketing = desk()
    for phase in range(2):
        card = marketing.begin_phase(phase)[0]
        record(marketing, phase, card['post_id'], kind='open')
        marketing.finish_phase(phase)
    third = marketing.begin_phase(2)[0]
    assert third['creative_id'] == 'A'
    record(marketing, 2, third['post_id'], kind='open')
    marketing.finish_phase(2)
    assert marketing.begin_phase(3)[0]['creative_id'] == 'B'


@pytest.mark.parametrize('exposed,reason', [(False, 'no_observed_feedback'), (True, 'zero_observed_openings')])
def test_feedback_waits_after_exploration_without_openings(exposed, reason):
    marketing = desk()
    for phase in range(2):
        card = marketing.begin_phase(phase)[0]
        if exposed:
            record(marketing, phase, card['post_id'])
            record(marketing, phase, card['post_id'], kind='open', accepted=False)
        marketing.finish_phase(phase)
    assert marketing.begin_phase(2) == []
    assert marketing.snapshots[-1]['decisions'][0]['action'] == {'kind': 'wait', 'reason': reason}
    assert len(marketing.published) == 2
    assert marketing.view('own', 2)['remaining_publications'] == 4


def test_threshold_waits_on_positive_but_insufficient_open_rate():
    config = owner()
    config['min_open_rate'] = .6
    marketing = desk([config])
    for phase in range(2):
        card = marketing.begin_phase(phase)[0]
        record(marketing, phase, card['post_id'], actor='opened', kind='open')
        record(marketing, phase, card['post_id'], actor='skipped')
        marketing.finish_phase(phase)
    assert marketing.begin_phase(2) == []
    assert marketing.snapshots[-1]['decisions'][0]['action']['reason'] == 'observed_open_rate_below_threshold'


def test_schedule_budget_and_single_publication_slot():
    config = owner(strategy='rotate')
    config.update(publish_phases=[1, 3, 4], publication_budget=2)
    marketing = desk([config])
    for phase in range(6):
        cards = marketing.begin_phase(phase)
        assert len(cards) == (1 if phase in (1, 3) else 0)
        current = marketing.view('own', phase)
        if cards:
            assert choose_marketing_action(current)['kind'] == 'wait'
        result, duplicate = marketing.apply(current, {'kind': 'publish', 'creative_id': 'A'})
        assert result['status'] == 'rejected' and duplicate is None
        marketing.finish_phase(phase)
    assert len(marketing.published) == 2
    assert marketing.view('own', 5)['remaining_publications'] == 0


def test_wait_does_not_consume_count_and_stale_or_forged_actions_do_not_publish():
    marketing = desk()
    view = marketing.view('own', 0)
    assert marketing.apply(view, {'kind': 'wait'})[0]['status'] == 'accepted'
    assert marketing.view('own', 0) == view
    for action in (None, {'kind': 'buy'}, {'kind': 'publish', 'creative_id': 'unknown'},
                   {'kind': 'publish', 'creative_id': 'A', 'institution_id': 'competitor'}):
        assert marketing.apply(view, action)[0]['status'] == 'rejected'
    assert marketing.published == []
    assert marketing.apply(view, {'kind': 'publish', 'creative_id': 'A'})[0]['status'] == 'accepted'
    with pytest.raises(ValueError, match='observation changed'):
        marketing.apply(view, {'kind': 'publish', 'creative_id': 'B'})
    assert len(marketing.published) == 1


@pytest.mark.parametrize('mode', ['available_phase', 'publish_phase', 'published_at'])
def test_future_creative_is_absent_until_eligible_and_cannot_be_published(mode):
    config = owner(strategy='rotate')
    future = config['creatives'][0]
    if mode == 'available_phase':
        future[mode] = 2
    else:
        future['card'][mode] = TIMES[2] if mode == 'published_at' else 2
    future['card']['title'] = 'FUTURE_CREATIVE_TEXT'
    marketing = desk([config])
    for phase in range(2):
        view = marketing.view('own', phase)
        assert 'FUTURE_CREATIVE_TEXT' not in json.dumps(view)
        assert [c['id'] for c in view['available_creatives']] == ['A']
        assert marketing.apply(view, {'kind': 'publish', 'creative_id': 'B'})[0]['status'] == 'rejected'
        marketing.begin_phase(phase)
        marketing.finish_phase(phase)
    assert marketing.begin_phase(2)[0]['creative_id'] == 'B'


def test_empty_library_at_current_phase_waits_and_timezone_equality_is_eligible():
    config = owner()
    for creative in config['creatives']:
        creative['card']['published_at'] = '2026-09-09T02:00:00Z'
    marketing = desk([config])
    assert marketing.begin_phase(0) == []
    assert marketing.snapshots[0]['decisions'][0]['action']['reason'] == 'no_available_creative'
    marketing.finish_phase(0)
    assert marketing.begin_phase(1)[0]['creative_id'] == 'A'


def test_before_view_only_contains_eligible_own_public_creatives_and_aggregate_feedback():
    config = [owner(), owner('competitor')]
    source = config[0]['creatives'][1]
    source['wallet'] = {'cash': 900000, 'agent_id': 'source_reader'}
    source['card'].update(private_state={'cash': 900000}, competitor_future='SECRET_FUTURE',
                          comments_prev=[{'handle': '@source_reader', 'text': 'PRIVATE_SOURCE_COMMENT'}])
    marketing = desk(config)
    cards = marketing.begin_phase(0)
    by_institution = {c['institution_id']: c for c in cards}
    own_post = by_institution['own']['post_id']
    for kind in ('open', 'like', 'save', 'comment', 'subscribe'):
        record(marketing, 0, own_post, actor='SECRET_READER', kind=kind)
    record(marketing, 0, own_post, kind='like', accepted=False)
    for index in range(5):
        record(marketing, 0, by_institution['competitor']['post_id'], actor=str(index), kind='open')
    record(marketing, 1, own_post, actor='FUTURE_READER', kind='open')
    marketing.finish_phase(0)
    view = marketing.view('own', 1)
    assert view['feedback'] == {'A': {'exposures': 2, 'opened': 1, 'opened_exposed': 1, 'like': 1,
                                      'save': 1, 'comment': 1, 'open_rate': .5}}
    assert view['policy_kind'] == 'RULE'
    encoded = json.dumps(view, ensure_ascii=False)
    for forbidden in ('SECRET_READER', 'FUTURE_READER', 'source_reader', 'PRIVATE_SOURCE_COMMENT',
                      'SECRET_FUTURE', 'competitor', 'private_state', 'cash',
                      'subscribe', 'order_id', 'agent_id', '900000'):
        assert forbidden not in encoded
    assert own_post not in encoded  # 聚合反馈不交付单条发布或读者明细。
    assert by_institution['own']['comments_prev'] == []
    assert all('wallet' not in creative for creative in view['available_creatives'])
    assert 'private_state' not in by_institution['own']


@pytest.mark.parametrize('timed', [False, True])
def test_publication_provenance_comments_and_detached_state(timed):
    config = owner()
    source = config['creatives'][1]['card']
    source['comments_prev'] = [{'handle': '@source', 'text': 'source comment'}]
    if timed:
        source['published_at'] = TIMES[0]
    times = copy.deepcopy(TIMES) if timed else None
    marketing = desk([config], times=times)
    source['title'] = 'MUTATED_INPUT'
    if times:
        times[0] = TIMES[-1]
    card = marketing.begin_phase(0)[0]
    assert card['title'] != 'MUTATED_INPUT'
    assert card['source_post_id'] == 'library_own_A'
    assert card['source_published_at'] == (TIMES[0] if timed else None)
    assert (card['institution_id'], card['creative_id'], card['publication_kind']) == ('own', 'A', 'simulated_campaign')
    assert card['post_id'] != card['source_post_id'] and card['comments_prev'] == []
    assert card.get('published_at') == (TIMES[0] if timed else None)
    card['title'] = 'MUTATED_RESULT'
    assert marketing.published[0]['title'] != 'MUTATED_RESULT'
    before = marketing.snapshots[0]['decisions'][0]['before']
    before['available_creatives'][0]['card']['title'] = 'MUTATED_VIEW'
    assert all(c['card']['title'] != 'MUTATED_VIEW' for c in marketing.view('own', 0)['available_creatives'])


@pytest.mark.parametrize('field,value', [
    ('id', None), ('id', 'space invalid'), ('org', '  '), ('market', 'UK'),
    ('strategy', 'llm'), ('publication_budget', True), ('publication_budget', -1),
    ('publication_budget', 1.5), ('publish_phases', [[1]]), ('publish_phases', [True]),
    ('publish_phases', [1, 1]), ('publish_phases', [6]), ('publish_phases', None),
    ('min_open_rate', float('nan')), ('min_open_rate', float('inf')),
    ('min_open_rate', True), ('min_open_rate', -1), ('min_open_rate', 2), ('min_open_rate', 10 ** 1000),
    ('creatives', []), ('creatives', [None]), ('creatives', 'library'),
])
def test_malformed_institution_config_raises_value_error(field, value):
    config = owner()
    config[field] = value
    with pytest.raises(ValueError):
        desk([config])


@pytest.mark.parametrize('field,value', [('id', ''), ('id', 'bad id'), ('available_phase', True),
                                        ('available_phase', 6), ('card', None), ('card', [])])
def test_malformed_creative_config_raises_value_error(field, value):
    config = owner()
    config['creatives'][0][field] = value
    with pytest.raises(ValueError):
        desk([config])


@pytest.mark.parametrize('field,value', [('post_id', None), ('org', 'someone else'), ('market', 'US'),
                                        ('title', ''), ('title', {}), ('caption', {}), ('arm', 'bad'),
                                        ('publish_phase', True), ('publish_phase', 6),
                                        ('published_at', None), ('published_at', '2026-09-08'),
                                        ('image_refs', 'bad'), ('image_refs', [{}]),
                                        ('image_descriptions', ['no matching refs'])])
def test_malformed_card_config_raises_value_error(field, value):
    config = owner()
    config['creatives'][0]['card'][field] = value
    with pytest.raises(ValueError):
        desk([config])


def test_invalid_duplicate_ids_and_clock_configuration():
    duplicate_creatives = owner()
    duplicate_creatives['creatives'][1]['id'] = 'B'
    for config in (None, [], [None], [owner(), owner()], [duplicate_creatives]):
        with pytest.raises(ValueError):
            validate_marketing(config, phases=6, phase_times=TIMES, handles={})
    for phases, times in [(True, None), (0, None), (6, TIMES[:2]), (6, TIMES[::-1]), (6, [TIMES[0]] * 6)]:
        with pytest.raises(ValueError):
            MarketingDesk([owner()], phases, times)
    config = owner()
    config['creatives'][0]['card']['published_at'] = TIMES[0]
    with pytest.raises(ValueError, match='requires phase_times'):
        desk([config], times=None)
    config['creatives'][0]['card']['publish_phase'] = 0
    with pytest.raises(ValueError, match='cannot combine'):
        desk([config])


def test_phase_lifecycle_and_empty_schedule():
    config = owner()
    config['publish_phases'] = []
    marketing = desk([config])
    with pytest.raises(ValueError):
        marketing.finish_phase(0)
    marketing.begin_phase(0)
    with pytest.raises(ValueError):
        marketing.begin_phase(0)
    with pytest.raises(ValueError):
        marketing.begin_phase(1)
    with pytest.raises(ValueError):
        marketing.finish_phase(1)
    marketing.finish_phase(0)
    assert marketing.begin_phase(1) == []
    for phase in (-1, 6, True):
        with pytest.raises(ValueError):
            marketing.view('own', phase)
    with pytest.raises(ValueError):
        marketing.view('unknown', 0)


def test_demo_reader_selects_observed_preferences_without_fixed_post_or_actor_ids():
    spec = marketing_demo_spec()
    view = {'agent_id': 'arbitrary_reader', 'phase': 0, 'step': 0,
            'private_state': spec['agents'][0]['private_state'], 'detail': None,
            'feed': [{'post_id': 'random_a', 'creative_id': 'A', 'title': '[B] misleading title'},
                     {'post_id': 'random_b', 'creative_id': 'B', 'title': 'plain title'}]}
    assert marketing_policy(view) == {'kind': 'open', 'post_id': 'random_b'}
    view['feed'] = [{'post_id': 'renamed_source', 'title': '[B] 费用与回撤'}]
    assert marketing_policy(view)['post_id'] == 'renamed_source'
    view['feed'][0]['title'] = '[A] 回顾表现'
    assert marketing_policy(view) == {'kind': 'finish'}
    view['private_state'] = {}
    assert marketing_policy(view) == {'kind': 'finish'}


@pytest.mark.parametrize('preferred', ['A', 'B'])
def test_changing_synthetic_reader_preference_changes_next_phase_selection(preferred):
    spec = marketing_demo_spec()
    agent = spec['agents'][0]
    agent['private_state']['beliefs']['marketing_demo'].update(
        preferred_creative=preferred, preferred_title_token=f'[{preferred}]')
    marketing = MarketingDesk(spec['marketing'][:1], spec['phases'], spec['phase_times'])
    board = PublicBoard([], {agent['id']: agent['handle']})
    for phase in range(2):
        board.publish_cards(marketing.begin_phase(phase))
        session = board.open_session(agent['id'], private_state=agent['private_state'], max_steps=6)
        while not session.done:
            view = {**session.view(), 'phase': phase}
            action = marketing_policy(view)
            result = session.apply(action)
            assert result['status'] == 'accepted'
            marketing.ledger.record({'phase': phase, 'agent_id': agent['id'], 'before': view,
                                     'action': action, 'result': result}, agent['arm'])
        board.commit_session(session)
        board.advance()
        marketing.finish_phase(phase)
    assert marketing.begin_phase(2)[0]['creative_id'] == preferred


def test_demo_rules_execute_social_actions_and_distinct_order_fill_settlement_in_memory():
    """直接组合现有平台与 broker；不依赖父代理尚在改写的 _World/持久化接口。"""
    spec = marketing_demo_spec()
    assert spec['phases'] == 6 and spec['cards'] == []
    assert spec['feed_policy']['kind'] == 'recent_rotating'
    assert len(spec['marketing']) == 2 and all(len(o['creatives']) == 2 for o in spec['marketing'])
    marketing = MarketingDesk(spec['marketing'], spec['phases'], spec['phase_times'])
    board = PublicBoard([], {a['id']: a['handle'] for a in spec['agents']})
    broker = SimBroker(spec['trading'], spec['agents'], spec['market_data'], spec['phase_times'][0])
    frames, accounts = [], []
    for phase, now in enumerate(spec['phase_times']):
        broker.advance(now)
        board.publish_cards(marketing.begin_phase(phase))
        for agent in spec['agents']:
            cards = [c['post_id'] for c in marketing.published if c['market'] == agent['market']]
            session = board.open_session(agent['id'], arm=agent['arm'], private_state=agent['private_state'],
                                         page_size=spec['page_size'], max_steps=spec['max_steps'], post_ids=cards)
            while not session.done:
                view = {**session.view(), 'phase': phase, 'account': broker.view(agent['id'], now),
                        'tradable_instruments': broker.catalog(agent['id'])}
                action = marketing_policy(view)
                if action['kind'] in TRADE_KINDS:
                    result = session.apply_private_action(action, lambda a: broker.submit(agent['id'], a, now))
                else:
                    result = session.apply(action)
                assert result['status'] == 'accepted', (phase, action, result)
                frame = {'phase': phase, 'agent_id': agent['id'], 'before': view, 'action': action, 'result': result}
                frames.append(frame)
                marketing.ledger.record(frame, agent['arm'])
            board.commit_session(session)
        board.advance()
        marketing.finish_phase(phase)
        accounts.append(broker.snapshot(now))
    for identity in ('cn_demo', 'us_demo'):
        assert [c['creative_id'] for c in marketing.published if c['institution_id'] == identity] == ['A', 'B', 'B', 'B', 'B', 'B']
    kinds = {f['action']['kind'] for f in frames}
    assert {'open', 'like', 'save', 'comment', 'subscribe', 'buy', 'redeem', 'sell'} <= kinds
    assert 'follow' not in kinds and board.public_snapshot()['edges'] == []
    posts = {c['post_id'] for c in marketing.published}
    for agent in spec['agents']:
        actor = agent['id']
        assert accounts[0][actor]['orders'] == []  # A 被看到，但不会被强行打开或交易。
        assert accounts[1][actor]['orders'][0]['status'] == 'submitted'
        assert accounts[1][actor]['fees'] == 0
        assert accounts[2][actor]['orders'][0]['status'] == 'filled'
        assert accounts[2][actor]['positions'][0]['available_units'] == 0
        assert accounts[3][actor]['orders'][0]['status'] == 'settled'
        final = accounts[-1][actor]
        assert final['currency'] == ('CNY' if agent['market'] == 'CN' else 'USD')
        assert len(final['orders']) == 2 and all(o['status'] == 'settled' for o in final['orders'])
        assert final['fees'] > 0 and final['receivable'] == 0
        assert all(o['source_post_id'] in posts for o in final['orders'])
        assert accounts[3][actor]['orders'][1]['status'] == 'submitted'
        assert accounts[4][actor]['orders'][1]['status'] == 'filled'


def test_cli_accepts_marketing_demo_and_recognizes_saved_offline_policy(monkeypatch, capsys):
    calls = []
    def run(spec, out, policy, **kwargs):
        calls.append((spec, policy))
        return {'status': 'complete', 'model_calls': 0}
    monkeypatch.setattr('flowmirror.platform.browse_cli.run_browsing', run)
    assert main(['--marketing-demo', '--out', 'unused-no-files-written']) == 0
    assert calls[0][0]['policy_name'] == 'marketing-rule-v1' and calls[0][1] is marketing_policy
    monkeypatch.setattr('flowmirror.platform.browse_cli.read_checkpoint', lambda out: {'spec': calls[0][0]})
    assert main(['--resume', '--out', 'unused-no-files-written']) == 0
    assert calls[1][1] is marketing_policy
    assert '"model_calls": 0' in capsys.readouterr().out

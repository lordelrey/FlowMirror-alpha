"""精确数值用合成 checkpoint 和离线券商验证，不触碰研究 run 或源数据库。"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from flowmirror.analysis.marketing_behavior import build_report, main, summarize_marketing
from flowmirror.market.broker import SimBroker
from flowmirror.platform.behavior import BehaviorLedger
from flowmirror.platform.broker_demo import account_demo_spec
from flowmirror.platform.browse_journal import EVENTS, FORMAT, _encode


def frame(actor='a', phase=0, posts=('p',), kind='open', post='p', status='accepted', detail=None):
    return {'agent_id': actor, 'phase': phase,
            'before': {'feed': [{'post_id': p} for p in posts],
                       'detail': {'post_id': detail} if detail else None},
            'action': {'kind': kind, 'post_id': post}, 'result': {'status': status},
            'after': {'feed': [{'post_id': 'never_delivered'}], 'detail': {'post_id': 'never_delivered'}}}


def bare_state(frames=()):
    return {'format': 'flowmirror-browse-v1', 'spec': {'agents': [{'id': 'a', 'arm': 'T'}]},
            'status': 'paused', 'snapshots': [], 'frames': list(frames), 'policy_calls': 0,
            'pending': None}


def test_ledger_exact_delivery_dedup_accepted_rejected_and_unattributed_actions():
    ledger = BehaviorLedger()
    for item in [frame(posts=('p', 'p'), detail='p'), frame(kind='comments'),
                 frame(kind='like'), frame(kind='like', status='rejected'),
                 frame(actor='b', status='rejected'),
                 frame(kind='follow'), frame(kind='scroll', post=None),
                 frame(posts=(), detail='detail_only', kind='finish', post=None)]:
        ledger.record(item, 'T')
    ledger.record(frame(actor='c'), 'TV')
    ledger.record(frame(phase=1), 'T')
    rows = {(r['phase'], r['post_id'], r['arm']): r for r in ledger.report()}
    row = rows[(0, 'p', 'T')]
    assert {k: row[k] for k in ('exposures', 'opened', 'opened_exposed', 'rejected_opened', 'open_rate')} == {
        'exposures': 2, 'opened': 1, 'opened_exposed': 1, 'rejected_opened': 1, 'open_rate': .5}
    assert row['accepted'] == {'open': 1, 'comments': 1, 'like': 1}
    assert row['rejected'] == {'open': 1, 'like': 1}
    assert rows[(0, None, 'T')]['accepted'] == {'follow': 1, 'scroll': 1, 'finish': 1}
    assert rows[(0, 'detail_only', 'T')]['exposures'] == 1
    assert rows[(0, 'p', 'TV')]['open_rate'] == 1
    assert rows[(1, 'p', 'T')]['exposures'] == 1
    assert all(r['post_id'] != 'never_delivered' for r in rows.values())
    assert len(ledger.report(0, {'p'})) == 2
    assert ledger.report(-1) == []


def test_open_rate_uses_delivered_intersection_and_invalid_action_is_counted():
    ledger = BehaviorLedger()
    ledger.record(frame(posts=(), post='not_delivered'), 'T')
    ledger.record(frame(actor='b', posts=('not_delivered',), kind=['bad']), 'T')
    rows = {row['post_id']: row for row in ledger.report()}
    assert rows['not_delivered']['opened'] == 1
    assert rows['not_delivered']['exposures'] == 1
    assert rows['not_delivered']['opened_exposed'] == 0
    assert rows['not_delivered']['open_rate'] == 0
    assert rows[None]['accepted'] == {'invalid': 1}
    empty = BehaviorLedger()
    empty.record(frame(posts=()), 'T')
    assert empty.report()[0]['open_rate'] is None


def test_creative_feedback_lag_privacy_and_zero_delivery():
    ledger = BehaviorLedger()
    ledger.record(frame(actor='private_agent_1', post='private_post_1', posts=('private_post_1',)), 'T')
    ledger.record(frame(actor='private_agent_2', post='private_post_1', posts=('private_post_1',), kind='like'), 'T')
    ledger.record(frame(actor='private_agent_1', phase=1, post='private_post_1', posts=('private_post_1',)), 'T')
    trade = frame(actor='private_agent_1', post='private_post_1', posts=('private_post_1',), kind='buy')
    trade['result']['order_id'] = 'private_order_1'
    ledger.record(trade, 'T')
    publications = {'private_post_1': {'creative_id': 'creative_a', 'phase': 0},
                    'private_post_2': {'creative_id': 'creative_b', 'phase': 0},
                    'future_post': {'creative_id': 'future_creative', 'phase': 1}}
    feedback = ledger.creative_feedback(publications, through_phase=0)
    assert feedback == {
        'creative_a': {'exposures': 2, 'opened': 1, 'opened_exposed': 1, 'like': 1, 'save': 0, 'comment': 0, 'open_rate': .5},
        'creative_b': {'exposures': 0, 'opened': 0, 'opened_exposed': 0, 'like': 0, 'save': 0, 'comment': 0, 'open_rate': None}}
    assert ledger.creative_feedback(publications, through_phase=-1) == {}
    assert ledger.creative_feedback({}, through_phase=0) == {}
    assert ledger.creative_feedback(publications, through_phase=1)['creative_a']['open_rate'] == 2 / 3
    encoded = json.dumps(feedback)
    assert all(token not in encoded for token in ('private_', 'order', 'agent_id', 'post_id', 'buy', 'future_', 'T"'))


def add_marketing(state):
    state['spec']['marketing'] = [
        {'id': 'bank', 'org': '机构甲', 'market': 'CN', 'strategy': 'rotate',
         'creatives': [{'id': 'alpha', 'card': {'post_id': 'source_1', 'published_at': 'source_time'}},
                       {'id': 'unpublished', 'card': {'post_id': 'source_unused'}}]},
        {'id': 'other', 'org': '机构乙', 'market': 'US', 'strategy': 'feedback_select',
         'creatives': [{'id': 'beta', 'card': {'post_id': 'source_2'}}]}]
    state['institution_snapshots'] = [
        {'phase': 0, 'decisions': [
            {'institution_id': 'bank', 'before': {'feedback_through_phase': -1, 'strategy': 'rotate'},
             'action': {'kind': 'publish', 'creative_id': 'alpha', 'reason': 'rotation'},
             'result': {'status': 'accepted', 'post_id': 'actual_A'}},
            {'institution_id': 'other', 'before': {}, 'action': {'kind': 'wait'}, 'result': {'status': 'accepted'}}],
         'feedback_after': {'never_export_private_marker': 100}},
        {'phase': 1, 'decisions': [
            {'institution_id': 'bank', 'before': {}, 'action': {'kind': 'wait'}, 'result': {'status': 'accepted'}}]},
        {'phase': 2, 'decisions': [
            {'institution_id': 'bank', 'before': {}, 'action': {'kind': 'publish', 'creative_id': 'unpublished'},
             'result': {'status': 'rejected', 'post_id': 'rejected_post'}},
            {'institution_id': 'other', 'before': {'feedback_through_phase': 1},
             'action': {'kind': 'publish', 'creative_id': 'beta'}, 'result': {'status': 'accepted', 'post_id': 'actual_B'}}]}]


def test_campaign_provenance_and_observer_rollups_without_marketing_inputs_mutation():
    state = bare_state([frame(post='actual_A', posts=('actual_A',)),
                        frame(actor='b', phase=1, post='actual_A', posts=('actual_A',), status='rejected'),
                        frame(phase=2, kind='follow', post='actual_A', posts=('actual_A',)),
                        frame(phase=2, post='actual_B', posts=('actual_B',))])
    state['spec']['agents'].append({'id': 'b', 'arm': 'TV'})
    add_marketing(state)
    before = copy.deepcopy(state)
    report = build_report(state)
    assert state == before
    summary = summarize_marketing(state)
    assert (summary['institution_count'], summary['publication_count'], summary['wait_count']) == (2, 2, 2)
    assert summary['strategy_label'] == '离线规则策略：feedback_select, rotate'
    campaigns = {row['post_id']: row for row in report['campaign_posts']}
    assert set(campaigns) == {'actual_A', 'actual_B'}
    assert campaigns['actual_A']['source_post_id'] == 'source_1'
    assert campaigns['actual_A']['creative_id'] == 'alpha'
    assert campaigns['actual_A']['source_published_at'] == 'source_time'
    assert campaigns['actual_B']['institution_id'] == 'other'
    assert report['ledger'][0]['campaign']['post_id'] == 'actual_A'
    posts = {row['post_id']: row for row in report['per_post']}
    assert (posts['actual_A']['exposures'], posts['actual_A']['opened'], posts['actual_A']['open_rate']) == (3, 1, 1 / 3)
    assert posts[None]['accepted'] == {'follow': 1}
    assert [row['exposures'] for row in report['per_phase']] == [1, 1, 2]
    assert {row['arm']: row['exposures'] for row in report['per_arm']} == {'T': 3, 'TV': 1}
    assert 'never_export_private_marker' not in json.dumps(report)


@pytest.fixture
def account_state():
    spec = account_demo_spec()
    extra_agent = copy.deepcopy(next(a for a in spec['agents'] if a['id'] == 'reader_a'))
    extra_agent.update(id='reader_b', handle='@reader_b', arm='TC')
    spec['agents'].append(extra_agent)
    # 最后一个窗口还未到达，允许 checkpoint 同时包含 submitted 和终态订单。
    for product in spec['trading']['instruments']:
        product['execution_windows'].append({'accept_until': '2026-09-12T15:00:00+08:00',
            'observed_at': '2026-09-12T15:00:01+08:00', 'settle_at': '2026-09-13T15:00:00+08:00'})
    broker = SimBroker(spec['trading'], spec['agents'], spec['market_data'], spec['phase_times'][0])
    state = bare_state()
    state['spec'] = spec
    add_marketing(state)
    state['account_snapshots'] = [broker.snapshot(spec['phase_times'][0])]

    def act(actor, phase, action, reported_post=None):
        now = spec['phase_times'][phase]
        item = frame(actor=actor, phase=phase, posts=('actual_A', 'actual_B'))
        item['before']['account'] = broker.view(actor, now)
        item['action'] = copy.deepcopy(action)
        item['result'] = broker.submit(actor, action, now)
        if reported_post:
            item['action']['post_id'] = reported_post
        item['after']['account'] = broker.view(actor, now)
        state['frames'].append(item)
        return item['result']

    cn = {'kind': 'subscribe', 'market': 'CN', 'instrument_id': 'CN_DEMO_FUND'}
    us = {'kind': 'buy', 'market': 'US', 'instrument_id': 'US_DEMO_ETF'}
    act('reader_a', 0, {**cn, 'amount': 1000, 'post_id': 'actual_A'})
    cancelled = act('reader_b', 0, {**cn, 'amount': 100, 'post_id': 'actual_B'})
    act('reader_b', 0, {'kind': 'cancel_order', 'order_id': cancelled['order_id']})
    act('reader_c', 0, {**us, 'amount': 1000, 'post_id': 'actual_A'})
    act('reader_c', 0, {**us, 'amount': .01, 'post_id': 'actual_B'})
    broker.advance(spec['phase_times'][1])
    state['account_snapshots'].append(broker.snapshot(spec['phase_times'][1]))
    act('reader_a', 1, {'kind': 'redeem', 'market': 'CN', 'instrument_id': 'CN_DEMO_FUND',
                      'units': 100, 'post_id': 'actual_A'})
    act('reader_b', 1, {'kind': 'cancel_order', 'order_id': 'nonexistent'})
    broker.advance(spec['phase_times'][2])
    state['account_snapshots'].append(broker.snapshot(spec['phase_times'][2]))
    act('reader_a', 2, {'kind': 'redeem', 'market': 'CN', 'instrument_id': 'CN_DEMO_FUND',
                      'units': 100, 'post_id': 'actual_A'})
    broker.advance(spec['phase_times'][3])
    state['account_snapshots'].append(broker.snapshot(spec['phase_times'][3]))
    # 观察报告不得仅根据 action.post_id 为实际没有 source_post_id 的订单补链接。
    act('reader_b', 3, {**cn, 'amount': 50}, reported_post='actual_A')
    return state


def test_orders_composite_ids_status_lifecycle_requested_and_executed(account_state):
    before = copy.deepcopy(account_state)
    report = build_report(account_state)
    assert account_state == before
    trading = report['trading']
    assert trading['order_count'] == 6
    assert trading['source_linked_order_count'] == trading['campaign_linked_order_count'] == 5
    assert trading['status_counts'] == {'submitted': 1, 'filled': 1, 'settled': 2, 'cancelled': 1, 'rejected': 1}
    orders = {(o['agent_id'], o['order_id']): o for o in trading['orders']}
    assert len(orders) == 6
    cn, us = orders[('reader_a', 'order_000001')], orders[('reader_c', 'order_000001')]
    assert (cn['requested_amount'], cn['executed_gross'], cn['fees'], cn['executed_units']) == (1000, 999, 1, 989.10891089)
    assert (us['requested_amount'], us['executed_gross'], us['fees'], us['executed_units']) == (1000, 909, .91, 9)
    assert cn['requested_units'] is us['requested_units'] is None
    assert (cn['cash_delta_at_fill'], us['cash_delta_at_fill']) == (-1000, -909.91)
    assert cn['cash_delta_at_settlement'] == us['cash_delta_at_settlement'] == 0
    assert cn['lifecycle_states'] == ['submitted', 'filled', 'settled']
    sell = orders[('reader_a', 'order_000002')]
    assert (sell['phase'], sell['requested_units'], sell['executed_units'], sell['executed_gross'], sell['fees']) == (2, 100, 100, 104, .21)
    assert sell['requested_amount'] is None
    assert sell['pending_receivable'] == 103.79
    assert sell['cash_delta_at_fill'] == sell['cash_delta_at_settlement'] == 0
    assert orders[('reader_b', 'order_000001')]['status'] == 'cancelled'
    assert orders[('reader_b', 'order_000001')]['executed_gross'] == 0
    assert orders[('reader_b', 'order_000002')]['source_post_id'] is None
    assert orders[('reader_b', 'order_000002')]['campaign'] is None
    assert orders[('reader_b', 'order_000002')]['phase'] == 3
    assert orders[('reader_c', 'order_000002')]['executed_units'] == 0
    assert trading['rejected_without_order_id'] == {'cancel_order': 1, 'redeem': 1}
    assert trading['actions'] == {'accepted': {'subscribe': 3, 'buy': 2, 'cancel_order': 1, 'redeem': 1},
                                  'rejected': {'redeem': 1, 'cancel_order': 1}}
    assert not any(o['order_id'] == 'nonexistent' for o in trading['orders'])
    assert {g['currency'] for g in trading['order_groups']} == {'CNY', 'USD'}
    group = next(g for g in trading['order_groups'] if g['arm'] == 'T' and g['side'] == 'buy')
    assert group['status_counts']['settled'] == 1
    assert group['lifecycle_counts'] == {'submitted': 1, 'filled': 1, 'settled': 1, 'cancelled': 0, 'rejected': 0}
    assert group['missing_values'] == {}


def test_same_currency_products_stay_separate_and_missing_cash_is_unknown(account_state):
    state = copy.deepcopy(account_state)
    extra = copy.deepcopy(state['account_snapshots'][-1]['reader_a']['orders'][0])
    extra.update(order_id='other_product_order', instrument_id='ANOTHER_FUND', amount=10,
                 gross=9.99, units=3, fee=.01)
    extra.pop('cash_delta')
    state['account_snapshots'][-1]['reader_a']['orders'].append(extra)
    report = build_report(state)
    groups = [g for g in report['trading']['order_groups'] if g['currency'] == 'CNY' and g['side'] == 'buy']
    other = next(g for g in groups if g['instrument_id'] == 'ANOTHER_FUND')
    assert other['executed_gross'] == 9.99
    assert other['cash_delta_at_fill'] is other['cash_delta_at_settlement'] is None
    assert other['missing_values'] == {'cash_delta_at_fill': 1, 'cash_delta_at_settlement': 1}
    assert any(g['instrument_id'] == 'CN_DEMO_FUND' and g['executed_gross'] == 999 for g in groups)


def test_settlement_cash_is_counted_once_and_final_snapshot_wins(account_state):
    final = copy.deepcopy(account_state['account_snapshots'][-1])
    final['reader_b'] = copy.deepcopy(account_state['frames'][-1]['after']['account'])
    sell = final['reader_a']['orders'][1]
    sell.update(status='settled', receivable=0)
    final['reader_a']['ledger'].append({'index': 5, 'agent_id': 'reader_a', 'order_id': sell['order_id'],
        'event': 'settled', 'at': '2026-09-11T10:00:00+08:00', 'cash_delta': 103.79})
    account_state['account_snapshots'].append(final)
    report = build_report(account_state)
    row = next(o for o in report['trading']['orders'] if o['side'] == 'sell')
    assert row['status'] == 'settled'
    assert row['cash_delta_at_fill'] == row['pending_receivable'] == 0
    assert row['cash_delta_at_settlement'] == 103.79
    assert row['executed_gross'] == 104 and row['fees'] == .21
    group = next(g for g in report['trading']['order_groups'] if g['side'] == 'sell')
    assert group['cash_delta_at_settlement'] == 103.79
    assert report['trading']['status_counts'] == {'submitted': 1, 'filled': 0, 'settled': 3, 'cancelled': 1, 'rejected': 1}


def test_order_group_sums_each_real_order_once(account_state):
    account = account_state['account_snapshots'][-1]['reader_a']
    repeated = copy.deepcopy(account['orders'][0])
    repeated['order_id'] = 'order_000003'
    account['orders'].append(repeated)
    history = [e for e in account['ledger'] if e['order_id'] == 'order_000001']
    account['ledger'].extend({**e, 'order_id': 'order_000003', 'index': 6 + i} for i, e in enumerate(history))
    submission = frame(actor='reader_a', kind='subscribe', post='actual_A', posts=('actual_A',))
    submission['result']['order_id'] = 'order_000003'
    account_state['frames'].append(submission)
    report = build_report(account_state)
    group = next(g for g in report['trading']['order_groups'] if g['arm'] == 'T' and g['side'] == 'buy')
    assert (group['order_count'], group['requested_amount'], group['executed_gross'], group['fees']) == (2, 2000, 1998, 2)
    assert group['executed_units'] == 1978.21782178
    assert group['cash_delta_at_fill'] == -2000
    assert group['lifecycle_counts']['settled'] == 2


def test_phase_rollups_keep_numeric_order():
    state = bare_state([frame(phase=phase) for phase in (0, 1, 2, 10, 11)])
    assert [row['phase'] for row in build_report(state)['per_phase']] == [0, 1, 2, 10, 11]


def test_no_marketing_no_trading_empty_and_pending_are_graceful():
    state = bare_state()
    state['pending'] = {'action': {'kind': 'open', 'post_id': 'pending_post'}}
    report = build_report(state)
    assert report['committed_frames'] == 0 and report['pending_excluded']
    assert report['ledger'] == report['per_phase'] == report['per_post'] == report['per_arm'] == []
    assert report['marketing']['enabled'] is False
    assert report['marketing']['institution_count'] == report['marketing']['publication_count'] == 0
    assert report['trading']['enabled'] is False
    assert report['trading']['orders'] == report['trading']['order_groups'] == []
    assert report['trading']['order_count'] == 0
    assert all(count == 0 for count in report['trading']['status_counts'].values())


def test_cli_snapshot_explicit_new_artifact_and_no_overwrite(tmp_path, account_state):
    run = tmp_path / 'run'
    run.mkdir()
    checkpoint = run / 'browse_run.json'
    checkpoint.write_text(json.dumps(account_state), encoding='utf-8')
    original = checkpoint.read_bytes()
    output = tmp_path / 'private' / 'report.json'
    assert main(['--run', str(run), '--out', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['trading']['order_count'] == 6
    assert report['marketing']['publication_count'] == 2
    saved = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(['--run', str(run), '--out', str(output)])
    assert error.value.code == 2 and output.read_bytes() == saved
    with pytest.raises(SystemExit):
        main(['--run', str(run), '--out', str(checkpoint)])
    with pytest.raises(SystemExit):
        main(['--run', str(run)])
    assert checkpoint.read_bytes() == original
    assert sorted(p.name for p in run.iterdir()) == ['browse_run.json']


def test_module_cli_reads_only_committed_journal_prefix_and_never_repairs(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    initial = bare_state()
    initial.pop('frames')
    initial['format'] = FORMAT
    header = run / 'browse_run.json'
    header.write_text(json.dumps(initial), encoding='utf-8')
    committed = frame()
    committed['index'] = 0
    pending = frame(post='uncommitted', posts=('uncommitted',))
    pending['index'] = 1
    events = run / EVENTS
    events.write_bytes(_encode({'kind': 'frame', 'value': committed})
                       + _encode({'kind': 'commit', 'frame_count': 1, 'status': 'paused', 'pending': None, 'policy_calls': 1})
                       + _encode({'kind': 'frame', 'value': pending}) + b'{"kind":"commit"')
    original = {p.name: p.read_bytes() for p in run.iterdir()}
    output = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'flowmirror.analysis.marketing_behavior',
                             '--run', str(run), '--out', str(output)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['committed_frames'] == 1
    assert [r['post_id'] for r in report['ledger']] == ['p']
    assert {p.name: p.read_bytes() for p in run.iterdir()} == original

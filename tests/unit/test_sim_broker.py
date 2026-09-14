import copy
import json
from decimal import Decimal

import pytest

from flowmirror.market.broker import SimBroker, number
from flowmirror.platform.broker_demo import account_demo_spec, account_policy
from flowmirror.platform.browse_run import run_browsing, replay_run
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.analysis.browse_observer import BrowseObserver


def make_broker(spec=None):
    spec = spec or account_demo_spec()
    return SimBroker(spec['trading'], spec['agents'], spec['market_data'], spec['phase_times'][0])


def buy(market='CN', amount=1000):
    return {'kind': 'subscribe' if market == 'CN' else 'buy', 'market': market,
            'instrument_id': 'CN_DEMO_FUND' if market == 'CN' else 'US_DEMO_ETF', 'amount': amount}


def sell(market='CN', units=100):
    return {'kind': 'redeem' if market == 'CN' else 'sell', 'market': market,
            'instrument_id': 'CN_DEMO_FUND' if market == 'CN' else 'US_DEMO_ETF', 'units': units}


def test_nav_order_reservation_price_publication_settlement_and_return():
    spec, broker = account_demo_spec(), make_broker()
    t0 = spec['phase_times'][0]
    accepted = broker.submit('reader_a', buy(), t0)
    assert accepted['reason'] == 'order_submitted_not_filled'
    initial = broker.view('reader_a', t0)
    assert initial['cash'] == 10000 and initial['available_cash'] == 9000
    assert initial['reserved_cash'] == 1000 and initial['positions'] == []
    broker.advance('2026-09-08T20:29:59+08:00')
    assert broker.view('reader_a', '2026-09-08T20:29:59+08:00')['positions'] == []
    t1 = spec['phase_times'][1]
    broker.advance(t1)
    account = broker.view('reader_a', t1)
    assert account['orders'][0]['price'] == 1.01  # never buy at previously visible NAV 1.0
    assert account['cash'] == 9000 and account['fees'] == 1
    assert account['positions'][0]['units'] == pytest.approx(989.10891089)
    assert account['positions'][0]['available_units'] == 0
    assert account['equity'] == 9999 and account['return_pct'] == pytest.approx(-.01)
    assert broker.submit('reader_a', sell(), t1)['reason'] == 'insufficient_settled_units'
    t2 = spec['phase_times'][2]
    broker.advance(t2)
    assert broker.submit('reader_a', sell(), t2)['status'] == 'accepted'
    assert broker.view('reader_a', t2)['positions'][0]['available_units'] == pytest.approx(889.10891089)
    t3 = spec['phase_times'][3]
    broker.advance(t3)
    pending = broker.view('reader_a', t3)
    assert pending['receivable'] == 103.79
    assert pending['cash'] == 9000 and pending['available_cash'] == 9000
    assert pending['orders'][1]['status'] == 'filled'
    assert pending['orders'][1]['fee'] == .21
    assert pending['realized_pnl_net'] == 2.69
    t4 = spec['phase_times'][4]
    broker.advance(t4)
    end = broker.view('reader_a', t4)
    assert end['cash'] == 9103.79 and end['receivable'] == 0
    assert all(o['status'] == 'settled' for o in end['orders'])
    assert end['pnl_since_start'] == pytest.approx(end['realized_pnl_net'] + end['unrealized_pnl_net'])
    frozen = copy.deepcopy(end)
    broker.advance(t4)
    assert broker.view('reader_a', t4) == frozen  # never apply fills/settlements twice


def test_exchange_whole_units_charge_fees_on_actual_gross_not_unused_budget():
    spec, broker = account_demo_spec(), make_broker()
    broker.submit('reader_c', buy('US'), spec['phase_times'][0])
    t1 = spec['phase_times'][1]
    broker.advance(t1)
    view = broker.view('reader_c', t1)
    order = view['orders'][0]
    assert order['units'] == 9 and order['gross'] == 909 and order['fee'] == .91
    assert view['cash'] == 14090.09 and view['reserved_cash'] == 0
    assert view['equity'] == 14999.09
    assert broker.submit('reader_c', sell('US', 1), t1)['reason'] == 'insufficient_settled_units'


def test_cash_is_reserved_across_orders_cancel_releases_and_cutoff_blocks_cancel():
    spec, broker = account_demo_spec(), make_broker()
    now = spec['phase_times'][0]
    first = broker.submit('reader_a', buy(amount=9000), now)
    assert broker.submit('reader_a', buy(amount=1001), now)['reason'] == 'insufficient_available_cash'
    assert broker.view('reader_a', now)['reserved_cash'] == 9000
    assert broker.submit('reader_a', {'kind': 'cancel_order', 'order_id': first['order_id']}, now)['status'] == 'accepted'
    assert broker.view('reader_a', now)['available_cash'] == 10000
    second = broker.submit('reader_a', buy(), now)
    cutoff = '2026-09-08T15:00:00+08:00'
    broker.advance(cutoff)
    assert broker.submit('reader_a', {'kind': 'cancel_order', 'order_id': second['order_id']}, cutoff)['reason'] == 'order_not_cancellable'
    third = broker.submit('reader_a', buy(), cutoff)
    assert third['status'] == 'accepted'
    assert broker.view('reader_a', cutoff)['orders'][-1]['window']['observed_at'].startswith('2026-09-09')


def test_fifo_reserved_lots_and_partial_cost_basis():
    spec = account_demo_spec()
    spec['agents'][0]['account']['positions'] = [
        {'market': 'CN', 'instrument_id': 'CN_DEMO_FUND', 'units': 60, 'unit_cost': .8, 'acquired_at': '2026-08-01T00:00:00+08:00'},
        {'market': 'CN', 'instrument_id': 'CN_DEMO_FUND', 'units': 40, 'unit_cost': 1.2, 'acquired_at': '2026-09-01T00:00:00+08:00'},
    ]
    broker, t0 = make_broker(spec), spec['phase_times'][0]
    assert broker.submit('reader_a', sell(units=80), t0)['status'] == 'accepted'
    assert broker.submit('reader_a', sell(units=21), t0)['reason'] == 'insufficient_settled_units'
    assert broker.submit('reader_a', sell(units=20), t0)['status'] == 'accepted'
    broker.advance(spec['phase_times'][1])
    view = broker.view('reader_a', spec['phase_times'][1])
    assert [o['cost_basis'] for o in view['orders']] == [72, 24]
    assert view['positions'] == []
    assert view['receivable'] == 100.8  # 80.80-.16 + 20.20-.04


@pytest.mark.parametrize('action,reason', [
    ({**buy(), 'agent_id': 'reader_c'}, 'forged_agent_id'),
    (buy('US'), 'currency_mismatch_no_fx'),
    ({**buy(), 'instrument_id': 'unknown'}, 'unknown_instrument'),
    ({**buy(), 'kind': 'buy'}, 'action_incompatible_with_product'),
    (buy(amount=-1), 'invalid_quantity'),
    (buy(amount=True), 'invalid_quantity'),
    (buy(amount='Infinity'), 'invalid_quantity'),
    (buy(amount='1e500'), 'invalid_quantity'),
    (buy(amount=.001), 'amount_precision'),
    (sell(units=.000000001), 'units_precision'),
    ({'kind': 'cancel_order', 'order_id': 'unknown'}, 'unknown_own_order'),
])
def test_rejections_do_not_mutate_private_account(action, reason):
    broker, now = make_broker(), account_demo_spec()['phase_times'][0]
    before = broker.view('reader_a', now)
    assert broker.submit('reader_a', action, now)['reason'] == reason
    assert broker.view('reader_a', now) == before


def test_other_agents_activity_does_not_leak_through_private_order_ids_or_ledger_indices():
    spec, isolated, combined = account_demo_spec(), make_broker(), make_broker()
    now = spec['phase_times'][0]
    combined.submit('reader_c', buy('US'), now)
    combined.submit('author_b', buy(), now)
    isolated.submit('reader_a', buy(), now)
    combined.submit('reader_a', buy(), now)
    assert isolated.view('reader_a', now) == combined.view('reader_a', now)
    next_time = spec['phase_times'][4]
    isolated.advance(next_time)
    combined.advance(next_time)
    assert isolated.view('reader_a', next_time) == combined.view('reader_a', next_time)


def test_absent_quote_keeps_order_pending_and_never_uses_later_or_old_nav():
    spec = account_demo_spec()
    spec['market_data'] = [r for r in spec['market_data']
                           if not (r['market'] == 'CN' and r['observed_at'].startswith('2026-09-08'))]
    broker, now = make_broker(spec), spec['phase_times'][0]
    broker.submit('reader_a', buy(), now)
    broker.advance(spec['phase_times'][-1])
    view = broker.view('reader_a', spec['phase_times'][-1])
    assert view['positions'] == [] and view['reserved_cash'] == 1000
    assert view['orders'][0]['status'] == 'submitted'


def test_account_loop_resume_replay_and_private_observations(tmp_path):
    spec = account_demo_spec()
    delivered = []

    def policy(view):
        delivered.append((view['agent_id'], view['phase'], view['step']))
        assert all(o['agent_id'] == view['agent_id'] for o in view['account']['orders'])
        assert all(e['agent_id'] == view['agent_id'] for e in view['account']['ledger'])
        assert view['private_state']['cash'] == view['account']['available_cash']
        return account_policy(view)

    assert run_browsing(spec, tmp_path, policy, max_new_calls=16)['pending_orders'] == 2
    result = run_browsing(spec, tmp_path, policy, max_new_calls=100)
    assert result['settled_orders'] == 4 and result['pending_orders'] == 0
    assert len(delivered) == len(set(delivered)) == 50
    check = replay_run(tmp_path)
    assert check['identical'] and check['financially_settled']
    state = read_checkpoint(tmp_path)
    assert all('account' not in json.dumps(s) and 'order_' not in json.dumps(s) for s in state['snapshots'])
    summary = BrowseObserver(tmp_path).summary()
    assert summary['rejected'] == {'redeem': 1, 'sell': 1}
    assert [f['currency'] for f in summary['financial']['flows']] == ['CNY', 'USD']
    assert sum(f['settled_orders'] for f in summary['financial']['flows']) == 4


def test_browsing_completion_is_not_false_financial_settlement(tmp_path):
    spec = account_demo_spec()
    spec['phases'] = 1
    spec['phase_times'] = spec['phase_times'][:1]
    result = run_browsing(spec, tmp_path, account_policy, max_new_calls=100)
    assert result['status'] == 'completed' and result['pending_orders'] == 2
    assert replay_run(tmp_path)['complete']
    assert replay_run(tmp_path)['financially_settled'] is False


def test_account_snapshot_tampering_fails_replay(tmp_path):
    run_browsing(account_demo_spec(), tmp_path, account_policy, max_new_calls=10)
    state = read_checkpoint(tmp_path)
    state['account_snapshots'][0]['reader_a']['cash'] = 1
    (tmp_path / 'browse_run.json').write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError, match='account snapshots'):
        replay_run(tmp_path)


def test_unexposed_source_post_is_rejected_before_account_mutation(tmp_path):
    spec = account_demo_spec()
    result = run_browsing(spec, tmp_path, lambda v: {**buy(), 'post_id': 'not_seen'}, max_new_calls=1)
    assert result['pending_orders'] == 0
    assert read_checkpoint(tmp_path)['frames'][0]['result']['reason'] == 'source_post_not_exposed'


def test_large_account_and_small_price_do_not_overflow_decimal_context():
    from decimal import getcontext

    spec = account_demo_spec()
    spec['agents'][0]['account']['cash'] = '1e18'
    for row in spec['market_data']:
        if row['market'] == 'CN':
            row['price'] = 1e-18
    precision = getcontext().prec
    broker = make_broker(spec)
    assert broker.submit('reader_a', buy(amount='1e18'), spec['phase_times'][0])['status'] == 'accepted'
    broker.advance(spec['phase_times'][1])
    account = broker.view('reader_a', spec['phase_times'][1])
    assert account['cash'] >= 0 and account['positions'][0]['units'] > 1e35
    assert account['equity'] == pytest.approx(1e18 - account['fees'])
    assert getcontext().prec == precision
    assert broker.submit('reader_a', buy(amount='1e-1000000'), spec['phase_times'][1])['reason'] == 'invalid_quantity'


def test_first_published_execution_price_is_not_changed_by_later_correction():
    spec = account_demo_spec()
    corrected = copy.deepcopy(next(r for r in spec['market_data']
                                  if r['market'] == 'CN' and r['observed_at'].startswith('2026-09-08')))
    corrected.update(price=1.5, available_at='2026-09-09T04:00:00+08:00')
    spec['market_data'].append(corrected)
    broker = make_broker(spec)
    broker.submit('reader_a', buy(), spec['phase_times'][0])
    broker.advance(spec['phase_times'][1])
    account = broker.view('reader_a', spec['phase_times'][1])
    assert account['orders'][0]['price'] == 1.01
    assert account['positions'][0]['price'] == 1.5  # mark can update, original fill cannot


def test_missing_opening_valuation_is_unknown_not_false_zero_return():
    spec = account_demo_spec()
    spec['agents'][0]['account']['positions'] = [
        {'market': 'CN', 'instrument_id': 'CN_DEMO_FUND', 'units': 100, 'unit_cost': 1}]
    spec['market_data'] = [r for r in spec['market_data']
                           if not (r['market'] == 'CN' and r['observed_at'].startswith('2026-09-07'))]
    broker = make_broker(spec)
    account = broker.view('reader_a', spec['phase_times'][0])
    assert account['equity'] is None and account['return_pct'] is None
    broker.advance(spec['phase_times'][1])
    later = broker.view('reader_a', spec['phase_times'][1])
    assert later['equity'] == 10101 and later['return_pct'] is None


def test_financial_policy_receives_only_current_private_account_and_parses_order():
    from flowmirror.platform.browse_driver import JsonBrowsePolicy

    spec, calls = account_demo_spec(), []
    account = make_broker(spec).view('reader_a', spec['phase_times'][0])
    view = {'agent_id': 'reader_a', 'feed': [], 'account': account,
            'recent_actions': [], 'tradable_instruments': make_broker(spec).catalog('reader_a')}

    def provider(messages, **kwargs):
        calls.append(messages)
        assert kwargs['max_provider_attempts'] == 1
        assert '提交订单不等于成交或交收' in messages[0]['content']
        assert 'cancel_order' in messages[0]['content']
        assert '不包含历史' not in messages[0]['content']
        body = messages[1]['content'][0]['text']
        assert 'reader_c' not in body and 'author_b' not in body
        parsed, _ = kwargs['parser'](json.dumps(buy()))
        return {'parsed': parsed}

    policy = JsonBrowsePolicy(provider)
    assert policy(view) == buy() and len(calls) == 1

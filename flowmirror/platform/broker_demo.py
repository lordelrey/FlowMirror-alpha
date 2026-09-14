"""Labelled synthetic account-loop scenario; never produces LLM evidence."""
from flowmirror.platform.browse_cli import timed_demo_spec, scripted_policy


def account_demo_spec():
    spec = timed_demo_spec()
    spec.update(name='account-cn-us-synthetic-demo', policy_name='account-script-v1', phases=6)
    spec['phase_times'] = ['2026-09-08T10:00:00+08:00', '2026-09-09T05:00:00+08:00',
                           '2026-09-10T05:00:00+08:00', '2026-09-11T05:00:00+08:00',
                           '2026-09-12T05:00:00+08:00', '2026-09-12T10:00:00+08:00']
    spec['market_data'] = [spec['market_data'][0], spec['market_data'][2]]
    products = []
    for market, instrument, currency, kind, prices in [
        ('CN', 'CN_DEMO_FUND', 'CNY', 'fund_nav', [1.01, .99, 1.04]),
        ('US', 'US_DEMO_ETF', 'USD', 'exchange_price', [101, 99, 105]),
    ]:
        windows = []
        for day, price in zip([8, 9, 10], prices):
            if market == 'CN':
                cutoff = observed = f'2026-09-{day:02d}T15:00:00+08:00'
                available = f'2026-09-{day:02d}T20:30:00+08:00'
                settle = f'2026-09-{day + 1:02d}T10:00:00+08:00'
            else:
                cutoff = f'2026-09-{day:02d}T09:30:00-04:00'
                observed = f'2026-09-{day:02d}T09:30:01-04:00'
                available = f'2026-09-{day:02d}T09:30:02-04:00'
                settle = f'2026-09-{day + 1:02d}T16:00:00-04:00'
            windows.append({'accept_until': cutoff, 'observed_at': observed, 'settle_at': settle})
            spec['market_data'].append({'market': market, 'instrument_id': instrument, 'currency': currency,
                                       'kind': kind, 'price': price, 'observed_at': observed,
                                       'available_at': available, 'synthetic': True, 'source': 'synthetic_fixture'})
        products.append({'market': market, 'instrument_id': instrument, 'currency': currency, 'kind': kind,
                         'buy_fee_rate': .001, 'sell_fee_rate': .002,
                         'unit_decimals': 8 if market == 'CN' else 0, 'execution_windows': windows})
    spec['trading'] = {'instruments': products, 'assumption': 'synthetic explicit windows; unlimited quote liquidity'}
    for agent in spec['agents']:
        agent['account'] = {'currency': 'USD' if agent['market'] == 'US' else 'CNY',
                            'cash': agent['private_state']['cash'], 'positions': []}
    return spec


def account_policy(view):
    actor, phase, step = view['agent_id'], view['phase'], view['step']
    market = 'US' if actor == 'reader_c' else 'CN'
    instrument = 'US_DEMO_ETF' if market == 'US' else 'CN_DEMO_FUND'
    if actor in ('reader_a', 'reader_c'):
        if step == 0:
            return {'kind': 'open', 'post_id': 'p1'}
        if step == 1 and phase == 0:
            return {'kind': 'buy' if market == 'US' else 'subscribe', 'market': market,
                    'instrument_id': instrument, 'amount': 1000, 'post_id': 'p1'}
        if step == 1 and phase in (1, 2):
            # Phase 1 deliberately probes unsettled-sale refusal; phase 2 can sell.
            return {'kind': 'sell' if market == 'US' else 'redeem', 'market': market,
                    'instrument_id': instrument, 'units': 2 if market == 'US' else 100, 'post_id': 'p1'}
        if step == 2 and phase == 1:
            return {'kind': 'follow', 'handle': '@author_b'}
        return {'kind': 'finish'}
    return scripted_policy(view)

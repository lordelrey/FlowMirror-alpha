import copy

import pytest

from flowmirror.market.tape import MarketTape, instant
from flowmirror.platform.browse_cli import timed_demo_spec, scripted_policy
from flowmirror.platform.browse_run import run_browsing, replay_run
from flowmirror.platform.browse_store import read_checkpoint


def test_delayed_nav_and_cross_timezone_market_visibility():
    spec = timed_demo_spec()
    tape = MarketTape(spec['market_data'])
    assert tape.snapshot(spec['phase_times'][1], market='CN')['quotes'][0]['price'] == 1.0
    assert tape.snapshot(spec['phase_times'][2], market='CN')['quotes'][0]['price'] == 1.01
    # At 04:00 in China, the 16:00 US observation exists but is not yet available.
    assert tape.snapshot(spec['phase_times'][2], market='US')['quotes'][0]['price'] == 100
    assert tape.snapshot(spec['phase_times'][3], market='US')['quotes'][0]['price'] == 101
    assert all(q['market'] == 'US' for q in tape.snapshot(spec['phase_times'][3], market='US')['quotes'])


def test_publication_and_prices_are_replayed_in_actual_actor_views(tmp_path):
    spec = timed_demo_spec()
    assert run_browsing(spec, tmp_path, scripted_policy, max_new_calls=100)['status'] == 'completed'
    state = read_checkpoint(tmp_path)
    for frame in state['frames']:
        quotes = frame['before']['market_snapshot']['quotes']
        assert all(q['market'] == ('US' if frame['agent_id'] == 'reader_c' else 'CN') for q in quotes)
        assert all(instant(q['available_at']) <= instant(frame['before']['sim_time']) for q in quotes)
        if frame['phase'] == 0:
            assert {p['post_id'] for p in frame['before']['feed']} == {'p1'}
    assert replay_run(tmp_path)['identical'] is True


@pytest.mark.parametrize('change', [
    {'price': -1}, {'price': float('nan')}, {'price': True}, {'synthetic': None},
    {'observed_at': '2026-09-07'}, {'available_at': '2020-01-01T00:00:00Z'},
    {'kind': 'unknown'}, {'market': 'unknown'}, {'currency': None},
])
def test_bad_market_data_is_rejected(change):
    row = timed_demo_spec()['market_data'][0]
    row.update(change)
    with pytest.raises(ValueError):
        MarketTape([row])


def test_duplicate_observation_and_instrument_identity_change_rejected():
    row = timed_demo_spec()['market_data'][0]
    with pytest.raises(ValueError, match='duplicate'):
        MarketTape([row, copy.deepcopy(row)])
    other = {**row, 'available_at': '2026-09-08T20:00:00+08:00', 'currency': 'USD'}
    with pytest.raises(ValueError, match='changed'):
        MarketTape([row, other])


def test_newer_revision_only_after_publication_and_no_secret_metadata():
    old = timed_demo_spec()['market_data'][0]
    new = {**old, 'available_at': '2026-09-08T20:00:00+08:00', 'price': 0.99,
           'private_token': 'not-for-agents'}
    tape = MarketTape([old, new])
    assert tape.snapshot('2026-09-08T19:00:00+08:00')['quotes'][0]['price'] == 1.0
    latest = tape.snapshot('2026-09-08T21:00:00+08:00')['quotes'][0]
    assert latest['price'] == 0.99 and 'private_token' not in latest
    latest['price'] = 123
    assert tape.snapshot('2026-09-08T21:00:00+08:00')['quotes'][0]['price'] == 0.99

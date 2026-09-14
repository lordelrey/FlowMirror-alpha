"""Offline selected-session tests; no run, database, browser or provider access."""
import copy
import json

import pytest

from flowmirror.analysis.session_behavior import summarize_session
from flowmirror.platform.browsing import BrowseSession


def session(**kwargs):
    return BrowseSession('a', [{'post_id': p, 'title': p} for p in ('p', 'q', 'r')],
                         max_steps=20, page_size=1, **kwargs)


def act(s, kind, **fields):
    before = s.view()
    action = {'kind': kind, **fields}
    result = s.apply(action)
    return {'phase': 2, 'agent_id': 'a', 'before': before, 'action': action,
            'result': result, 'after': s.view()}


def test_repeated_delivery_and_undelivered_after_scroll():
    s = session()
    frames = [act(s, 'open', post_id='p'), act(s, 'comments', post_id='p'),
              act(s, 'scroll')]
    original = copy.deepcopy(frames)
    m = summarize_session(iter(frames), policy_mode='scripted')
    assert m['source'] == {'phase': 2, 'agent_id': 'a', 'policy_mode': 'scripted'}
    assert frames[-1]['after']['feed'][0]['post_id'] == 'q'
    assert (m['exposed_posts'], m['opened_posts'], m['opened_exposed_posts'], m['open_rate']) == (1, 1, 1, 1)
    assert m['accepted'] == {'comments': 1, 'open': 1, 'scroll': 1}
    assert m['consumed_action_steps'] == 3
    assert m['remaining_steps_after'] == 17
    assert frames == original


def test_invalid_rejected_open_and_intersection():
    s = session()
    bad = act(s, 'open', post_id='not_exposed')
    m = summarize_session([bad])
    assert m['opened_posts'] == 0 and m['open_rate'] == 0
    assert m['rejected'] == {'open': 1}
    assert m['consumed_action_steps'] == 1
    # Even inconsistent legacy accepted-open evidence cannot enlarge the numerator.
    bad['result']['status'] = 'accepted'
    m = summarize_session([bad])
    assert m['opened_posts'] == 1 and m['opened_exposed_posts'] == 0
    assert m['open_rate'] == 0


def test_follow_targets_and_zero_not_lack_of_opportunity():
    s = session(recommendations=[{'handle': 'peer', 'followers': 3}])
    idle = act(s, 'open', post_id='p')
    zero = summarize_session([idle])
    assert zero['following_actions']['follow'] == {'accepted': 0, 'rejected': 0}
    assert zero['following_before'] == zero['following_after'] == 0
    assert '零关注不代表没有机会' in zero['notice']
    frames = [idle, act(s, 'follow', handle='peer', post_id='p'),
              act(s, 'follow', handle='peer'), act(s, 'unfollow', handle='peer')]
    m = summarize_session(frames)
    assert m['following_actions'] == {'follow': {'accepted': 1, 'rejected': 1},
                                      'unfollow': {'accepted': 1, 'rejected': 0}}
    assert m['following_targets'] == [
        {'kind': 'follow', 'status': 'accepted', 'handle': 'peer'},
        {'kind': 'follow', 'status': 'rejected', 'handle': 'peer'},
        {'kind': 'unfollow', 'status': 'accepted', 'handle': 'peer'}]
    assert summarize_session(frames[:2])['following_after'] == 1
    assert m['following_after'] == 0


def test_pause_prefix_and_missing_after_are_not_future_state():
    s = session()
    first = act(s, 'scroll')
    prefix = summarize_session([first])
    later = act(s, 'open', post_id='q')
    assert summarize_session([first]) == prefix
    assert prefix['opened_posts'] == 0 and prefix['exposed_posts'] == 1
    assert summarize_session([first, later])['exposed_posts'] == 2
    del first['after']
    m = summarize_session([first])
    assert m['remaining_steps_after'] is None
    assert m['consumed_action_steps'] is None
    assert m['following_after'] is None


def test_no_frames_vs_observed_zero_actions():
    empty = summarize_session([])
    assert empty['observation_status'] == 'no_observations'
    assert empty['exposed_posts'] is empty['action_attempts'] is empty['consumed_action_steps'] is None
    assert empty['interactions']['like']['accepted'] is None
    observed = summarize_session([{'agent_id': 'a', 'phase': 0, 'before': session().view()}])
    assert observed['observation_status'] == 'observed'
    assert observed['action_attempts'] == observed['consumed_action_steps'] == 0
    assert observed['interactions']['like'] == {'accepted': 0, 'rejected': 0}
    assert observed['open_rate'] == 0


@pytest.mark.parametrize('location,key,value', [
    ('frame', 'agent_id', 'other'), ('frame', 'phase', 3),
    ('before', 'agent_id', 'other'), ('after', 'agent_id', 'other'),
    ('before', 'phase', 3), ('after', 'phase', 3)])
def test_same_actor_phase_guard(location, key, value):
    s = session()
    first, second = act(s, 'open', post_id='p'), act(s, 'like', post_id='p')
    target = second if location == 'frame' else second[location]
    target[key] = value
    with pytest.raises(ValueError, match='agent_id.*phase'):
        summarize_session([first, second])


def test_account_actions_do_not_create_profit_or_settlement_claims():
    s = session()
    frames = []
    for status in ('accepted', 'rejected'):
        action = {'kind': 'buy', 'instrument_id': 'x', 'quantity': 2}
        before = s.view()
        result = s.apply_private_action(action, lambda _: {'status': status, 'reason': 'test'})
        frames.append({'phase': 2, 'agent_id': 'a', 'before': before,
                       'after': s.view(), 'action': action, 'result': result})
    for f in frames:
        f['before']['account'] = {'equity': 123456789, 'currency': 'USD'}
        f['after']['account'] = {'equity': 987654321, 'currency': 'CNY'}
    m = summarize_session(frames, policy_mode='external')
    assert m['trading_actions']['buy'] == {'accepted': 1, 'rejected': 1}
    assert m['consumed_action_steps'] == 2
    encoded = json.dumps(m)
    assert all(v not in encoded for v in ('123456789', '987654321', 'equity', 'profit', 'return_pct'))
    assert '交易接受不等于成交' in m['notice']
    assert 'external 标签本身不证明 LLM 调用' in m['notice']


def test_closed_session_rejection_does_not_consume_step():
    s = session()
    m = summarize_session([act(s, 'finish'), act(s, 'open', post_id='p')])
    assert m['action_attempts'] == 2 and m['consumed_action_steps'] == 1
    assert m['rejected'] == {'open': 1}


def test_interactions_count_acceptance_not_repeated_state():
    s = session()
    frames = [act(s, 'open', post_id='p'), act(s, 'like', post_id='p'),
              act(s, 'like', post_id='p'), act(s, 'save', post_id='p'),
              act(s, 'comment', post_id='p', text='hello')]
    m = summarize_session(frames)
    assert m['interactions'] == {'like': {'accepted': 1, 'rejected': 1},
                                 'save': {'accepted': 1, 'rejected': 0},
                                 'comment': {'accepted': 1, 'rejected': 0}}
    json.dumps(m, allow_nan=False)

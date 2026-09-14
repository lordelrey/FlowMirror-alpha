import copy
import json

import pytest

from flowmirror.platform.browse_cli import demo_spec, main, scripted_policy
from flowmirror.platform import browse_run
from flowmirror.platform.browse_run import normalize_spec, replay_run, run_browsing
from flowmirror.platform.browse_store import RunBusy, read_checkpoint, writer_lock
from flowmirror.analysis.browse_observer import BrowseObserver, graph_metrics


def test_offline_pause_resume_is_identical_and_no_duplicate_calls(tmp_path):
    spec = demo_spec()
    calls = []

    def policy(view):
        calls.append((view['phase'], view['agent_id'], view['step']))
        return scripted_policy(view)

    part = run_browsing(spec, tmp_path / 'resume', policy, max_new_calls=12)
    assert part['status'] == 'paused' and part['completed_phases'] == 1
    assert replay_run(tmp_path / 'resume')['complete'] is False
    done = run_browsing(spec, tmp_path / 'resume', policy, max_new_calls=100)
    assert done['status'] == 'completed' and done['new_policy_calls'] == 24
    assert len(calls) == len(set(calls)) == 36
    assert run_browsing(spec, tmp_path / 'resume', policy, max_new_calls=100)['new_policy_calls'] == 0
    run_browsing(spec, tmp_path / 'fresh', scripted_policy, max_new_calls=100)
    assert read_checkpoint(tmp_path / 'resume') == read_checkpoint(tmp_path / 'fresh')
    before = (tmp_path / 'resume' / 'browse_run.json').read_bytes()
    assert replay_run(tmp_path / 'resume') == {
        'identical': True, 'complete': True, 'cached_steps': 36, 'model_calls': 0,
        'pending': False, 'note': 'recorded actions and public snapshots reproduced',
    }
    assert (tmp_path / 'resume' / 'browse_run.json').read_bytes() == before


def test_zero_follow_is_completed_not_a_trigger_for_synthetic_activity(tmp_path):
    spec = demo_spec(null=True)
    result = run_browsing(spec, tmp_path, lambda v: {'kind': 'finish'}, max_new_calls=100)
    assert result['cached_steps'] == 12 and result['status'] == 'completed'
    state = read_checkpoint(tmp_path)
    assert all(not s['edges'] for s in state['snapshots'])
    metrics = BrowseObserver(tmp_path).summary()
    assert metrics['accepted'] == {'finish': 12}
    assert metrics['rejected'] == {} and metrics['follow_opportunity_sessions'] == 0
    assert metrics['graph_timeline'][-1]['hhi'] is None


def test_unknown_response_stops_and_cannot_auto_retry(tmp_path):
    attempts = []

    def fail(view):
        attempts.append(view)
        raise TimeoutError('SECRET-MUST-NOT-BE-PERSISTED')

    result = run_browsing(demo_spec(), tmp_path, fail, max_new_calls=20)
    assert result['status'] == 'uncertain' and len(attempts) == 1
    again = run_browsing(demo_spec(), tmp_path, fail, max_new_calls=20)
    assert again['status'] == 'uncertain' and again['new_policy_calls'] == 0
    assert len(attempts) == 1
    content = (tmp_path / 'browse_run.json').read_text(encoding='utf-8')
    assert 'SECRET-MUST-NOT-BE-PERSISTED' not in content
    assert read_checkpoint(tmp_path)['pending']['error_type'] == 'TimeoutError'
    assert replay_run(tmp_path)['pending'] is True


def test_saved_response_recovered_without_reinvoking_policy(tmp_path, monkeypatch):
    actual_apply = browse_run._World.apply

    def crash(self, action, index):
        raise SystemExit('simulate power loss after response was persisted')

    monkeypatch.setattr(browse_run._World, 'apply', crash)
    with pytest.raises(SystemExit):
        run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=10)
    assert read_checkpoint(tmp_path)['pending']['action'] == {'kind': 'open', 'post_id': 'p1'}
    monkeypatch.setattr(browse_run._World, 'apply', actual_apply)

    def must_not_call(view):
        raise AssertionError('unexpected call')

    recovered = run_browsing(demo_spec(), tmp_path, must_not_call, max_new_calls=0)
    assert recovered['cached_steps'] == 1 and recovered['new_policy_calls'] == 0
    assert read_checkpoint(tmp_path)['pending'] is None
    assert replay_run(tmp_path)['identical']


def test_changed_spec_rejected_before_new_calls(tmp_path):
    spec = demo_spec()
    run_browsing(spec, tmp_path, scripted_policy, max_new_calls=1)
    changed = copy.deepcopy(spec)
    changed['agents'][0]['private_state']['cash'] = 9999
    with pytest.raises(ValueError, match='spec differs'):
        run_browsing(changed, tmp_path, scripted_policy, max_new_calls=100)
    assert read_checkpoint(tmp_path)['policy_calls'] == 1


@pytest.mark.parametrize('field', ['before', 'after', 'index', 'snapshots', 'policy_calls'])
def test_replay_comparison_actually_rejects_corruption(tmp_path, field):
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=2)
    state = read_checkpoint(tmp_path)
    if field in ('snapshots', 'policy_calls'):
        state[field] = [] if field == 'snapshots' else 99
    elif field == 'index':
        state['frames'][0][field] = 99
    else:
        state['frames'][0][field]['private_state']['cash'] = 9999
    (tmp_path / 'browse_run.json').write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError):
        replay_run(tmp_path)
    with pytest.raises(ValueError):
        run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=100)


def test_writer_exclusion_does_not_delete_or_take_over_existing_owner(tmp_path):
    with writer_lock(tmp_path):
        with pytest.raises(RunBusy):
            run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=2)
        assert not (tmp_path / 'browse_run.json').exists()
    result = run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=1)
    assert result['cached_steps'] == 1


def test_real_delivered_views_are_private_and_have_only_own_history(tmp_path):
    spec = demo_spec()
    markers = {a['id']: a['private_state']['memory'][0] for a in spec['agents']}

    def isolated_policy(view):
        text = json.dumps(view, ensure_ascii=False)
        assert markers[view['agent_id']] in text
        for actor, marker in markers.items():
            if actor != view['agent_id']:
                assert marker not in text
        assert all(item['result']['agent_id'] == view['agent_id'] for item in view['recent_actions'])
        response = scripted_policy(view)
        # Mutating a delivered copy must not change the saved observation/state.
        view['private_state']['cash'] = -123456
        return response

    assert run_browsing(spec, tmp_path, isolated_policy, max_new_calls=100)['status'] == 'completed'
    state = read_checkpoint(tmp_path)
    assert all(f['before']['private_state']['cash'] >= 0 for f in state['frames'])
    for marker in markers.values():
        assert marker not in json.dumps(state['snapshots'], ensure_ascii=False)
    assert replay_run(tmp_path)['identical']


def test_cold_start_public_lag_follow_reward_recommendation_and_unfollow(tmp_path):
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=100)
    state = read_checkpoint(tmp_path)
    assert [len(s['edges']) for s in state['snapshots']] == [0, 0, 1, 2, 1]
    phase0 = [f for f in state['frames'] if f['phase'] == 0]
    assert all(f['before']['recommendations'] == [] for f in phase0)
    assert all(not (f['before']['detail'] or {}).get('comments_prev') for f in phase0)
    reader_phase2 = next(f for f in state['frames'] if f['phase'] == 2 and f['agent_id'] == 'reader_c')
    assert reader_phase2['before']['recommendations'] == [{'handle': '@author_b', 'followers': 1}]
    promoted = next(f for f in state['frames'] if f['phase'] == 2 and f['agent_id'] == 'reader_a')
    assert promoted['after']['detail']['comments_prev'][0]['handle'] == '@author_b'
    assert promoted['after']['detail']['comments_prev'][0]['followed'] is True
    observer = BrowseObserver(tmp_path)
    phase1 = observer.agent_trace('reader_a', 1)
    assert phase1['public_before']['edges'] == []  # never show future graph as current
    assert observer.summary()['accepted']['follow'] == 2
    assert observer.summary()['rejected'] == {}


def test_metrics_denominators_and_zero_graph():
    empty = graph_metrics({'epoch': 0, 'followers': {'a': 0, 'b': 0}, 'edges': []})
    assert empty['top1_share'] is None and empty['gini_all_agents'] is None
    full = graph_metrics({'epoch': 1, 'followers': {'a': 0, 'b': 2, 'c': 0}, 'edges': [{}, {}]})
    assert full['gini_all_agents'] == pytest.approx(2 / 3)
    assert full['hhi'] == 1 and full['top1_share'] == 1


@pytest.mark.parametrize('value', [-1, True, 1.5])
def test_budget_validation_is_before_output_creation(tmp_path, value):
    out = tmp_path / 'unused'
    with pytest.raises(ValueError):
        run_browsing(demo_spec(), out, scripted_policy, max_new_calls=value)
    assert not out.exists()


def test_cli_external_policy_never_silently_substitutes_or_opens_network(tmp_path):
    spec = demo_spec()
    spec['policy_mode'] = 'external'
    path = tmp_path / 'spec.json'
    path.write_text(json.dumps(spec), encoding='utf-8')
    with pytest.raises(SystemExit):
        main(['--spec', str(path), '--out', str(tmp_path / 'output')])
    assert not (tmp_path / 'output').exists()


def test_unknown_agents_and_future_phase_have_no_private_trace(tmp_path):
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=1)
    observer = BrowseObserver(tmp_path)
    assert observer.agent_trace('reader_a', 3)['frames'] == []
    assert observer.agent_trace('reader_a', 3)['public_before'] is None
    with pytest.raises(ValueError):
        observer.agent_trace('unknown', 0)
    with pytest.raises(ValueError):
        observer.agent_trace('reader_a', 4)


def test_scheduled_institution_post_is_not_visible_before_release(tmp_path):
    spec = demo_spec(null=True)
    spec['cards'][0]['publish_phase'] = 2
    # Choose finish so each phase records its starting feed without fabricating clicks.
    run_browsing(spec, tmp_path, lambda v: {'kind': 'finish'}, max_new_calls=100)
    state = read_checkpoint(tmp_path)
    for frame in state['frames']:
        cards = {p['post_id'] for p in frame['before']['feed']}
        if frame['phase'] < 2:
            assert 'p1' not in cards
            assert 'p1' not in state['snapshots'][frame['phase']]['comments']
        else:
            assert 'p1' in state['snapshots'][frame['phase']]['comments']
    assert replay_run(tmp_path)['identical']


def test_publishing_batch_is_atomic_and_cannot_change_active_phase():
    from flowmirror.platform.public_board import PublicBoard
    board = PublicBoard([], {'a': '@a'})
    with pytest.raises(ValueError):
        board.publish_cards([{'post_id': 'p1'}, {'post_id': ''}])
    assert board.public_snapshot()['comments'] == {}
    session = board.open_session('a')
    with pytest.raises(ValueError, match='active phase'):
        board.publish_cards([{'post_id': 'p2'}])
    session.apply({'kind': 'finish'})
    board.commit_session(session)
    board.advance()
    board.publish_cards([{'post_id': 'p2'}])
    assert 'p2' in board.public_snapshot()['comments']

import copy
import json

import pytest

from flowmirror.platform.browse_cli import demo_spec
from flowmirror.platform.browse_run import normalize_spec, replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.analysis.browse_observer import BrowseObserver


def spec_for_test():
    spec = demo_spec()
    spec.update(phases=5, page_size=20, cards=[])
    for actor in spec['agents']:
        actor['market'] = 'CN'
    spec['marketing'] = [{'id': 'fund_a', 'org': '虚构机构甲', 'market': 'CN',
                          'strategy': 'feedback_select', 'publication_budget': 4,
                          'creatives': [
                              {'id': name, 'card': {'post_id': 'original_' + name,
                                                   'org': '虚构机构甲', 'title': name,
                                                   'comments_prev': []}}
                              for name in ('A', 'B')]}]
    return spec


def choose_b(view):
    if view['step'] == 0:
        posts = [p for p in view['feed'] if p.get('creative_id') == 'B']
        if posts:
            return {'kind': 'open', 'post_id': posts[-1]['post_id']}
    return {'kind': 'finish'}


@pytest.mark.parametrize('storage', ['snapshot', 'journal'])
def test_institution_adapts_on_lagged_feedback_and_resume_replays(tmp_path, storage):
    spec = spec_for_test()
    part = run_browsing(spec, tmp_path / 'resumed', choose_b, max_new_calls=5, storage=storage)
    assert part['status'] == 'paused'
    assert replay_run(tmp_path / 'resumed')['identical']
    done = run_browsing(spec, tmp_path / 'resumed', choose_b, max_new_calls=100)
    assert done['status'] == 'completed' and done['model_calls'] == 0
    assert done['institution_rule_decisions'] == 5 and done['simulated_publications'] == 4
    run_browsing(spec, tmp_path / 'fresh', choose_b, max_new_calls=100, storage=storage)
    left, right = (read_checkpoint(tmp_path / name) for name in ('resumed', 'fresh'))
    assert list(left['frames']) == list(right['frames'])
    assert left['institution_snapshots'] == right['institution_snapshots']
    decisions = [s['decisions'][0] for s in left['institution_snapshots']]
    assert [d['action'].get('creative_id') for d in decisions] == ['A', 'B', 'B', 'B', None]
    assert decisions[0]['before']['feedback'] == {}
    assert decisions[1]['before']['feedback']['A']['open_rate'] == 0
    assert decisions[2]['before']['feedback']['B']['open_rate'] == 1
    assert decisions[4]['action']['reason'] == 'publication_budget_exhausted'
    assert all(d['before']['feedback_through_phase'] == i - 1 for i, d in enumerate(decisions))
    replay = replay_run(tmp_path / 'resumed')
    assert replay['identical'] and replay['complete'] and replay['institution_decisions'] == 5
    again = run_browsing(spec, tmp_path / 'resumed', lambda v: pytest.fail('new policy call'), max_new_calls=0)
    assert again['new_policy_calls'] == 0


def test_institution_private_library_and_feedback_are_not_in_investor_views(tmp_path):
    spec = spec_for_test()
    spec['marketing'][0]['creatives'].append({'id': 'future', 'available_phase': 4,
        'card': {'post_id': 'FUTURE_SOURCE_SENTINEL', 'org': '虚构机构甲', 'title': 'FUTURE_TEXT_SENTINEL'}})
    run_browsing(spec, tmp_path, choose_b, max_new_calls=100)
    state = read_checkpoint(tmp_path)
    for frame in state['frames']:
        text = json.dumps(frame['before'], ensure_ascii=False)
        assert 'remaining_publications' not in text and 'available_creatives' not in text
        if frame['phase'] < 4:
            assert 'FUTURE_SOURCE_SENTINEL' not in text and 'FUTURE_TEXT_SENTINEL' not in text
        for actor in spec['agents']:
            if actor['id'] != frame['agent_id']:
                assert actor['private_state']['memory'][0] not in text
    private = json.dumps(state['institution_snapshots'], ensure_ascii=False)
    for actor in spec['agents']:
        assert actor['private_state']['memory'][0] not in private
        assert actor['id'] not in private
    trace = BrowseObserver(tmp_path).agent_trace('reader_a', 1)
    assert trace['institution_before']['phase'] == 1
    assert 'feedback_after' not in trace['institution_before']
    assert 'B' not in trace['institution_before']['decisions'][0]['before']['feedback']


def test_replay_rejects_changed_institution_record(tmp_path):
    run_browsing(spec_for_test(), tmp_path, choose_b, max_new_calls=100)
    state = read_checkpoint(tmp_path)
    state['institution_snapshots'][2]['decisions'][0]['before']['feedback']['B']['opened'] += 1
    (tmp_path / 'browse_run.json').write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError, match='institution snapshots differ'):
        replay_run(tmp_path)


@pytest.mark.parametrize('storage', ['snapshot', 'journal'])
def test_all_wait_empty_feeds_complete_without_investor_calls(tmp_path, storage):
    spec = spec_for_test()
    spec['marketing'][0]['publication_budget'] = 0
    result = run_browsing(spec, tmp_path, lambda v: pytest.fail('empty feed policy invoked'),
                          max_new_calls=0, storage=storage)
    assert result['status'] == 'completed' and result['completed_phases'] == 5
    assert result['policy_calls'] == result['simulated_publications'] == 0
    assert result['institution_rule_decisions'] == 5
    assert replay_run(tmp_path)['complete']
    assert all(s['feedback_after'] == {'fund_a': {}}
               for s in read_checkpoint(tmp_path)['institution_snapshots'])


def test_campaign_namespace_collision_precedes_output_creation(tmp_path):
    spec = spec_for_test()
    spec['cards'] = [{'post_id': 'campaign_fund_a_000001'}]
    with pytest.raises(ValueError, match='collides'):
        run_browsing(spec, tmp_path / 'unused', choose_b, max_new_calls=100)
    assert not (tmp_path / 'unused').exists()


def test_market_filter_and_source_provenance(tmp_path):
    spec = spec_for_test()
    other = copy.deepcopy(spec['marketing'][0])
    other.update(id='fund_us', org='虚构US', market='US')
    for creative in other['creatives']:
        creative['card']['org'] = other['org']
    spec['marketing'].append(other)
    spec['agents'][-1]['market'] = 'US'
    run_browsing(spec, tmp_path, choose_b, max_new_calls=100)
    state = read_checkpoint(tmp_path)
    for frame in state['frames']:
        expected = 'fund_us' if frame['agent_id'] == 'reader_c' else 'fund_a'
        for card in frame['before']['feed']:
            assert card['institution_id'] == expected
            assert card['publication_kind'] == 'simulated_campaign'
            assert card['source_post_id'].startswith('original_')
    assert replay_run(tmp_path)['identical']

"""Offline marketing observer regressions; generated sources live only in tmp_path."""
import json
import os

import pytest

from flowmirror.analysis.browse_index import IndexUnavailable, build_index, load_index
from flowmirror.analysis.browse_observer import BrowseObserver
from flowmirror.platform.browse_journal import EVENTS, JournalFrames
from flowmirror.platform.browse_run import replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.platform.marketing_demo import marketing_demo_spec, marketing_policy


def completed(tmp_path, *, interval=1, empty=None):
    spec = marketing_demo_spec()
    spec['offline_checkpoint_interval'] = interval
    if empty == 'all_wait':
        for owner in spec['marketing']:
            owner['publication_budget'] = 0
    elif empty == 'later_release':
        for owner in spec['marketing']:
            for creative in owner['creatives']:
                creative['available_phase'] = 2
    out, cache = tmp_path / 'run', tmp_path / 'index.json'
    result = run_browsing(spec, out, marketing_policy, max_new_calls=1000, storage='journal')
    assert result['status'] == 'completed' and result['model_calls'] == 0
    build_index(out, cache)
    return out, cache, spec


def source_bytes(out):
    return {path: path.read_bytes() for path in (out / 'browse_run.json', out / EVENTS)}


def traces(observer, spec):
    return {(actor['id'], phase): observer.agent_trace(actor['id'], phase)
            for actor in spec['agents'] for phase in range(spec['phases'])}


@pytest.mark.parametrize('interval', [1, 1000])
def test_marketing_index_matches_complete_summary_snapshots_and_every_trace(tmp_path, monkeypatch, interval):
    out, cache, spec = completed(tmp_path, interval=interval)
    before = source_bytes(out)
    before[cache] = cache.read_bytes()
    source = BrowseObserver(out)
    expected_summary, expected_traces = source.summary(), traces(source, spec)
    snapshots = {key: list(source.state[key]) for key in
                 ('snapshots', 'account_snapshots', 'institution_snapshots')}
    assert len(snapshots['institution_snapshots']) == spec['phases']
    assert len(snapshots['snapshots']) == len(snapshots['account_snapshots']) == spec['phases'] + 1
    assert replay_run(out)['identical']
    monkeypatch.setattr(JournalFrames, 'load', lambda *_a, **_k: pytest.fail('unexpected source scan'))
    indexed = BrowseObserver(out, cache)
    assert indexed.indexed and indexed.summary() == expected_summary
    for key, expected in snapshots.items():
        assert list(indexed.state[key]) == expected
        assert indexed.state[key][-1] == expected[-1]
    for (actor, phase), expected in expected_traces.items():
        actual = indexed.agent_trace(actor, phase)
        assert indexed.indexed and actual == expected
        institution = actual['institution_before']
        assert institution['phase'] == phase and 'feedback_after' not in institution
        assert all(d['before']['feedback_through_phase'] == phase - 1
                   for d in institution['decisions'])
        assert len(actual['account_history']) == phase + 1
    final = indexed.state['institution_snapshots'][-1]
    assert final['phase'] == spec['phases'] - 1 and final['feedback_after']
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('empty', ['all_wait', 'later_release'])
def test_authoritative_empty_sessions_keep_safe_source_fallback(tmp_path, empty):
    out, cache, spec = completed(tmp_path, empty=empty)
    state = read_checkpoint(out)
    assert len(state['frames'].sessions) < len(spec['agents']) * spec['phases']
    missing_phases = range(spec['phases']) if empty == 'all_wait' else range(2)
    for actor in spec['agents']:
        for phase in missing_phases:
            assert list(state['frames'].select(actor['id'], phase)) == []
    if empty == 'all_wait':
        assert len(state['frames']) == 0 and state['policy_calls'] == 0
    else:
        assert len(state['frames']) > 0
    before = source_bytes(out)
    before[cache] = cache.read_bytes()
    # No relaxation based solely on the presence of a marketing spec: an absent
    # session in a derived file is not proof that its source session was empty.
    assert load_index(out, cache) is None
    observer, source = BrowseObserver(out, cache), BrowseObserver(out)
    assert not observer.indexed and observer.summary() == source.summary()
    assert traces(observer, spec) == traces(source, spec)
    assert replay_run(out)['identical']
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('target,replacement', [(0, 1), (5, 0)])
def test_wrong_institution_phase_offset_is_rejected_and_trace_falls_back(tmp_path, target, replacement):
    out, cache, spec = completed(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    locations = data['snapshot_locations']['institution_snapshots']
    locations[target] = locations[replacement]
    cache.write_text(json.dumps(data), encoding='utf-8')
    before = source_bytes(out)
    before[cache] = cache.read_bytes()
    observer = BrowseObserver(out, cache)
    assert observer.indexed
    with pytest.raises(IndexUnavailable, match='different phase'):
        observer.state['institution_snapshots'][target]
    actor = spec['agents'][0]['id']
    assert observer.agent_trace(actor, target) == BrowseObserver(out).agent_trace(actor, target)
    assert not observer.indexed
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('key', ['snapshots', 'account_snapshots', 'institution_snapshots'])
def test_truncated_marketing_snapshot_history_is_rejected(tmp_path, key):
    out, cache, _ = completed(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    data['snapshot_locations'][key].pop()
    cache.write_text(json.dumps(data), encoding='utf-8')
    assert load_index(out, cache) is None


@pytest.mark.parametrize('corruption', ['missing', 'misrouted'])
def test_marketing_does_not_bypass_session_coverage_or_actor_identity(tmp_path, corruption):
    out, cache, spec = completed(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    actor, other = spec['agents'][0]['id'], spec['agents'][1]['id']
    first = next(row for row in data['sessions'] if row[:2] == [actor, 0])
    second = next(row for row in data['sessions'] if row[:2] == [other, 0])
    if corruption == 'missing':
        data['sessions'].remove(first)
    else:
        first[2], second[2] = second[2], first[2]
    cache.write_text(json.dumps(data), encoding='utf-8')
    observer = BrowseObserver(out, cache)
    assert observer.indexed == (corruption == 'misrouted')
    assert observer.agent_trace(actor, 0) == BrowseObserver(out).agent_trace(actor, 0)
    assert not observer.indexed


@pytest.mark.parametrize('entrypoint', ['summary', 'trace'])
def test_changed_marketing_source_invalidates_cached_and_new_observers(tmp_path, entrypoint):
    out, cache, spec = completed(tmp_path)
    observer = BrowseObserver(out, cache)
    assert observer.indexed
    before = source_bytes(out)
    before[cache] = cache.read_bytes()
    path = out / EVENTS
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    source = BrowseObserver(out)
    if entrypoint == 'summary':
        assert observer.summary() == source.summary()
    else:
        actor, phase = spec['agents'][0]['id'], spec['phases'] - 1
        assert observer.agent_trace(actor, phase) == source.agent_trace(actor, phase)
    assert not observer.indexed and load_index(out, cache) is None
    assert observer.summary() == source.summary()
    assert all(path.read_bytes() == content for path, content in before.items())

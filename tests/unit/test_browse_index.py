import json
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from flowmirror.analysis import browse_index
from flowmirror.analysis.browse_index import build_index
from flowmirror.analysis.browse_observer import BrowseObserver
from flowmirror.platform.browse_cli import demo_spec, scripted_policy
from flowmirror.platform.broker_demo import account_demo_spec, account_policy
from flowmirror.platform.browse_run import run_browsing, replay_run
from flowmirror.platform.browse_journal import JournalFrames, EVENTS
from flowmirror.platform.browse_store import read_checkpoint
from web.community_server import make_server, _State


def completed(tmp_path, account=False):
    out = tmp_path / 'runs' / 'probe'
    spec, policy = (account_demo_spec(), account_policy) if account else (demo_spec(), scripted_policy)
    run_browsing(spec, out, policy, max_new_calls=100, storage='journal')
    cache = tmp_path / 'indexes' / 'probe.json'
    build_index(out, cache)
    return out, cache, spec


@pytest.mark.parametrize('account', [False, True])
def test_cached_projection_equals_every_source_trace_without_scanning_the_log(tmp_path, monkeypatch, account):
    out, cache, spec = completed(tmp_path, account)
    source = BrowseObserver(out)
    expected_summary = source.summary()
    expected = {(a['id'], p): source.agent_trace(a['id'], p)
                for a in spec['agents'] for p in range(spec['phases'])}
    monkeypatch.setattr(JournalFrames, 'load', lambda *a: (_ for _ in ()).throw(AssertionError('full scan')))
    cached = BrowseObserver(out, cache)
    assert cached.indexed and cached.summary() == expected_summary
    for key, trace in expected.items():
        assert cached.agent_trace(*key) == trace
    for actor, phase in [('not-an-agent', 0), ('reader_a', spec['phases'])]:
        with pytest.raises(ValueError):
            cached.agent_trace(actor, phase)


@pytest.mark.parametrize('corrupt', ['invalid_json', 'version', 'offset', 'sessions', 'missing_session', 'summary'])
def test_bad_index_falls_back_without_mutating_source_or_cache(tmp_path, corrupt):
    out, cache, _ = completed(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    if corrupt == 'invalid_json':
        cache.write_text('{broken', encoding='utf-8')
    else:
        if corrupt == 'version': data['source_version'][1][1] += 1
        if corrupt == 'offset': data['offsets'][0] = -1
        if corrupt == 'sessions': data['sessions'][0][2].append(0)
        if corrupt == 'missing_session': data['sessions'].pop()
        if corrupt == 'summary': data['summary']['model_calls'] = 999
        cache.write_text(json.dumps(data), encoding='utf-8')
    before = {p: p.read_bytes() for p in [cache, out / 'browse_run.json', out / EVENTS]}
    cached = BrowseObserver(out, cache)
    assert not cached.indexed and cached.summary() == BrowseObserver(out).summary()
    assert all(p.read_bytes() == value for p, value in before.items())


def test_misrouted_frame_cannot_return_another_actors_private_state(tmp_path):
    out, cache, _ = completed(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    a = next(row for row in data['sessions'] if row[:2] == ['reader_a', 0])
    b = next(row for row in data['sessions'] if row[:2] == ['author_b', 0])
    a[2], b[2] = b[2], a[2]  # valid partition, deliberately wrong actor mapping
    cache.write_text(json.dumps(data), encoding='utf-8')
    cached = BrowseObserver(out, cache)
    assert cached.indexed
    trace = cached.agent_trace('reader_a', 0)
    assert not cached.indexed
    assert trace == BrowseObserver(out).agent_trace('reader_a', 0)
    assert 'B的私有便签' not in json.dumps(trace, ensure_ascii=False)


def test_future_snapshot_pointer_is_not_shown_as_past(tmp_path):
    out, cache, _ = completed(tmp_path, account=True)
    for key in ['snapshots', 'account_snapshots']:
        data = json.loads(cache.read_text(encoding='utf-8'))
        data['snapshot_locations'][key][0] = data['snapshot_locations'][key][2]
        cache.write_text(json.dumps(data), encoding='utf-8')
        cached = BrowseObserver(out, cache)
        assert cached.indexed
        trace = cached.agent_trace('reader_a', 0)
        assert not cached.indexed
        assert trace == BrowseObserver(out).agent_trace('reader_a', 0)


def test_source_update_invalidates_existing_and_new_cached_observers(tmp_path):
    out, cache, _ = completed(tmp_path)
    cached = BrowseObserver(out, cache)
    assert cached.indexed
    path = out / EVENTS
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
    assert cached.summary() == BrowseObserver(out).summary() and not cached.indexed
    assert not BrowseObserver(out, cache).indexed


def test_missing_and_wrong_source_indexes_fall_back(tmp_path):
    out, cache, _ = completed(tmp_path)
    assert not BrowseObserver(out, tmp_path / 'missing.json').indexed
    data = json.loads(cache.read_text(encoding='utf-8'))
    data['source_run'] = str(tmp_path / 'somebody-else')
    cache.write_text(json.dumps(data), encoding='utf-8')
    assert not BrowseObserver(out, cache).indexed


def test_index_generation_is_outside_source_and_never_overwrites(tmp_path, monkeypatch):
    out, cache, _ = completed(tmp_path)
    before = cache.read_bytes()
    with pytest.raises(FileExistsError): build_index(out, cache)
    with pytest.raises(ValueError, match='outside'): build_index(out, out / 'index.json')
    assert cache.read_bytes() == before and not (out / 'index.json').exists()
    actual_version = browse_index.source_version
    calls = []
    def changing(root):
        value = actual_version(root)
        calls.append(1)
        if len(calls) > 1: value[1][1] += 1
        return value
    monkeypatch.setattr(browse_index, 'source_version', changing)
    with pytest.raises(ValueError, match='source changed'): build_index(out, tmp_path / 'new.json')
    assert not (tmp_path / 'new.json').exists()


@pytest.mark.parametrize('mode', ['paused', 'snapshot', 'unfinished_tail'])
def test_non_completed_or_legacy_sources_keep_normal_reader(tmp_path, mode):
    out = tmp_path / 'run'
    run_browsing(demo_spec(), out, scripted_policy, max_new_calls=2 if mode == 'paused' else 100,
                 storage='snapshot' if mode == 'snapshot' else 'journal')
    if mode == 'unfinished_tail':
        with (out / EVENTS).open('ab') as stream: stream.write(b'{"unfinished":')
    cache = tmp_path / 'cache.json'
    with pytest.raises(ValueError, match='clean completed'): build_index(out, cache)
    assert not cache.exists()
    assert BrowseObserver(out, cache).summary()['frames'] > 0


def test_replay_ignores_indexes_and_still_detects_real_source_corruption(tmp_path, monkeypatch):
    out, cache, _ = completed(tmp_path)
    monkeypatch.setattr(browse_index, 'load_index', lambda *a: (_ for _ in ()).throw(AssertionError('cache used')))
    assert replay_run(out)['identical']
    records = [json.loads(line) for line in (out / EVENTS).read_text(encoding='utf-8').splitlines()]
    next(r['value'] for r in records if r['kind'] == 'frame')['after']['private_state']['cash'] += 1
    (out / EVENTS).write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    with pytest.raises(ValueError): replay_run(out)


def test_read_only_http_uses_index_without_serving_it(tmp_path, monkeypatch):
    out, cache, _ = completed(tmp_path)
    source_before = (out / EVENTS).read_bytes()
    cache_before = cache.read_bytes()
    monkeypatch.setattr(JournalFrames, 'load', lambda *a: (_ for _ in ()).throw(AssertionError('full scan')))
    server = make_server(tmp_path, port=0, browse_root=out.parent, browse_index_root=cache.parent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(base + '/api/community/browse/summary?run=probe') as response:
            assert json.load(response)['frames'] == 36
        with urllib.request.urlopen(base + '/api/community/browse/frame?run=probe&agent=reader_a&phase=1') as response:
            trace = json.load(response)
            assert all(f['agent_id'] == 'reader_a' for f in trace['frames'])
        for path in ['/indexes/probe.json', '/browse_events.jsonl']:
            with pytest.raises(urllib.error.HTTPError) as exc: urllib.request.urlopen(base + path)
            assert exc.value.code == 404
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()
    assert source_before == (out / EVENTS).read_bytes() and cache_before == cache.read_bytes()


def test_measurement_uses_full_source_projection_not_cache_as_ground_truth(tmp_path):
    from script.measure_browse_index import measure
    out, cache, _ = completed(tmp_path)
    result = measure(out, cache)
    assert result['frames'] == 36 and result['sampled_traces_equal'] == 12
    assert result['all_offsets_sessions_and_snapshot_locations_equal']
    assert result['source_version_unchanged'] and result['model_calls'] == 0
    with pytest.raises(ValueError, match='unavailable'):
        measure(out, tmp_path / 'absent.json')

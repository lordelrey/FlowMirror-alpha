import copy
import json
import threading
import urllib.request

import pytest

from flowmirror.platform import browse_run
from flowmirror.platform.browse_cli import demo_spec, scripted_policy, main
from flowmirror.platform.broker_demo import account_demo_spec, account_policy
from flowmirror.platform.browse_journal import FORMAT, EVENTS, JournalFrames, _encode
from flowmirror.platform.browse_run import replay_run, run_browsing
from flowmirror.platform.browse_store import read_checkpoint, checkpoint_version, writer_lock, RunBusy
from flowmirror.analysis.browse_observer import BrowseObserver
from web.community_server import make_server, _State


def materialize(state):
    return {**state, 'frames': list(state['frames'])}


@pytest.mark.parametrize('account', [False, True])
def test_journal_is_exactly_equivalent_to_snapshot_including_private_accounts(tmp_path, account):
    spec, policy = (account_demo_spec(), account_policy) if account else (demo_spec(), scripted_policy)
    old, new = tmp_path / 'snapshot', tmp_path / 'journal'
    run_browsing(spec, old, policy, max_new_calls=100)
    first = run_browsing(spec, new, policy, max_new_calls=11, storage='journal')
    assert first['cached_steps'] == 11 and first['status'] == 'paused'
    assert replay_run(new)['identical'] and not replay_run(new)['complete']
    header = (new / 'browse_run.json').read_bytes()
    before_events = (new / EVENTS).read_bytes()
    done = run_browsing(spec, new, policy, max_new_calls=100)
    assert done['status'] == 'completed'
    assert (new / 'browse_run.json').read_bytes() == header
    assert (new / EVENTS).read_bytes().startswith(before_events)
    state = read_checkpoint(new)
    assert isinstance(state['frames'], JournalFrames)
    assert not state['frames'].buffer
    assert materialize(state) == read_checkpoint(old)
    assert replay_run(new) == replay_run(old)
    assert BrowseObserver(new).summary() == BrowseObserver(old).summary()
    for agent in spec['agents']:
        for phase in range(spec['phases']):
            assert BrowseObserver(new).agent_trace(agent['id'], phase) == BrowseObserver(old).agent_trace(agent['id'], phase)


def test_unknown_external_response_is_not_retried_even_after_crash(tmp_path):
    spec = demo_spec()
    spec['policy_mode'] = 'external'
    calls = []

    def fail(view):
        calls.append(view)
        raise SystemExit('after the request may already have reached the provider')

    with pytest.raises(SystemExit):
        run_browsing(spec, tmp_path, fail, max_new_calls=100, storage='journal')
    cached = read_checkpoint(tmp_path)
    assert cached['policy_calls'] == 1 and cached['pending']['action'] is None
    result = run_browsing(spec, tmp_path, fail, max_new_calls=100)
    assert result['status'] == 'uncertain' and result['new_policy_calls'] == 0
    assert len(calls) == 1
    assert replay_run(tmp_path)['pending']


def test_cached_external_response_survives_uncommitted_frame_and_torn_control(tmp_path, monkeypatch):
    spec = demo_spec()
    spec['policy_mode'] = 'external'
    save = JournalFrames.save
    calls = []
    tail = []

    def crash(self, state):
        if self.buffer:
            data = _encode({'kind': 'frame', 'value': self.buffer[0]}) + b'{"kind":"commit"'
            with self.path.open('ab') as stream:
                stream.write(data)
            tail.append(data)
            raise SystemExit('frame not yet committed')
        return save(self, state)

    def policy(view):
        calls.append(view['agent_id'])
        return scripted_policy(view)

    monkeypatch.setattr(JournalFrames, 'save', crash)
    with pytest.raises(SystemExit):
        run_browsing(spec, tmp_path, policy, max_new_calls=100, storage='journal')
    original = (tmp_path / EVENTS).read_bytes()
    observed = read_checkpoint(tmp_path)
    assert len(observed['frames']) == 0 and observed['pending']['action']['kind'] == 'open'
    assert replay_run(tmp_path)['pending']
    assert (tmp_path / EVENTS).read_bytes() == original  # readers did not repair
    monkeypatch.setattr(JournalFrames, 'save', save)
    recovered = run_browsing(spec, tmp_path, policy, max_new_calls=0)
    assert recovered['cached_steps'] == 1 and recovered['new_policy_calls'] == 0
    assert len(calls) == 1 and read_checkpoint(tmp_path)['pending'] is None
    backups = list(tmp_path.glob('browse_events.uncommitted.*.jsonl'))
    assert len(backups) == 1 and backups[0].read_bytes() == tail[0]
    assert recovered['recovered_uncommitted_tail'] == backups[0].name
    assert replay_run(tmp_path)['identical']


def test_incomplete_request_record_never_causes_a_policy_call_before_persistence(tmp_path, monkeypatch):
    save = JournalFrames.save
    spec = demo_spec()
    spec['policy_mode'] = 'external'
    calls = []

    def failed_request(self, state):
        if state['status'] == 'calling':
            with self.path.open('ab') as stream:
                stream.write(b'{"kind":"commit","pending":')
            raise OSError('disk unavailable')
        return save(self, state)

    monkeypatch.setattr(JournalFrames, 'save', failed_request)
    with pytest.raises(OSError):
        run_browsing(spec, tmp_path, lambda v: calls.append(v), max_new_calls=1, storage='journal')
    assert not calls and read_checkpoint(tmp_path)['pending'] is None
    monkeypatch.setattr(JournalFrames, 'save', save)
    assert run_browsing(spec, tmp_path, scripted_policy, max_new_calls=1)['cached_steps'] == 1


def test_offline_batch_commits_only_saved_prefix(tmp_path, monkeypatch):
    spec = demo_spec()
    spec['offline_checkpoint_interval'] = 7
    count = 0

    def power_loss(view):
        nonlocal count
        count += 1
        if count == 10:
            raise SystemExit('power loss between offline batches')
        return scripted_policy(view)

    with pytest.raises(SystemExit):
        run_browsing(spec, tmp_path, power_loss, max_new_calls=100, storage='journal')
    assert len(read_checkpoint(tmp_path)['frames']) == 7
    assert replay_run(tmp_path)['identical']
    result = run_browsing(spec, tmp_path, scripted_policy, max_new_calls=100)
    assert result['cached_steps'] == 36 and result['new_policy_calls'] == 29
    assert replay_run(tmp_path)['complete']


def test_index_reads_only_selected_session_not_the_entire_population(tmp_path, monkeypatch):
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=100, storage='journal')
    observer = BrowseObserver(tmp_path)
    # A session query must work when a full frame iteration is forbidden.
    monkeypatch.setattr(JournalFrames, '__iter__', lambda self: (_ for _ in ()).throw(AssertionError('full scan')))
    trace = observer.agent_trace('reader_a', 1)
    assert len(trace['frames']) == 5
    assert all(f['agent_id'] == 'reader_a' and f['phase'] == 1 for f in trace['frames'])
    assert 'B的私有便签' not in json.dumps(trace, ensure_ascii=False)
    assert observer.agent_trace('reader_a', 0)['public_before']['edges'] == []


@pytest.mark.parametrize('field', ['before', 'after', 'index', 'frame_count'])
def test_committed_corruption_stops_before_any_policy_call(tmp_path, field):
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=2, storage='journal')
    path = tmp_path / EVENTS
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    if field == 'frame_count':
        records[-1]['frame_count'] += 3
    else:
        frame = next(r['value'] for r in records if r['kind'] == 'frame')
        if field == 'index':
            frame['index'] = 100
        else:
            frame[field]['private_state']['cash'] += 1
    path.write_bytes(b''.join(_encode(record) for record in records))
    calls = []
    with pytest.raises(ValueError):
        replay_run(tmp_path)
    with pytest.raises(ValueError):
        run_browsing(demo_spec(), tmp_path, lambda v: calls.append(v), max_new_calls=5)
    assert not calls


def test_journal_single_writer_and_changed_spec_protections_remain(tmp_path):
    with writer_lock(tmp_path):
        with pytest.raises(RunBusy):
            run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=2, storage='journal')
    run_browsing(demo_spec(), tmp_path, scripted_policy, max_new_calls=2, storage='journal')
    saved = (tmp_path / EVENTS).read_bytes()
    changed = demo_spec()
    changed['page_size'] = 1
    with pytest.raises(ValueError, match='spec differs'):
        run_browsing(changed, tmp_path, scripted_policy, max_new_calls=2)
    assert (tmp_path / EVENTS).read_bytes() == saved


def test_cli_defaults_to_journal_and_preserves_legacy_on_resume(tmp_path):
    assert main(['--demo', '--out', str(tmp_path / 'new'), '--max-new-calls', '2']) == 0
    assert json.loads((tmp_path / 'new' / 'browse_run.json').read_text(encoding='utf-8'))['format'] == FORMAT
    run_browsing(demo_spec(), tmp_path / 'old', scripted_policy, max_new_calls=1)
    assert main(['--resume', '--out', str(tmp_path / 'old'), '--max-new-calls', '1']) == 0
    assert not (tmp_path / 'old' / EVENTS).exists()


def test_observer_cache_updates_with_events_while_header_is_unchanged(tmp_path):
    root = tmp_path / 'runs'
    out = root / 'probe'
    run_browsing(demo_spec(), out, scripted_policy, max_new_calls=2, storage='journal')
    service = _State(tmp_path, None, root)
    first = service.browse_observer(out)
    assert first.summary()['frames'] == 2
    header = (out / 'browse_run.json').stat().st_mtime_ns
    version = checkpoint_version(out)
    run_browsing(demo_spec(), out, scripted_policy, max_new_calls=1)
    assert (out / 'browse_run.json').stat().st_mtime_ns == header
    assert version != checkpoint_version(out)
    assert service.browse_observer(out).summary()['frames'] == 3
    assert first.summary()['frames'] == 2  # prior observer snapshot stays consistent


def test_http_journal_trace_and_raw_files_are_read_only(tmp_path):
    root = tmp_path / 'runs'
    out = root / 'probe'
    run_browsing(demo_spec(), out, scripted_policy, max_new_calls=100, storage='journal')
    before = (out / EVENTS).read_bytes()
    server = make_server(tmp_path, port=0, browse_root=root)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url + '/api/community/browse/frame?run=probe&agent=reader_c&phase=2') as response:
            trace = json.load(response)
        assert all(f['agent_id'] == 'reader_c' for f in trace['frames'])
        assert 'A的私有便签' not in json.dumps(trace, ensure_ascii=False)
        for raw in ['/browse_events.jsonl', '/runs/browse_out/probe/browse_events.jsonl']:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + raw)
            assert error.value.code == 404
        assert (out / EVENTS).read_bytes() == before
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_storage_measurement_requires_offline_policy_and_compares_real_records(tmp_path):
    from script.measure_browse_storage import measure, compare_recorded, process_peak_bytes

    spec = demo_spec()
    spec['policy_mode'] = 'external'
    path = tmp_path / 'spec.json'
    path.write_text(json.dumps(spec), encoding='utf-8')
    with pytest.raises(ValueError, match='only the named offline'):
        measure(path, tmp_path / 'no-run', max_new_calls=10, storage='journal')
    assert not (tmp_path / 'no-run').exists()
    for storage in ('journal', 'snapshot'):
        run_browsing(demo_spec(), tmp_path / storage, scripted_policy, max_new_calls=2, storage=storage)
    assert compare_recorded(tmp_path / 'journal', tmp_path / 'snapshot') == {'identical': True, 'frames_compared': 2}
    peak = process_peak_bytes()
    assert peak is None or peak > 0
    run_browsing(demo_spec(), tmp_path / 'journal', scripted_policy, max_new_calls=1)
    with pytest.raises(ValueError, match='differs'):
        compare_recorded(tmp_path / 'journal', tmp_path / 'snapshot')


def test_resolved_event_path_cannot_escape_run_directory(tmp_path, monkeypatch):
    from pathlib import Path
    from flowmirror.platform.browse_journal import event_path

    actual = Path.resolve
    outside = tmp_path / 'outside' / EVENTS

    def redirected(path, *args, **kwargs):
        if path.name == EVENTS:
            return outside
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    with pytest.raises(ValueError, match='escapes'):
        event_path(tmp_path / 'run')

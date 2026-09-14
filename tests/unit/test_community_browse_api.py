import json
import threading
import urllib.error
import urllib.request

import pytest

from flowmirror.platform.browse_cli import demo_spec, scripted_policy
from flowmirror.platform.browse_run import run_browsing
from web.community_server import _State, make_server


@pytest.fixture
def local_viewer(tmp_path):
    root = tmp_path / 'browse'
    run_browsing(demo_spec(), root / 'probe', scripted_policy, max_new_calls=100)
    server = make_server(tmp_path / 'historical', port=0, browse_root=root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, root
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def get(server, path, *, method='GET', host=None):
    headers = {} if host is None else {'Host': host}
    req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode('utf-8')
    except urllib.error.HTTPError as response:
        return response.code, response.read().decode('utf-8')


def test_persisted_api_returns_only_selected_actor_trace(local_viewer):
    server, root = local_viewer
    before = (root / 'probe' / 'browse_run.json').read_bytes()
    status, body = get(server, '/api/community/browse/runs')
    assert status == 200 and json.loads(body)['runs'] == [{'tag': 'probe'}]
    status, body = get(server, '/api/community/browse/summary?run=probe')
    summary = json.loads(body)
    assert status == 200 and summary['frames'] == 36
    assert 'private_state' not in body
    status, body = get(server, '/api/community/browse/frame?run=probe&agent=reader_a&phase=1')
    trace = json.loads(body)
    assert status == 200 and len(trace['frames']) == 5
    assert all(f['agent_id'] == 'reader_a' for f in trace['frames'])
    assert 'B的私有便签' not in json.dumps(trace, ensure_ascii=False)
    assert trace['public_before']['edges'] == []
    assert len(trace['behavior_timeline']) == len(trace['frames'])
    assert trace['behavior_timeline'][0]['action_attempts'] == 1
    assert trace['behavior_timeline'][0]['following_actions']['follow']['accepted'] == 0
    assert trace['behavior']['following_actions']['follow']['accepted'] == 1
    assert 'cash' not in json.dumps(trace['behavior'])
    assert (root / 'probe' / 'browse_run.json').read_bytes() == before


@pytest.mark.parametrize('path,expected', [
    ('/api/community/browse/summary?run=..', 404),
    ('/api/community/browse/summary?run=probe%2F..', 404),
    ('/api/community/browse/summary?run=missing', 404),
    ('/api/community/browse/frame?run=probe&agent=unknown&phase=0', 400),
    ('/api/community/browse/frame?run=probe&agent=reader_a&phase=-1', 400),
    ('/runs/browse_out/probe/browse_run.json', 404),
    ('/config/api.yaml', 404),
    ('/web/community_server.py', 404),
])
def test_api_does_not_serve_arbitrary_files(local_viewer, path, expected):
    server, _ = local_viewer
    status, body = get(server, path)
    assert status == expected
    assert 'private_state' not in body and 'Traceback' not in body


def test_viewer_is_loopback_read_only_and_serves_new_static_module(local_viewer):
    server, _ = local_viewer
    assert server.server_address[0] == '127.0.0.1'
    assert get(server, '/api/community/browse/runs', method='POST')[0] == 405
    assert get(server, '/api/community/browse/runs', host='attacker.example')[0] == 403
    assert get(server, '/community_browse.js')[0] == 200
    assert get(server, '/community_account.js')[0] == 200
    assert get(server, '/community_marketing.js')[0] == 200
    assert get(server, '/community_behavior.js')[0] == 200


def test_account_api_excludes_other_accounts_and_future_history(local_viewer):
    from flowmirror.platform.broker_demo import account_demo_spec, account_policy

    server, root = local_viewer
    run_browsing(account_demo_spec(), root / 'accounts', account_policy, max_new_calls=100)
    before = (root / 'accounts' / 'browse_run.json').read_bytes()
    status, body = get(server, '/api/community/browse/frame?run=accounts&agent=reader_a&phase=1')
    trace = json.loads(body)
    assert status == 200 and len(trace['account_history']) == 2
    assert all(row['phase'] <= 1 and row['currency'] == 'CNY' for row in trace['account_history'])
    for frame in trace['frames']:
        for when in ('before', 'after'):
            account = frame[when]['account']
            assert account['currency'] == 'CNY'
            assert all(o['agent_id'] == 'reader_a' for o in account['orders'])
            assert all(e['agent_id'] == 'reader_a' for e in account['ledger'])
            assert all(not o['window']['observed_at'].startswith('2026-09-10') for o in account['orders'])
    assert (root / 'accounts' / 'browse_run.json').read_bytes() == before


def test_resolved_browse_paths_reject_outside_symlinks(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'browse_run.json').write_text('{}', encoding='utf-8')
    root = tmp_path / 'root'
    root.mkdir()
    try:
        (root / 'escape').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('platform does not permit creating test symlinks')
    state = _State(tmp_path, None, root)
    assert state.browse_path('escape') is None

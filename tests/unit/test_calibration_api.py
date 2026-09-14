import json
import threading
import urllib.request
import urllib.error

import pytest

from flowmirror.analysis.calibration_observer import CalibrationObserver
from web.community_server import make_server


def seed(root):
    path=root/'example'/'report.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'kind':'offline_temporal_calibration','label':'test','nav':{'support':{'rows':7}},
                                'private_path':'should_not_be_served'}),encoding='utf-8')
    return path


def test_only_report_summary_and_read_only(tmp_path):
    path=seed(tmp_path)
    before=path.read_bytes()
    reader=CalibrationObserver(tmp_path)
    assert reader.reports()=={'reports':[{'tag':'example'}]}
    assert reader.report('example')['nav']['support']['rows']==7
    assert 'private_path' not in reader.report('example')
    assert path.read_bytes()==before
    assert CalibrationObserver(None).reports()=={'reports':[]}


@pytest.mark.parametrize('tag',['..','.','example/report.json','example\\..','/etc/passwd','missing'])
def test_arbitrary_paths_not_accepted(tmp_path,tag):
    seed(tmp_path)
    assert CalibrationObserver(tmp_path).report(tag) is None


def test_symlink_escape(tmp_path):
    outside=tmp_path/'outside'
    seed(outside)
    root=tmp_path/'configured'
    root.mkdir()
    try:
        (root/'escape').symlink_to(outside/'example',target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation not permitted on this host')
    assert CalibrationObserver(root).reports()=={'reports':[]}


def test_malformed_and_wrong_kind_not_listed(tmp_path):
    path=seed(tmp_path)
    path.write_text('{bad',encoding='utf-8')
    assert CalibrationObserver(tmp_path).reports()=={'reports':[]}
    path.write_text('{"kind":"agent_state"}',encoding='utf-8')
    assert CalibrationObserver(tmp_path).report('example') is None


def test_http_no_post_launch_or_private_file(tmp_path):
    path=seed(tmp_path/'reports')
    before=path.read_bytes()
    server=make_server(tmp_path,port=0,calibration_root=tmp_path/'reports')
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def request(url,method='GET',host=None):
        req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+url,method=method,
                                   headers={} if host is None else {'Host':host})
        try:
            with urllib.request.urlopen(req,timeout=5) as resp:
                return resp.status,resp.read().decode('utf-8')
        except urllib.error.HTTPError as resp:
            return resp.code,resp.read().decode('utf-8')
    try:
        assert request('/api/community/calibration/reports')[0]==200
        status,body=request('/api/community/calibration/report?report=example')
        assert status==200 and json.loads(body)['nav']['support']['rows']==7
        assert 'private_path' not in body
        assert request('/api/community/calibration/report?report=example%2F..')[0]==404
        assert request('/api/community/calibration/report?report=example','POST')[0]==405
        assert request('/api/community/calibration/reports',host='attacker.example')[0]==403
        assert request('/reports/example/report.json')[0]==404
        assert path.read_bytes()==before
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

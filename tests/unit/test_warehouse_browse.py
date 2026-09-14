import json
from pathlib import Path
import sqlite3

import pytest

from flowmirror.platform.warehouse import dated, export_warehouse, build_spec, write_lines, warehouse_policy
from flowmirror.platform.browse_cli import demo_spec
from flowmirror.platform.browse_run import normalize_spec, run_browsing, replay_run
from flowmirror.platform.browse_store import read_checkpoint
from flowmirror.platform.browse_driver import JsonBrowsePolicy
from web.community_server import _State


def fixture_database(tmp_path):
    path = tmp_path / 'source.db'
    definitions = {
        'xhs_note':'note_id,org_name,title,content,note_type,submit_time,interact',
        'asset':'asset_id,market,channel,asset_kind,org_name,note_id,filing_acc,seq,abs_root,rel_path,ext',
        'asset_ocr':'asset_id,text,model,batch',
        'asset_fund_code':'asset_id,fund_code,is_valid_fund INTEGER',
        'fund_nav':'fund_code,nav_date,nav REAL',
        'fund_flow_panel':'fund_code,quarter,sub REAL,red REAL,shares REAL,aum REAL',
        'guba_post':'board_fund,pid,title,post_date,read_n INTEGER,reply_n INTEGER,is_own_board INTEGER',
        'guba_anchor':'pid,fund,publish',
        'us_filing':'acc,company,filed_date',
        'dim_org':'org_name,market,note_cnt INTEGER,asset_cnt INTEGER',
        'dim_fund':'fund_code,fund_name,fund_type,market,has_nav INTEGER,has_panel INTEGER,has_guba INTEGER',
        'pool_note':'note_id,pool_version,intent,title,caption,ocr_text,submit_time',
        'pool_image_caption':'note_id,img_index INTEGER,pool_version,caption,model',
        'note_compliance_label':'note_id,rule_id,labels_version,hit INTEGER,evidence,severity',
    }
    with sqlite3.connect(path) as db:
        for table, columns in definitions.items():
            db.execute(f'CREATE TABLE {table} ({columns})')
        db.execute('CREATE TABLE secrets (cookie TEXT)')
        db.execute("INSERT INTO secrets VALUES ('DO_NOT_EXPORT')")
        counters = json.dumps({'2026-09-01': {'likedCount':12, 'commentCount':2, 'cookie':'DO_NOT_EXPORT'}})
        db.executemany('INSERT INTO xhs_note VALUES (?,?,?,?,?,?,?)', [
            ('n1','org','known title','known body','normal','2025-11-02 12:00:00',counters),
            ('n2','org','undated','body','normal',None,counters),
            ('n3','org','future','body','normal','2025-12-02 12:00:00',counters)])
        image = tmp_path / 'real.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n')
        db.execute('INSERT INTO asset VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   ('im1','CN','xhs','inner','org','n1',None,0,str(tmp_path),'real.png','.png'))
        db.execute("INSERT INTO asset_ocr VALUES ('im1','visible OCR','model','batch')")
        db.executemany('INSERT INTO asset_fund_code VALUES (?,?,?)',[('im1','000001',1),('im1','123456',0)])
        db.executemany('INSERT INTO fund_nav VALUES (?,?,?)',[('000001','2025-11-01',1.1),('000001','2025-11-02',1.2)])
        db.executemany('INSERT INTO guba_post VALUES (?,?,?,?,?,?,?)',[
            ('000001','p1','old','1973-01-01',3,1,1),('000001','p2','other board','2025-01-01',3,1,0)])
    return path


def test_export_preserves_database_and_excludes_credentials_or_unowned_posts(tmp_path):
    source = fixture_database(tmp_path)
    before = source.read_bytes()
    out = tmp_path/'corpus'
    result = export_warehouse(source, out)
    assert source.read_bytes() == before
    assert result['coverage']['xhs_dated'] == 2
    assert result['coverage']['xhs_undated'] == 1
    assert result['exported']['guba_posts'] == 1
    assert result['exported']['local_images'] == 1
    exported = ''.join(p.read_text(encoding='utf-8') for p in out.iterdir())
    assert 'DO_NOT_EXPORT' not in exported
    assert result['exported']['fund_links'] == 1
    with pytest.raises(FileExistsError):
        export_warehouse(source, out)


def test_real_corpus_runs_with_time_visibility_and_modality_isolation(tmp_path):
    source = fixture_database(tmp_path)
    corpus = tmp_path/'corpus'
    export_warehouse(source,corpus)
    spec = build_spec(corpus,start='2025-11-01',days=3,agents=6)
    assert len(spec['cards']) == 1
    assert spec['cards'][0]['fund_codes'] == ['000001']
    root = tmp_path/'run'
    result = run_browsing(spec,root,warehouse_policy,max_new_calls=100)
    assert result['status'] == 'completed'
    state = read_checkpoint(root)
    assert all(f['phase'] >= 1 for f in state['frames'])  # no dated posts yet on day 1
    for f in state['frames']:
        view = f['before']
        for card in view['feed'] + ([view['detail']] if view['detail'] else []):
            assert card['post_id'] == 'xhs_n1'
            assert 'likedCount' not in json.dumps(card)
            if card['arm'] != 'TV': assert 'image_refs' not in card
            if card['arm'] != 'TC': assert 'ocr_text' not in card
        for quote in view['market_snapshot']['quotes']:
            assert quote['available_at'] <= view['sim_time']
    assert replay_run(root)['identical']


def test_batch_checkpointing_preserves_resume_and_never_applies_to_external(tmp_path):
    spec = demo_spec()
    spec['offline_checkpoint_interval'] = 10
    result = run_browsing(spec,tmp_path,lambda v:{'kind':'finish'},max_new_calls=5)
    assert result['status'] == 'paused' and result['cached_steps'] == 5
    assert replay_run(tmp_path)['identical']
    assert run_browsing(spec,tmp_path,lambda v:{'kind':'finish'},max_new_calls=100)['cached_steps'] == 12
    spec['policy_mode']='external'
    with pytest.raises(ValueError,match='per-request'):
        normalize_spec(spec)


def test_warehouse_images_are_delivered_by_primary_key_not_fictitious_hash():
    captured=[]
    def provider(messages,**kwargs):
        captured.append(messages)
        return {'parsed':{'kind':'finish'}}
    policy=JsonBrowsePolicy(provider,image_loader=lambda ref:'data:image/png;base64,AA==' if ref=='asset_ok' else None)
    policy({'feed':[{'post_id':'n','arm':'TV','image_refs':['asset_ok','asset_missing']}]})
    assert policy.last_delivery['visual_delivery']=='partial'
    assert policy.last_delivery['attached_refs']==['asset_ok']
    assert policy.last_delivery['missing_refs']==['asset_missing']
    assert policy.last_delivery['attached_shas']==[]
    assert len(captured[0][1]['content'])==2


def test_asset_index_cannot_serve_unlisted_paths_or_non_raster_files(tmp_path):
    images=tmp_path/'images'; images.mkdir()
    image=images/'okay.jpg'; image.write_bytes(b'fixture')
    secret=tmp_path/'private.png'; secret.write_bytes(b'private')
    script=images/'unsafe.html'; script.write_text('unsafe',encoding='utf-8')
    index=tmp_path/'index.jsonl'
    write_lines(index,[{'asset_ref':'asset_ok','path':str(image)},
                       {'asset_ref':'asset_outside','path':str(secret)},
                       {'asset_ref':'asset_script','path':str(script)}])
    state=_State(tmp_path,None,asset_index=index,asset_roots=[images])
    assert state.asset_path('asset_ok')==image
    assert state.asset_path('asset_outside') is None
    assert state.asset_path('asset_script') is None
    assert state.asset_path('../private.png') is None


@pytest.mark.parametrize('raw,expected',[('1973-01-01',None),(None,None),('nonsense',None),
    ('2025-11-01','2025-11-01T00:00:00+08:00'),('2025-11-01T10:00:00Z','2025-11-01T10:00:00+00:00')])
def test_explicit_time_handling(raw,expected):
    assert dated(raw)==expected


def test_feed_market_filter_and_matched_triplets(tmp_path):
    corpus=tmp_path/'corpus';corpus.mkdir()
    write_lines(corpus/'posts.jsonl',[
        {'post_id':f'p{i}','title':str(i),'caption':'body','market':'CN' if i<30 else 'US',
         'published_at':'2025-11-01T10:00:00+08:00','image_refs':[], 'comments_prev':[]}
        for i in range(40)])
    write_lines(corpus/'nav.jsonl',[])
    spec=build_spec(corpus,start='2025-11-02',days=1,agents=6)
    run_browsing(spec,tmp_path/'run',warehouse_policy,max_new_calls=100)
    state=read_checkpoint(tmp_path/'run')
    starts=[f['before']['feed'] for f in state['frames'] if f['step']==0]
    ids=lambda cards:[c['post_id'] for c in cards]
    assert ids(starts[0])==ids(starts[1])==ids(starts[2])
    assert ids(starts[0])!=ids(starts[3])
    assert all(c['market']=='CN' for cards in starts for c in cards)


def test_corpus_catalogue_includes_undated_items_without_forged_timeline(tmp_path):
    from flowmirror.analysis.corpus_observer import CorpusObserver
    source=fixture_database(tmp_path);out=tmp_path/'corpus'
    export_warehouse(source,out)
    observer=CorpusObserver(out)
    assert observer.page()['total']==2
    assert observer.page(timing='undated')['total']==1
    assert observer.page(start='2025-11-01',end='2025-11-30')['total']==1
    assert observer.page(timing='all',query='known body')['total']==1
    assert observer.page(channel='assets')['total']==1
    assert observer.page(offset=100)['items']==[]
    with pytest.raises(ValueError):observer.page(limit=5000)


def test_corpus_http_serves_local_material_without_database_or_file_browsing(tmp_path):
    import threading
    import urllib.request
    import urllib.error
    from web.community_server import make_server

    source=fixture_database(tmp_path);before=source.read_bytes();corpus=tmp_path/'corpus'
    export_warehouse(source,corpus)
    server=make_server(tmp_path,port=0,asset_index=corpus/'image_index.jsonl',asset_roots=[tmp_path],corpus_root=corpus)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(base+'/api/community/corpus/posts?timing=undated') as response:
            result=json.load(response)
        assert result['total']==1 and result['items'][0]['published_at'] is None
        with urllib.request.urlopen(base+'/api/community/asset/asset_im1') as response:
            assert response.headers['Content-Type']=='image/png'
            assert response.read().startswith(b'\x89PNG')
        for path in ['/source.db','/image_index.jsonl','/api/community/asset/asset_unknown']:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base+path)
            assert error.value.code==404
        assert source.read_bytes()==before
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def test_legacy_release_metadata_does_not_change_saved_agent_observations(tmp_path):
    from flowmirror.platform.browse_cli import timed_demo_spec, scripted_policy
    run_browsing(timed_demo_spec(),tmp_path,scripted_policy,max_new_calls=100)
    for frame in read_checkpoint(tmp_path)['frames']:
        assert all('published_at' not in p for p in frame['before']['feed'])
    assert replay_run(tmp_path)['identical']

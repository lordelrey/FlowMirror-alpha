from datetime import date,timedelta
import json

import pytest

from flowmirror.analysis.calibration_run import run


def test_offline_pipeline_separates_raw_rows_and_refuses_overwrite(tmp_path):
    corpus=tmp_path/'corpus'
    corpus.mkdir()
    nav=[{'fund_code':'001','nav_date':(date(2026,1,1)+timedelta(days=i)).isoformat(),'nav':1+i/100}
         for i in range(50)]
    posts=[{'post_id':'post','market':'CN','published_at':'2026-01-01T00:00:00+08:00',
            'fund_codes':['001'],'image_refs':['a']}]
    snaps=[{'post_id':'post','crawl_date':at,'counters':{'likedCount':n}} for at,n in [('2026-02-01',2),('2026-02-03',4)]]
    for name,rows in [('nav',nav),('posts',posts),('engagement_snapshot',snaps)]:
        (corpus/f'{name}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    before={p.name:p.read_bytes() for p in corpus.iterdir()}
    out=tmp_path/'new_run'
    kwargs=dict(train_until='2026-01-20T23:59:59+08:00',calibration_until='2026-02-01T23:59:59+08:00',
                start='2026-01-01',end='2026-02-28')
    result=run(corpus,out,**kwargs)
    report=json.loads((out/'report.json').read_text(encoding='utf-8'))
    assert result['receipt']['new_model_calls']==0
    assert result['receipt']['source_file_metadata_unchanged']
    assert result['receipt']['status']=='ok'
    assert report['engagement']['support']['intervals']==1
    assert 'intervals' not in report['engagement']
    assert 'row_ids' not in report['nav']['evaluation']['splits']['train']
    for name,model in report['nav']['evaluation']['models'].items():
        assert 'predictions' not in model
        assert (out/f'predictions_{name}.jsonl').is_file()
    assert (out/'dataset.jsonl').is_file()
    assert (out/'engagement_intervals.jsonl').is_file()
    assert before=={p.name:p.read_bytes() for p in corpus.iterdir()}
    files={p.name:p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError):
        run(corpus,out,**kwargs)
    assert files=={p.name:p.read_bytes() for p in out.iterdir()}

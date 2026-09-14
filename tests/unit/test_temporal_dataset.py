import copy
from datetime import date, timedelta
import json
import math

import pytest

from flowmirror.analysis.temporal_dataset import build_dataset


def write_corpus(tmp_path, nav=None, posts=None):
    if nav is None:
        nav = [{'fund_code':'001', 'nav_date':(date(2026,1,1)+timedelta(days=i)).isoformat(),
                'nav':1+i/100} for i in range(12)]
    for name, data in [('nav',nav),('posts',posts or [])]:
        (tmp_path/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in data),encoding='utf-8')
    return nav


def card(at, pid='p', codes=None):
    return {'post_id':pid,'market':'CN','published_at':at,'fund_codes':['001'] if codes is None else codes,
            'image_refs':['asset_1']}


def build(root, **kwargs):
    return build_dataset(root,start='2026-01-01',end='2026-01-31',**kwargs)


def test_actual_target_and_before_only_publications(tmp_path):
    write_corpus(tmp_path, posts=[card('2026-01-07T00:00:00+08:00'),card('2026-01-07T00:00:01+08:00','future')])
    result = build(tmp_path)
    first = result['rows'][0]
    assert first['origin_at']=='2026-01-07T00:00:00+08:00'
    assert first['target_at']=='2026-01-08T00:00:00+08:00'
    assert first['target']==pytest.approx(math.log(1.06)-math.log(1.05))
    assert first['support_post_ids']==['p']
    assert first['features']['linked_posts_7d']==1
    assert 'target' not in first['features']
    assert 'target_gap_days' not in first['features']


def test_no_future_target_or_later_post_changes_features(tmp_path):
    nav = write_corpus(tmp_path)
    before = build(tmp_path)['rows'][0]
    nav[6]['nav']=9
    write_corpus(tmp_path,nav=nav,posts=[card('2026-01-08T00:00:00+08:00')])
    after = build(tmp_path)['rows'][0]
    assert before['features']==after['features']
    assert before['target']!=after['target']


def test_missing_is_barrier_no_fill(tmp_path):
    nav = write_corpus(tmp_path)
    nav[5]['nav']=None
    write_corpus(tmp_path,nav=nav)
    result = build(tmp_path)
    assert result['rows']==[]
    assert result['support']['rows_excluded_invalid_nav']>0


def test_duplicates_collapse_conflicts_do_not_pick_last(tmp_path):
    nav=write_corpus(tmp_path)
    write_corpus(tmp_path,nav=nav+[nav[3]])
    assert len(build(tmp_path)['rows'])==6
    write_corpus(tmp_path,nav=nav+[dict(nav[3],nav=7),nav[3]])
    result=build(tmp_path)
    assert result['rows'][0]['origin_nav_date']=='2026-01-10'
    assert result['support']['nav_conflicting_rows']>0


def test_source_unchanged_and_post_conflict_excluded(tmp_path):
    write_corpus(tmp_path,posts=[card('2026-01-07T00:00:00+08:00'),card('2026-01-06T00:00:00+08:00')])
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    result=build(tmp_path)
    assert all(not r['support_post_ids'] for r in result['rows'])
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


def test_no_timezone_no_link_unknown_fund_not_included(tmp_path):
    write_corpus(tmp_path,posts=[card('2026-01-07T00:00:00'),card('2026-01-07T00:00:00+08:00','none',[]),
                                  card('2026-01-07T00:00:00+08:00','unknown',['999'])])
    report=build(tmp_path)
    assert report['support']['used_source_posts']==0
    assert report['support']['post_fund_links_without_nav']==1


def test_lower_window_exclusive_and_timezone_conversion(tmp_path):
    write_corpus(tmp_path,posts=[card('2025-12-31T00:00:00+08:00','lower'),card('2026-01-06T16:00:00+00:00','at')])
    row=build(tmp_path)['rows'][0]
    assert row['support_post_ids']==['at']


def test_target_cutoff_and_gap(tmp_path):
    nav=write_corpus(tmp_path)
    report=build_dataset(tmp_path,start='2026-01-07',end='2026-01-07')
    assert not report['rows']
    nav[6]['nav_date']='2026-03-01'
    nav=nav[:7]
    write_corpus(tmp_path,nav=nav)
    assert build_dataset(tmp_path,start='2026-01-01',end='2026-04-01')['support']['rows_excluded_gap']==1


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),True,'bad'])
def test_invalid_nav_is_not_zero_return(tmp_path,value):
    nav=write_corpus(tmp_path)
    nav[5]['nav']=value
    write_corpus(tmp_path,nav=nav)
    assert not build(tmp_path)['rows']

"""Independent dataset edge cases, using temporary JSONL only.

These are ordinary regression tests, including reproducible currently failing
cases. They deliberately do not import or test temporal_evaluation. Rejection
of malformed evidence must not silently manufacture a usable historical row.
"""
from copy import deepcopy
from datetime import date, timedelta
from itertools import permutations
import json

import pytest

from flowmirror.analysis.temporal_dataset import build_dataset


def _nav(count=14, code='001'):
    return [{'fund_code': code, 'nav_date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             'nav': 1 + i / 100} for i in range(count)]


def _post(pid='p', at='2026-01-06T12:00:00+08:00', codes=None):
    return {'post_id': pid, 'market': 'CN', 'published_at': at,
            'fund_codes': ['001'] if codes is None else codes, 'image_refs': ['asset_1']}


def _build(tmp_path, nav=None, posts=None):
    for name, rows in [('nav', _nav() if nav is None else nav), ('posts', posts or [])]:
        with (tmp_path / f'{name}.jsonl').open('w', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    try:
        return build_dataset(tmp_path, start='2026-01-01', end='2026-01-31')
    finally:
        assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_post_dictionary_boolean_integer_difference_is_a_conflict(tmp_path):
    a = _post()
    a['metadata'] = {'revision': 1}
    b = deepcopy(a)
    b['metadata']['revision'] = True
    # Different JSON source objects sharing a primary key must be excluded;
    # Python dictionary equality incorrectly equates True and 1 recursively.
    result = _build(tmp_path, posts=[a, b])
    assert result['support']['post_conflicting_keys'] == 1
    assert all('p' not in row['support_post_ids'] for row in result['rows'])


def test_float_rounding_does_not_hide_conflicting_nav_source_values(tmp_path):
    nav = _nav()
    nav[5]['nav'] = '1.00000000000000001'
    other = dict(nav[5], nav='1.00000000000000002')
    # Both strings become float(1), but are distinct exact source values.
    result = _build(tmp_path, nav=nav + [other])
    assert result['support'].get('nav_conflicting_rows', 0) > 0
    assert all(row['origin_nav_date'] >= '2026-01-12' for row in result['rows'])


def test_unknown_date_observation_cannot_silently_bridge_history(tmp_path):
    nav = _nav(count=8)
    # An observation exists but its position is unknown; skipping it silently
    # turns the surrounding observations into an apparently clean history.
    nav[3]['nav_date'] = '2026-01-INVALID'
    nav[3]['nav'] = None
    result = _build(tmp_path, nav=nav)
    assert result['support']['nav_invalid_date'] == 1
    assert result['rows'] == [], 'Unknown chronological position must not be silently bridged'


def test_timezone_conversion_overflow_is_counted_as_invalid_publication(tmp_path):
    # Valid ISO timestamp whose conversion to +08:00 exceeds datetime.max.
    result = _build(tmp_path, posts=[_post(at='9999-12-31T23:59:59-12:00')])
    assert result['support']['post_without_valid_time'] == 1
    assert all(not row['support_post_ids'] for row in result['rows'])


@pytest.mark.parametrize('invalid', [None, 0, -2, 'bad', True])
def test_invalid_and_valid_duplicate_permutations_remain_barriers(tmp_path, invalid):
    nav = _nav()
    variants = [nav[5], dict(nav[5], nav=invalid), nav[5]]
    retained = None
    for order in permutations(variants):
        result = _build(tmp_path, nav=nav[:5] + nav[6:] + list(order))
        ids = [row['row_id'] for row in result['rows']]
        assert ids
        assert result['rows'][0]['origin_nav_date'] == '2026-01-12'
        if retained is None:
            retained = ids
        assert ids == retained


def test_future_suffix_and_other_fund_do_not_change_existing_features(tmp_path):
    nav = _nav()
    before = _build(tmp_path, nav=nav, posts=[_post()])
    later_nav = deepcopy(nav)
    for row in later_nav[10:]:
        row['nav'] = 999
    later_nav += _nav(code='002')
    after = _build(tmp_path, nav=later_nav, posts=[
        _post(), _post('future', at='2026-01-25T00:00:00Z'),
        _post('other', codes=['002']),
    ])
    early = {r['row_id']: r for r in before['rows'] if r['origin_nav_date'] < '2026-01-10'}
    matched = [r for r in after['rows'] if r['row_id'] in early]
    assert len(matched) == len(early)
    for row in matched:
        assert row['features'] == early[row['row_id']]['features']
        assert row['support_post_ids'] == early[row['row_id']]['support_post_ids']


def test_offset_equivalent_times_and_microsecond_future_boundary(tmp_path):
    result = _build(tmp_path, posts=[
        _post('utc', '2026-01-06T16:00:00Z'),
        _post('west', '2026-01-06T11:00:00-05:00'),
        _post('future', '2026-01-06T16:00:00.000001Z'),
    ])
    first = result['rows'][0]
    assert set(first['support_post_ids']) == {'utc', 'west'}
    assert first['features']['linked_posts_7d'] == 2


def test_no_publications_or_unmatched_links_do_not_remove_nav_units(tmp_path):
    nav = _nav() + _nav(code='002')
    original = _build(tmp_path, nav=nav)
    linked = _build(tmp_path, nav=nav, posts=[
        _post(codes=['001', '001', 'does-not-exist']),
        _post('future', at='2027-01-01T00:00:00+08:00', codes=['002']),
    ])
    assert [r['row_id'] for r in original['rows']] == [r['row_id'] for r in linked['rows']]
    assert linked['support']['source_funds'] == linked['support']['funds'] == 2
    assert linked['support']['used_source_posts'] == 1
    assert linked['support']['post_fund_links_without_nav'] == 1
    assert linked['rows'][0]['features']['linked_posts_7d'] == 1
    assert all(not r['support_post_ids'] for r in linked['rows'] if r['unit_id'] == '002')

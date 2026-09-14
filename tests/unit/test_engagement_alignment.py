"""Temporary exported-corpus fixtures; no database, API or live-run writes."""
import json

import pytest

from flowmirror.analysis.engagement_alignment import build_alignment


def corpus(tmp_path, posts, snapshots):
    for name, rows in [('posts', posts), ('engagement_snapshot', snapshots)]:
        with (tmp_path / f'{name}.jsonl').open('w', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    return tmp_path


def post(pid='p', published='2026-08-30T12:00:00+08:00'):
    return {'post_id': pid, 'published_at': published}


def snap(day, likes=0, pid='p', **counters):
    return {'post_id': pid, 'crawl_date': day,
            'counters': {'likedCount': likes, **counters}}


def test_sorted_adjacent_intervals_and_read_only(tmp_path):
    root = corpus(tmp_path, [post(), post('alone'), post('never')], [
        snap('2026-09-04', '1,006'), snap('2026-08-31', 1000),
        snap('2026-09-01', '1002'), snap('2026-09-01', 7, pid='alone'),
    ])
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    result = build_alignment(root)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    assert [(r['start_date'], r['end_date'], r['elapsed_days']) for r in result['intervals']] == [
        ('2026-08-31', '2026-09-01', 1), ('2026-09-01', '2026-09-04', 3)]
    assert [r['metrics']['likes']['delta'] for r in result['intervals']] == [2, 4]
    assert result['intervals'][0]['age_start_days'] == 1
    assert result['intervals'][0]['metrics']['shares']['delta'] is None
    assert result['support']['snapshot_posts_with_at_most_one_usable_snapshot'] == 1
    assert result['support']['posts_without_snapshots'] == 1
    assert result['support']['metric_eligible_pairs']['likes'] == 2
    assert result['evaluation_only'] is True and result['agent_input'] is False
    assert result['time_granularity'] == 'day'
    assert len(result['snapshot_summaries']) == 3
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('value,expected,state', [
    (0, 0, 'exact'), (12, 12, 'exact'), (' 1,234 ', 1234, 'exact'),
    ('001', 1, 'exact'), ('0', 0, 'exact'),
    ('1.2万', None, 'approximate'), ('1k+', None, 'approximate'),
    ('2M', None, 'approximate'), ('10+', None, 'approximate'),
    ('', None, 'missing'), ('  ', None, 'missing'), (None, None, 'missing'),
    (-1, None, 'invalid'), ('-3', None, 'invalid'), (True, None, 'invalid'),
    (1.0, None, 'invalid'), ('1.0', None, 'invalid'), ('1,23', None, 'invalid'),
    ('oops', None, 'invalid'), ({}, None, 'invalid'), ([], None, 'invalid'),
])
def test_exact_counter_parsing(tmp_path, value, expected, state):
    result = build_alignment(corpus(tmp_path, [post()], [
        snap('2026-09-01', value), snap('2026-09-03', 2000)]))
    metric = result['intervals'][0]['metrics']['likes']
    assert metric['start'] == expected and metric['start_status'] == state
    assert metric['delta'] == (2000 - expected if expected is not None else None)
    assert metric['eligible_for_calibration'] == (state == 'exact')
    assert result['snapshot_summaries'][0]['metrics']['likes'][state] == 1


def test_missing_approximate_do_not_bridge_and_decreases_are_signed(tmp_path):
    result = build_alignment(corpus(tmp_path, [post()], [
        snap('2026-09-01', 10, collectedCount=8, commentCount='1k+', shareCount=0),
        snap('2026-09-03', None, collectedCount=3, commentCount=10, shareCount=0),
        snap('2026-09-04', 20, collectedCount=4, commentCount=12, shareCount=1),
    ]))
    first, second = result['intervals']
    assert first['metrics']['likes']['delta'] is None
    assert second['metrics']['likes']['delta'] is None
    assert first['metrics']['comments']['start_status'] == 'approximate'
    assert first['metrics']['collections']['delta'] == -5
    assert first['metrics']['collections']['status'] == 'counter_decrease'
    assert first['metrics']['collections']['eligible_for_calibration'] is False
    assert first['metrics']['shares']['delta'] == 0


def test_duplicates_and_conflicts_are_order_independent_breaks(tmp_path):
    duplicate = snap('2026-09-01', 1)
    rows = [snap('2026-08-31', 0), duplicate, dict(reversed(list(duplicate.items()))),
            snap('2026-09-03', 3), snap('2026-09-03', 4), snap('2026-09-04', 5)]
    first = build_alignment(corpus(tmp_path, [post()], rows))
    second = build_alignment(corpus(tmp_path, [post()], list(reversed(rows))))
    assert first == second
    assert len(first['intervals']) == 1
    assert first['support']['duplicate_snapshot_rows'] == 1
    assert first['support']['conflicting_snapshot_keys'] == 1
    assert first['support']['adjacent_pairs_excluded_conflict'] == 2
    assert first['snapshot_summaries'][2]['usable_keys'] == 0


@pytest.mark.parametrize('published,status', [
    (None, 'missing'), ('', 'missing'), ('garbage', 'invalid'),
    ('2026-02-30', 'invalid'), ('2026-09-02', 'future'),
    ('2027-01-01', 'future'), ('2026-09-01', 'valid'),
])
def test_publication_validity_is_explicit(tmp_path, published, status):
    result = build_alignment(corpus(tmp_path, [post(published=published)], [
        snap('2026-09-01', 1), snap('2026-09-03', 2)]))
    interval = result['intervals'][0]
    assert interval['publication_status'] == status
    assert interval['metrics']['likes']['eligible_for_calibration'] == (status == 'valid')
    assert result['support']['interval_publication_statuses'] == {status: 1}


def test_bad_crawl_date_blocks_unknown_chronology(tmp_path):
    result = build_alignment(corpus(tmp_path, [post()], [
        snap('2026-09-01', 1), snap('not-a-date', 2), snap('2026-09-03', 3)]))
    assert not result['intervals']
    assert result['support']['posts_excluded_unknown_chronology'] == 1
    assert result['support']['invalid_crawl_date_rows'] == 1


def test_post_conflict_and_orphan_are_ineligible(tmp_path):
    result = build_alignment(corpus(tmp_path, [post(), post(published='2026-08-29')], [
        snap('2026-09-01', 1), snap('2026-09-03', 2),
        snap('2026-09-01', 1, pid='orphan'), snap('2026-09-03', 2, pid='orphan')]))
    assert {r['publication_status'] for r in result['intervals']} == {'post_conflict', 'post_missing'}
    assert result['support']['metric_eligible_pairs']['likes'] == 0
    assert result['support']['snapshot_posts_absent_from_posts'] == 1


def test_empty_and_malformed_input(tmp_path):
    root = corpus(tmp_path, [], [])
    assert build_alignment(root)['intervals'] == []
    with (root / 'engagement_snapshot.jsonl').open('w', encoding='utf-8') as stream:
        stream.write('{bad json}\n')
    with pytest.raises(ValueError, match='engagement_snapshot.jsonl:1'):
        build_alignment(root)


def test_missing_counters_object_does_not_bridge(tmp_path):
    result = build_alignment(corpus(tmp_path, [post()], [
        snap('2026-09-01', 1), {'post_id': 'p', 'crawl_date': '2026-09-03'},
        snap('2026-09-04', 4)]))
    assert len(result['intervals']) == 2
    assert all(r['metrics']['likes']['delta'] is None for r in result['intervals'])


def test_type_difference_in_duplicate_key_is_conflict(tmp_path):
    result = build_alignment(corpus(tmp_path, [post()], [
        snap('2026-09-01', 1), snap('2026-09-01', True), snap('2026-09-03', 3)]))
    assert result['support']['conflicting_snapshot_keys'] == 1
    assert result['intervals'] == []

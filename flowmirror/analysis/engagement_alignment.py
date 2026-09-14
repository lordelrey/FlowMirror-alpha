"""Read-only engagement alignment from exported JSONL; evaluation-only.

``build_alignment(corpus_root)`` reads posts.jsonl (post_id, published_at)
and engagement_snapshot.jsonl (post_id, crawl_date, counters). It returns:

* support: row/post/key counts, duplicate/conflict exclusions, publication
  status counts, per-metric interval status counts and eligible pair counts.
* intervals: adjacent dated observations per post, sorted by post_id/date.
  Each contains post_id, start_date, end_date, elapsed_days, publication_date,
  publication_status, age_start_days/age_end_days and metrics. Each metric has
  start/end/delta (integer or null), start_status/end_status, status and
  eligible_for_calibration. A signed delta is a cumulative counter difference;
  counter_decrease is NOT a negative number of real engagement events.
* snapshot_summaries: per crawl date, raw rows, unique/conflicting/usable keys,
  and per-metric exact/missing/approximate/invalid observation counts.

Dates and ages have calendar-day granularity in the source's recorded calendar;
there is no inferred capture hour, event timestamp or daily event label. Missing
publication dates, malformed dates and publication after either endpoint are
explicitly ineligible. Same-day publication has unknown within-day ordering.
Identical JSON objects (ignoring object-key order) are deduplicated. Conflicting
objects for a (post_id, date) exclude that entire key, without bridging it.
Unknown crawl dates exclude pairing for the whole affected post because their
chronological position cannot be established. Missing metric endpoints likewise
never cause a search for a more distant observation. Invalid JSON/row shapes
raise ValueError with filename/line number, rather than silently skip evidence.

These are offline evaluation labels, never agent observations. No fitting,
database, UI, model/network calls or source writes occur. No impressions-based
open rate is available. A simulated 30-day open count and real lifetime likes
are different quantities and must not be compared using MAE.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import json
from pathlib import Path
import re


METRICS = {
    'likes': 'likedCount', 'collections': 'collectedCount',
    'comments': 'commentCount', 'shares': 'shareCount',
}


def _rows(path):
    with path.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f'{path.name}:{number}: invalid JSON') from exc
            if not isinstance(row, dict) or not isinstance(row.get('post_id'), str) or not row['post_id'].strip():
                raise ValueError(f'{path.name}:{number}: expected object with nonempty post_id')
            yield row


def _counter(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None, 'missing'
    if type(value) is int:
        return (value, 'exact') if value >= 0 else (None, 'invalid')
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', value):
            return int(value.replace(',', '')), 'exact'
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[kKmMwW万亿]\+?|\+)', value):
            return None, 'approximate'
    return None, 'invalid'


def _crawl_date(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _publication(row):
    if row is None:
        return None, 'post_missing'
    value = row.get('published_at')
    if value is None or value == '':
        return None, 'missing'
    if not isinstance(value, str):
        return None, 'invalid'
    day = _crawl_date(value)
    if day is not None:
        return day, 'valid'
    try:
        # Preserve the source calendar date, without inventing capture times.
        if not re.match(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]', value):
            return None, 'invalid'
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date(), 'valid'
    except ValueError:
        return None, 'invalid'


def _identity(row):
    # Full structural identity; type differences (e.g. 1 vs true) conflict.
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def build_alignment(corpus_root) -> dict:
    """Build the module-documented schema without writing any files.

    Only an exact endpoint pair with valid publication chronology and a
    nonnegative difference is eligible_for_calibration. Eligibility does not
    establish event counts, human validity or matched simulation estimands.
    """
    root = Path(corpus_root)
    posts, post_conflicts = {}, set()
    post_rows = post_duplicates = 0
    for row in _rows(root / 'posts.jsonl'):
        post_rows += 1
        pid = row['post_id']
        if pid in posts:
            if _identity(posts[pid]) == _identity(row):
                post_duplicates += 1
            else:
                post_conflicts.add(pid)
        else:
            posts[pid] = row

    grouped = defaultdict(dict)
    snapshot_posts, invalid_date_posts = set(), set()
    raw_dates = Counter()
    snapshot_rows = invalid_dates = duplicate_rows = 0
    for row in _rows(root / 'engagement_snapshot.jsonl'):
        snapshot_rows += 1
        pid = row['post_id']
        snapshot_posts.add(pid)
        day = _crawl_date(row.get('crawl_date'))
        if day is None:
            invalid_dates += 1
            invalid_date_posts.add(pid)
            continue
        raw_dates[day.isoformat()] += 1
        key = (pid, day)
        identity = _identity(row)
        if identity in grouped[key]:
            duplicate_rows += 1
        grouped[key][identity] = row

    summaries = {
        day: {'crawl_date': day, 'raw_rows': count, 'unique_keys': 0,
              'conflicting_keys': 0, 'usable_keys': 0,
              'metrics': {m: dict.fromkeys(('exact', 'missing', 'approximate', 'invalid'), 0)
                          for m in METRICS}}
        for day, count in sorted(raw_dates.items())
    }
    timelines = defaultdict(list)
    conflicts = 0
    for (pid, day), variants in sorted(grouped.items()):
        summary = summaries[day.isoformat()]
        summary['unique_keys'] += 1
        if len(variants) > 1:
            conflicts += 1
            summary['conflicting_keys'] += 1
            timelines[pid].append((day, None))
            continue
        summary['usable_keys'] += 1
        counters = next(iter(variants.values())).get('counters')
        parsed = {m: _counter(counters.get(field)) if isinstance(counters, dict)
                  else (None, 'invalid') for m, field in METRICS.items()}
        for metric, (_, status) in parsed.items():
            summary['metrics'][metric][status] += 1
        timelines[pid].append((day, parsed))

    intervals = []
    metric_statuses = {m: Counter() for m in METRICS}
    eligible_pairs = Counter(dict.fromkeys(METRICS, 0))
    publication_counts = Counter()
    skipped_conflict_pairs = 0
    for pid, timeline in sorted(timelines.items()):
        if pid in invalid_date_posts:
            continue
        published, pub_status = _publication(posts.get(pid))
        if pid in post_conflicts:
            published, pub_status = None, 'post_conflict'
        for (start_day, start), (end_day, end) in zip(timeline, timeline[1:]):
            if start is None or end is None:
                skipped_conflict_pairs += 1
                continue
            status = 'future' if published and published > start_day else pub_status
            publication_counts[status] += 1
            interval = {
                'post_id': pid, 'start_date': start_day.isoformat(),
                'end_date': end_day.isoformat(), 'elapsed_days': (end_day - start_day).days,
                'time_granularity': 'day',
                'publication_date': published.isoformat() if published else None,
                'publication_status': status,
                'age_start_days': (start_day - published).days if published else None,
                'age_end_days': (end_day - published).days if published else None,
                'metrics': {},
            }
            for metric in METRICS:
                a, sa = start[metric]
                b, sb = end[metric]
                delta = b - a if a is not None and b is not None else None
                state = ('counter_decrease' if delta < 0 else 'ok') if delta is not None else 'inexact_endpoint'
                eligible = status == 'valid' and state == 'ok'
                interval['metrics'][metric] = {
                    'start': a, 'end': b, 'delta': delta, 'status': state,
                    'start_status': sa, 'end_status': sb,
                    'eligible_for_calibration': eligible,
                }
                metric_statuses[metric][state] += 1
                eligible_pairs[metric] += eligible
            intervals.append(interval)

    usable_counts = Counter()
    for (pid, _), variants in grouped.items():
        if len(variants) == 1:
            usable_counts[pid] += 1
    return {
        'evaluation_only': True, 'agent_input': False, 'time_granularity': 'day',
        'label_kind': 'adjacent_crawl_cumulative_counter_difference',
        'limitations': [
            'Not daily real engagement events; capture hours are unknown.',
            'Same-day publication/crawl ordering is unknown; ages are calendar-day differences.',
            'Counter decreases remain signed differences, not engagement event counts.',
            'Zero or one snapshot cannot identify an engagement increment.',
            'No impressions denominator: no real open rate can be calculated.',
            'Do not compute MAE between simulated 30-day opens and real lifetime likes.',
            'Offline evaluation only; do not deliver these labels to agents.',
        ],
        'support': {
            'post_rows': post_rows, 'unique_posts': len(posts),
            'duplicate_post_rows': post_duplicates, 'conflicting_post_ids': len(post_conflicts),
            'snapshot_rows': snapshot_rows, 'snapshot_posts': len(snapshot_posts),
            'crawl_dates': sorted(raw_dates), 'snapshot_rows_by_date': dict(sorted(raw_dates.items())),
            'unique_dated_snapshot_keys': len(grouped), 'duplicate_snapshot_rows': duplicate_rows,
            'conflicting_snapshot_keys': conflicts, 'invalid_crawl_date_rows': invalid_dates,
            'posts_excluded_unknown_chronology': len(invalid_date_posts),
            'snapshot_posts_absent_from_posts': len(snapshot_posts - posts.keys()),
            'posts_without_snapshots': len(posts.keys() - snapshot_posts),
            'snapshot_posts_with_at_most_one_usable_snapshot': sum(usable_counts[p] <= 1 for p in snapshot_posts),
            'adjacent_pairs_excluded_conflict': skipped_conflict_pairs,
            'intervals': len(intervals), 'posts_with_intervals': len({r['post_id'] for r in intervals}),
            'interval_publication_statuses': dict(publication_counts),
            'metric_interval_statuses': {m: dict(c) for m, c in metric_statuses.items()},
            'metric_eligible_pairs': dict(eligible_pairs),
        },
        'intervals': intervals, 'snapshot_summaries': list(summaries.values()),
    }

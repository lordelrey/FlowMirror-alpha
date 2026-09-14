"""Retrospective NAV/publication panel, never an investor observation.

NAV(t) is assumed available at local midnight t+1. The target is the next
recorded raw NAV log change, not a total return or next exchange-session price.
Source linkage and local-image availability come from the current export, not
a vintage archive. Publication features are observational metadata, not vision
embeddings or identified interventions. No engagement outcomes enter features.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import statistics

from flowmirror.platform.warehouse import lines

CN = timezone(timedelta(hours=8))
STATE_FEATURES = ['last_log_change', 'mean_log_change_5', 'std_log_change_5', 'last_gap_days']
PUBLICATION_FEATURES = ['linked_posts_7d', 'linked_image_posts_7d']
LIMITATIONS = [
    'Raw unit NAV changes are not dividend-adjusted total returns or tradable prices.',
    'NAV availability assumes next calendar day midnight Asia/Shanghai; actual historical release times are unknown.',
    'Content, fund links and local-image availability are retrospectively reconstructed from the current warehouse export.',
    'Publication counts are observational proxies, not identified marketing interventions or visual understanding.',
    'No cumulative engagement counters, quarterly flows or future NAV values are policy features.',
    'Only Chinese NAV data are supported; US disclosures do not supply US prices.',
]


def _date(value):
    if not isinstance(value, str):
        raise ValueError('expected ISO calendar date')
    return date.fromisoformat(value)


def available_at(value):
    return datetime.combine(_date(value) + timedelta(days=1), time(), CN)


def _positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def build_dataset(corpus_root, *, start='2025-09-05', end='2026-09-05', max_gap_days=14):
    """Return JSON-safe rows and source support, without modifying any inputs.

    Rows require five prior changes (six NAV observations). Gaps greater than
    max_gap_days within that history or to the target are excluded, not filled.
    Exact duplicate NAV keys collapse; conflicting/invalid keys remain barriers
    in the time series so a missing value cannot be silently bridged.
    """
    first, last = _date(start), _date(end)
    if first > last or type(max_gap_days) is not int or max_gap_days < 1:
        raise ValueError('invalid date range or maximum gap')
    corpus = Path(corpus_root)
    counts = Counter()
    keyed, source_values, unknown_date_funds = {}, {}, set()
    for row in lines(corpus / 'nav.jsonl'):
        counts['nav_source_rows'] += 1
        code = row.get('fund_code')
        if not isinstance(code, str) or not code:
            counts['nav_invalid_identity'] += 1
            continue
        try:
            at = _date(row.get('nav_date'))
        except (ValueError, TypeError):
            counts['nav_invalid_date'] += 1
            # Its chronological position is unknown, so no clean history can
            # be reconstructed for this fund by simply dropping the row.
            unknown_date_funds.add(code)
            continue
        key = code, at
        value = _positive(row.get('nav'))
        try:
            exact = Decimal(str(row.get('nav'))) if value is not None else None
        except InvalidOperation:
            exact = None
        if key in keyed:
            counts['nav_duplicate_rows'] += 1
            if source_values[key] != exact or keyed[key] is None and value is not None:
                counts['nav_conflicting_rows'] += 1
                keyed[key] = None
        else:
            keyed[key] = value
            source_values[key] = exact
        if value is None:
            counts['nav_invalid_value_rows'] += 1
    nav = defaultdict(list)
    for (code, at), value in sorted(keyed.items()):
        if code in unknown_date_funds:
            counts['nav_rows_excluded_unknown_chronology'] += 1
            continue
        nav[code].append((at, value))
    counts['nav_funds_excluded_unknown_chronology'] = len(unknown_date_funds)

    # Identity is the source post primary key. A conflict makes the entire key
    # unusable; neither file order nor the last occurrence chooses the stimulus.
    post_keys, post_conflicts = {}, set()
    for card in lines(corpus / 'posts.jsonl'):
        counts['post_source_rows'] += 1
        pid = card.get('post_id')
        if not isinstance(pid, str) or not pid:
            counts['post_invalid_identity'] += 1
            continue
        if pid in post_keys:
            counts['post_duplicate_rows'] += 1
            if json.dumps(post_keys[pid], sort_keys=True) != json.dumps(card, sort_keys=True):
                post_conflicts.add(pid)
        else:
            post_keys[pid] = card
    counts['post_conflicting_keys'] = len(post_conflicts)
    publications = defaultdict(list)
    dated_linked_ids = set()
    for pid, card in sorted(post_keys.items()):
        if pid in post_conflicts or card.get('market') != 'CN':
            continue
        counts['cn_posts'] += 1
        try:
            at = datetime.fromisoformat(card.get('published_at') or '')
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError('timezone missing')
            at = at.astimezone(CN)
        except (TypeError, ValueError, OverflowError):
            counts['post_without_valid_time'] += 1
            continue
        codes = card.get('fund_codes')
        if not isinstance(codes, list):
            counts['post_invalid_fund_list'] += 1
            continue
        codes = sorted({c for c in codes if isinstance(c, str) and c})
        if not codes:
            counts['post_without_fund_link'] += 1
            continue
        dated_linked_ids.add(pid)
        refs = card.get('image_refs') or []
        for code in codes:
            if code in nav:
                publications[code].append((at, pid, bool(refs)))
            else:
                counts['post_fund_links_without_nav'] += 1
    for values in publications.values():
        values.sort()
    pub_times = {code: [p[0] for p in values] for code, values in publications.items()}
    rows, supported = [], set()
    for code, observations in sorted(nav.items()):
        posts = publications.get(code, [])
        times = pub_times.get(code, [])
        for i in range(5, len(observations)-1):
            at, value = observations[i]
            origin = datetime.combine(at + timedelta(days=1), time(), CN)
            if not first <= origin.date() <= last:
                continue
            counts['candidate_origin_rows'] += 1
            window = observations[i-5:i+2]
            if any(v is None for _, v in window):
                counts['rows_excluded_invalid_nav'] += 1
                continue
            gaps = [(b[0]-a[0]).days for a, b in zip(window, window[1:])]
            if max(gaps) > max_gap_days:
                counts['rows_excluded_gap'] += 1
                continue
            target_date, target_value = observations[i+1]
            target_at = datetime.combine(target_date + timedelta(days=1), time(), CN)
            if target_at.date() > last:
                counts['rows_excluded_target_after_end'] += 1
                continue
            # log(b)-log(a) avoids overflow for otherwise finite NAV values.
            changes = [math.log(b[1])-math.log(a[1]) for a, b in zip(window[:5], window[1:6])]
            visible = posts[bisect_right(times, origin-timedelta(days=7)):bisect_right(times, origin)]
            support_ids = [p[1] for p in visible]
            supported.update(support_ids)
            feature = dict(zip(STATE_FEATURES, [changes[-1], statistics.mean(changes),
                                               statistics.pstdev(changes), gaps[-2]]))
            feature.update(linked_posts_7d=len(visible), linked_image_posts_7d=sum(p[2] for p in visible))
            rows.append({'row_id': f'{code}:{at.isoformat()}:{target_date.isoformat()}',
                'unit_id': code, 'origin_at': origin.isoformat(), 'target_at': target_at.isoformat(),
                'origin_nav_date': at.isoformat(), 'target_nav_date': target_date.isoformat(),
                'origin_nav': value, 'target_nav': target_value,
                'target': math.log(target_value)-math.log(value), 'target_gap_days': gaps[-1],
                'features': feature, 'state_features': list(STATE_FEATURES),
                'publication_features': list(PUBLICATION_FEATURES), 'support_post_ids': support_ids})
    rows.sort(key=lambda r:(r['origin_at'], r['unit_id']))
    support = dict(counts)
    support.update(rows=len(rows), source_funds=len(nav), funds=len({r['unit_id'] for r in rows}),
                   dated_linked_posts=len(dated_linked_ids), used_source_posts=len(supported),
                   publication_supported_rows=sum(bool(r['support_post_ids']) for r in rows),
                   origin_dates=len({r['origin_at'] for r in rows}),
                   feature_names=STATE_FEATURES+PUBLICATION_FEATURES)
    return {'kind':'retrospective_cn_raw_nav_publication_panel', 'rows':rows, 'support':support,
            'settings':{'start':start, 'end':end, 'max_gap_days':max_gap_days, 'lookback_changes':5,
                        'publication_lookback_days':7, 'target':'next_observed_raw_nav_log_change'},
            'limitations': list(LIMITATIONS)}

"""Read-only warehouse -> local, non-distributable corpus and scheduled probes.

No credentials, raw URLs, database writes, downloads, or model calls. Snapshot
engagement and estimated guba dates are calibration data, never historical cues.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

CN = timezone(timedelta(hours=8))
RASTER = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def dated(value, *, date_only=False):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN)
        if not 1990 <= dt.year <= 2100:
            return None
        if date_only:
            # Date-only facts become visible the next day, not at an invented time.
            dt = dt.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        return dt.isoformat()
    except ValueError:
        return None


def lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def write_lines(path, rows):
    count = 0
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            count += 1
    return count


def write_json(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)


def export_warehouse(database, out):
    database, out = Path(database).resolve(strict=True), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'source_kind': 'local_readonly_warehouse', 'tables': {}, 'exported': {},
              'notice': 'Private retrospective reconstruction; not a point-in-time archive of content revisions.'}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')  # a consistent read snapshot, never a write transaction
        def query(sql):
            return (dict(r) for r in db.execute(sql))
        for name in ('xhs_note', 'asset', 'asset_ocr', 'asset_fund_code', 'fund_nav',
                     'fund_flow_panel', 'guba_post', 'guba_anchor', 'us_filing', 'dim_org',
                     'dim_fund', 'pool_note', 'pool_image_caption', 'note_compliance_label'):
            report['tables'][name] = db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
        images_by_note, images_by_filing = defaultdict(list), defaultdict(list)
        image_index, assets = [], []
        raster_missing = 0
        for a in query('SELECT asset_id,market,channel,asset_kind,org_name,note_id,filing_acc,seq,abs_root,rel_path,ext FROM asset ORDER BY asset_id'):
            root = Path(a.pop('abs_root')).resolve()
            relative = a.pop('rel_path').replace('\\', '/')
            path = (root / relative).resolve()
            exists = path.is_relative_to(root) and path.is_file()
            a['local_available'] = exists
            assets.append(a)
            if path.suffix.lower() not in RASTER:
                continue
            if not exists:
                raster_missing += 1
                continue
            ref = 'asset_' + a['asset_id']  # reuse source primary key; no new hash
            image_index.append({'asset_ref': ref, 'path': str(path)})
            item = (0 if a['asset_kind'] == 'inner' else 1, a['seq'] or 0, ref)
            if a['note_id']:
                images_by_note[a['note_id']].append(item)
            if a['filing_acc']:
                images_by_filing[a['filing_acc']].append(item)
        report['exported']['assets'] = write_lines(out / 'assets.jsonl', assets)
        report['exported']['local_images'] = write_lines(out / 'image_index.jsonl', image_index)
        report['missing_raster_files'] = raster_missing
        ocr_by_note = defaultdict(list)
        for row in query("SELECT a.note_id,o.text FROM asset_ocr o JOIN asset a USING(asset_id) WHERE a.note_id IS NOT NULL ORDER BY a.note_id,a.seq"):
            if row['text']:
                ocr_by_note[row['note_id']].append(row['text'])
        captions = defaultdict(list)
        for row in query("SELECT note_id,caption FROM pool_image_caption WHERE pool_version='content_pool_v1_captioned_v2' ORDER BY note_id,img_index"):
            if row['caption']:
                captions[row['note_id']].append(row['caption'])
        codes = defaultdict(set)
        for row in query('SELECT a.note_id,f.fund_code FROM asset_fund_code f JOIN asset a USING(asset_id) WHERE f.is_valid_fund=1 AND a.note_id IS NOT NULL'):
            codes[row['note_id']].add(row['fund_code'])
        coverage = Counter()
        def posts():
            for row in query('SELECT note_id,org_name,title,content,note_type,submit_time FROM xhs_note ORDER BY note_id'):
                nid = row['note_id']
                images = [v[2] for v in sorted(images_by_note[nid])]
                published = dated(row['submit_time'])
                coverage['xhs_dated' if published else 'xhs_undated'] += 1
                coverage['xhs_with_local_image'] += bool(images)
                coverage['xhs_with_body'] += bool((row['content'] or '').strip())
                yield {'post_id': 'xhs_' + nid, 'market': 'CN', 'channel': 'xhs',
                       'org': row['org_name'] or '', 'title': row['title'] or '',
                       'caption': row['content'] or '', 'published_at': published,
                       'time_basis': 'source_submit_time_assumed_Asia_Shanghai' if published else 'unknown',
                       'image_refs': images, 'ocr_text': '\n'.join(ocr_by_note[nid]),
                       'image_caption_frozen': '\n'.join(captions[nid]),
                       'fund_codes': sorted(codes[nid]), 'comments_prev': [],
                       'source_kind': 'historical_database_content_latest_snapshot'}
            for row in query('SELECT acc,company,filed_date FROM us_filing ORDER BY acc'):
                # A filing image is a disclosure, not a scraped social/ad post.
                images = [v[2] for v in sorted(images_by_filing[row['acc']])]
                published = dated(row['filed_date'], date_only=True)
                if published:
                    # Date-only SEC filing: next day midnight Eastern, conservatively
                    # fixed -05:00 throughout (later than EDT), not exchange hours.
                    published = published[:19] + '-05:00'
                coverage['us_filings_dated' if published else 'us_filings_undated'] += 1
                yield {'post_id': 'sec_' + row['acc'], 'market': 'US', 'channel': 'sec_497',
                       'org': row['company'], 'title': 'SEC filing ' + row['acc'], 'caption': '',
                       'published_at': published, 'time_basis': 'filing_date_next_day_fixed_EST_assumption',
                       'image_refs': images, 'ocr_text': '', 'image_caption_frozen': '',
                       'fund_codes': [], 'comments_prev': [], 'source_kind': 'historical_filing_disclosure'}
        report['exported']['posts'] = write_lines(out / 'posts.jsonl', posts())
        report['coverage'] = dict(coverage)
        exports = {
            'nav': 'SELECT fund_code,nav_date,nav FROM fund_nav ORDER BY fund_code,nav_date',
            'flow_panel': 'SELECT fund_code,quarter,sub,red,shares,aum FROM fund_flow_panel ORDER BY fund_code,quarter',
            'funds': 'SELECT fund_code,fund_name,fund_type,market,has_nav,has_panel,has_guba FROM dim_fund ORDER BY fund_code',
            'organizations': 'SELECT org_name,market,note_cnt,asset_cnt FROM dim_org ORDER BY org_name',
            'ocr': 'SELECT asset_id,text,model,batch FROM asset_ocr ORDER BY asset_id',
            'fund_links': 'SELECT asset_id,fund_code FROM asset_fund_code WHERE is_valid_fund=1 ORDER BY asset_id,fund_code',
            'caption_versions': 'SELECT note_id,img_index,pool_version,caption,model FROM pool_image_caption ORDER BY note_id,img_index,pool_version',
            'compliance': 'SELECT note_id,rule_id,labels_version,hit,evidence,severity FROM note_compliance_label ORDER BY note_id,rule_id',
            'pool_versions': 'SELECT note_id,pool_version,intent,title,caption,ocr_text,submit_time FROM pool_note ORDER BY note_id,pool_version',
            'guba_posts': 'SELECT board_fund,pid,title,post_date,read_n,reply_n FROM guba_post WHERE is_own_board=1 ORDER BY board_fund,pid',
            'guba_anchors': 'SELECT pid,fund,publish FROM guba_anchor ORDER BY pid',
        }
        for name, sql in exports.items():
            report['exported'][name] = write_lines(out / (name + '.jsonl'), query(sql))
        def engagement():
            for row in query('SELECT note_id,interact FROM xhs_note ORDER BY note_id'):
                try:
                    observations = json.loads(row['interact'] or '{}')
                except (ValueError, TypeError):
                    continue
                if not isinstance(observations, dict):
                    continue
                for at, counters in observations.items():
                    if not dated(at) or not isinstance(counters, dict):
                        continue
                    safe = {k: counters[k] for k in ('likedCount','collectedCount','commentCount','shareCount')
                            if k in counters and isinstance(counters[k], (str,int,float))}
                    yield {'post_id':'xhs_'+row['note_id'],'crawl_date':at,'counters':safe}
        report['exported']['engagement_snapshot'] = write_lines(out / 'engagement_snapshot.jsonl', engagement())
        report['not_policy_inputs'] = {
            'guba_posts': 'Estimated dates include implausible extrapolation; not exact scheduled events.',
            'flow_panel': 'Quarterly disclosures lack publication time; calibration only, never daily market flows.',
            'engagement_snapshot': 'Cumulative crawl-time counters, not historical likes at publication.',
            'compliance': 'Offline labels are evaluation metadata, not investor observations.',
            'undated_posts_and_site_ads': 'No fabricated publication date; available in the local corpus only.',
        }
        db.rollback()
    write_json(out / 'inventory.json', report)
    return report


def build_spec(corpus, *, start, days=30, agents=300, market='CN', seed=2027):
    corpus = Path(corpus)
    if type(days) is not int or days < 1 or type(agents) is not int or agents < 1:
        raise ValueError('days and agents must be positive integers')
    if market not in ('CN', 'US'):
        raise ValueError('market must be CN or US')
    first = datetime.fromisoformat(start).replace(tzinfo=CN, hour=23, minute=59, second=59)
    times = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    earliest, latest = first - timedelta(days=30), first + timedelta(days=days-1)
    cards, published_counts = [], Counter()
    for card in lines(corpus / 'posts.jsonl'):
        if card['market'] != market or not card['published_at']:
            continue
        published = datetime.fromisoformat(card['published_at'])
        if not earliest <= published <= latest:
            continue
        if not (card['title'] or card['caption'] or card['image_refs']):
            continue
        cards.append(card)
        published_counts[card['published_at'][:10]] += 1
    if not cards:
        raise ValueError('no dated source posts in the selected window')
    nav_rows, instruments = [], set()
    if market == 'CN':
        for row in lines(corpus / 'nav.jsonl'):
            if not earliest.date().isoformat() <= row['nav_date'] <= latest.date().isoformat():
                continue
            price = row['nav']
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            available = dated(row['nav_date'], date_only=True)
            nav_rows.append({'market': 'CN', 'currency': 'CNY', 'kind': 'fund_nav',
                             'instrument_id': row['fund_code'], 'price': price,
                             'observed_at': row['nav_date'] + 'T23:59:59+08:00',
                             'available_at': available, 'synthetic': False,
                             'source': 'warehouse_nav_date_next_day_availability_assumption'})
            instruments.add(row['fund_code'])
    instruments = sorted(instruments)
    population = []
    for i in range(agents):
        actor = f'investor_{i:05d}'
        watch = [instruments[(i//3*6+j) % len(instruments)] for j in range(min(6,len(instruments)))] if instruments else []
        population.append({'id': actor, 'handle': '@' + actor, 'arm': ('T','TC','TV')[i%3],
                           'market': market, 'market_instruments': watch,
                           'private_state': {'persona': f'Synthetic observer {i}; risk group {i%5}; not a real user.',
                                             'cash': (10000,30000,50000,100000,200000)[i%5], 'memory': []}})
    spec = {'name': f'warehouse-{market}-{start}-{days}d-{agents}agents',
            'phases': days, 'phase_times': times, 'max_steps': 2, 'page_size': 3,
            'policy_mode': 'scripted', 'policy_name': 'warehouse-open-finish-v1',
            'offline_checkpoint_interval': 600, 'feed_policy': {'kind':'recent_rotating','limit':24,'seed':seed,'lookback_days':30},
            'agents': population, 'cards': cards, 'market_data': nav_rows,
            'corpus_info': {'source': 'private_readonly_warehouse', 'selected_posts': len(cards),
                            'selected_images': len({r for c in cards for r in c['image_refs']}),
                            'dated_posts_per_day': dict(sorted(published_counts.items())),
                            'nav_rows': len(nav_rows), 'nav_funds': len(instruments),
                            'agent_population': 'synthetic_balanced_T_TC_TV_not_observed_humans',
                            'timing': 'historical reconstruction; NAV next-day availability assumed, not verified publication',
                            'execution': 'browse-only; no product-specific trade calendar or fees invented'}}
    return spec


def warehouse_policy(view):
    if view['step'] == 0 and view['feed']:
        return {'kind':'open','post_id':view['feed'][0]['post_id']}
    return {'kind':'finish'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export'); export.add_argument('--database',required=True); export.add_argument('--out',required=True)
    build = sub.add_parser('build'); build.add_argument('--corpus',required=True); build.add_argument('--out',required=True)
    build.add_argument('--start',required=True); build.add_argument('--days',type=int,default=30)
    build.add_argument('--agents',type=int,default=300); build.add_argument('--market',choices=['CN','US'],default='CN')
    args = p.parse_args(argv)
    if args.command == 'export':
        print(json.dumps(export_warehouse(args.database,args.out),ensure_ascii=False,indent=2))
    else:
        spec = build_spec(args.corpus,start=args.start,days=args.days,agents=args.agents,market=args.market)
        from flowmirror.platform.browse_run import normalize_spec
        write_json(args.out,normalize_spec(spec))
        print(json.dumps(spec['corpus_info'],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

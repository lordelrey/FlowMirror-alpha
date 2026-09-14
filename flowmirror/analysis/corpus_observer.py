"""Local researcher catalogue; not a simulation observation or model tool."""
import copy
import json
from pathlib import Path

from flowmirror.platform.warehouse import lines


class CorpusObserver:
    def __init__(self, root):
        self.root = Path(root)
        with (self.root/'inventory.json').open(encoding='utf-8') as stream:
            self.inventory = json.load(stream)
        self.posts = list(lines(self.root/'posts.jsonl'))
        self.assets = list(lines(self.root/'assets.jsonl'))

    def page(self, *, channel='xhs', start='', end='', timing='dated', query='', offset=0, limit=24):
        if channel not in ('xhs','sec_497','assets') or timing not in ('dated','undated','all'):
            raise ValueError('unknown filter')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 48:
            raise ValueError('invalid page size')
        query = query.casefold().strip()
        rows = []
        for row in self.assets if channel == 'assets' else self.posts:
            if channel == 'assets':
                row = {'post_id':row['asset_id'], 'org':row['org_name'],
                       'title':f"{row['market']} · {row['channel']} · {row['asset_kind']}",
                       'caption':'资产目录；不推定此文件的真实发布时间。',
                       'published_at':None, 'channel':row['channel'],
                       'image_refs':['asset_'+row['asset_id']] if row['local_available'] and (row['ext'] or '').lower().lstrip('.') in ('jpg','jpeg','png','webp','gif') else []}
            elif row['channel'] != channel:
                continue
            at = row.get('published_at')
            if channel != 'assets':
                if timing == 'dated' and not at or timing == 'undated' and at:
                    continue
                if (start or end) and not at:
                    continue
                if at and (start and at[:10] < start or end and at[:10] > end):
                    continue
            if query and query not in ' '.join(str(row.get(k,'') or '') for k in ('title','caption','org','post_id')).casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda r:(r.get('published_at') or '',r['post_id']),reverse=True)
        return {'total':len(rows),'offset':offset,'limit':limit,
                'items':copy.deepcopy(rows[offset:offset+limit]),
                'notice':'研究者数据目录，不是任何agent的当前信息；未定时素材不进入历史排期。'}

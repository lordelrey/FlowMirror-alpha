"""实际策略输入和动作的观察账本；不推断动机、关注归因或交易因果。"""
from collections import Counter, defaultdict


POST_ACTIONS = frozenset(('open', 'comments', 'like', 'save', 'comment',
                          'subscribe', 'redeem', 'buy', 'sell'))


class BehaviorLedger:
    """IDs are used internally for de-duplication; reports expose aggregate counts."""
    def __init__(self):
        self.rows = {}

    def _row(self, phase, post, arm):
        return self.rows.setdefault((phase, post, arm),
                                   {'exposed': set(), 'opened': set(), 'rejected_opened': set(),
                                    'accepted': Counter(), 'rejected': Counter()})

    def record(self, frame, arm):
        phase, actor, view = frame['phase'], frame['agent_id'], frame['before']
        # after 中的下一页可能从未交给策略，不能算作投递。
        for card in list(view.get('feed', [])) + ([view['detail']] if view.get('detail') else []):
            self._row(phase, card['post_id'], arm)['exposed'].add(actor)
        action, result = frame['action'], frame['result']
        post, kind = action.get('post_id'), action.get('kind')
        kind = kind if isinstance(kind, str) else 'invalid'
        # 关注的目标是 handle；即使动作夹带 post_id，也没有帖子归因证据。
        if not isinstance(post, str) or kind not in POST_ACTIONS:
            post = None
        row = self._row(phase, post, arm)
        if result['status'] == 'accepted':
            row['accepted'][kind] += 1
            if kind in ('open', 'comments'):
                row['opened'].add(actor)
        else:
            row['rejected'][kind] += 1
            if kind in ('open', 'comments'):
                row['rejected_opened'].add(actor)

    def report(self, through_phase=None, posts=None):
        """一行一个 phase×post×arm；动作计次数，曝光和打开按 agent 去重。"""
        output = []
        for (phase, post, arm), row in sorted(
                self.rows.items(), key=lambda item: (item[0][0], item[0][1] or '', item[0][2] or '')):
            if through_phase is not None and phase > through_phase:
                continue
            if posts is not None and post not in posts:
                continue
            exposed, opened = len(row['exposed']), len(row['opened'])
            opened_exposed = len(row['exposed'] & row['opened'])
            output.append({'phase': phase, 'post_id': post, 'arm': arm,
                           'exposures': exposed, 'opened': opened,
                           'opened_exposed': opened_exposed,
                           'rejected_opened': len(row['rejected_opened']),
                           'open_rate': opened_exposed / exposed if exposed else None,
                           'accepted': dict(sorted(row['accepted'].items())),
                           'rejected': dict(sorted(row['rejected'].items()))})
        return output

    def creative_feedback(self, publications, *, through_phase):
        """Only the caller-supplied institution's publications, no identities or orders."""
        totals = defaultdict(Counter)
        publications = {post: item for post, item in publications.items()
                        if item.get('phase', -1) <= through_phase}
        for item in publications.values():
            totals[item['creative_id']].update(dict.fromkeys(
                ('exposures', 'opened', 'opened_exposed', 'like', 'save', 'comment'), 0))
        for row in self.report(through_phase=through_phase, posts=publications):
            creative = publications[row['post_id']]['creative_id']
            total = totals[creative]
            for field in ('exposures', 'opened', 'opened_exposed'):
                total[field] += row[field]
            for action in ('like', 'save', 'comment'):
                total[action] += row['accepted'].get(action, 0)
        return {creative: {**dict(c), 'open_rate': c['opened_exposed'] / c['exposures'] if c['exposures'] else None}
                for creative, c in sorted(totals.items())}

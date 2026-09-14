"""Observer-only measurements over an already selected session (or its prefix)."""
from collections import Counter


TRADE_KINDS = ('subscribe', 'redeem', 'buy', 'sell', 'cancel_order')


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _following(view):
    values = view.get('following')
    if isinstance(values, list):
        return len({v for v in values if isinstance(v, str)})
    return _count(view.get('counts', {}).get('following'))


def summarize_session(frames, policy_mode=None):
    """Use only supplied frames, never a run, account history or future snapshot.

    Exposures use delivered BEFORE feed/detail; opens use accepted open/comments
    actions, matching BehaviorLedger. After views supply own counters only.
    Missing observations are null, whereas observed absence of actions is zero.
    """
    frames = list(frames)
    source = {'phase': None, 'agent_id': None, 'policy_mode': policy_mode}
    if frames:
        source.update(phase=frames[0]['phase'], agent_id=frames[0]['agent_id'])
    exposed, opened = set(), set()
    accepted, rejected = Counter(), Counter()
    targets = []
    consumed, measured = 0, 0
    for frame in frames:
        for key in ('agent_id', 'phase'):
            if frame[key] != source[key]:
                raise ValueError('session frames must share agent_id and phase')
        before, after = frame['before'], frame.get('after') or {}
        for view in (before, after):
            for key in ('agent_id', 'phase'):
                if key in view and view[key] != source[key]:
                    raise ValueError('session view has mixed agent_id or phase')
        cards = list(before.get('feed') or [])
        if before.get('detail'):
            cards.append(before['detail'])
        exposed.update(c['post_id'] for c in cards
                       if isinstance(c.get('post_id'), str))
        action, result = frame.get('action'), frame.get('result')
        if isinstance(action, dict) and isinstance(result, dict):
            kind = action.get('kind')
            kind = kind if isinstance(kind, str) else 'invalid'
            status = result.get('status')
            if status in ('accepted', 'rejected'):
                (accepted if status == 'accepted' else rejected)[kind] += 1
                if status == 'accepted' and kind in ('open', 'comments'):
                    post = action.get('post_id')
                    if isinstance(post, str):
                        opened.add(post)
                if kind in ('follow', 'unfollow'):
                    # A supplied post_id is deliberately not an attribution.
                    handle = result.get('handle', action.get('handle'))
                    targets.append({'kind': kind, 'status': status,
                                    'handle': handle if isinstance(handle, str) else None})
            start, end = _count(before.get('step')), _count(after.get('step'))
            if start is not None and end is not None and end >= start:
                consumed += end - start
                measured += 1
    n = len(frames)
    attempts = sum(accepted.values()) + sum(rejected.values())
    action_frames = sum(isinstance(f.get('action'), dict) and
                        isinstance(f.get('result'), dict) for f in frames)
    def pair(kind):
        return {'accepted': accepted[kind] if n else None,
                'rejected': rejected[kind] if n else None}
    last_after = (frames[-1].get('after') or {}) if n else {}
    return {
        'source': source, 'observation_status': 'observed' if n else 'no_observations',
        'observed_frames': n,
        'exposed_posts': len(exposed) if n else None,
        'opened_posts': len(opened) if n else None,
        'opened_exposed_posts': len(opened & exposed) if n else None,
        'open_rate': len(opened & exposed) / len(exposed) if exposed else None,
        'action_attempts': attempts if n else None,
        'accepted': dict(sorted(accepted.items())),
        'rejected': dict(sorted(rejected.items())),
        'consumed_action_steps': consumed if n and measured == action_frames else None,
        'steps_observed_actions': measured,
        'remaining_steps_after': _count(last_after.get('remaining_steps')),
        'following_before': _following(frames[0]['before']) if n else None,
        'following_after': _following(last_after),
        'interactions': {k: pair(k) for k in ('like', 'save', 'comment')},
        'following_actions': {k: pair(k) for k in ('follow', 'unfollow')},
        'following_targets': targets,
        'trading_actions': {k: pair(k) for k in TRADE_KINDS},
        'units': {'posts': 'unique posts within selected actor and phase',
                  'open_rate': 'accepted opened posts intersect delivered posts / delivered posts',
                  'actions': 'action attempts by kind and acceptance status',
                  'steps': 'observed after.step minus before.step per action',
                  'following': 'own unique handles at first before / last after'},
        'notice': ('仅统计所选会话已保存前缀；曝光仅来自实际投递的 before。'
                   '打开包含 accepted open/comments。脚本策略与 LLM 策略不混同；'
                   'external 标签本身不证明 LLM 调用。关注仅记录目标动作，不推断帖子归因；'
                   '零关注不代表没有机会。交易接受不等于成交，不汇总钱包或价格，不推断收益或心理。'),
    }

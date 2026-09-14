"""私有浏览 checkpoint 的只读行为报告，不运行策略、不重放、不访问源数据库。

用法：python -m flowmirror.analysis.marketing_behavior --run RUN --out PRIVATE/report.json
输出是新的 JSON 文件；调用方须选择私有产物目录，已有文件不会覆盖。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from pathlib import Path

from flowmirror.platform.behavior import BehaviorLedger
from flowmirror.platform.browse_store import read_checkpoint


TRADE_ACTIONS = frozenset(('subscribe', 'redeem', 'buy', 'sell', 'cancel_order'))
ORDER_STATES = ('submitted', 'filled', 'settled', 'cancelled', 'rejected')
NOTICE = ('仅描述已保存的浏览行为与模拟券商记录；订单链接不代表因果营销效果，'
          '请求金额不等于资金流，不合并币种或产品；机构策略只读取自身聚合反馈。')
UNITS = {
    'exposures': 'distinct agent_id × phase × post_id in frame.before feed or detail only',
    'opened': 'distinct agent_id × phase × post_id with accepted open/comments action',
    'rejected_opened': 'distinct agent_id × phase × post_id with rejected open/comments action; may overlap opened',
    'open_rate': 'opened_exposed / exposures; numerator is the intersection of delivered and accepted-open units',
    'actions': 'committed frame count, split accepted/rejected; action post targets are attempts, not broker order links; post_id=null means no post attribution',
    'rollups': 'sum of phase×post×arm rows; not globally unique people',
    'orders': 'distinct (agent_id, order_id); current status is exclusive, lifecycle counts overlap',
    'requested_amount': 'buy/subscribe order budget in its currency, including cancelled/rejected orders',
    'requested_units': 'sell/redeem requested product units; buy budgets do not specify requested units',
    'executed_gross': 'actual filled gross by currency, market, instrument and side; excludes fees',
    'executed_units': 'actual filled product units; never sum across products',
    'cash': 'cash_delta_at_fill plus cash_delta_at_settlement; pending_receivable is not cash received',
    'source_post_id': 'only the actual broker order source_post_id; no last-viewed or action-based inference',
    'order_phase': 'accepted submission frame phase when available; execution times remain in lifecycle_events',
}


def _decisions(state):
    for snapshot in state.get('institution_snapshots', []):
        for decision in snapshot.get('decisions', []):
            yield snapshot['phase'], decision


def campaign_posts(state):
    """发布结果给出实际帖子主键；创意库提供素材来源，不猜测 campaign 命名。"""
    owners = {owner['id']: owner for owner in state.get('spec', {}).get('marketing', [])}
    output = {}
    for phase, decision in _decisions(state):
        action, result = decision.get('action', {}), decision.get('result', {})
        if action.get('kind') != 'publish' or result.get('status') != 'accepted':
            continue
        post = result.get('post_id')
        if not isinstance(post, str):
            continue
        identity, before = decision.get('institution_id'), decision.get('before', {})
        owner = owners.get(identity, {})
        creative_id = action.get('creative_id')
        library = owner.get('creatives', [])
        creative = next((c for c in library if c.get('id') == creative_id), None)
        if creative is None:
            creative = next((c for c in before.get('available_creatives', [])
                             if c.get('id') == creative_id), {})
        card = creative.get('card', {})
        output[post] = {
            'post_id': post, 'institution_id': identity, 'creative_id': creative_id,
            'phase': phase, 'source_post_id': card.get('post_id'),
            'source_published_at': card.get('published_at'),
            'org': owner.get('org', before.get('org')),
            'market': owner.get('market', before.get('market')),
            'strategy': before.get('strategy', owner.get('strategy')),
            'action_reason': action.get('reason'), 'result_reason': result.get('reason'),
            'feedback_through_phase': before.get('feedback_through_phase'),
            'provenance': 'institution_snapshots.accepted_publish + creative_library',
        }
    return [output[key] for key in sorted(output)]


def summarize_marketing(state):
    """供 BrowseObserver.summary 使用的紧凑摘要，无投资者或订单明细。"""
    config = state.get('spec', {}).get('marketing', [])
    identities = {owner['id'] for owner in config}
    strategies = {owner['strategy'] for owner in config if owner.get('strategy')}
    waits = 0
    for _, decision in _decisions(state):
        if decision.get('institution_id') is not None:
            identities.add(decision['institution_id'])
        if decision.get('before', {}).get('strategy'):
            strategies.add(decision['before']['strategy'])
        waits += (decision.get('action', {}).get('kind') == 'wait'
                  and decision.get('result', {}).get('status') == 'accepted')
    return {'enabled': bool(identities), 'institution_count': len(identities),
            'publication_count': len(campaign_posts(state)), 'wait_count': waits,
            'strategy_label': ('离线规则策略：' + ', '.join(sorted(strategies))) if strategies else '未配置机构策略',
            'notice': NOTICE}


def _rollup(rows, fields):
    groups = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        target = groups.setdefault(key, {**dict(zip(fields, key)),
            **dict.fromkeys(('exposures', 'opened', 'opened_exposed', 'rejected_opened'), 0),
            'accepted': Counter(), 'rejected': Counter()})
        for field in ('exposures', 'opened', 'opened_exposed', 'rejected_opened'):
            target[field] += row[field]
        for field in ('accepted', 'rejected'):
            target[field].update(row[field])
    output = []
    for _, row in sorted(groups.items(), key=lambda item: tuple((v is not None, v) for v in item[0])):
        row['open_rate'] = row['opened_exposed'] / row['exposures'] if row['exposures'] else None
        for field in ('accepted', 'rejected'):
            row[field] = dict(sorted(row[field].items()))
        output.append(row)
    return output


def _remember_accounts(latest, accounts, position):
    for actor, account in accounts.items():
        if actor not in latest or position > latest[actor][0]:
            latest[actor] = (position, account)


def _number(value):
    return Decimal(str(value)) if value is not None else None


def _event_cash(events, name, fallback=None):
    values = [_number(e['cash_delta']) for e in events
              if e.get('event') == name and 'cash_delta' in e]
    return sum(values, Decimal(0)) if values else _number(fallback)


def _order_rows(latest, arms, submissions, campaigns, products):
    output = []
    for actor, (_, account) in sorted(latest.items()):
        events = defaultdict(list)
        for event in account.get('ledger', []):
            # broker ledger 在该账户中是累积历史；只读最新账户，避免逐帧重复相加。
            events[(actor, event['order_id'])].append(event)
        own_orders = {(actor, o['order_id']): o for o in account.get('orders', [])}
        for key, order in sorted(own_orders.items()):
            history = events[key]
            status, side = order['status'], order['side']
            filled = status in ('filled', 'settled') or any(e['event'] == 'filled' for e in history)
            source = order.get('source_post_id')
            product = products.get((order.get('market'), order.get('instrument_id')), {})
            submitted = next((e for e in history if e['event'] == 'submitted'), {})
            lifecycle = {e['event'] for e in history} | {status, 'submitted'}
            if filled:
                lifecycle.add('filled')
            output.append({
                'agent_id': actor, 'order_id': key[1], 'arm': arms.get(actor),
                'phase': submissions.get(key), 'source_post_id': source,
                'campaign': campaigns.get(source),
                'market': order.get('market'), 'instrument_id': order.get('instrument_id'),
                'currency': order.get('currency'), 'product_kind': product.get('kind'),
                'kind': order.get('kind'), 'side': side, 'status': status,
                'requested_amount': _number(submitted.get('reserved_cash', order.get('amount'))) if side == 'buy' else None,
                'requested_units': _number(submitted.get('reserved_units', order.get('units'))) if side == 'sell' else None,
                'executed_gross': _number(order.get('gross')) if filled else Decimal(0),
                'executed_units': _number(order.get('units')) if filled else Decimal(0),
                'fees': _number(order.get('fee')) if filled else Decimal(0),
                'cash_delta_at_fill': _event_cash(history, 'filled', order.get('cash_delta')) if filled else Decimal(0),
                'cash_delta_at_settlement': _event_cash(history, 'settled') if status == 'settled' else Decimal(0),
                'pending_receivable': _number(order.get('receivable')) if status == 'filled' else Decimal(0),
                'lifecycle_states': [s for s in ORDER_STATES if s in lifecycle],
                'lifecycle_events': [{k: e[k] for k in ('event', 'at', 'reason', 'cash_delta') if k in e}
                                     for e in history],
            })
    return output


def _order_groups(orders):
    fields = ('phase', 'source_post_id', 'arm', 'currency', 'market', 'instrument_id', 'product_kind', 'side')
    amounts = ('requested_amount', 'requested_units', 'executed_gross', 'executed_units',
               'fees', 'cash_delta_at_fill', 'cash_delta_at_settlement', 'pending_receivable')
    groups = {}
    for order in orders:
        key = tuple(order[field] for field in fields)
        target = groups.setdefault(key, {**dict(zip(fields, key)), 'order_count': 0,
            'status_counts': dict.fromkeys(ORDER_STATES, 0),
            'lifecycle_counts': dict.fromkeys(ORDER_STATES, 0),
            **dict.fromkeys(amounts, Decimal(0)), 'missing_values': Counter()})
        target['order_count'] += 1
        target['status_counts'][order['status']] += 1
        for status in order['lifecycle_states']:
            target['lifecycle_counts'][status] += 1
        for field in amounts:
            if order[field] is not None:
                target[field] += order[field]
            elif (field, order['side']) not in (('requested_amount', 'sell'), ('requested_units', 'buy')):
                target['missing_values'][field] += 1
    output = []
    for _, row in sorted(groups.items(), key=lambda item: tuple((v is not None, v) for v in item[0])):
        # 缺失记录保持未知，不把可见的部分金额伪装成完整合计。
        for field in row['missing_values']:
            row[field] = None
        row['requested_units' if row['side'] == 'buy' else 'requested_amount'] = None
        row['missing_values'] = dict(row['missing_values'])
        output.append(row)
    return output


def _json_numbers(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_numbers(item) for item in value]
    return value


def build_report(state):
    """分析调用方交来的已提交状态；不把 pending 策略请求纳入行为。"""
    with localcontext() as context:
        context.prec = 80
        return _json_numbers(_build_report(state))


def _build_report(state):
    spec = state.get('spec', {})
    arms = {agent['id']: agent.get('arm') for agent in spec.get('agents', [])}
    campaigns = {row['post_id']: row for row in campaign_posts(state)}
    latest, submissions = {}, {}
    for phase, accounts in enumerate(state.get('account_snapshots', [])):
        _remember_accounts(latest, accounts, (phase, -1, 0))
    ledger = BehaviorLedger()
    trade_counts = {'accepted': Counter(), 'rejected': Counter()}
    rejected_without_order = Counter()
    frame_count = 0
    for index, frame in enumerate(state.get('frames', [])):
        frame_count += 1
        actor, phase = frame['agent_id'], frame['phase']
        ledger.record(frame, arms.get(actor))
        for side, name in enumerate(('before', 'after')):
            account = frame.get(name, {}).get('account')
            if account is not None:
                _remember_accounts(latest, {actor: account}, (phase, index, side))
        action, result = frame.get('action', {}), frame.get('result', {})
        kind = action.get('kind')
        if isinstance(kind, str) and kind in TRADE_ACTIONS:
            status = 'accepted' if result.get('status') == 'accepted' else 'rejected'
            trade_counts[status][kind] += 1
            if status == 'rejected' and not result.get('order_id'):
                rejected_without_order[kind] += 1
            if status == 'accepted' and kind != 'cancel_order' and result.get('order_id'):
                submissions[(actor, result['order_id'])] = phase
    rows = ledger.report()
    for row in rows:
        row['campaign'] = campaigns.get(row['post_id'])
    products = {(p['market'], p['instrument_id']): p
                for p in spec.get('trading', {}).get('instruments', [])}
    orders = _order_rows(latest, arms, submissions, campaigns, products)
    status_counts = dict.fromkeys(ORDER_STATES, 0)
    for order in orders:
        status_counts[order['status']] += 1
    return {
        'report_kind': 'observer_only_marketing_behavior',
        'run_name': spec.get('name'), 'status': state.get('status'),
        'committed_frames': frame_count, 'pending_excluded': state.get('pending') is not None,
        'units': dict(UNITS), 'notice': NOTICE, 'marketing': summarize_marketing(state),
        'campaign_posts': list(campaigns.values()), 'ledger': rows,
        'per_phase': _rollup(rows, ('phase',)), 'per_post': _rollup(rows, ('post_id',)),
        'per_arm': _rollup(rows, ('arm',)),
        'trading': {'enabled': 'trading' in spec or bool(latest),
            'order_count': len(orders), 'source_linked_order_count': sum(o['source_post_id'] is not None for o in orders),
            'campaign_linked_order_count': sum(o['campaign'] is not None for o in orders),
            'status_counts': status_counts,
            'actions': {status: dict(sorted(counts.items())) for status, counts in trade_counts.items()},
            'rejected_without_order_id': dict(sorted(rejected_without_order.items())),
            'orders': orders, 'order_groups': _order_groups(orders)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path, help='existing browsing run directory (read only)')
    parser.add_argument('--out', required=True, type=Path, help='new JSON file in a private output directory')
    args = parser.parse_args(argv)
    try:
        report = build_report(read_checkpoint(args.run))
        payload = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(str(args.out.resolve()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

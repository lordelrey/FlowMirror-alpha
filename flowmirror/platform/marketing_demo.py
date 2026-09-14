"""Private synthetic CN/US marketing exercise using transparent offline rules.

读者的 B 偏好是显式的合成设定。曝光、互动和订单是模拟执行结果，
不代表真实人群偏好、LLM 自主决策或营销的因果效果。
"""
from __future__ import annotations

from flowmirror.market.tape import instant
from flowmirror.platform.broker_demo import account_demo_spec


def marketing_demo_spec():
    spec = account_demo_spec()
    spec.update(name='marketing-cn-us-synthetic-demo', policy_name='marketing-rule-v1',
                phases=6, max_steps=6, page_size=6, cards=[],
                feed_policy={'kind': 'recent_rotating', 'limit': 6, 'lookback_days': 7, 'seed': 0})
    spec['phase_times'][-1] = '2026-09-13T05:00:00+08:00'
    spec['synthetic_notice'] = (
        '合成机制演练：机构与读者均为 RULE；读者显式偏好 B，机构按上一阶段及更早的'
        '自身聚合反馈选择素材。价格外生；CNY/USD 分账，无外汇换算；费用、成交与结算分离。'
    )
    spec['marketing'] = []
    for market, identity, org in [('CN', 'cn_demo', '虚构青禾基金'),
                                   ('US', 'us_demo', 'Fictional Cedar Funds')]:
        creatives = []
        for variant, title, caption in [
            ('A', '[A] 回顾表现 / Performance recap', '虚构素材 A：回顾合成历史表现。'),
            ('B', '[B] 费用与回撤 / Fees and drawdowns', '虚构素材 B：解释费用、波动与持有期限。'),
        ]:
            creatives.append({'id': variant, 'available_phase': 0,
                              'card': {'post_id': f'synthetic_library_{identity}_{variant}',
                                       'org': org, 'market': market, 'title': title, 'caption': caption,
                                       'published_at': spec['phase_times'][0],
                                       'channel': 'synthetic_creative_library', 'comments_prev': []}})
        spec['marketing'].append({'id': identity, 'org': org, 'market': market,
                                  'strategy': 'feedback_select', 'publication_budget': 6,
                                  'publish_phases': list(range(6)), 'creatives': creatives})
    for agent in spec['agents']:
        agent['private_state']['beliefs'] = {'marketing_demo': {
            'synthetic': True, 'preferred_creative': 'B', 'preferred_title_token': '[B]',
            'order_amount': 1000, 'sell_units': 2 if agent['market'] == 'US' else 100,
        }}
        agent['private_state']['persona'] = '合成读者：只打开偏好的 B 素材；互动后按账户状态模拟交易。'
    # B 首次在 phase 1 发布；买入结算后仍需一个明确的卖出成交窗口。
    for product in spec['trading']['instruments']:
        market = product['market']
        if market == 'CN':
            cutoff = observed = '2026-09-11T15:00:00+08:00'
            available, settle, price = '2026-09-11T20:30:00+08:00', '2026-09-12T10:00:00+08:00', 1.03
        else:
            cutoff, observed = '2026-09-11T09:30:00-04:00', '2026-09-11T09:30:01-04:00'
            available, settle, price = '2026-09-11T09:30:02-04:00', '2026-09-12T16:00:00-04:00', 104
        product['execution_windows'].append({'accept_until': cutoff, 'observed_at': observed,
                                             'settle_at': settle})
        spec['market_data'].append({**{k: product[k] for k in ('market', 'instrument_id', 'currency', 'kind')},
                                   'price': price, 'observed_at': observed, 'available_at': available,
                                   'source': 'synthetic_fixture', 'synthetic': True})
    return spec


def _preferred(card, preferences):
    creative = card.get('creative_id')
    if isinstance(creative, str):
        return creative == preferences.get('preferred_creative')
    token, title = preferences.get('preferred_title_token'), card.get('title')
    return isinstance(token, str) and bool(token) and isinstance(title, str) and token in title


def marketing_policy(view):
    """RULE: act on visible preferred cards and this reader's observed account."""
    preferences = view.get('private_state', {}).get('beliefs', {}).get('marketing_demo', {})
    if view.get('done') or preferences.get('synthetic') is not True:
        return {'kind': 'finish'}
    detail = view.get('detail')
    if detail is None:
        choices = [c for c in view.get('feed', []) if _preferred(c, preferences)]
        if not choices:
            return {'kind': 'finish'}
        # 仅比较已经看到的卡片；发布时间决定新旧，post_id 只用于确定性打破平局。
        chosen = max(choices, key=lambda c: (instant(c['published_at']).timestamp()
                     if c.get('published_at') else float('-inf'), c['post_id']))
        return {'kind': 'open', 'post_id': chosen['post_id']}
    if not _preferred(detail, preferences):
        return {'kind': 'finish'}
    post = detail['post_id']
    step = view['step']
    if step in (1, 2):
        return {'kind': 'like' if step == 1 else 'save', 'post_id': post}
    if step == 3:
        return {'kind': 'comment', 'post_id': post,
                'text': '合成偏好说明：我更关注费用与回撤；这是离线规则演练。'}
    if step != 4:
        return {'kind': 'finish'}
    account = view.get('account', {})
    products = [p for p in view.get('tradable_instruments', [])
                if p['market'] == detail.get('market') and p['currency'] == account.get('currency')
                and p.get('next_window') is not None]
    for product in sorted(products, key=lambda p: p['instrument_id']):
        orders = [o for o in account.get('orders', [])
                  if o['market'] == product['market'] and o['instrument_id'] == product['instrument_id']
                  and o['status'] != 'cancelled']
        common = {'market': product['market'], 'instrument_id': product['instrument_id'], 'post_id': post}
        # broker 将当前可见的 campaign post_id 记为订单 source_post_id。
        amount = preferences.get('order_amount', 0)
        if not orders and 0 < amount <= account.get('available_cash', 0):
            return {**common, 'kind': 'subscribe' if product['kind'] == 'fund_nav' else 'buy', 'amount': amount}
        units = preferences.get('sell_units', 0)
        if orders and not any(o['side'] == 'sell' for o in orders) and units > 0:
            positions = account.get('positions', [])
            if any(p['market'] == product['market'] and p['instrument_id'] == product['instrument_id']
                   and p['available_units'] >= units for p in positions):
                return {**common, 'kind': 'redeem' if product['kind'] == 'fund_nav' else 'sell', 'units': units}
    return {'kind': 'finish'}

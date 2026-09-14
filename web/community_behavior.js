// Observer-only: the caller supplies the selected session summary.
const add = (parent, tag, value) => {
  const node = parent.ownerDocument.createElement(tag);
  node.textContent = value;
  parent.appendChild(node);
  return node;
};
const number = value => Number.isInteger(value) && value >= 0 ? value : '未观测';
const labels = {open: '打开', comments: '查看评论', like: '点赞', save: '收藏',
  comment: '评论', follow: '关注', unfollow: '取消关注', scroll: '滚动', finish: '结束',
  subscribe: '申购', redeem: '赎回', buy: '买入', sell: '卖出', cancel_order: '撤单'};

export function clearSessionBehavior(parent) {
  if (!parent) return;
  parent.replaceChildren();
  parent.hidden = true;
}

export function renderSessionBehavior(parent, metrics) {
  clearSessionBehavior(parent);
  if (!parent || !metrics) return;
  const block = add(parent, 'div', '');
  block.className = 'session-behavior';
  Object.assign(block.style, {minWidth: '0', maxWidth: '100%', overflowWrap: 'anywhere',
    fontSize: '0.9rem', lineHeight: '1.5'});
  add(block, 'h3', '截至当前微步的会话行为');
  const source = metrics.source || {};
  const mode = {scripted: '脚本规则（非 LLM）', null: '空策略（非 LLM）',
    external: '外部策略（是否 LLM 需调用证据）'}[source.policy_mode] || source.policy_mode || '策略未标注';
  add(block, 'p', `阶段 ${source.phase ?? '未观测'} · ${source.agent_id ?? '未观测'} · ${mode}`);
  if (metrics.observation_status === 'no_observations') {
    add(block, 'p', '暂无会话观测，不能解释为零动作。');
  } else {
    const rate = Number.isFinite(metrics.open_rate) && metrics.open_rate >= 0 && metrics.open_rate <= 1
      ? `${(metrics.open_rate * 100).toFixed(1)}%` : '未定义';
    add(block, 'p', `去重帖子：投递 ${number(metrics.exposed_posts)} · 接受打开 ${number(metrics.opened_posts)} · 交集 ${number(metrics.opened_exposed_posts)} · 打开率 ${rate}`);
    add(block, 'p', `动作 ${number(metrics.action_attempts)} 次 · 实耗 ${number(metrics.consumed_action_steps)} 步 · 最后动作后剩余 ${number(metrics.remaining_steps_after)} 步`);
    add(block, 'p', `自身关注数（人）：${number(metrics.following_before)} → ${number(metrics.following_after)}`);
    for (const [title, values] of [['互动', metrics.interactions], ['关注动作', metrics.following_actions], ['交易指令（非成交）', metrics.trading_actions]]) {
      add(block, 'p', `${title}（接受 / 拒绝，次）：${Object.entries(values || {}).map(([kind, v]) => `${labels[kind] || kind} ${number(v.accepted)}/${number(v.rejected)}`).join(' · ')}`);
    }
    const detail = add(block, 'details', '');
    add(detail, 'summary', '全部动作与关注目标明细');
    const kinds = [...new Set([...Object.keys(metrics.accepted || {}), ...Object.keys(metrics.rejected || {})])].sort();
    if (!kinds.length) add(detail, 'p', '已观测记录中没有已记录结果的动作。');
    for (const kind of kinds) add(detail, 'p', `${labels[kind] || kind}：接受 ${number(metrics.accepted?.[kind] ?? 0)} / 拒绝 ${number(metrics.rejected?.[kind] ?? 0)} 次`);
    for (const target of metrics.following_targets || []) add(detail, 'p', `${labels[target.kind] || target.kind} → ${target.handle ?? '目标未记录'}：${target.status === 'accepted' ? '接受' : '拒绝'}（无帖子归因）`);
  }
  add(block, 'p', metrics.notice || '仅当前所选会话的已观测行为。');
  parent.hidden = false;
}

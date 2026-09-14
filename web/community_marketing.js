// 观察者专用：只读取所选阶段的机构动作前记录，不读取本阶段结束后的反馈。
const text = (parent, tag, value, className) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value ?? '未记录';
  parent.appendChild(node);
  return node;
};
const count = value => Number.isInteger(value) && value >= 0 ? value : null;
const rate = value => Number.isFinite(value) && value >= 0 && value <= 1
  ? `${(value * 100).toFixed(1)}%` : '未定义';
const reasons = {
  outside_publish_schedule: '不在发布阶段', publication_budget_exhausted: '发布名额已用完',
  phase_publication_slot_exhausted: '本阶段已发布过', zero_observed_openings: '历史曝光中没有打开，暂不重复投放',
  no_available_creative: '暂无可用素材', rotation: '按轮换规则选择', first_publication: '首次投放该素材',
  no_observed_feedback: '尚无可用的历史反馈', observed_open_rate_below_threshold: '历史打开率低于阈值',
  past_open_rate: '按历史打开率选择', no_publication: '本次未发帖',
  simulated_post_published: '模拟帖子已发布', unsupported_institution_action: '不支持此机构动作',
  unavailable_creative_or_publication_slot: '素材或发布名额不可用',
};
const reason = value => reasons[value] || value || '未记录';

export function clearInstitutionPanel(panel) {
  if (!panel) return;
  panel.hidden = true;
  panel.replaceChildren();
}

function feedbackLine(parent, label, feedback) {
  text(parent, 'p', `${label}：曝光 ${count(feedback.exposures) ?? '未记录'} · 打开 ${count(feedback.opened) ?? '未记录'} · 打开率 ${rate(feedback.open_rate)}`);
}

function renderFeedback(parent, before, creativeId, phase) {
  const through = before.feedback_through_phase;
  // 不把反馈时点不明或晚于决策时点的数字显示成决策依据。
  if (!Number.isInteger(through) || through < -1 || through >= phase) {
    text(parent, 'p', '动作前反馈：未记录有效的历史截止阶段。', 'muted');
    return;
  }
  text(parent, 'p', through < 0 ? '动作前自身反馈：开局，尚无历史阶段。'
    : `动作前自身反馈：累计截至阶段 ${through}。`, 'muted');
  const feedback = before.feedback || {};
  const rows = Object.values(feedback);
  if (!rows.length) {
    text(parent, 'p', '尚无自身投放反馈；打开率未定义。');
    return;
  }
  const exposures = rows.every(row => count(row.exposures) !== null)
    ? rows.reduce((n, row) => n + row.exposures, 0) : null;
  const opened = rows.every(row => count(row.opened) !== null)
    ? rows.reduce((n, row) => n + row.opened, 0) : null;
  feedbackLine(parent, '自身全部已投素材', {exposures, opened,
    open_rate: exposures > 0 && opened !== null ? opened / exposures : null});
  if (creativeId && Object.hasOwn(feedback, creativeId)) {
    const selected = feedback[creativeId];
    feedbackLine(parent, '本次所选素材', selected);
    text(parent, 'p', `所选素材历史互动：赞 ${count(selected.like) ?? '未记录'} · 收藏 ${count(selected.save) ?? '未记录'} · 评论 ${count(selected.comment) ?? '未记录'}`, 'muted');
  } else if (creativeId) {
    text(parent, 'p', '本次所选素材尚无自身投放反馈。', 'muted');
  }
}

export function renderInstitutionPanel(panel, snapshot, phase) {
  clearInstitutionPanel(panel);
  if (!panel || !Number.isInteger(phase) || !snapshot || snapshot.phase !== phase) return;
  const decisions = (snapshot.decisions || []).filter(d => d.before?.phase === phase);
  if (!decisions.length) return;
  text(panel, 'h2', '机构决策');
  text(panel, 'p', `阶段 ${phase} · 规则策略，非真模型 · 观察者专用`, 'institution-note');
  text(panel, 'p', '机构在阶段起点决策，反馈来自历史阶段；此面板不属于投资者 agent 的策略输入。', 'muted institution-note');
  const list = text(panel, 'div', '', 'institution-list');
  for (const decision of decisions) {
    const before = decision.before, action = decision.action || {}, result = decision.result || {};
    const item = text(list, 'article', '', 'institution-decision');
    item.dataset.institutionId = decision.institution_id;
    text(item, 'h3', `${before.org || decision.institution_id} · ${before.market || '市场未记录'}`);
    const strategy = {rotate: '轮换发布', feedback_select: '历史反馈选择'}[before.strategy] || before.strategy || '未记录';
    text(item, 'p', `${decision.institution_id} · 策略：${strategy}`, 'muted');
    const published = action.kind === 'publish' && result.status === 'accepted' && !!result.post_id;
    const acceptedWait = action.kind === 'wait' && result.status === 'accepted';
    const actionName = {publish: '发布', wait: '等待'}[action.kind] || action.kind || '未记录';
    text(item, 'p', published ? '实际动作：已发布（模拟转发）' : acceptedWait ? '实际动作：等待（未发布）'
      : `实际动作：${actionName} · ${result.status === 'rejected' ? '未执行' : '执行未确认'}`, 'institution-action');
    text(item, 'p', `选择原因：${reason(action.reason)}`);
    text(item, 'p', `执行结果：${reason(result.reason)}`, 'muted');
    const creativeId = action.creative_id;
    text(item, 'p', `所选素材：${creativeId || '未选择'}`);
    // 只取本次所选素材；不将完整素材库或未来可用素材写入 DOM。
    const creative = creativeId && before.available_creatives?.find(c => c.id === creativeId);
    if (creative?.card) {
      const source = creative.card;
      if (source.title) text(item, 'p', source.title, 'institution-source');
      text(item, 'p', `来源：${source.channel || '渠道未记录'} · 原帖 ${source.post_id || '未记录'}`, 'institution-source');
      if (source.published_at) text(item, 'p', `来源发布时间：${source.published_at}`, 'muted');
    } else if (creativeId) {
      text(item, 'p', '来源：动作前记录未提供所选素材。', 'muted');
    }
    text(item, 'p', `模拟帖子 ID：${published ? result.post_id : acceptedWait || result.status === 'rejected' ? '未发帖' : '未记录'}`);
    const remaining = count(before.remaining_publications);
    const after = remaining !== null && (acceptedWait || result.status === 'rejected') ? remaining
      : remaining > 0 && published ? remaining - 1 : null;
    text(item, 'p', `剩余发布名额（次）：${remaining ?? '未记录'} → ${after ?? '未确认'}`, 'institution-slots');
    text(item, 'p', '名额变化按本次执行结果计算。', 'muted');
    renderFeedback(item, before, creativeId, phase);
  }
  panel.hidden = false;
}

export function addPublicationProvenance(parent, card) {
  if (!parent || !card) return;
  const simulated = card.publication_kind === 'simulated_campaign';
  // 普通脚本样例没有真实来源声明，不把它标成真实原帖。
  const historical = !card.publication_kind && ['xhs', 'sec_497'].includes(card.channel);
  if (!simulated && !historical) return;
  const label = text(parent, 'p', simulated
    ? `模拟转发 · 原帖 ${card.source_post_id || '来源未记录'}`
    : '真实原帖（历史素材）', 'publication-provenance');
  parent.insertBefore(label, parent.firstChild);
  if (simulated) {
    const source = text(parent, 'p', `模拟帖子：${card.post_id || '未记录'}${card.source_published_at ? ` · 原帖发布：${card.source_published_at}` : ''}`, 'publication-provenance publication-source');
    parent.insertBefore(source, label.nextSibling);
  }
}

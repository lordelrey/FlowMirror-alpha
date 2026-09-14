// Persisted microstep observer. This module never launches runs or calls models.
import {renderTradingAccount} from './community_account.js';
import {renderInstitutionPanel, clearInstitutionPanel, addPublicationProvenance} from './community_marketing.js';
import {renderSessionBehavior, clearSessionBehavior} from './community_behavior.js';
export function createBrowseUI(h) {
  const $ = (id) => document.getElementById(id);
  const {getJSON, addText, clearNode, setStatus, stopPlay, renderCard,
    detailMaterial, actionLabel, resultStatus, resetCards} = h;
  let token = 0, summary = null, trace = null, run = '';
  let visibleAgents = [];
  let ready = false;
  // 一次性深链：仅初始加载消费，之后不强制旧查询参数。
  let initialDeepLink = null;
  {
    const qp = new URLSearchParams(location.search);
    if (qp.get('mode') === 'browse' && qp.get('run')) initialDeepLink = {
      agent: qp.get('agent'),
      run: qp.get('run'),
      phase: qp.get('phase'),
      step: /^\d+$/.test(qp.get('step') || '') ? Number(qp.get('step')) : null,
    };
  }
  let pendingStep = null;
  const active = (t) => t === token && $('mode').value === 'browse';
  const option = (select, value, label) => {
    const o = document.createElement('option'); o.value = value; o.textContent = label;
    select.appendChild(o);
  };
  function empty(message) {
    clearInstitutionPanel($('institution-panel'));
    clearSessionBehavior($('behavior-panel'));
    $('market-panel').hidden = true;
    for (const id of ['feed', 'timeline', 'detail', 'account', 'network']) clearNode($(id));
    resetCards();
    addText($('detail'), 'p', message);
    $('detail').hidden = false;
    $('step').max = 0; $('step').value = 0; $('step-label').textContent = '暂无微步';
    $('agent-name').textContent = $('agent').value || '未选择';
    $('agent-state').textContent = '未发生记录';
  }
  function resetSelection() {
    clearInstitutionPanel($('institution-panel'));
    clearSessionBehavior($('behavior-panel'));
    ready = false; summary = null; trace = null; visibleAgents = [];
    $('agent-search').value = ''; $('agent-search').disabled = true;
    $('agent-search-count').textContent = '';
    $('agent').disabled = true; $('day').disabled = true;
  }
  function leave() {
    ++token; resetSelection();
  }
  async function enter() {
    stopPlay(); const t = ++token;
    resetSelection(); $('run').disabled = true;
    $('agent-search-panel').hidden = false;
    clearNode($('summary'));
    empty('正在读取浏览运行；暂不展示上一个模式的动作。');
    $('mode-note').textContent = '读取本地轨迹中 · 不会启动模型';
    setStatus('读取已保存的自主浏览运行…');
    const data = await getJSON('/api/community/browse/runs');
    if (!active(t)) return;
    const select = $('run'); clearNode(select);
    for (const item of data.runs) option(select, item.tag, item.tag);
    select.disabled = !data.runs.length;
    if (!data.runs.length) {
      summary = null; trace = null;
      for (const id of ['agent', 'day']) { clearNode($(id)); $(id).disabled = true; }
      clearNode($('summary'));
      empty('尚无已保存的浏览运行。先用 browse_cli 运行离线演练。');
      $('mode-note').textContent = '持久化微步模式 · 尚无运行';
      setStatus('没有轨迹；未启动任何模型或实验。'); return;
    }
    const requested = new URLSearchParams(location.search).get('run');
    if (data.runs.some(x => x.tag === (run || requested))) select.value = run || requested;
    else if (data.runs.some(x => x.tag === 'cold_start_script_v1')) select.value = 'cold_start_script_v1';
    await selectRun();
  }
  async function selectRun() {
    stopPlay(); const t = ++token;
    run = $('run').value;
    const macroLink = $('macro-link');
    if (macroLink) macroLink.href = `/market.html?run=${encodeURIComponent(run)}`;
    resetSelection();
    clearNode($('summary'));
    empty('正在读取所选运行；此时不展示上一个实验的动作。');
    $('mode-note').textContent = '读取本地轨迹中 · 不会启动模型';
    setStatus('正在为所选运行读取记录并建立观察索引，大轨迹首次加载可能需要数秒…');
    const data = await getJSON(`/api/community/browse/summary?run=${encodeURIComponent(run)}`);
    if (!active(t)) return;
    summary = data;
    visibleAgents = data.agents;
    $('agent-search-count').textContent = `${data.agents.length} / ${data.agents.length} 个 agent`;
    $('mode-note').textContent = `${data.label} · ${data.status} · ${data.financial?.enabled ? '含模拟订单与账户结算' : '不含交易与收益更新'}`;
    if (data.corpus_info) $('mode-note').textContent += ` · 真实历史素材 ${data.corpus_info.selected_posts} 帖 / ${data.corpus_info.selected_images} 图；`
      + (data.policy_mode === 'external' ? '行为来自外部策略，模型来源须核实' : '代理行为仍为脚本');
    const agent = $('agent'), phase = $('day');
    const previousAgent = agent.value, previousPhase = phase.value;
    clearNode(agent); clearNode(phase);
    for (const a of data.agents) option(agent, a.id, `${a.handle} · ${a.arm}`);
    for (let p = 0; p < data.phases; p++) option(phase, p,
      data.phase_times ? `${p} · ${data.phase_times[p]}` : `阶段 ${p}（非日历日）`);
    if (data.agents.some(a => a.id === previousAgent)) agent.value = previousAgent;
    if (/^\d+$/.test(previousPhase) && Number(previousPhase) < data.phases) phase.value = previousPhase;
    // 仅在初始深链匹配到该 run 时消费一次；此后修改 run/agent/phase 不再受旧参数影响。
    if (initialDeepLink) {
      const q = initialDeepLink; initialDeepLink = null;
      if (q.run === run) {
        const agentKnown = q.agent === null || q.agent === undefined || data.agents.some(a => a.id === q.agent);
        const phaseValid = q.phase === null || q.phase === undefined || (/^\d+$/.test(q.phase) && Number(q.phase) < data.phases);
        if (agentKnown && phaseValid) {
          if (q.agent !== null && q.agent !== undefined) agent.value = q.agent;
          if (q.phase !== null && q.phase !== undefined && /^\d+$/.test(q.phase) && Number(q.phase) < data.phases) phase.value = q.phase;
          if (q.step !== null && q.step !== undefined) pendingStep = q.step;
        }
      }
    }
    agent.disabled = !data.agents.length; phase.disabled = !data.phases;
    const box = $('summary'); clearNode(box);
    for (const [label, value] of [
      ['已保存动作', data.frames], ['完成阶段', `${data.completed_phases}/${data.phases}`],
      ['全程接受关注', data.accepted.follow || 0], ['实验模型调用', data.model_calls ?? '来源待核实'],
    ]) {
      const card = document.createElement('div'); card.className = 'metric-card'; box.appendChild(card);
      addText(card, 'div', label, 'label'); addText(card, 'div', value, 'value');
    }
    ready = true; $('agent-search').disabled = false;
    await selectTrace();
  }
  async function selectTrace() {
    if (!ready || $('mode').value !== 'browse' || $('day').disabled || !/^\d+$/.test($('day').value)) return;
    stopPlay(); const t = ++token;
    trace = null;
    if (!$('agent').value) { empty('没有匹配的 agent，请调整搜索。'); setStatus('没有匹配的 agent。'); return; }
    empty('正在读取所选 agent 与日期的已保存动作…');
    setStatus('读取所选 agent 的实际记录…');
    const data = await getJSON(`/api/community/browse/frame?run=${encodeURIComponent(run)}`
      + `&agent=${encodeURIComponent($('agent').value)}&phase=${encodeURIComponent($('day').value)}`);
    if (!active(t)) return;
    trace = data;
    $('step').min = 0; $('step').max = Math.max(0, data.frames.length - 1); $('step').value = 0;
    if (pendingStep !== null) {
      // 深链 step 按 frame.step 匹配（非数组下标，除非恰好相同）。
      let idx = data.frames.findIndex(f => f.step === pendingStep);
      pendingStep = null;
      if (idx >= 0) { $('step').value = idx; render(idx); setStatus(`从磁盘载入 ${data.frames.length} 个实际记录步骤；已定位深链微步。`); return; }
    }
    render(0); setStatus(`从磁盘载入 ${data.frames.length} 个实际记录步骤；没有重新生成演练。`);
  }
  async function filterAgents() {
    if (!ready || !summary || $('mode').value !== 'browse') return;
    const query = $('agent-search').value.trim().toLowerCase();
    const previous = $('agent').value;
    const armQuery = ['t', 'tc', 'tv'].includes(query);
    visibleAgents = summary.agents.filter(a => !query || (armQuery ? a.arm.toLowerCase() === query :
      [a.id, a.handle].some(v => v.toLowerCase().includes(query))));
    clearNode($('agent'));
    for (const a of visibleAgents) option($('agent'), a.id, `${a.handle} · ${a.arm}`);
    if (visibleAgents.some(a => a.id === previous)) $('agent').value = previous;
    $('agent').disabled = !visibleAgents.length;
    $('agent-search-count').textContent = `${visibleAgents.length} / ${summary.agents.length} 个 agent`;
    if ($('agent').value !== previous || !trace) await selectTrace();
  }
  function renderGraph(publicState, currentHandle) {
    const parent = $('network'); clearNode(parent);
    addText(parent, 'h3', '当前阶段公开网络');
    if (!publicState) { addText(parent, 'p', '此阶段尚未开始'); return; }
    const all = Object.keys(publicState.followers);
    let handles = all.sort((a, b) => publicState.followers[b] - publicState.followers[a] || a.localeCompare(b)).slice(0, 12);
    if (!handles.includes(currentHandle) && all.includes(currentHandle)) handles = [currentHandle, ...handles.slice(0, 11)];
    const ns = 'http://www.w3.org/2000/svg';
    const el = (name, attrs) => {
      const n = document.createElementNS(ns, name);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n;
    };
    const svg = el('svg', {viewBox: '0 0 320 230', role: 'img', 'aria-label': '当前阶段公开关注图'});
    svg.style.width = '100%';
    const defs = el('defs', {}), marker = el('marker', {id: 'browse-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto-start-reverse'});
    marker.appendChild(el('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#4d8178'}));
    defs.appendChild(marker); svg.appendChild(defs);
    const positions = new Map(handles.map((handle, i) => {
      const angle = i * Math.PI * 2 / handles.length - Math.PI / 2;
      return [handle, {x: 160 + 95 * Math.cos(angle), y: 107 + 75 * Math.sin(angle)}];
    }));
    for (const edge of publicState.edges) {
      const a = positions.get(edge.from), b = positions.get(edge.to);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
      svg.appendChild(el('line', {x1: a.x + dx * 14 / length, y1: a.y + dy * 14 / length,
        x2: b.x - dx * 18 / length, y2: b.y - dy * 18 / length, stroke: '#4d8178', 'stroke-width': 2, 'marker-end': 'url(#browse-arrow)'}));
    }
    for (const [handle, point] of positions) {
      svg.appendChild(el('circle', {cx: point.x, cy: point.y, r: 13,
        fill: handle === currentHandle ? '#ed735b' : '#4d8178'}));
      const label = el('text', {x: point.x, y: point.y + 29, 'text-anchor': 'middle', 'font-size': 10, fill: '#303a37'});
      label.textContent = `${handle} · ${publicState.followers[handle]}粉丝`; svg.appendChild(label);
    }
    parent.appendChild(svg);
    addText(parent, 'p', trace.notice, 'muted');
    const metrics = trace.public_metrics;
    addText(parent, 'p', `公开边 ${metrics.edges} · 获关注作者 ${metrics.followed_authors}/${metrics.agents}`);
    addText(parent, 'p', `头部份额：${metrics.top1_share === null ? '无关注，不定义' : (metrics.top1_share * 100).toFixed(1) + '%'} · HHI：${metrics.hhi === null ? '不定义' : metrics.hhi.toFixed(3)}`);
    if (all.length > 12) addText(parent, 'p', '图示最多12人，统计使用全体 agent。');
  }
  function render(index) {
    const f = trace && trace.frames[index];
    if (!f) {
      empty('该 agent 在这个阶段尚无已保存动作。');
      // A scheduled institution can wait even when no investor has a visible post.
      renderInstitutionPanel($('institution-panel'), trace?.institution_before, Number($('day').value));
      renderSessionBehavior($('behavior-panel'), trace?.behavior);
      return;
    }
    clearNode($('feed')); clearNode($('timeline')); resetCards();
    renderInstitutionPanel($('institution-panel'), trace.institution_before, Number($('day').value));
    renderSessionBehavior($('behavior-panel'), trace.behavior_timeline?.[index]);
    for (const p of f.before.feed) {
      const card = renderCard(p);
      addPublicationProvenance(card, p);
      // 保留现有素材/图片详情处理；在其渲染完成后补充公开来源标记。
      card.querySelector('button')?.addEventListener('click', () => addPublicationProvenance($('detail'), p));
    }
    const actor = summary.agents.find(a => a.id === f.agent_id);
    $('agent-name').textContent = actor.handle;
    $('avatar').textContent = actor.id.slice(-1).toUpperCase();
    $('agent-state').textContent = `${actor.arm} · 阶段 ${f.phase} · ${resultStatus(f.result)}`;
    for (const [i, frame] of trace.frames.entries()) {
      const li = addText($('timeline'), 'li', `${i + 1}. ${actionLabel(frame.action)} · ${resultStatus(frame.result)}`, 'event');
      li.classList.toggle('selected', i === index); li.tabIndex = 0;
      const select = () => { stopPlay(); $('step').value = i; render(i); };
      li.addEventListener('click', select);
      li.addEventListener('keydown', e => { if (e.key === 'Enter') select(); });
    }
    const detail = $('detail'); clearNode(detail); detail.hidden = false;
    addText(detail, 'h3', `步骤 ${index + 1} · 实际输入 → 动作 → 后状态`);
    if (f.before.detail) {
      detailMaterial(detail, f.before.detail);
      addPublicationProvenance(detail, f.before.detail);
    }
    else addText(detail, 'p', '动作前未展开详情，agent 此刻只看到左侧卡片预览。');
    addText(detail, 'h4', `动作：${actionLabel(f.action)}`);
    if (f.action.text) addText(detail, 'p', f.action.text);
    if (f.action.reason) addText(detail, 'p', `策略给出的理由：${f.action.reason}（不等于因果解释）`);
    addText(detail, 'p', `执行：${resultStatus(f.result)} · ${f.result.reason || '无附加说明'}`);
    addText(detail, 'p', `自己的关注（即时）：${f.before.following.length} → ${f.after.following.length}`);
    addText(detail, 'p', `动作后关注对象：${f.after.following.join('、') || '无'}`);
    addText(detail, 'h4', '动作前平台推荐');
    addText(detail, 'p', f.before.recommendations.map(r => `${r.handle}（${r.followers}粉丝）`).join('、') || '冷启动或暂无符合条件的账号');
    if (f.delivery) addText(detail, 'p', `附件投递记录：${f.delivery.visual_delivery || '未记录'}`);
    const disclosure = document.createElement('details');
    addText(disclosure, 'summary', '展开这一步的完整记录（观察者专用）');
    const pre = addText(disclosure, 'pre', JSON.stringify(f, null, 2));
    pre.style.whiteSpace = 'pre-wrap'; pre.style.overflowWrap = 'anywhere';
    detail.appendChild(disclosure);
    const close = addText(detail, 'button', '关闭详情', 'btn ghost'); close.id = 'close-detail'; close.type = 'button';
    close.addEventListener('click', () => { detail.hidden = true; });
    const account = $('account'); clearNode(account);
    addText(account, 'p', summary.account_note);
    const own = f.before.private_state;
    if (f.after.account) {
      renderTradingAccount(account, f.after.account, trace.account_history, {addText, clearNode});
    } else {
      addText(account, 'p', `该 agent 的初始现金：${own.cash ?? '未记录'}`);
    }
    const recent = document.createElement('details');
    addText(recent, 'summary', '仅自己的最近动作（最多8条）');
    for (const item of f.before.recent_actions) addText(recent, 'p', `阶段 ${item.phase} · ${actionLabel(item.action)}`);
    if (!f.before.recent_actions.length) addText(recent, 'p', '尚无个人动作历史');
    account.appendChild(recent);
    renderGraph(trace.public_before, actor.handle);
    const market = f.before.market_snapshot;
    $('market-panel').hidden = !market;
    if (market) {
      const box = $('market-data'); clearNode(box);
      addText(box, 'p', `模拟时钟：${market.as_of}`);
      addText(box, 'p', '外生历史/合成数据，不是实时行情，不是预测。');
      for (const quote of market.quotes) {
        addText(box, 'h4', `${quote.market} · ${quote.instrument_id}`);
        addText(box, 'p', `${quote.kind === 'fund_nav' ? '基金净值' : '交易价格'} ${quote.price} ${quote.currency}`);
        addText(box, 'p', `${quote.synthetic ? '合成样例' : '来源记录'} · ${quote.source}`);
        addText(box, 'p', `观测：${quote.observed_at}；可用：${quote.available_at}`);
      }
      if (!market.quotes.length) addText(box, 'p', '此刻没有已发布且可见的报价。');
    }
    $('step-label').textContent = `阶段 ${f.phase} · 微步 ${index + 1}/${trace.frames.length} · 全局序号 ${f.index}`;
  }
  return {enter, leave, selectRun, selectTrace, filterAgents, render, agents: () => visibleAgents};
}

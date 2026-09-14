// 市场观察台：只读观察视图。全部 GET，无写入、无执行、无预测请求。
const $ = (id) => document.getElementById(id);
let token = 0;
let state = null; // 最近一次成功加载的 macro 数据
let quoteLimit = 50;
let selectedInstrument = null;
let selectedAgent = null;
let selectedProduct = null;

const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '未知' :
  (typeof v === 'number' ? v.toLocaleString('zh-CN', {maximumFractionDigits: d}) : v);
const pct = (v, d = 2) => (v === null || v === undefined) ? '未知' : `${v.toFixed(d)}%`;
const frac = (v, d = 1) => (v === null || v === undefined) ? '未知' : `${(v * 100).toFixed(d)}%`;
const cls = (v) => (typeof v === 'number' && v > 0) ? 'pos' : (typeof v === 'number' && v < 0) ? 'neg' : '';

function addText(parent, tag, text, className) {
  const n = document.createElement(tag);
  n.textContent = text === null || text === undefined ? '未知' : String(text);
  if (className) n.className = className;
  parent.appendChild(n); return n;
}
function cell(row, text, className) { return addText(row, 'td', text, className); }
async function getJSON(url) {
  const r = await fetch(url, {method: 'GET'});
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
function setStatus(msg) { $('status').textContent = msg; }
function setControls(disabled) {
  for (const id of ['run', 'phase', 'phase-prev', 'phase-next', 'market-filter', 'currency-filter', 'reload']) $(id).disabled = disabled;
}
function marketClass(m) { return String(m).toUpperCase().includes('CN') ? 'CN' : String(m).toUpperCase().includes('US') ? 'US' : m; }
function marketMatch(item) {
  const f = $('market-filter').value;
  if (!f || f === 'all') return true;
  if (f === 'CN' || f === 'US') return String(item.market).toUpperCase().includes(f);
  return item.market === f;
}
function currencyMatch(item) {
  const f = $('currency-filter').value;
  return !f || f === 'all' || item.currency === f;
}
function microLink(agentId, phase, step) {
  const p = new URLSearchParams();
  p.set('mode', 'browse'); p.set('run', $('run').value);
  if (agentId !== undefined) p.set('agent', agentId);
  if (phase !== undefined && phase !== null) p.set('phase', phase);
  if (step !== undefined && step !== null) p.set('step', step);
  return `/community.html?${p.toString()}`;
}
function fillSelect(select, values, allLabel) {
  const prev = select.value;
  clearNode(select);
  if (allLabel) { const o = document.createElement('option'); o.value = 'all'; o.textContent = allLabel; select.appendChild(o); }
  for (const v of values) { const o = document.createElement('option'); o.value = v.value ?? v; o.textContent = v.label ?? v; select.appendChild(o); }
  if ([...select.options].some(o => o.value === prev)) select.value = prev;
}
function clearNode(n) { while (n.firstChild) n.removeChild(n.firstChild); }
function metricCard(box, label, value) {
  const c = document.createElement('div'); c.className = 'metric-card';
  addText(c, 'div', label, 'label'); addText(c, 'div', value, 'value'); box.appendChild(c);
}

async function init() {
  // 移动端控件宽度安全（不改 HTML/CSS 文件）
  const st = document.createElement('style');
  st.textContent = '.controls select{max-width:100%;min-width:0}.controls>*{max-width:100%}main{min-width:0}';
  document.head.appendChild(st);
  setControls(true);
  setStatus('读取可用运行…');
  let runs;
  try { runs = (await getJSON('/api/community/browse/runs')).runs; }
  catch (e) { setStatus(`读取运行列表失败：${e.message}`); return; }
  const select = $('run'); clearNode(select);
  if (!runs.length) { setStatus('尚无已保存的运行。'); return; }
  for (const r of runs) { const o = document.createElement('option'); o.value = r.tag; o.textContent = r.tag; select.appendChild(o); }
  const requested = new URLSearchParams(location.search).get('run');
  const preferred = runs.some(r => r.tag === requested) ? requested : runs[0].tag;
  select.value = preferred;
  select.disabled = false;
  $('reload').addEventListener('click', () => loadMacro($('phase').value));
  select.addEventListener('change', () => { clearNode($('phase')); state = null; selectedProduct = null; selectedAgent = null; selectedInstrument = null; const si = $('instrument-search'); if (si) si.value = ''; quoteLimit = 50; loadMacro(); });
  $('phase').addEventListener('change', () => loadMacro($('phase').value));
  $('phase-prev').addEventListener('click', () => stepPhase(-1));
  $('phase-next').addEventListener('click', () => stepPhase(1));
  const onFilterChange = () => {
    if (!state?.selected) return;
    selectedProduct = null; selectedInstrument = null; selectedAgent = null;
    renderMarket(); renderFinance(); renderFlows(); renderSocial(); renderInstitutions();
  };
  $('market-filter').addEventListener('change', onFilterChange);
  $('currency-filter').addEventListener('change', onFilterChange);
  $('instrument-search').addEventListener('input', () => { quoteLimit = 50; renderQuotes(); });
  $('quote-more').addEventListener('click', () => { quoteLimit += 50; renderQuotes(); });
  $('quote-reset').addEventListener('click', () => { selectedInstrument = null; quoteLimit = 50; renderQuotes(); renderGroups(); renderTrend(); });
  $('account-reset').addEventListener('click', () => { selectedAgent = null; renderAccounts(); });
  $('order-cumulative').addEventListener('change', renderOrders);
  $('order-reset').addEventListener('click', () => { selectedProduct = null; renderFlows(); });
  await loadMacro();
}
function stepPhase(d) {
  const s = $('phase');
  if (!s.options.length) return;
  const i = Math.min(s.options.length - 1, Math.max(0, s.selectedIndex + d));
  if (i !== s.selectedIndex) { s.selectedIndex = i; loadMacro(s.value); }
}
let urlPhaseConsumed = false;
async function loadMacro(explicitPhase) {
  const t = ++token;
  setControls(true);
  setStatus('读取宏观视图（GET，只读）…');
  const run = $('run').value;
  // 请求前清空旧运行展示，避免旧数据挂在新 run 标签下
  state = null; selectedProduct = null; selectedInstrument = null; selectedAgent = null;
  for (const id of ['overview', 'timeline-body', 'market-overview', 'group-body', 'trend', 'quote-body',
    'finance-overview', 'currency-body', 'account-body', 'flow-body', 'order-body',
    'social-overview', 'post-body', 'graph-metrics', 'institution-overview', 'decision-body']) {
    const n = $(id); if (n) clearNode(n);
  }
  for (const id of ['overview-notice', 'flow-note', 'quote-count', 'order-title', 'account-title']) {
    const n = $(id); if (n) n.textContent = '';
  }
  let url = `/api/community/browse/macro?run=${encodeURIComponent(run)}`;
  const urlPhase = new URLSearchParams(location.search).get('phase');
  const requestedPhase = explicitPhase ?? (!urlPhaseConsumed && urlPhase !== null && urlPhase !== '' ? urlPhase : null);
  urlPhaseConsumed = true; // URL 阶段参数只消费一次，不作用于后续不同 run
  if (requestedPhase !== null && requestedPhase !== '' && /^\d+$/.test(requestedPhase)) url += `&phase=${requestedPhase}`;
  let data;
  try { data = await getJSON(url); }
  catch (e) {
    if (t !== token) return;
    setStatus(`读取失败：${e.message}`);
    $('run').disabled = false; $('reload').disabled = false;
    for (const id of ['phase', 'phase-prev', 'phase-next', 'market-filter', 'currency-filter']) $(id).disabled = true;
    return;
  }
  if (t !== token) return; // 丢弃过期响应
  state = data;
  if (!data.selected) {
    setStatus('暂无已保存阶段');
    $('run').disabled = false;
    $('reload').disabled = false;
    return;
  }
  // 阶段下拉
  const ph = $('phase'); const prevPhase = ph.value;
  clearNode(ph);
  for (const p of data.overview.available_phases) {
    const o = document.createElement('option'); o.value = p;
    const tl = data.timeline.find(x => x.phase === p);
    o.textContent = `阶段 ${p}${tl && tl.as_of ? ` · ${tl.as_of}` : ''}`;
    ph.appendChild(o);
  }
  ph.value = String(data.selected.phase);
  // 过滤器选项：行情 + 机构决策 + 社交帖子 + 资金流的市场并集（CN/US 恒可用）
  const markets = [...new Set([
    ...(data.selected.market?.quotes || []).map(q => q.market),
    ...(data.selected.institutions?.decisions || []).map(d => d.market),
    ...((data.selected.social?.posts || []).map(p => p.market).filter(Boolean)),
    ...(data.selected.financial?.flows || []).map(f => f.market)
  ].filter(Boolean))];
  const classes = [...new Set(markets.map(marketClass).concat(['CN', 'US']))];
  fillSelect($('market-filter'), markets.concat(classes.filter(c => !markets.includes(c))), '全部市场');
  const currencies = [...new Set([
    ...(data.selected.financial?.currencies || []).map(c => c.currency),
    ...(data.selected.market?.quotes || []).map(q => q.currency),
    'CNY', 'USD'
  ].filter(Boolean))];
  fillSelect($('currency-filter'), currencies, '全部币种');
  const cl = $('community-link');
  cl.href = `/community.html?mode=browse&run=${encodeURIComponent(run)}`;
  renderOverview(); renderMarket(); renderFinance(); renderFlows(); renderSocial(); renderInstitutions();
  setControls(false);
  setStatus(`已载入 run=${run} · 阶段 ${data.selected.phase}（只读，无执行）`);
}
function renderOverview() {
  if (!state?.selected) return;
  const d = state; const box = $('overview'); clearNode(box);
  const o = d.overview;
  metricCard(box, '运行', o.name);
  metricCard(box, '状态', o.status);
  metricCard(box, '策略模式', o.policy_mode === 'external' ? '外部策略（来源须核实）' : o.policy_mode);
  metricCard(box, '标签', o.label);
  metricCard(box, 'agent 数', num(o.agent_count, 0));
  metricCard(box, '完成阶段', `${o.completed_phases}/${o.total_phases}`);
  if (o.pending_excluded) metricCard(box, '未提交请求已排除', '是');
  const body = $('timeline-body'); clearNode(body);
  for (const row of d.timeline) {
    const tr = document.createElement('tr');
    tr.classList.toggle('selected-row', row.phase === d.selected.phase);
    cell(tr, row.phase); cell(tr, row.as_of ?? '未知'); cell(tr, row.phase_status);
    cell(tr, num(row.committed_frame_count, 0));
    cell(tr, num((row.market_groups || []).length, 0));
    cell(tr, num((row.financial_currencies || []).length, 0));
    cell(tr, num((row.financial_flows || []).length, 0));
    cell(tr, num(row.exposures, 0)); cell(tr, num(row.opened_exposed, 0));
    cell(tr, num(row.follow_count, 0)); cell(tr, num(row.graph_edges, 0));
    tr.addEventListener('click', () => loadMacro(String(row.phase)));
    body.appendChild(tr);
  }
  $('overview-notice').textContent = [o.notice, d.selected.market?.notice, d.selected.financial?.notice,
    d.selected.social?.notice].filter(Boolean).join(' · ') || '当前 run 支持的资产集合，不代表全市场。';
}
function groupKey(g) { return `${g.market}|${g.currency}|${g.kind}`; }
function filteredGroups() {
  return (state.selected.market?.groups || []).filter(g => marketMatch(g) && currencyMatch(g));
}
function renderMarket() { if (!state?.selected) return; renderMarketHeadline(); renderGroups(); renderTrend(); renderQuotes(); }
function renderMarketHeadline() {
  const box = $('market-overview'); clearNode(box);
  const m = state.selected.market;
  if (!m || !m.enabled) { addText(box, 'p', '本运行未启用市场模块。', 'muted'); return; }
  addText(box, 'p', `行情时点：${m.as_of ?? '未知'} · 前值时点：${m.previous_as_of ?? '未知'} · 数据陈旧阈值：${m.stale_after_seconds / 3600} 小时`, 'muted');
}
function renderGroups() {
  const body = $('group-body'); clearNode(body);
  if (!state?.selected) return;
  const groups = filteredGroups();
  for (const g of groups) {
    const tr = document.createElement('tr');
    if (selectedInstrument && groupKey(g) === selectedInstrument.group) tr.classList.add('selected-row');
    cell(tr, g.market); cell(tr, g.currency); cell(tr, g.kind);
    cell(tr, `${g.quoted_count}/${g.support_count}`);
    cell(tr, num(g.comparable_count, 0)); cell(tr, num(g.up_count, 0), 'pos');
    cell(tr, num(g.down_count, 0), 'neg'); cell(tr, num(g.flat_count, 0)); cell(tr, num(g.missing_count, 0));
    cell(tr, pct(g.mean_change_pct), cls(g.mean_change_pct));
    cell(tr, pct(g.median_change_pct), cls(g.median_change_pct));
    cell(tr, num(g.advanced_count, 0)); cell(tr, num(g.stale_count, 0)); cell(tr, num(g.synthetic_count, 0));
    tr.addEventListener('click', () => { selectedInstrument = {group: groupKey(g), id: null}; quoteLimit = 50; renderQuotes(); renderGroups(); renderTrend(); });
    body.appendChild(tr);
  }
  if (!groups.length) addText(body.insertBefore(document.createElement('tr'), body.firstChild), 'td', '当前筛选下没有市场组。');
}
function renderTrend() {
  const box = $('trend'); clearNode(box);
  if (!state?.selected) return;
  const groups = filteredGroups();
  if (!groups.length) { addText(box, 'p', '无可用趋势。', 'muted'); return; }
  const ns = 'http://www.w3.org/2000/svg';
  for (const g of groups.slice(0, 6)) {
    const wrap = document.createElement('div');
    addText(wrap, 'p', `${g.market} · ${g.currency} · ${g.kind}（样本资产等权阶段变动，非指数；缺失阶段不画值）`, 'muted');
    // 逐阶段从 timeline.market_groups 行数据推导真实均值；缺失为 null，不造 0
    const series = state.timeline.map(t => {
      const mg = (t.market_groups || []).find(x => groupKey(x) === groupKey(g));
      return (mg && typeof mg.mean_change_pct === 'number') ? mg.mean_change_pct : null;
    });
    const maxabs = Math.max(1e-9, ...series.map(v => (v === null || v === undefined) ? 0 : Math.abs(v)));
    const H = 90, mid = 46, half = 34;
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${Math.max(160, series.length * 26)} ${H}`);
    svg.setAttribute('role', 'img');
    const atitle = document.createElementNS(ns, 'title');
    atitle.textContent = `${g.market} ${g.currency} ${g.kind} 各阶段平均变动百分比（点击柱跳转阶段）`;
    svg.appendChild(atitle);
    svg.style.width = '100%'; svg.style.maxWidth = '520px'; svg.style.height = 'auto';
    // 零基线
    const base = document.createElementNS(ns, 'rect');
    base.setAttribute('x', 0); base.setAttribute('y', mid); base.setAttribute('width', '100%');
    base.setAttribute('height', 1); base.setAttribute('fill', '#c7d0cc');
    svg.appendChild(base);
    let x = 4;
    state.timeline.forEach((t, i) => {
      const v = series[i];
      const phase = t.phase;
      if (typeof v === 'number') {
        const h = (Math.abs(v) / maxabs) * half; // 0 值高度为 0，不是 1
        const r = document.createElementNS(ns, 'rect');
        r.setAttribute('x', x); r.setAttribute('y', v >= 0 ? mid - h : mid);
        r.setAttribute('width', 16); r.setAttribute('height', h);
        r.setAttribute('fill', v >= 0 ? '#ed735b' : '#4d8178');
        r.setAttribute('tabindex', '0');
        const rt = document.createElementNS(ns, 'title');
        rt.textContent = `阶段 ${phase}：${v.toFixed(2)}%`;
        r.appendChild(rt);
        r.addEventListener('click', () => loadMacro(String(phase)));
        r.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); loadMacro(String(phase)); } });
        svg.appendChild(r);
        const t0 = document.createElementNS(ns, 'text');
        t0.setAttribute('x', x + 8); t0.setAttribute('y', v >= 0 ? mid - h - 2 : mid + h + 8);
        t0.setAttribute('font-size', 8); t0.setAttribute('text-anchor', 'middle'); t0.setAttribute('fill', '#303a37');
        t0.textContent = v.toFixed(1); svg.appendChild(t0);
      } else {
        const t0 = document.createElementNS(ns, 'text');
        t0.setAttribute('x', x + 8); t0.setAttribute('y', mid - 6); t0.setAttribute('font-size', 9);
        t0.setAttribute('text-anchor', 'middle'); t0.setAttribute('fill', '#7c8884');
        t0.textContent = '未知'; svg.appendChild(t0);
      }
      const lp = document.createElementNS(ns, 'text');
      lp.setAttribute('x', x + 8); lp.setAttribute('y', H - 2); lp.setAttribute('font-size', 8);
      lp.setAttribute('text-anchor', 'middle'); lp.setAttribute('fill', '#7c8884');
      lp.textContent = phase; svg.appendChild(lp);
      x += 26;
    });
    wrap.appendChild(svg); box.appendChild(wrap);
  }
}
function filteredQuotes() {
  const q = $('instrument-search').value.trim().toLowerCase();
  return (state.selected.market?.quotes || []).filter(x =>
    marketMatch(x) && currencyMatch(x) &&
    (!q || String(x.instrument_id).toLowerCase().includes(q)) &&
    (!selectedInstrument || groupKey(x) === selectedInstrument.group));
}
function renderQuotes() {
  const body = $('quote-body'); clearNode(body);
  if (!state?.selected) { $('quote-count').textContent = ''; return; }
  const all = filteredQuotes();
  const shown = all.slice(0, quoteLimit);
  $('quote-count').textContent = `显示 ${shown.length} / ${all.length} 条`;
  $('quote-more').disabled = shown.length >= all.length;
  for (const q of shown) {
    const tr = document.createElement('tr');
    cell(tr, q.market); cell(tr, q.instrument_id); cell(tr, q.currency);
    cell(tr, q.kind === 'fund_nav' ? '基金净值' : q.kind);
    cell(tr, num(q.price, 4)); cell(tr, num(q.previous_price, 4));
    cell(tr, pct(q.change_pct), cls(q.change_pct));
    cell(tr, q.comparison_status); cell(tr, q.synthetic ? '合成' : `来源：${q.source ?? '未知'}`);
    cell(tr, q.observed_at ?? '未知'); cell(tr, q.available_at ?? '未知');
    cell(tr, q.age_seconds === null || q.age_seconds === undefined ? '未知' : (q.age_seconds / 3600).toFixed(1));
    const td = document.createElement('td'); const b = document.createElement('button');
    b.type = 'button'; b.textContent = '查看资金流/订单'; b.className = 'btn-primary';
    b.addEventListener('click', () => {
      selectedInstrument = {group: groupKey(q), id: q.instrument_id};
      selectedProduct = {market: q.market, instrument_id: q.instrument_id};
      quoteLimit = 50;
      renderQuotes(); renderGroups(); renderTrend(); renderFlows();
      const sec = document.getElementById('flow-section');
      if (sec) sec.scrollIntoView({behavior: 'smooth'});
    });
    td.appendChild(b); tr.appendChild(td);
    body.appendChild(tr);
  }
  if (!shown.length) { const tr = document.createElement('tr'); cell(tr, '没有匹配的报价（价格未知的报价也在统计口径内）。'); body.appendChild(tr); }
}
function renderFinance() {
  const box = $('finance-overview'); clearNode(box);
  clearNode($('currency-body'));
  if (!state?.selected) return;
  const fin = state.selected.financial;
  if (!fin || !fin.enabled) {
    addText(box, 'p', '本运行未启用交易，无法报告收益。', 'muted');
    clearNode($('account-body')); return;
  }
  const acts = fin.actions || {};
  if (acts.rejected !== undefined) {
    let rejected;
    let breakdown = '';
    if (acts.rejected && typeof acts.rejected === 'object' && !Array.isArray(acts.rejected)) {
      rejected = Object.values(acts.rejected).reduce(
        (s, v) => s + (Number.isFinite(Number(v)) ? Number(v) : 0), 0);
      try { breakdown = `（分类：${JSON.stringify(acts.rejected)}）`; } catch (e) { breakdown = ''; }
    } else {
      rejected = Number.isFinite(Number(acts.rejected)) ? Number(acts.rejected) : 0;
    }
    addText(box, 'p', `本阶段即时动作拒绝（下单动作被拒，区别于账本/结算拒绝口径）：${num(rejected, 0)}${breakdown}`, 'muted');
  }
  const curFilter = $('currency-filter').value;
  const currencies = (fin.currencies || []).filter(c => !curFilter || curFilter === 'all' || c.currency === curFilter);
  const body = $('currency-body'); clearNode(body);
  for (const c of currencies) {
    const tr = document.createElement('tr');
    cell(tr, c.currency); cell(tr, num(c.agent_count, 0)); cell(tr, num(c.valued_count, 0));
    cell(tr, `${num(c.loss_count, 0)}/${num(c.return_count, 0)}（有效收益分母）`);
    cell(tr, frac(c.loss_rate));
    cell(tr, num(c.cash)); cell(tr, num(c.available_cash)); cell(tr, num(c.reserved_cash));
    cell(tr, num(c.receivable)); cell(tr, num(c.known_market_value)); cell(tr, num(c.equity));
    cell(tr, num(c.fees));
    cell(tr, pct(c.return_min_pct), 'neg'); cell(tr, pct(c.return_median_pct)); cell(tr, pct(c.return_max_pct), 'pos');
    body.appendChild(tr);
  }
  if (currencies.length) {
    addText(box, 'p', '预算现金含占用部分（现金 = 可用 + 占用），不合并计算；收益分布为有效收益账户的 min/中位/max，无效收益不计为 0。', 'muted');
  }
  renderAccounts();
}
function renderAccounts() {
  const body = $('account-body'); clearNode(body);
  if (!state?.selected) return;
  const fin = state.selected.financial;
  if (!fin || !fin.enabled) return;
  const curFilter = $('currency-filter').value;
  let accounts = (fin.accounts || []).filter(a => !curFilter || curFilter === 'all' || a.currency === curFilter);
  $('account-title').textContent = selectedAgent ? `选定账户：${selectedAgent}` : '按币种的全体账户（不区分市场筛选）';
  if (selectedAgent) accounts = accounts.filter(a => a.agent_id === selectedAgent);
  for (const a of accounts) { // 全量展示，不静默截断
    const tr = document.createElement('tr');
    cell(tr, a.agent_id); cell(tr, a.currency);
    cell(tr, num(a.cash)); cell(tr, num(a.available_cash)); cell(tr, num(a.reserved_cash));
    cell(tr, num(a.receivable)); cell(tr, num(a.known_market_value)); cell(tr, num(a.equity));
    cell(tr, num(a.fees)); cell(tr, pct(a.return_pct), cls(a.return_pct));
    const td = document.createElement('td'); const link = document.createElement('a');
    link.href = microLink(a.agent_id, state.selected.phase, null);
    link.textContent = '查看微步'; td.appendChild(link); tr.appendChild(td);
    tr.addEventListener('click', (e) => { if (e.target.tagName !== 'A') { selectedAgent = a.agent_id; renderAccounts(); } });
    body.appendChild(tr);
  }
  if (!accounts.length) { const tr = document.createElement('tr'); cell(tr, '无匹配账户。'); body.appendChild(tr); }
}
function renderFlows() {
  const body = $('flow-body'); clearNode(body);
  if (!state?.selected) { renderOrders(); return; }
  const fin = state.selected.financial;
  const prodMatch = (f) => !selectedProduct || (f.market === selectedProduct.market && f.instrument_id === selectedProduct.instrument_id);
  const flows = (fin?.flows || []).filter(f => marketMatch(f) && currencyMatch(f) && prodMatch(f));
  for (const f of flows) {
    const tr = document.createElement('tr');
    tr.classList.toggle('selected-row', !!(selectedProduct && prodMatch(f) && selectedProduct.instrument_id === f.instrument_id && selectedProduct.market === f.market));
    cell(tr, f.market); cell(tr, f.instrument_id); cell(tr, f.currency);
    cell(tr, num(f.submitted, 0)); cell(tr, num(f.filled, 0)); cell(tr, num(f.settled, 0));
    cell(tr, num(f.cancelled, 0)); cell(tr, num(f.rejected, 0));
    cell(tr, num(f.buy_gross)); cell(tr, num(f.sell_gross)); cell(tr, num(f.fees));
    cell(tr, num(f.cash_delta)); cell(tr, num(f.net_executed_gross));
    tr.addEventListener('click', () => {
      selectedProduct = (selectedProduct && selectedProduct.market === f.market && selectedProduct.instrument_id === f.instrument_id)
        ? null : {market: f.market, instrument_id: f.instrument_id};
      renderFlows();
    });
    body.appendChild(tr);
  }
  $('flow-note').textContent = !fin?.enabled ? '本运行未启用交易，无资金流统计。'
    : flows.length ? '仅本阶段新成交计入成交额；提交、成交、交收分别计数；现金变动含成交扣款与交收到账，不重复计算成交额。'
    : '本阶段在此筛选下无新增账本事件。';
  renderOrders();
}
function renderOrders() {
  const body = $('order-body'); clearNode(body);
  if (!state?.selected) { $('order-title').textContent = ''; return; }
  const fin = state.selected.financial;
  if (!fin || !fin.enabled) { const tr = document.createElement('tr'); cell(tr, '本运行未启用交易。'); body.appendChild(tr); return; }
  const cumulative = $('order-cumulative').checked;
  const phase = state.selected.phase;
  const prodMatch = (o) => !selectedProduct || (o.market === selectedProduct.market && o.instrument_id === selectedProduct.instrument_id);
  let orders = (fin.orders || []).filter(o => marketMatch(o) && currencyMatch(o) && prodMatch(o));
  if (!cumulative) orders = orders.filter(o => (o.phase_events || []).length > 0);
  $('order-title').textContent = selectedProduct
    ? `订单 · ${selectedProduct.market} / ${selectedProduct.instrument_id}（${cumulative ? '累计' : '本阶段事件'}）`
    : `订单（${cumulative ? '累计' : '本阶段事件'}）`;
  for (const o of orders) {
    const tr = document.createElement('tr');
    cell(tr, o.agent_id); cell(tr, o.order_id); cell(tr, o.market); cell(tr, o.instrument_id);
    cell(tr, o.side); cell(tr, o.status); cell(tr, o.submitted_at ?? '未知');
    cell(tr, num(o.gross)); cell(tr, num(o.fee)); cell(tr, num(o.units, 4));
    if (o.source_post_id) {
      const td = document.createElement('td'); const a = document.createElement('a');
      a.href = microLink(o.agent_id, o.submission?.phase, o.submission?.step);
      a.textContent = o.source_post_id; td.appendChild(a); tr.appendChild(td);
    } else cell(tr, '无直接帖子来源');
    cell(tr, (o.phase_events || []).join('、') || '本阶段无事件');
    if (o.submission) {
      const td = document.createElement('td'); const a = document.createElement('a');
      a.href = microLink(o.agent_id, o.submission.phase, o.submission.step);
      a.textContent = '查看下单前所见'; td.appendChild(a); tr.appendChild(td);
    } else cell(tr, '未记录提交微步');
    body.appendChild(tr);
  }
  if (!orders.length) { const tr = document.createElement('tr'); cell(tr, cumulative ? '没有匹配订单。' : '本阶段没有订单事件（合法为 0）。'); body.appendChild(tr); }
}
function renderSocial() {
  const box = $('social-overview'); clearNode(box);
  clearNode($('post-body')); clearNode($('graph-metrics'));
  if (!state?.selected) return;
  const s = state.selected.social;
  if (!s) { addText(box, 'p', '无社交数据。', 'muted'); return; }
  const dictCount = (v, key) => (v && typeof v === 'object')
    ? (key ? (v[key] ?? 0) : Object.values(v).reduce((a, x) => a + (typeof x === 'number' ? x : 0), 0))
    : (v ?? 0);
  metricCard(box, '曝光', num(s.exposures, 0));
  metricCard(box, '已曝光后打开', num(s.opened_exposed, 0));
  metricCard(box, '打开率', frac(s.open_rate));
  metricCard(box, '全平台接受关注', num(dictCount(s.accepted, 'follow'), 0));
  metricCard(box, '全平台拒绝关注', num(dictCount(s.rejected, 'follow'), 0));
  addText(box, 'p', `曝光/已曝光后打开为动作前（before-only）口径；接受/拒绝计数为全平台口径，不随市场筛选缩放；网络图为全平台总图（basis：${s.graph_basis ?? '未知'}）。`, 'muted');
  const curFilter = $('currency-filter').value;
  if (curFilter && curFilter !== 'all') addText(box, 'p', '资金币种筛选不作用于全平台社交数据。', 'muted');
  const mf = $('market-filter').value;
  const posts = s.posts || [];
  const known = posts.filter(p => p.market);
  const unknown = posts.filter(p => !p.market);
  const filteredKnown = known.filter(p => !mf || mf === 'all' ||
    (mf === 'CN' || mf === 'US' ? String(p.market).toUpperCase().includes(mf) : p.market === mf));
  const rows = (mf && mf !== 'all') ? filteredKnown.concat(unknown) : posts;
  const sumKey = (list, k) => list.reduce((a, p) => a + (p[k] || 0), 0);
  addText(box, 'p', `市场筛选下已知市场曝光合计：${sumKey(filteredKnown, 'exposures')} · 已曝光后打开合计：${sumKey(filteredKnown, 'opened_exposed')}；未知市场帖子 ${unknown.length} 条单列展示，不并入任何市场合计。`, 'muted');
  const body = $('post-body');
  for (const p of rows) {
    const tr = document.createElement('tr');
    cell(tr, p.post_id); cell(tr, p.title ?? '未知'); cell(tr, p.market ?? '未知市场');
    cell(tr, p.institution_id ?? '未知'); cell(tr, num(p.exposures, 0)); cell(tr, num(p.opened_exposed, 0));
    cell(tr, frac(p.open_rate));
    cell(tr, `${dictCount(p.accepted)}/${dictCount(p.rejected)}`);
    if (p.sample) {
      const td = document.createElement('td'); const a = document.createElement('a');
      a.href = microLink(p.sample.agent_id, p.sample.phase, p.sample.step);
      a.textContent = `${p.sample.agent_id} · 微步`; td.appendChild(a); tr.appendChild(td);
    } else cell(tr, '无样本');
    body.appendChild(tr);
  }
  if (!rows.length) { const tr = document.createElement('tr'); cell(tr, '无匹配帖子。'); body.appendChild(tr); }
  const g = s.graph || {};
  const gm = $('graph-metrics');
  addText(gm, 'p', `全平台总图：边 ${g.edges ?? '未知'} · 覆盖 agent ${g.agents ?? '未知'} · 获关注作者 ${g.followed_authors ?? '未知'}`);
  addText(gm, 'p', `头部份额 ${g.top1_share === null || g.top1_share === undefined ? '无关注，不定义' : frac(g.top1_share)} · HHI ${g.hhi === null || g.hhi === undefined ? '未知（不视为 0）' : g.hhi.toFixed(3)} · 基尼 ${g.gini_all_agents === null || g.gini_all_agents === undefined ? '未知' : g.gini_all_agents.toFixed(3)}`);
  for (const l of (g.leaders || [])) addText(gm, 'p', `${l.handle} · ${l.followers} 粉丝`, 'muted');
}
function renderInstitutions() {
  const box = $('institution-overview'); clearNode(box);
  const body = $('decision-body'); clearNode(body); // 先清空旧行，再判断是否禁用
  if (!state?.selected) return;
  const inst = state.selected.institutions;
  if (!inst || !inst.enabled) { addText(box, 'p', '本运行无机构决策数据。', 'muted'); return; }
  const curFilter = $('currency-filter').value;
  const decisions = (inst.decisions || []).filter(d => marketMatch(d) &&
    (!curFilter || curFilter === 'all' ||
      (d.currency ? currencyMatch(d)
        : (curFilter === 'CNY' ? marketClass(d.market) === 'CN'
          : curFilter === 'USD' ? marketClass(d.market) === 'US' : false))));
  for (const d of decisions) {
    const tr = document.createElement('tr');
    cell(tr, d.institution_id); cell(tr, d.org ?? '未知'); cell(tr, d.market);
    cell(tr, d.strategy ?? '未知'); cell(tr, d.policy_kind ?? '未知');
    cell(tr, d.kind ?? '未知'); cell(tr, d.status ?? '未知');
    cell(tr, d.post_id ?? '无'); cell(tr, d.creative_id ?? '无');
    cell(tr, d.feedback_through_phase ?? '未知');
    cell(tr, d.remaining_publications_before === null || d.remaining_publications_before === undefined ? '未知' : `${d.remaining_publications_before} 次（发布预算按次数，非金额）`);
    const td = document.createElement('td');
    const fb = d.feedback || {};
    const parts = Object.entries(fb).map(([cid, f]) => `${cid}：曝光 ${f.exposures ?? '未知'} / 已开 ${f.opened_exposed ?? '未知'} / 打开率 ${frac(f.open_rate)}（滞后口径至阶段 ${d.feedback_through_phase ?? '未知'}）`);
    td.textContent = parts.join('；') || '暂无反馈';
    tr.appendChild(td);
    body.appendChild(tr);
  }
  if (!decisions.length) { const tr = document.createElement('tr'); cell(tr, '无匹配机构决策。'); body.appendChild(tr); }
  addText(box, 'p', '决策含发布/等待；反馈为滞后阶段口径，不包含投资人私有信息。', 'muted');
}
init();

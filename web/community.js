// community.js — plain ES module, no frameworks, own-origin GET API only.
import {createBrowseUI} from './community_browse.js';
const $ = (id) => document.getElementById(id);
const evLabel = { imp: '曝光', dec: '决策', act: '动作', co: '互动', fin: '结束', other: '事件' };

const S = {
  runs: [], run: null, summary: null, frame: null,
  demo: null, demoAgentFrames: [], playTimer: null, token: 0, frameCards: [],
};
const browseUI = createBrowseUI({getJSON, addText, clearNode, setStatus, stopPlay,
  renderCard, detailMaterial, actionLabel, resultStatus,
  resetCards: () => { S.frameCards = []; }});

function setStatus(msg, isErr = false) {
  const el = $('status');
  el.textContent = msg || '';
  el.setAttribute('role', 'status');
  el.classList.toggle('error', !!isErr);
}
function fmtMoney(v) { return v === null || v === undefined ? '未记录' : Number(v).toFixed(2); }
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return r.json();
}
function clearNode(el) { while (el.firstChild) el.removeChild(el.firstChild); }
function addText(parent, tag, text, cls) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  n.textContent = text === null || text === undefined ? '未记录' : text;
  parent.appendChild(n);
  return n;
}
function stopPlay() {
  if (S.playTimer) { clearInterval(S.playTimer); S.playTimer = null; $('play').textContent = '播放'; }
  $('play').setAttribute('aria-pressed', 'false');
}

async function loadRuns() {
  const token = ++S.token;
  const data = await getJSON('/api/community/runs');
  if (token !== S.token || $('mode').value !== 'history') return;
  S.runs = (data.runs || []).map((r) => r.tag);
  const sel = $('run'); clearNode(sel);
  if (!S.runs.length) {
    enterDemo('无可用历史运行，已自动进入演练模式。');
    return;
  }
  S.run = S.runs[0];
  for (const t of S.runs) {
    const o = document.createElement('option');
    o.value = t; o.textContent = t;
    if (t === S.run) o.selected = true;
    sel.appendChild(o);
  }
  sel.disabled = false;
  await loadSummary();
}
async function loadSummary() {
  stopPlay();
  const token = ++S.token;
  setStatus('加载运行摘要…');
  const sm = await getJSON(`/api/community/summary?run=${encodeURIComponent(S.run)}`);
  if (token !== S.token) return;
  S.summary = sm;
  $('mode-note').textContent = '历史批量曝光，不含逐步刷帖';
  const a = $('agent'); clearNode(a);
  for (const ag of sm.agents || []) {
    const o = document.createElement('option');
    o.value = ag.id; o.textContent = `${ag.id} · ${ag.arm}`;
    a.appendChild(o);
  }
  const d = $('day'); clearNode(d);
  for (const day of sm.days || []) {
    const o = document.createElement('option');
    o.value = day.t; o.textContent = day.date || `t=${day.t}`;
    d.appendChild(o);
  }
  a.disabled = false; d.disabled = false;
  const s = $('summary'); clearNode(s);
  const mk = (label, val) => {
    const c = document.createElement('div');
    c.className = 'metric-card';
    addText(c, 'div', label, 'label');
    addText(c, 'div', val, 'value');
    s.appendChild(c);
  };
  const mode = !sm.provider_mode ? '来源未确认'
    : (sm.provider_mode === 'mock') ? '替身或脚本'
    : (sm.provider_mode === 'real_model') ? '真模型配置' : '来源未确认';
  const modeNote = mode + ' · 历史批量曝光，不含逐步刷帖';
  $('mode-note').textContent = modeNote;
  mk('agent数', String((sm.agents || []).length));
  mk('模型', sm.model || '未记录');
  mk('运行模式', mode);
  mk('模拟日', String((sm.days || []).length));
  const warn = sm.warnings || [];
  addText(s, 'p', warn.length ? warn.join('；') : '无警告');
  await loadFrame();
}
async function loadFrame() {
  const token = ++S.token;
  stopPlay();
  setStatus('加载帧…');
  const url = `/api/community/frame?run=${encodeURIComponent(S.run)}` +
    `&agent=${encodeURIComponent($('agent').value)}&day=${encodeURIComponent($('day').value)}`;
  try {
    const fr = await getJSON(url);
    if (token !== S.token) return;
    S.frame = fr;
    renderFrame(fr);
    updateSidebar();
    setStatus('已加载。');
  } catch (e) {
    if (token !== S.token) return; // stale request, ignore silently
    setStatus('加载失败：' + e.message, true);
  }
}
function timelineFromEvents(evts) {
  const tl = $('timeline'); clearNode(tl);
  S.frameCards = [];
  const feed = $('feed'); clearNode(feed);
  const byPost = {};
  for (const p of S.frame.feed || []) {
    const card = renderCard(p);
    byPost[p.post_id] = card;
  }
  const list = (evts || []).map((e, i) => {
    const li = document.createElement('li');
    li.className = 'event';
    const kind = (e.ev === 'act' && (e.kind || e.fund || e.amt)) ? actionLabel(e)
      : (evLabel[e.ev] || evLabel.other);
    const date = e.d !== undefined && e.d !== null ? ` · ${e.d}` : '';
    addText(li, 'span', `${kind}${date}`, 'ev');
    const hasPost = e.p && byPost[e.p];
    if (hasPost) {
      li.dataset.postId = e.p;
      li.tabIndex = 0;
      const go = () => {
        const st = $('step');
        st.value = i;
        st.dispatchEvent(new Event('input'));
      };
      li.addEventListener('click', go);
      li.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') go(); });
    }
    tl.appendChild(li);
    return li;
  });
  const max = Math.max(list.length - 1, 0);
  const st = $('step'); st.min = 0; st.max = max; st.value = max;
  updateStepLabel(list, max);
  return list;
}
function updateStepLabel(list, idx) {
  const li = list[idx];
  $('step-label').textContent = list.length
    ? `事件 ${idx + 1}/${list.length}${li ? ' · ' + (li.textContent || '') : ''}（时间顺序仅为日志顺序，不代表心理顺序）`
    : '无事件记录';
  for (let i = 0; i < list.length; i++) list[i].classList.toggle('selected', i === idx);
  const card = li && li.dataset && li.dataset.postId
    ? S.frameCards.find((c) => c.dataset.postId === li.dataset.postId) : null;
  for (const c of S.frameCards) c.classList.toggle('selected', c === card);
  if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });

  // Render the selected actual recorded event into #detail (historical observation,
  // not microstep input) so stale material from another agent/day is replaced.
  const detail = $('detail');
  if (!detail) return;

  // Reuse or recreate the close button: detach before clearNode to keep listener.
  let closeBtn = $('close-detail');
  if (closeBtn) closeBtn.parentNode.removeChild(closeBtn);
  clearNode(detail);

  const events = (S.frame && S.frame.events) || [];
  const event = events[idx];

  addText(detail, 'h3', '该 agent 的已记录事件（观察者回放）');

  if (!event) {
    addText(detail, 'p', '无事件记录');
  } else {
    if (typeof event.reason === 'string' && event.reason.length) {
      const rEl = addText(detail, 'p', 'reason: ' + event.reason);
      rEl.className = 'event-reason';
    }

    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '查看原始事件';
    details.appendChild(summary);
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(event, null, 2);
    // prevent horizontal overflow
    pre.style.whiteSpace = 'pre-wrap';
    pre.style.wordBreak = 'break-all';
    pre.style.overflowWrap = 'anywhere';
    details.appendChild(pre);
    detail.appendChild(details);
  }

  if (!closeBtn) {
    closeBtn = document.createElement('button');
    closeBtn.id = 'close-detail';
    closeBtn.type = 'button';
    closeBtn.textContent = '关闭';
    closeBtn.addEventListener('click', () => { detail.hidden = true; });
  }
  detail.appendChild(closeBtn);
  closeBtn.hidden = false;
  detail.hidden = false;
}
function renderCard(p) {
  const card = document.createElement('article');
  card.className = 'post-card arm-' + (p.arm || 'U');
  if (p.post_id) card.dataset.postId = p.post_id;
  addText(card, 'div', `${p.org || '未知来源'} · ${p.arm || ''}`, 'byline');
  const t = addText(card, 'h3', p.title || '(无标题)', 'post-title');
  t.title = p.title || '';
  addText(card, 'p', p.caption || '', 'post-body');
  if (p.arm === 'TC' && p.image_caption_frozen) {
    addText(card, 'p', '图像描述：' + p.image_caption_frozen, 'post-body');
  }
  if (p.published_at) addText(card, 'p', `${p.channel || ''} · ${p.published_at}`, 'byline');
  if (p.arm === 'TV' && (p.image_sha || p.image_refs?.length)) {
    const img = document.createElement('img');
    img.className = 'cover';
    img.style.maxWidth = '100%';
    img.alt = p.image_caption_frozen || '图像';
    img.src = p.image_sha ? '/api/community/image/' + encodeURIComponent(p.image_sha)
      : '/api/community/asset/' + encodeURIComponent(p.image_refs[0]);
    img.loading = 'lazy';
    img.addEventListener('error', () => {
      const d = document.createElement('p');
      d.className = 'img-fallback'; d.textContent = '图像未提供/未加载';
      card.replaceChild(d, img);
    });
    card.appendChild(img);
  } else if (p.arm === 'TV') {
    addText(card, 'p', '图像未提供/未加载', 'img-fallback');
  }
  const btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = '查看详情';
  btn.addEventListener('click', () => showDetail(p));
  card.appendChild(btn);
  S.frameCards.push(card);
  $('feed').appendChild(card);
  return card;
}
function detailImage(parent, p) {
  if (p.image_refs?.length) {
    addText(parent, 'p', `原帖本地图片：${p.image_refs.length} 张`);
    for (const ref of p.image_refs) {
      const img = document.createElement('img'); img.className = 'cover';
      img.alt = '原始素材'; img.loading = 'lazy';
      img.src = '/api/community/asset/' + encodeURIComponent(ref);
      img.addEventListener('error', () => img.replaceWith('图片未加载'));
      parent.appendChild(img);
    }
    return;
  }
  if (p.image_sha) {
    const img = document.createElement('img');
    img.className = 'cover';
    img.style.maxWidth = '100%';
    img.alt = p.image_caption_frozen || '图像';
    img.src = '/api/community/image/' + encodeURIComponent(p.image_sha);
    img.loading = 'lazy';
    img.addEventListener('error', () => img.replaceWith(''));
    parent.appendChild(img);
  } else {
    addText(parent, 'p', '图像未提供/未加载', 'img-fallback');
  }
}
function detailMaterial(d, p) {
  addText(d, 'div', `${p.org || ''} · ${p.arm || ''}`, 'byline');
  addText(d, 'h4', p.title || '(无标题)');
  addText(d, 'p', p.caption || '');
  if (p.arm === 'TV') detailImage(d, p);
  if (p.ocr_text) { addText(d, 'h4', 'OCR 文本'); addText(d, 'pre', p.ocr_text); }
  if (p.image_caption_frozen) { addText(d, 'h4', '图像描述（冻结）'); addText(d, 'p', p.image_caption_frozen); }
  if (p.attachment_recorded) addText(d, 'p', '含附件记录');
  addText(d, 'h4', '评论（匿名标签）');
  const ul = document.createElement('ul');
  for (const c of p.comments_prev || []) {
    const li = document.createElement('li');
    addText(li, 'span', `${c.handle || '匿名'}（${c.stance || '未记录'}${c.followed ? ' · 已关注' : ''}）：${c.text || ''}`);
    ul.appendChild(li);
  }
  if (!(p.comments_prev || []).length) addText(ul, 'li', '无评论');
  d.appendChild(ul);
}
function showDetail(p) {
  const d = $('detail');
  const close = $('close-detail');
  if (close) close.remove(); // detach before clear so its listener survives
  clearNode(d);
  addText(d, 'h3', '素材查看（观察者视角，非 agent 点击）');
  detailMaterial(d, p);
  if (close) { d.appendChild(close); close.hidden = false; }
  else {
    const b = document.createElement('button');
    b.type = 'button'; b.id = 'close-detail'; b.textContent = '关闭详情';
    b.addEventListener('click', () => { $('detail').hidden = true; });
    d.appendChild(b);
  }
  d.hidden = false; d.setAttribute('tabindex', '-1'); d.focus && d.focus();
}
function renderAccount(ac, phase, demoPriv) {
  const a = $('account'); clearNode(a);
  if (demoPriv) { addText(a, 'p', `演练私有状态：现金 ${demoPriv}（脚本初值，非真实）`); }
  if (!ac) { addText(a, 'p', '无账户数据'); return; }
  addText(a, 'h3', `账户 · ${phase || '未记录'}（日终）`);
  addText(a, 'p', `现金：${fmtMoney(ac.cash)} · 现金变动：${fmtMoney(ac.cash_change)}`);
  addText(a, 'p', `市值：${fmtMoney(ac.market_value)} · 已实现盈亏（未扣费用）：${fmtMoney(ac.realized_pnl)} · 费用：${fmtMoney(ac.fees)}`);
  addText(a, 'p', `收益率：${ac.return_pct === null || ac.return_pct === undefined ? '未记录' : ac.return_pct}`);
  const ul = document.createElement('ul');
  for (const pos of ac.positions || []) {
    const li = document.createElement('li');
    addText(li, 'span',
      `${pos.fund}：${pos.units} 份 · NAV ${fmtMoney(pos.nav)}（${pos.nav_date || '日期未记录'}）· 市值 ${fmtMoney(pos.market_value)} · 浮动盈亏 ${fmtMoney(pos.unrealized_pnl)}`);
    ul.appendChild(li);
  }
  if (!(ac.positions || []).length) addText(ul, 'li', '无持仓');
  a.appendChild(ul);
}
function renderNetwork(edges, sm) {
  const n = $('network'); clearNode(n);
  if (!edges) { // unavailable, do not pretend it is an empty valid graph
    addText(n, 'h3', '关注网络');
    addText(n, 'p', (sm && sm.social_graph_enabled === false) ? '此运行未启用关注图' : '历史关注边尚未投影');
    return;
  }
  addText(n, 'h3', '关注网络（已接受的关注边，截至当前所选帧）');
  const ul = document.createElement('ul');
  for (const e of edges) addText(ul, 'li', `${e.from} → ${e.to}`);
  if (!edges.length) addText(ul, 'li', '暂无已接受的关注边');
  n.appendChild(ul);
}
function currentAgents() {
  if ($('mode').value === 'browse') return browseUI.agents();
  return $('mode').value === 'demo' ? ((S.demo && S.demo.agents) || [])
    : ((S.summary && S.summary.agents) || []);
}
function updateSidebar(fr) {
  const f = fr || ($('mode').value === 'demo'
    ? S.demoAgentFrames[Number($('step').value)] : S.frame);
  const name = $('agent-name');
  if (name) name.textContent = f ? (f.agent_id || f.agent || '未记录') : '未选择';
  const av = $('avatar');
  if (av) av.textContent = '';
  if (av && f && f.agent_id) av.textContent = String(f.agent_id).slice(-2);
  const st = $('agent-state');
  if (st) {
    if (!f) st.textContent = '';
    else {
      const ag = currentAgents().find((x) => x.id === (f.agent_id || f.agent));
      st.textContent =
        `分组 ${ag && ag.arm ? ag.arm : '未记录'} · 日 ${f.date !== undefined && f.date !== null ? f.date : '未记录'}` +
        (f.result ? ` · ${resultStatus(f.result)}` : '');
    }
  }
}
function renderFrame(fr) {
  $('market-panel').hidden = true;
  timelineFromEvents(fr.events || []);
  renderAccount(fr.account, (fr.account || {}).phase, null);
  if (fr.notice) addText($('account'), 'p', `提示：${fr.notice}`);
  renderNetwork(fr.follow_edges === undefined ? null : fr.follow_edges, S.summary);
}
// ---- demo mode ----
function enterDemo(notice) {
  $('market-panel').hidden = true;
  stopPlay();
  S.run = null; S.summary = null; S.frame = null;
  S.demo = null; S.demoAgentFrames = [];
  $('mode').value = 'demo';
  $('mode-note').textContent = '脚本策略演练 · 0次模型调用 · 非真模型结果';
  $('run').disabled = true; $('day').disabled = true; $('agent').disabled = true;
  for (const id of ['agent', 'feed', 'timeline', 'detail', 'account', 'network']) clearNode($(id));
  S.frameCards = [];
  $('step').max = 0; $('step').value = 0; $('step-label').textContent = '读取演练中';
  $('agent-name').textContent = '未选择'; $('agent-state').textContent = '';
  setStatus('正在读取脚本演练…');
  clearNode($('run')); clearNode($('day'));
  [['run', '脚本演练'], ['day', '不适用']].forEach(function (pair) {
    var o = document.createElement('option');
    o.value = '';
    o.textContent = pair[1];
    $(pair[0]).appendChild(o);
  });
  clearNode($('summary'));
  if (notice) addText($('summary'), 'p', notice);
  const token = ++S.token;
  getJSON('/api/community/demo').then((d) => {
    if (token !== S.token || $('mode').value !== 'demo') return;
    S.demo = d.demo_trace || d;
    $('mode-note').textContent = '脚本策略演练 · 0次模型调用 · 非真模型结果';
    const a = $('agent'); clearNode(a);
    for (const ag of S.demo.agents || []) {
      const o = document.createElement('option');
      o.value = ag.id; o.textContent = `${ag.id} · ${ag.arm}`;
      a.appendChild(o);
    }
    selectDemoAgent();
    setStatus('演练模式已加载。');
  }).catch((e) => { if (token === S.token && $('mode').value === 'demo') setStatus('演练加载失败：' + e.message, true); });
}
function selectDemoAgent() {
  if (!S.demo || $('mode').value !== 'demo') return;
  const id = $('agent').value;
  S.demoAgentFrames = (S.demo.frames || []).map((f, i) => ({ ...f, index: i })).filter((f) => f.agent_id === id);
  const st = $('step'); st.min = 0; st.max = Math.max(S.demoAgentFrames.length - 1, 0); st.value = 0;
  const a = $('agent');
  a.disabled = !(S.demo.agents || []).length;
  renderDemoFrame(0);
}
function renderDemoFrame(i) {
  const f = S.demoAgentFrames[i];
  const feed = $('feed'); clearNode(feed); S.frameCards = [];
  const tl = $('timeline'); clearNode(tl);
  updateSidebar(f);
  if (!f) { setStatus('该 agent 无演练帧。'); return; }
  for (const p of (f.before && f.before.feed) || []) renderCard(p);
  for (const [k, li] of S.demoAgentFrames.map((fr, j) => {
    const li = document.createElement('li');
    li.className = 'event';
    addText(li, 'span', `步骤 ${j + 1} · ${actionLabel(fr.action)}`);
    li.tabIndex = 0;
    li.addEventListener('click', () => {
      const st = $('step'); st.value = j;
      st.dispatchEvent(new Event('input'));
    });
    tl.appendChild(li); return [j, li];
  })) {
    li.classList.toggle('selected', k === i);
  }
  const d = $('detail');
  const close = $('close-detail');
  if (close) close.remove();
  clearNode(d);
  addText(d, 'h3', `演练步骤 ${i + 1}/${S.demoAgentFrames.length}`);
  const bd = f.before && f.before.detail;
  if (bd) {
    addText(d, 'h4', '动作前 agent 看到的详情');
    detailMaterial(d, bd.card || bd);
  } else {
    addText(d, 'p', '动作前：无打开页面');
  }
  addText(d, 'p', `动作：${actionLabel(f.action)} · 结果：${resultStatus(f.result)}`);
  const bef = (f.before && f.before.following) || [];
  const aft = (f.after && f.after.following) || [];
  addText(d, 'p', `关注数（before→after）：${bef.length} → ${aft.length}`);
  if (f.result && f.result.reason) addText(d, 'p', `说明：${f.result.reason}`);
  if (close) { d.appendChild(close); close.hidden = false; }
  else {
    const b = document.createElement('button');
    b.type = 'button'; b.id = 'close-detail'; b.textContent = '关闭详情';
    b.addEventListener('click', () => { $('detail').hidden = true; });
    d.appendChild(b);
  }
  d.hidden = false;
  renderAccount(f.after && f.after.detail && f.after.detail.account,
    f.after && f.after.detail && f.after.detail.account ? f.after.detail.account.phase : null,
    f.before && f.before.private_state && f.before.private_state.cash);
  const edgeMap = new Map();
  for (const fr of S.demo.frames || []) {
    if (fr.index === undefined || fr.index > f.index) continue;
    const acts = fr.action || {};
    const res = fr.result || {};
    if (res.status !== 'accepted') continue;
    const to = acts.handle;
    if (!to) continue;
    const key = `${fr.agent_id}\u2192${to}`;
    if (acts.kind === 'follow') edgeMap.set(key, { from: fr.agent_id, to });
    else if (acts.kind === 'unfollow') edgeMap.delete(key);
  }
  renderNetwork(Array.from(edgeMap.values()));
  $('step-label').textContent = `演练步骤 ${i + 1}/${S.demoAgentFrames.length}`;
}
function actionLabel(a) {
  const k = a && (a.kind || a.type);
  const map = {
    scroll: '刷帖', open: '打开详情', comments: '查看评论',
    like: '点赞', save: '收藏', comment: '评论',
    follow: '关注', unfollow: '取关', finish: '结束',
    subscribe: '申购', redeem: '赎回', dca: '定投',
    buy: '买入', sell: '卖出', cancel_order: '撤单',
    view: '浏览', post: '发帖', trade: '交易', noop: '无操作',
  };
  const target = a && (a.handle || a.instrument_id || a.order_id || a.post_id);
  const extra = target ? ` → ${target}` : '';
  return (map[k] || k || '未记录') + extra;
}
function resultStatus(r) {
  if (!r) return '未记录';
  if (r.status === 'accepted' || r.ok === true || r.accepted === true) return '已接受';
  if (r.status === 'rejected' || r.ok === false || r.accepted === false) return '未接受';
  return r.status || '完成';
}
// ---- wiring ----
function init() {
  $('mode').addEventListener('change', () => {
    stopPlay(); ++S.token;
    browseUI.leave();
    $('agent-search-panel').hidden = $('mode').value !== 'browse';
    if ($('mode').value === 'demo') enterDemo('');
    else if ($('mode').value === 'browse') browseUI.enter().catch(showErr);
    else { $('mode-note').textContent = ''; $('run').disabled = false; loadRuns().catch(showErr); }
  });
  $('reload').addEventListener('click', () => {
    stopPlay();
    if ($('mode').value === 'demo') enterDemo('');
    else if ($('mode').value === 'browse') browseUI.enter().catch(showErr);
    else loadRuns().catch(showErr);
  });
  $('run').addEventListener('change', () => {
    if ($('mode').value === 'browse') browseUI.selectRun().catch(showErr);
    else { S.run = $('run').value; loadSummary().catch(showErr); }
  });
  $('agent').addEventListener('change', () => {
    if ($('mode').value === 'demo') { stopPlay(); selectDemoAgent(); }
    else if ($('mode').value === 'browse') browseUI.selectTrace().catch(showErr);
    else loadFrame().catch(showErr);
  });
  $('agent-search').addEventListener('input', () => browseUI.filterAgents().catch(showErr));
  $('day').addEventListener('change', () => {
    if ($('mode').value === 'browse') browseUI.selectTrace().catch(showErr);
    else loadFrame().catch(showErr);
  });
  $('prev').addEventListener('click', () => {
    const st = $('step'); if (Number(st.value) > Number(st.min)) { st.value = Number(st.value) - 1; st.dispatchEvent(new Event('input')); }
  });
  $('next').addEventListener('click', () => {
    const st = $('step'); if (Number(st.value) < Number(st.max)) { st.value = Number(st.value) + 1; st.dispatchEvent(new Event('input')); }
  });
  $('play').addEventListener('click', () => {
    if (S.playTimer) { stopPlay(); return; }
    const st = $('step');
    if (Number(st.value) >= Number(st.max)) st.value = st.min;
    $('play').textContent = '暂停'; $('play').setAttribute('aria-pressed', 'true');
    S.playTimer = setInterval(() => {
      if (Number(st.value) >= Number(st.max)) { stopPlay(); return; }
      st.value = Number(st.value) + 1;
      st.dispatchEvent(new Event('input'));
    }, 900);
  });
  $('step').addEventListener('input', () => {
    const v = Number($('step').value);
    if ($('mode').value === 'demo') renderDemoFrame(v);
    else if ($('mode').value === 'browse') browseUI.render(v);
    else if (S.frame) {
      const items = Array.from($('timeline').children);
      updateStepLabel(items, v);
    }
  });
  $('close-detail').addEventListener('click', () => { $('detail').hidden = true; });
  document.querySelectorAll('.pill[data-arm]').forEach((pill) => {
    pill.addEventListener('click', () => {
      const arm = pill.getAttribute('data-arm');
      const ag = currentAgents().find((x) => x.arm === arm);
      if (!ag) { setStatus('当前模式下没有该分组的 agent。', true); return; }
      $('agent').value = ag.id;
      if ($('mode').value === 'demo') { stopPlay(); selectDemoAgent(); }
      else if ($('mode').value === 'browse') browseUI.selectTrace().catch(showErr);
      else loadFrame().catch(showErr);
    });
  });
  $('run').disabled = true; $('agent').disabled = true; $('day').disabled = true;
  if (new URLSearchParams(location.search).get('mode') === 'browse') {
    $('mode').value = 'browse'; browseUI.enter().catch(showErr);
  } else loadRuns().catch(showErr);
}
function showErr(e) { setStatus('初始化失败：' + (e && e.message ? e.message : e), true); }
init();

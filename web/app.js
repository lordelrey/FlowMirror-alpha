/* FlowMirror viewer — the shell.
   Contract: docs/WEB_CONTRACT_2026-09-07.md §2-3. This file owns exactly four things:
   the hash route, the loaded model, the shared header, and the day cursor with its
   keyboard and playback. Page modules own their own DOM and nothing else — they never
   read location, never write the hash, and never touch the header.

   Page modules load through dynamic import() per route, so a module that is missing or
   throws degrades to one broken page with a readable message instead of a blank app. */

import { loadRun, probeLocalApi, esc } from './data.js';

const params = new URLSearchParams(location.search);
const BASE = (params.get('base') || '..').replace(/\/+$/, '');
const RUN = params.get('run');

const $ = (id) => document.getElementById(id);
const PAGES = ['home', 'run', 'replay', 'agent', 'modality', 'audit', 'data'];

const S = {
  model: null, localApi: false, mounted: null, mountedPage: null,
  day: 0, playing: false, speed: 1, raf: 0, lastTs: 0,
  selection: null, field: null, panels: null,
};

/* ---------- routing ----------------------------------------------------- */

function parseHash() {
  const h = (location.hash || '#/').replace(/^#\/?/, '');
  const [head, ...rest] = h.split('/');
  return {
    name: PAGES.includes(head) ? head : 'home',
    arg: rest.length && rest.join('/') ? decodeURIComponent(rest.join('/')) : null,
  };
}

function markNav(name) {
  for (const a of $('topnav').querySelectorAll('a')) {
    if (a.dataset.page === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

/** Keep ?run= and ?base= on every internal link, so navigating never loses the run. */
function fixNavLinks() {
  const q = location.search || '';
  for (const a of $('topnav').querySelectorAll('a')) {
    const hash = a.getAttribute('href');
    a.dataset.page = hash.replace(/^#\/?/, '') || 'home';
    a.setAttribute('href', `${location.pathname}${q}${hash}`);
  }
}

const MODULES = {
  home: () => import('./home.js'),
  run: () => import('./configure.js'),
  agent: () => import('./agent.js'),
  modality: () => import('./modality.js'),
  audit: () => import('./audit.js'),
  data: () => import('./audit.js'),
};

async function show(route) {
  if (S.mounted && S.mounted.destroy) {
    try { S.mounted.destroy(); } catch (e) { console.warn('destroy failed', e); }
  }
  S.mounted = S.field = S.panels = null;
  setPlaying(false);
  clearTimeout(S.raf);
  S.mountedPage = route.name;

  for (const p of PAGES) $(`page-${p}`).hidden = true;
  const root = $(`page-${route.name}`);
  root.hidden = false;
  root.innerHTML = '';
  markNav(route.name);
  if (route.name !== 'run') setStepChip('');

  try {
    if (route.name === 'replay') await mountReplay(root);
    else S.mounted = await mountOne(route, root, await MODULES[route.name]());
  } catch (err) {
    console.error(err);
    root.innerHTML = '<div class="err"><h2>这一页没能打开</h2>'
      + `<p class="muted">${esc((err && err.message) || String(err))}</p>`
      + '<p class="muted">其他页面不受影响。</p></div>';
  }
}

async function mountOne(route, root, mod) {
  switch (route.name) {
    case 'home':
      return mod.mountHomePage(root, S.model, { base: BASE, tag: RUN });
    case 'run':
      return mod.mountRunPage(root, { base: BASE, localApi: S.localApi, onStep: setStepChip });
    case 'agent':
      return mod.mountAgentPage(root, requireModel(), route.arg);
    case 'modality':
      return mod.mountModalityPage(root, requireModel(), route.arg, { localApi: S.localApi, base: BASE });
    case 'audit':
      return mod.mountAuditPage(root, requireModel());
    case 'data':
      return mod.mountDataPage(root, S.model, { base: BASE, localApi: S.localApi });
    default:
      return null;
  }
}

function requireModel() {
  if (!S.model) {
    throw new Error('这一页需要一个已载入的运行。到「数据与场景」选一个，'
      + '或在地址里加 ?run=<运行标签>。');
  }
  return S.model;
}

/* ---------- the replay page: field + panels, one day cursor ------------ */

const REPLAY_HTML = `
  <div class="stage">
    <section class="panel">
      <h2>当日帖子 <span class="note" id="feed-count"></span></h2>
      <div class="feed-list" id="feed"></div>
    </section>
    <section class="panel">
      <h2>人口场 <span class="note">横轴年龄 · 纵轴资产 · 每格三条风险容忍带</span></h2>
      <div class="fieldwrap"><canvas id="field" role="img"
        aria-label="模拟人口场：每个点是一位投资者，按年龄、资产与风险容忍度分格排布"></canvas></div>
      <div class="legend" id="legend"></div>
      <div class="transport">
        <button class="icon" id="btn-first" title="回到第一天" aria-label="第一天">&#9198;</button>
        <button class="icon" id="btn-prev" title="上一天" aria-label="上一天">&#9664;</button>
        <button class="primary" id="btn-play" title="播放 / 暂停（空格）">&#9654; 播放</button>
        <button class="icon" id="btn-next" title="下一天" aria-label="下一天">&#9654;</button>
        <button class="icon" id="btn-last" title="最后一天" aria-label="最后一天">&#9197;</button>
        <span class="daylabel" id="daylabel">第 — 天</span>
        <input type="range" id="scrub" min="0" max="0" value="0" aria-label="按天跳转">
        <select id="speed" aria-label="播放速度">
          <option value="2">0.5×</option>
          <option value="1" selected>1×</option>
          <option value="0.5">2×</option>
          <option value="0.25">4×</option>
        </select>
      </div>
      <div class="tallies" id="tallies"></div>
    </section>
    <section class="panel">
      <h2 id="insp-title">图例与统计</h2>
      <div class="body" id="inspector"></div>
    </section>
  </div>
  <div class="grid2" style="margin-top:var(--gap)">
    <section class="panel">
      <h2>扩散热力图 <span class="note">行是投资者（按人口格排序） 列是交易日</span></h2>
      <div class="body">
        <canvas id="heat" role="img" aria-label="每位投资者每天的互动强度热力图"></canvas>
        <p class="muted" id="heat-note"></p>
      </div>
    </section>
    <section class="panel">
      <h2>适当性结账 <span class="note">suitability checkout</span></h2>
      <div class="body scrollx" id="checkout"></div>
    </section>
  </div>`;

async function mountReplay(root) {
  const model = requireModel();
  root.innerHTML = REPLAY_HTML;

  const [fieldMod, panelsMod] = await Promise.all([
    import('./field.js'), import('./panels.js'),
  ]);
  S.field = fieldMod.mountField(root, model);
  S.panels = panelsMod.mountReplayPanels(root, model, {
    onDay: (i) => setDay(i, 'panels'),
    onSelect: (sel) => setSelection(sel, 'panels'),
    onPlay: (on) => setPlaying(on),
    onSpeed: (v) => { S.speed = Number(v) || 1; },
  });
  if (S.field.onPick) S.field.onPick((sel) => setSelection(sel, 'field'));

  S.mounted = {
    destroy() {
      for (const m of [S.field, S.panels]) {
        try { m && m.destroy && m.destroy(); } catch (e) { console.warn(e); }
      }
    },
  };
  S.day = Math.min(S.day, Math.max(0, model.days.length - 1));
  setDay(S.day, 'init');
  setSelection(S.selection, 'init');
}

function setDay(i, from) {
  if (!S.model) return;
  const max = Math.max(0, S.model.days.length - 1);
  S.day = Math.max(0, Math.min(max, i | 0));
  // both halves are told every time: each is idempotent, and a one-way update was how
  // the scrubber and the field used to drift apart
  if (S.field) S.field.setDay(S.day);
  if (S.panels) S.panels.setDay(S.day);
}

function setSelection(sel, from) {
  S.selection = sel || null;
  if (S.field && from !== 'field') S.field.setSelection(S.selection);
  if (S.panels && from !== 'panels') S.panels.setSelection(S.selection);
}

function step(delta) {
  if (S.model) setDay(S.day + delta, 'shell');
}

/* A wall-clock timer, deliberately not requestAnimationFrame. Advancing one
   simulated day per ~900ms is a cadence, not an animation — the field runs its own
   rAF sweep for the exposure wave — and rAF is throttled to a near stop in a tab the
   browser considers hidden, which would make playback silently freeze whenever the
   viewer is not the foreground tab. */
function setPlaying(on) {
  S.playing = !!on && !!S.model && S.mountedPage === 'replay';
  if (S.panels && S.panels.setPlaying) S.panels.setPlaying(S.playing);
  clearTimeout(S.raf);
  if (S.playing) S.raf = setTimeout(tick, 900 * S.speed);
}

function tick() {
  if (!S.playing) return;
  if (S.day >= S.model.days.length - 1) { setPlaying(false); return; }
  step(1);
  if (S.playing) S.raf = setTimeout(tick, 900 * S.speed);
}

/* ---------- shared header ---------------------------------------------- */

function chip(id, text, cls) {
  const el = $(id);
  if (!el) return;
  el.hidden = !text;
  if (text) {
    el.textContent = text;
    el.className = 'chip' + (cls ? ' ' + cls : '');
  }
}

function setStepChip(text, cls) {
  const wrap = $('chip-stepwrap');
  wrap.hidden = !text;
  $('stepchip').textContent = text || '';
  wrap.className = 'chip' + (cls ? ' ' + cls : '');
}

function renderHeader() {
  const m = S.model;
  $('runtag').textContent = m ? m.tag : (RUN ? `${RUN}（未载入）` : '未选择运行');
  if (!m) {
    for (const id of ['chip-policy', 'chip-scale', 'chip-nav', 'chip-mock',
      'chip-img', 'chip-inv']) $(id).hidden = true;
    chip('chip-source', S.localApi ? '本地服务已连接' : '', 'local');
    return;
  }
  chip('chip-source', m.source === 'bundle' ? '展示包' : '原始产物',
    m.source === 'bundle' ? '' : 'warn');
  chip('chip-policy', m.agentPolicy === 'null' ? '规则型模拟，无 LLM 调用' : 'LLM agent');
  chip('chip-scale', `${m.agents.size} 人 × ${m.days.length} 日 · ${m.arms.length} 臂`);
  $('chip-nav').hidden = !m.syntheticNav;
  $('chip-mock').hidden = !m.mock;

  const img = m.images;
  if (!img || !img.root) chip('chip-img', '本次运行未附带图片', 'warn');
  else if (img.attached > 0) chip('chip-img', `已附图 ${img.attached}`, 'good');
  else chip('chip-img', '配了图片库但零附图', 'bad');

  const inv = m.invariants && m.invariants.summary;
  if (!inv) chip('chip-inv', '');
  else if (inv.failed > 0) chip('chip-inv', `不变量 ${inv.failed} 项未过`, 'bad');
  else chip('chip-inv', `不变量 ${inv.passed}/${inv.total} 过 · ${inv.skipped} 跳过`, 'good');
}

/* ---------- keyboard --------------------------------------------------- */

function onKey(e) {
  if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (S.mountedPage !== 'replay') return;
  // both spellings: e.code is the physical key and e.key the produced character, and
  // a synthetic or IME-mediated event may carry only one of them
  if (e.code === 'Space' || e.key === ' ' || e.key === 'Spacebar') {
    e.preventDefault();
    setPlaying(!S.playing);
  }
  else if (e.key === 'ArrowRight') { e.preventDefault(); setPlaying(false); step(1); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); setPlaying(false); step(-1); }
  else if (e.key === 'Home') { e.preventDefault(); setPlaying(false); setDay(0, 'shell'); }
  else if (e.key === 'End') { e.preventDefault(); setPlaying(false); setDay(1e9, 'shell'); }
}

/* ---------- boot -------------------------------------------------------- */

async function boot() {
  fixNavLinks();
  window.addEventListener('hashchange', () => show(parseHash()));
  window.addEventListener('keydown', onKey);

  S.localApi = await probeLocalApi();

  if (RUN) {
    try {
      S.model = await loadRun(BASE, RUN);
    } catch (err) {
      $('fatal').hidden = false;
      $('fatal-msg').textContent = (err && err.message) || String(err);
    }
  }
  $('loading').hidden = true;
  renderHeader();
  await show(parseHash());
}

boot();

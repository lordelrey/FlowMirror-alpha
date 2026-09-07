/* FlowMirror viewer — the replay panels: feed, transport, tallies, inspector,
   heatmap and the suitability checkout table.
   Contract: docs/WEB_CONTRACT_2026-09-07.md §2 (mountReplayPanels), §4 (tokens only),
   §6 (degradation must be stated on screen, never silently blank).

   The shell owns the day cursor, the selection, playback and the keyboard. This module
   only reports intent through `opts` and re-renders when told. Everything it draws it
   draws into the containers `app.js` already emitted; it creates no ids of its own.

   Two honesty rules are load-bearing here, not decorative:
   1. `model.source === 'raw'` means the run exported no presentation bundle. There is
      no per-arm card text and no opening holdings. The inspector says so where those
      would have been rather than leaving a gap.
   2. The engine's words and the model's words must not look alike: `.machine` for
      statuses and counts, `.said` for a reason or a comment. Under the rule-based
      policy there is no LLM reason at all, so the block is omitted, not emptied. */

/* The display vocabulary is canonical in web/vocab.js: the same suitability outcome
   used to be glossed three different ways across three modules. RISK_LONG_ZH is
   aliased to RISK_ZH here because a table cell needs the standalone wording
   ("风险脆弱") while the field's axis caption needs the short one. */
import {
  AGE_ORDER, ASSET_ORDER, RISK_ORDER, AGE_ZH, ASSET_ZH, INTENT_ZH, IG_ZH,
  ARM_ZH, ARM_LONG_ZH, STANCE_ZH, CLIM_ZH, ACT_ZH,
  RISK_LONG_ZH as RISK_ZH, OC_ZH, OC_SHORT_ZH, OC_COLOR, cssVar,
} from './vocab.js';

import { esc, num, pct, stateAt, tallyUpTo } from './data.js';

/* ---------- vocabulary -------------------------------------------------- */


/** Colour tokens are owned by vocab.js so panels cannot drift from agent.js; the
 *  local copy once missed below_min and one event rendered differently on two pages. */
const ocColor = (oc) => OC_COLOR[oc] || null;
const ocZh = (oc) => OC_ZH[oc] || oc || '—';
const ocShort = (oc) => OC_SHORT_ZH[oc] || oc || '—';

/* ---------- small helpers ---------------------------------------------- */

const clamp = (lo, v, hi) => Math.max(lo, Math.min(hi, v));
const ord = (list, v) => { const i = list.indexOf(v); return i < 0 ? list.length : i; };

/** Colours come from style.css through data.js; refuse anything that is not a colour
 *  before it reaches a style attribute. */
const SAFE_COLOR = /^[#a-zA-Z0-9(),.%\s/-]{1,64}$/;
const safeColor = (c, fallback = 'var(--ink-dim)') =>
  (typeof c === 'string' && SAFE_COLOR.test(c) && c.trim()) ? c.trim() : fallback;



const cellZh = (a) => [AGE_ZH[a.age] || a.age, ASSET_ZH[a.asset] || a.asset,
  RISK_ZH[a.risk] || a.risk].join(' · ');

const engOf = (r) => (r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0);
const heatOf = (r) => engOf(r) + (r.n_comment || 0);

/* ---------- mount ------------------------------------------------------- */

export function mountReplayPanels(root, model, opts = {}) {
  const pick = (id) => root.querySelector(`#${id}`);
  const el = {
    feed: pick('feed'), feedCount: pick('feed-count'),
    tallies: pick('tallies'), inspector: pick('inspector'), inspTitle: pick('insp-title'),
    checkout: pick('checkout'), heat: pick('heat'), heatNote: pick('heat-note'),
    daylabel: pick('daylabel'), scrub: pick('scrub'), speed: pick('speed'),
    first: pick('btn-first'), prev: pick('btn-prev'), play: pick('btn-play'),
    next: pick('btn-next'), last: pick('btn-last'),
  };

  const fire = (name, arg) => {
    const fn = opts && opts[name];
    if (typeof fn === 'function') fn(arg);
  };

  const nDays = model.days.length;
  const lastDay = Math.max(0, nDays - 1);
  const isBundle = model.source === 'bundle';
  const isNullPolicy = model.agentPolicy === 'null';
  const truncated = !!(model.truncation && model.truncation.applied);

  const state = { day: 0, sel: null, playing: false };
  const ac = new AbortController();
  const signal = ac.signal;
  const on = (node, type, fn) => { if (node) node.addEventListener(type, fn, { signal }); };

  /* ---- caches computed once: the day cursor moves far more often than the data --- */

  const stCache = new Map();
  const dayState = (i) => {
    if (!stCache.has(i)) stCache.set(i, stateAt(model, i));
    return stCache.get(i);
  };

  // rows of the heatmap: agents ordered by population cell, as the field orders them
  const heatRows = [...model.agents.values()].sort((a, b) => (
    ord(AGE_ORDER, a.age) - ord(AGE_ORDER, b.age)
    || ord(ASSET_ORDER, a.asset) - ord(ASSET_ORDER, b.asset)
    || ord(RISK_ORDER, a.risk) - ord(RISK_ORDER, b.risk)
    || String(a.id).localeCompare(String(b.id))
  )).map((a) => String(a.id));
  const heatIdx = new Map(heatRows.map((id, i) => [id, i]));

  const heatCols = model.byDay.map((g) => {
    const eng = new Map();
    for (const r of g.dec) eng.set(String(r.i), heatOf(r));
    return { t: g.t, eng, co: g.co.map((r) => ({ i: String(r.i), oc: r.oc })) };
  });

  const armCount = {};
  for (const a of model.agents.values()) armCount[a.arm] = (armCount[a.arm] || 0) + 1;

  /* ---- feed -------------------------------------------------------------- */

  /** What the agents actually read today. A creative published on day t is not
   *  necessarily shown on day t: the ranker draws from a pool spanning several days. */
  function shownToday(i) {
    const st = dayState(i);
    if (!st) return [];
    const reach = new Map();
    const g = model.byDay[i];
    for (const r of g.imp) {
      const pid = String(r.p);
      if (!reach.has(pid)) reach.set(pid, new Set());
      reach.get(pid).add(String(r.i));
    }
    return st.shown.map((p) => ({
      post: p,
      reach: (reach.get(String(p.post_id)) || new Set()).size,
      imps: g.imp.reduce((n, r) => n + (String(r.p) === String(p.post_id) ? 1 : 0), 0),
    })).sort((a, b) => ((b.post.published_t || 0) - (a.post.published_t || 0))
      || (b.reach - a.reach)
      || String(a.post.post_id).localeCompare(String(b.post.post_id)));
  }

  function climateOf(i, pid) {
    const st = dayState(i);
    const label = st ? st.climate.get(String(pid)) : null;
    const row = (model.byDay[i].clim || []).find((r) => String(r.p) === String(pid));
    const c = (row && row.counts) || {};
    const n = (c.bullish || 0) + (c.bearish || 0) + (c.watching || 0);
    return { label: label || (row && row.label) || null, counts: c, n, source: row && row.source };
  }

  function renderFeed() {
    if (!el.feed) return;
    const list = shownToday(state.day);
    const g = model.byDay[state.day];
    const today = g ? g.t : null;
    if (el.feedCount) el.feedCount.textContent = `（展示 ${list.length} 条）`;

    el.feed.innerHTML = list.map(({ post: p, reach, imps }) => {
      const pid = String(p.post_id);
      const age = (today == null || p.published_t == null) ? null : today - p.published_t;
      const when = age == null ? '发布日不明' : age === 0 ? '当天发布' : `${age} 天前发布`;
      const cl = climateOf(state.day, pid);
      const climate = cl.label
        ? `${CLIM_ZH[cl.label] || cl.label}${cl.n ? `（前一日 ${cl.n} 条评论）` : '（前一日无评论）'}`
        : '前一日口风未记录';
      const current = state.sel && state.sel.post === pid;
      return `<div class="post" role="button" tabindex="0" data-p="${esc(pid)}"${
        current ? ' aria-current="true"' : ''}>
        <div class="org">${esc(p.org || '未标注机构')}</div>
        <div class="intent i-${esc(p.intent || '')}">${esc(INTENT_ZH[p.intent] || p.intent || '意图未标注')}
          · ${esc(IG_ZH[p.ig] || p.ig || '意图组未标注')}${
        p.fund ? ` · 落地 ${esc(p.fund)}` : ' · 无落地基金'}</div>
        <div class="meta">#${esc(pid)} · 触达 ${num(reach)} 人 / 曝光 ${num(imps)} 次 · ${esc(when)}</div>
        <div class="meta">口风 ${esc(climate)}</div>
      </div>`;
    }).join('') || '<div class="body muted">今天没有帖子被展示给任何人。</div>';
  }

  function toggleFeed(pid) {
    if (!pid) return;
    const same = state.sel && state.sel.post === pid;
    fire('onSelect', same ? null : { post: pid });
  }

  on(el.feed, 'click', (e) => {
    const node = e.target && e.target.closest ? e.target.closest('.post') : null;
    if (node) toggleFeed(node.dataset.p);
  });
  // Enter only: the shell owns space and the arrow keys.
  on(el.feed, 'keydown', (e) => {
    if (e.key !== 'Enter') return;
    const node = e.target && e.target.closest ? e.target.closest('.post') : null;
    if (!node) return;
    e.preventDefault();
    toggleFeed(node.dataset.p);
  });

  /* ---- tallies ---------------------------------------------------------- */

  function renderTallies() {
    if (!el.tallies) return;
    const t = tallyUpTo(model, state.day);
    const item = (label, value, title) =>
      `<div class="tally"${title ? ` title="${esc(title)}"` : ''}><b>${value}</b>${esc(label)}</div>`;
    const parts = [
      item('曝光', num(t.imp), '累计 imp 事件数'),
      item('触达人数', num(t.reachN), '至今至少被曝光过一次的投资者'),
      item('互动人次', num(t.eng), '当日有赞/藏/关的投资者，逐日累加'),
      item('评论', num(t.cmt)),
      item(`${STANCE_ZH.bullish}/${STANCE_ZH.bearish}/${STANCE_ZH.watching}`, `${num(t.bull)}/${num(t.bear)}/${num(t.watch)}`),
      item('适当性结账', num(t.co)),
      item('签确认书/拒签', `${num(t.signed)}/${num(t.declined)}`,
        'confirm_signed / confirm_declined'),
    ];
    if (t.blocked) parts.push(item('拦截', num(t.blocked), 'hard_block / purchase_blocked'));
    parts.push(item('申购笔数', num(t.sub)));
    parts.push(item('申购金额（元）', num(Math.round(t.amt))));
    if (t.red) parts.push(item('赎回笔数', num(t.red)));
    parts.push(item('费用（元）', num(Math.round(t.fees)), 'act.fee 累计'));
    el.tallies.innerHTML = parts.join('');
  }

  /* ---- inspector -------------------------------------------------------- */

  const armDot = (a) =>
    `<i style="background:${safeColor(model.armColor[a])}"></i>`;

  function inspectorDefault() {
    const st = dayState(state.day);
    const g = model.byDay[state.day] || { imp: [], cmt: [], co: [], act: [] };
    const armRows = model.arms.map((a) => `<tr>
      <td><span class="armbadge">${armDot(a)} ${esc(a)}</span></td>
      <td>${esc(ARM_ZH[a] || a)}</td>
      <td class="num">${num(armCount[a] || 0)}</td></tr>`).join('');

    if (el.inspTitle) el.inspTitle.textContent = '图例与统计';
    return `
      <table><thead><tr><th>臂</th><th>含义</th><th class="num">人数</th></tr></thead>
        <tbody>${armRows || '<tr><td colspan="3" class="muted">本次运行没有可识别的模态臂。</td></tr>'}</tbody></table>
      <p class="muted" style="margin-top:10px">场上每个实心点是一位投资者，按年龄、资产、风险容忍度落格，
        颜色是所属模态臂。外环=当日有互动，上方短竖=当日评论，大圈=触发适当性结账。</p>
      <p class="muted">点场上任意一个点看这位投资者当天做了什么，点左侧任意一条帖子看它的逐臂触达。</p>
      <dl class="kv" style="margin-top:10px">
        <dt>当日曝光</dt><dd>${num(g.imp.length)} 次</dd>
        <dt>当日触达</dt><dd>${num(st ? st.seenBy.size : 0)} 人</dd>
        <dt>当日互动</dt><dd>${num(st ? st.engaged.size : 0)} 人</dd>
        <dt>当日评论</dt><dd>${num(g.cmt.length)} 条</dd>
        <dt>当日结账</dt><dd>${num(g.co.length)} 笔</dd>
        <dt>当日成交</dt><dd>${num(g.act.length)} 笔</dd>
      </dl>`;
  }

  function imageBlock(post) {
    const img = post.image;
    if (!img) {
      return `<p class="muted" style="margin-top:8px">图片元信息：${
        isBundle ? '本帖没有配图记录。' : '原始产物不带逐帖图片元信息（需要展示包）。'}</p>`;
    }
    const sha = String(img.sha || '');
    const shaTxt = sha ? `sha 前 8 位 <code>${esc(sha.slice(0, 8))}</code>` : 'sha 未记录';
    const attached = Number(img.attached_impressions || 0);
    const runAttached = model.images ? Number(model.images.attached || 0) : 0;
    if (attached > 0 && runAttached > 0) {
      return `<p class="muted" style="margin-top:8px">配图 ${num(img.n_images || 0)} 张，
        真实像素已附给 ${num(attached)} 次曝光。${shaTxt}。页面不展示像素——图片永不进仓库。</p>`;
    }
    const why = !model.images || !model.images.root
      ? '本次运行没有配置 images_root，任何一次曝光都没有附上像素。'
      : '配了图片库，但这一帖零附图（检查 images_root 与 sha 是否对得上）。';
    return `<div class="placeholder" style="margin-top:8px">配图 ${num(img.n_images || 0)} 张，
      ${esc(why)} ${shaTxt}。</div>`;
  }

  function inspectorPost(pid) {
    const post = model.postById.get(String(pid));
    if (!post) {
      if (el.inspTitle) el.inspTitle.textContent = '帖子未找到';
      return `<p class="muted">事件流里没有 <code>${esc(pid)}</code> 这条帖子。</p>`;
    }
    if (el.inspTitle) el.inspTitle.textContent = `帖子 ${post.post_id}`;
    const cl = climateOf(state.day, pid);
    const g = model.byDay[state.day] || { imp: [] };
    const seenToday = new Set(g.imp.filter((r) => String(r.p) === String(pid)).map((r) => String(r.i)));

    const cols = model.arms.map((a) => {
      const slot = post.arms && post.arms[a];
      const head = `<h3 style="color:${safeColor(model.armColor[a], 'var(--ink)')}">
        ${armDot(a)} ${esc(a)} · ${esc(ARM_ZH[a] || a)}</h3>`;
      if (!slot) {
        return `<div class="armcol">${head}
          <div class="txt muted">这一臂没有这条帖子的曝光记录。</div>
          <div class="stat">曝光 0 · 触达 0 人</div></div>`;
      }
      const body = isBundle
        ? (slot.text
          ? `<div class="txt">${esc(slot.text)}</div>`
          : `<div class="placeholder">展示包里这一臂没有文案：${
            esc(model.warnings.find((w) => w.includes('文案')) || '内容池未能载入')}</div>`)
        : '<div class="placeholder">该运行未导出展示包（bundle），逐臂文案不可用。'
          + '下面的触达与互动率由事件日志直接算得。跑 <code>flowmirror export-bundle</code> 补齐文案。</div>';
      const chars = isBundle && slot.chars != null
        ? ` · ${num(slot.chars)} 字${slot.truncated ? '（已截断）' : ''}` : '';
      return `<div class="armcol">${head}${body}
        <div class="stat">曝光 ${num(slot.impressions || 0)} · 触达 ${num(slot.reach || 0)} 人
          · 互动 ${num(slot.engaged_agents || 0)} 人 · 互动率 ${pct(slot.engagement_rate)}${chars}</div>
      </div>`;
    }).join('');

    return `
      <dl class="kv">
        <dt>机构</dt><dd>${esc(post.org || '—')}</dd>
        <dt>意图</dt><dd>${esc(INTENT_ZH[post.intent] || post.intent || '—')}（${esc(post.intent || '—')}）</dd>
        <dt>意图组</dt><dd>${esc(IG_ZH[post.ig] || post.ig || '—')}</dd>
        <dt>落地基金</dt><dd>${post.fund ? `<code>${esc(post.fund)}</code>` : '无'}</dd>
        <dt>发布日</dt><dd>${post.published_t == null ? '—' : `t=${num(post.published_t)}`} ${esc(post.published_d || '')}</dd>
        <dt>展示日</dt><dd>${(post.days_shown || []).length ? esc((post.days_shown || []).join(' ')) : '—'}</dd>
        <dt>当日触达</dt><dd>${num(seenToday.size)} 人</dd>
        <dt>前一日口风</dt><dd>${esc(cl.label ? (CLIM_ZH[cl.label] || cl.label) : '未记录')}
          ${cl.n ? `· 多 ${num(cl.counts.bullish || 0)} / 空 ${num(cl.counts.bearish || 0)} / 观望 ${num(cl.counts.watching || 0)}` : ''}</dd>
      </dl>
      <p class="faint" style="margin-top:6px">卡片顶部那个"热度"数字是日-(t-1) 的赞数，事件日志从不记录逐帖赞，
        所以导出的卡片一律显示 0；能对照的是上面这条口风标签。</p>
      ${imageBlock(post)}
      <div class="stack" style="margin-top:10px;gap:8px">${cols}</div>
      <p class="muted" style="margin-top:10px"><a href="#" data-act="clear">← 返回图例</a></p>`;
  }

  function inspectorAgent(aid) {
    const a = model.agents.get(String(aid));
    if (!a) {
      if (el.inspTitle) el.inspTitle.textContent = '投资者未找到';
      return `<p class="muted">人口队列里没有 <code>${esc(aid)}</code> 这位投资者。</p>`;
    }
    if (el.inspTitle) el.inspTitle.textContent = String(a.id);
    const g = model.byDay[state.day] || { imp: [], dec: [], cmt: [], act: [], co: [] };
    const id = String(a.id);
    const dec = g.dec.find((r) => String(r.i) === id) || null;
    const seen = g.imp.filter((r) => String(r.i) === id);
    const cmts = g.cmt.filter((r) => String(r.i) === id);
    const acts = g.act.filter((r) => String(r.i) === id);
    const cos = g.co.filter((r) => String(r.i) === id);

    const holdings = isBundle
      ? `<dt>开局现金</dt><dd>${a.cash0 == null ? '—' : `${num(Math.round(a.cash0))} 元`}</dd>
         <dt>开局持仓</dt><dd>${a.hold0 && Object.keys(a.hold0).length
        ? esc(Object.keys(a.hold0).join(' ')) : '空仓'}</dd>`
      : '<dt>开局持仓</dt><dd class="muted">未导出展示包，开局现金与持仓不可用</dd>';

    const machine = dec
      ? `<dl class="kv machine" style="margin-top:8px">
          <dt>解析状态</dt><dd>${esc(dec.status || '—')}${dec.failure_kind ? ` / ${esc(dec.failure_kind)}` : ''}</dd>
          <dt>心情</dt><dd>${dec.mood == null ? '—' : num(dec.mood)}</dd>
          <dt>读/赞/藏/关/评</dt><dd>${num(dec.n_read || 0)} / ${num(dec.n_like || 0)} / ${num(dec.n_save || 0)} / ${num(dec.n_follow || 0)} / ${num(dec.n_comment || 0)}</dd>
          <dt>好感增量</dt><dd>${dec.aff_sum == null ? '—' : num(dec.aff_sum, 2)}</dd>
          ${(dec.violations || []).length ? `<dt>违规项</dt><dd>${esc((dec.violations || []).join(' '))}</dd>` : ''}
        </dl>`
      : '<p class="machine" style="margin-top:8px">今天没有决策记录（未被抽中或解析失败）。</p>';

    // No LLM ran under the rule-based policy, so there is no reason to quote.
    const reason = (!isNullPolicy && dec && dec.reason)
      ? `<p class="muted" style="margin:8px 0 2px">这位投资者的说法</p>
         <div class="quote said">${esc(dec.reason)}</div>`
      : (isNullPolicy
        ? '<p class="machine" style="margin-top:8px">规则型模拟（rule-based）：没有 LLM 调用，因此没有自述理由。</p>'
        : '');

    const comments = cmts.length
      ? `<p class="muted" style="margin:8px 0 2px">评论</p>` + cmts.map((c) => `
        <div class="quote said"><span class="tag ${c.stance === 'bullish' ? 'ok'
        : c.stance === 'bearish' ? 'stop' : ''}">${esc(STANCE_ZH[c.stance] || c.stance || '—')}</span>
          ${esc(c.text || '')}</div>`).join('')
        + (isNullPolicy ? '<p class="faint">评论文本由规则基线套模板产生，不是语言模型写的。</p>' : '')
      : '';

    const checkouts = cos.length
      ? `<p class="muted" style="margin:8px 0 2px">适当性结账</p>` + cos.map((r) => {
        const tok = ocColor(r.oc);
        return `<p class="machine">${esc(r.fund || '—')} ${esc(ACT_ZH[r.act] || r.act || '')} →
          <span${tok ? ` style="color:var(${tok})"` : ''} title="${esc(ocZh(r.oc))}">${esc(ocShort(r.oc))}</span>
          · 反事实 <span title="${esc(ocZh(r.oc_cf))}">${esc(ocShort(r.oc_cf))}</span></p>`;
      }).join('')
      : '';

    const trades = acts.length
      ? `<p class="muted" style="margin:8px 0 2px">成交</p>` + acts.map((r) => `
        <p class="machine">${esc(ACT_ZH[r.kind] || r.kind || '')} ${esc(r.fund || '')}
          ${num(Math.round(r.amt || 0))} 元${r.fee ? ` · 费用 ${num(r.fee, 2)} 元` : ''}${
        r.nav ? ` · 净值 ${num(r.nav, 4)}` : ''}</p>`).join('')
      : '';

    return `
      <dl class="kv">
        <dt>人口格</dt><dd>${esc(cellZh(a))}</dd>
        <dt>风险测评</dt><dd>${esc(a.riskClass || '—')}</dd>
        <dt>模态臂</dt><dd><span class="armbadge">${armDot(a.arm)}
          <span style="color:${safeColor(model.armColor[a.arm], 'var(--ink)')}">${esc(a.arm || '—')}</span></span>
          ${esc(ARM_LONG_ZH[a.arm] || '')}</dd>
        <dt>当日曝光</dt><dd>${num(seen.length)} 条</dd>
        ${holdings}
      </dl>
      ${machine}${reason}${comments}${checkouts}${trades}
      <p class="muted" style="margin-top:10px">
        <a href="${esc(agentHref(a.id))}">看这位投资者的完整时间线 →</a></p>
      <p class="muted"><a href="#" data-act="clear">← 返回图例</a></p>`;
  }

  /** A bare relative hash makes the browser keep the current ?run= and ?base=
   *  (contract §9); nothing here needs to read location, so nothing does. */
  function agentHref(id) {
    return `#/agent/${encodeURIComponent(String(id))}`;
  }

  function renderInspector() {
    if (!el.inspector) return;
    const sel = state.sel || {};
    el.inspector.innerHTML = sel.agent ? inspectorAgent(sel.agent)
      : sel.post ? inspectorPost(sel.post)
        : inspectorDefault();
  }

  on(el.inspector, 'click', (e) => {
    const link = e.target && e.target.closest ? e.target.closest('[data-act="clear"]') : null;
    if (!link) return;
    e.preventDefault();
    fire('onSelect', null);
  });

  /* ---- checkout table --------------------------------------------------- */

  const CO_CAP = 300;

  function renderCheckout() {
    if (!el.checkout) return;
    const rows = [];
    for (let k = 0; k <= state.day && k < model.byDay.length; k++) {
      const g = model.byDay[k];
      for (const r of g.co) rows.push({ r, t: g.t, d: g.d, k });
    }
    if (!rows.length) {
      el.checkout.innerHTML = '<p class="muted">到今天为止还没有任何适当性结账事件。</p>';
      return;
    }
    rows.reverse();                                  // newest first
    const shown = rows.slice(0, CO_CAP);
    const body = shown.map(({ r, t, d, k }) => {
      const a = model.agents.get(String(r.i)) || {};
      const tok = ocColor(r.oc);
      const tokCf = ocColor(r.oc_cf);
      return `<tr>
        <td class="num">${num(k + 1)}<span class="faint"> ${esc(d || '')}</span></td>
        <td><code>${esc(r.i)}</code></td>
        <td>${esc(a.riskClass || '—')}</td>
        <td>${r.fund ? `<code>${esc(r.fund)}</code>` : '—'}</td>
        <td>${esc(ACT_ZH[r.act] || r.act || '—')}</td>
        <td${tok ? ` style="color:var(${tok})"` : ''} title="${esc(ocZh(r.oc))}">${esc(ocShort(r.oc))}</td>
        <td${tokCf ? ` style="color:var(${tokCf})"` : ''} title="${esc(ocZh(r.oc_cf))}">${esc(ocShort(r.oc_cf))}</td>
        <td class="num">${num(Math.round(r.amt || 0))}</td>
      </tr>`;
    }).join('');
    const note = rows.length > shown.length
      ? `<p class="faint">共 ${num(rows.length)} 笔，只列出最近 ${num(CO_CAP)} 笔。</p>` : '';
    el.checkout.innerHTML = `<table><thead><tr>
        <th class="num">第 N 天</th><th>投资者</th><th>测评</th><th>基金</th>
        <th>动作</th><th>结果</th><th>反事实</th><th class="num">金额（元）</th>
      </tr></thead><tbody>${body}</tbody></table>
      <p class="faint">反事实 <code>oc_cf</code> 是适当性闸门若生效会给出的结论：
        <code>suitability</code> 关掉时 <code>oc</code> 恒为"风险匹配"，两列的差就是闸门的作用。</p>${note}`;
  }

  /* ---- heatmap ---------------------------------------------------------- */

  let lastHeatW = 0;

  function drawHeat() {
    const cv = el.heat;
    if (!cv) return;
    const ctx = cv.getContext ? cv.getContext('2d') : null;
    if (!ctx) return;

    const host = cv.parentElement || cv;
    const cssW = Math.max(260, Math.round(cv.clientWidth || host.clientWidth || 640));
    const nR = heatRows.length;
    const nC = heatCols.length;
    const padL = 8, padR = 8, padT = 8, padB = 20;
    const rowH = nR ? clamp(2, 360 / nR, 10) : 6;
    const cssH = Math.round(padT + padB + Math.max(1, nR) * rowH);
    const dpr = clamp(1, window.devicePixelRatio || 1, 3);
    const wPx = Math.round(cssW * dpr), hPx = Math.round(cssH * dpr);
    if (cv.width !== wPx) cv.width = wPx;
    if (cv.height !== hPx) cv.height = hPx;
    cv.style.height = `${cssH}px`;
    lastHeatW = cssW;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = cssVar('--surface', '#151d2b');
    ctx.fillRect(0, 0, cssW, cssH);
    if (!nR || !nC) return;

    const dim = cssVar('--ink-dim', '#8593ac');
    const ink = cssVar('--ink', '#e6eaf2');
    const colOk = cssVar('--ok', '#4fa981');
    const colSig = cssVar('--signal', '#e8823c');
    const colStop = cssVar('--stop', '#c9536b');
    const cw = (cssW - padL - padR) / nC;
    const ch = (cssH - padT - padB) / nR;
    const labelEvery = Math.max(1, Math.ceil(46 / Math.max(1, cw)));

    heatCols.forEach((col, c) => {
      const x = padL + c * cw;
      for (const [aid, v] of col.eng) {
        const r = heatIdx.get(aid);
        if (r === undefined) continue;
        ctx.fillStyle = safeColor(model.armColor[(model.agents.get(aid) || {}).arm], dim);
        ctx.globalAlpha = 0.12 + 0.78 * Math.min(1, v / 4);
        ctx.fillRect(x, padT + r * ch, Math.max(1, cw - 1), Math.max(1, ch - 0.5));
      }
      ctx.globalAlpha = 1;
      for (const co of col.co) {
        const r = heatIdx.get(co.i);
        if (r === undefined) continue;
        ctx.fillStyle = co.oc === 'confirm_signed' ? colSig
          : ocColor(co.oc) === '--stop' ? colStop : colOk;
        ctx.fillRect(x + cw / 2 - 2, padT + r * ch, 4, Math.max(1.5, ch));
      }
      if (c % labelEvery === 0) {
        ctx.fillStyle = dim;
        ctx.font = '10px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`D${col.t}`, x + cw / 2, cssH - 7);
      }
      if (c === state.day) {
        ctx.strokeStyle = ink;
        ctx.lineWidth = 1;
        ctx.strokeRect(Math.round(x) + 0.5, padT + 0.5,
          Math.max(2, cw - 1), cssH - padT - padB - 1);
      }
    });
    ctx.globalAlpha = 1;
  }

  function renderHeatNote() {
    if (!el.heatNote) return;
    const lines = [`${num(heatRows.length)} 位投资者 × ${num(nDays)} 天。`
      + '格子越深表示这位投资者当天互动越多（赞 + 藏 + 关 + 评，4 次及以上为最深），'
      + '颜色是所属模态臂；竖条是当天的适当性结账（橙=签署确认书，红=拒签，绿=风险匹配）；'
      + '白框是当前这一天。'];
    if (truncated) {
      lines.push(`事件日志已截断：${model.truncation.rule || '规则未记录'}。`
        + '被丢掉的曝光与关注行不会出现在这张图里。');
    }
    if (!heatRows.length) lines.push('人口队列为空，这张图没有行可画。');
    el.heatNote.innerHTML = lines.map((s) => esc(s)).join('<br>');
  }

  let ro = null;
  if (typeof ResizeObserver === 'function' && el.heat) {
    ro = new ResizeObserver(() => {
      const cv = el.heat;
      const w = Math.round(cv.clientWidth || 0);
      if (w && Math.abs(w - lastHeatW) > 1) drawHeat();
    });
    ro.observe(el.heat.parentElement || el.heat);
  }

  /* ---- transport -------------------------------------------------------- */

  function goto(i) {
    fire('onPlay', false);
    fire('onDay', clamp(0, i, lastDay));
  }

  on(el.first, 'click', () => goto(0));
  on(el.prev, 'click', () => goto(state.day - 1));
  on(el.next, 'click', () => goto(state.day + 1));
  on(el.last, 'click', () => goto(lastDay));
  on(el.play, 'click', () => fire('onPlay', !state.playing));
  on(el.scrub, 'input', (e) => goto(Number(e.target.value) || 0));
  on(el.speed, 'change', (e) => fire('onSpeed', Number(e.target.value) || 1));

  if (el.scrub) {
    el.scrub.min = '0';
    el.scrub.max = String(lastDay);
    el.scrub.disabled = nDays <= 1;
  }
  if (el.play) el.play.setAttribute('aria-pressed', 'false');

  function renderTransport() {
    const g = model.byDay[state.day];
    const d = g ? (g.d || model.dateOf.get(g.t) || '') : '';
    if (el.daylabel) {
      el.daylabel.textContent = nDays
        ? `第 ${state.day + 1} 天 · ${d || '日期未记录'}` : '第 — 天';
      el.daylabel.title = `共 ${nDays} 个交易日`;
    }
    if (el.scrub && el.scrub.value !== String(state.day)) el.scrub.value = String(state.day);
    if (el.scrub) el.scrub.setAttribute('aria-valuetext', `第 ${state.day + 1} 天${d ? ` ${d}` : ''}`);
    const atStart = state.day <= 0, atEnd = state.day >= lastDay;
    if (el.first) el.first.disabled = atStart;
    if (el.prev) el.prev.disabled = atStart;
    if (el.next) el.next.disabled = atEnd;
    if (el.last) el.last.disabled = atEnd;
    if (el.play) el.play.disabled = nDays <= 1;
  }

  /* ---- the three entry points the shell calls --------------------------- */

  function renderAll() {
    renderTransport();
    renderFeed();
    renderTallies();
    renderInspector();
    renderCheckout();
    drawHeat();
  }

  function setDay(i) {
    const next = clamp(0, i | 0, lastDay);
    state.day = next;
    renderAll();
  }

  function setSelection(sel) {
    state.sel = (sel && (sel.post || sel.agent)) ? sel : null;
    renderFeed();
    renderInspector();
  }

  function setPlaying(playing) {
    state.playing = !!playing;
    if (!el.play) return;
    el.play.textContent = state.playing ? '⏸ 暂停' : '▶ 播放';
    el.play.setAttribute('aria-pressed', state.playing ? 'true' : 'false');
  }

  renderHeatNote();
  setPlaying(false);
  renderAll();

  return {
    setDay,
    setSelection,
    setPlaying,
    destroy() {
      ac.abort();
      if (ro) { try { ro.disconnect(); } catch { /* already gone */ } }
      ro = null;
      stCache.clear();
    },
  };
}

export default mountReplayPanels;

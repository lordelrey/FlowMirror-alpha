/* FlowMirror viewer — 模态对照.

   This page is the project's headline experiment made legible: one post, rendered as
   each modality arm actually saw it, side by side. Three things it is built to get
   right, because the previous viewer got them wrong:

   1. The number of columns is `model.arms.length` and nothing else. A two-arm run
      renders two columns. The old page laid out T / TC / TV unconditionally, so every
      two-arm run showed an empty gold column that read as "the image arm produced
      nothing" when in fact the arm did not exist.
   2. `source === 'raw'` has no card text at all. It says so, in words, where the text
      would have been, and then shows the counts it does have. It never renders an
      empty text box.
   3. The TV arm carries digests, never pixels. The default is a placeholder that names
      the sha prefix and how many impressions carried it. A real picture appears only
      when the local service is answering AND the run configured an images root, and
      then it is badged 本地专用 — the image is not redistributed with the repository.

   The run-level table at the bottom is descriptive counts from one run. The note under
   it says so; this page must not read like a result. */

import { esc, pct, num } from './data.js';
import { ARM_ZH, INTENT_ZH, IG_ZH } from './vocab.js';

const SHA64 = /^[0-9a-f]{64}$/i;
/* armColor comes from getComputedStyle of our own --arm-* vars, or from data.js's
   fallback hexes — but the arm NAME comes from the run, so a hostile name can only
   ever produce '' plus a fallback. Gate the value anyway before it reaches a style
   attribute: a colour is the one place where escaping HTML is not enough. */
const SAFE_COLOR = /^(#[0-9a-fA-F]{3,8}|rgba?\([0-9.,%\s/]+\)|hsla?\([0-9.,%\s/a-z]+\)|[a-zA-Z]{3,24})$/;

const armInk = (model, arm) => {
  const c = String(((model && model.armColor) || {})[arm] || '').trim();
  return SAFE_COLOR.test(c) ? c : 'var(--ink-dim)';
};

/* Layout and sizing only — every colour is a token from style.css. Scoped to this
   page's own root so nothing here can reach another page. */
const CSS = `
#page-modality .mod-tools {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: end;
  padding: 10px 13px; border-bottom: 1px solid var(--line-soft);
}
#page-modality .mod-tools label.field { min-width: 0; }
#page-modality .mod-tools .grow { flex: 1 1 190px; }
#page-modality .picker { max-height: 300px; overflow-y: auto; }
#page-modality .picker a.post { text-decoration: none; color: inherit; }
#page-modality .picker .row1 { display: flex; flex-wrap: wrap; gap: 4px 9px; align-items: baseline; }
#page-modality .picker .row2 { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
#page-modality .armcol .txt.empty-note {
  white-space: normal; color: var(--ink-dim); background: var(--surface-2);
}
#page-modality .imgslot { padding: 0 11px 10px; }
#page-modality .imgslot .placeholder { margin: 0; }
#page-modality figure.modimg { margin: 0; display: grid; gap: 6px; }
#page-modality figure.modimg img {
  display: block; width: 100%; max-height: 232px; object-fit: contain;
  background: var(--ground); border: 1px solid var(--line); border-radius: var(--r);
}
#page-modality figure.modimg figcaption {
  font-size: 11px; color: var(--ink-dim);
  display: flex; flex-wrap: wrap; gap: 5px; align-items: center;
}
#page-modality .armcol .stat { display: grid; gap: 1px; }
#page-modality .armcol .stat .line { display: flex; justify-content: space-between; gap: 10px; }
#page-modality .armcol .stat .line b { color: var(--ink); font-weight: 500; }
#page-modality .caveat { margin: 9px 0 0; }
`;

/* ---------- derivations ------------------------------------------------- */

/** Impressions and distinct reached agents per post, over the whole run. */
function postReach(model) {
  const imps = new Map();
  const reach = new Map();
  for (const r of model.rows) {
    if (r.ev !== 'imp') continue;
    const pid = String(r.p);
    imps.set(pid, (imps.get(pid) || 0) + 1);
    let s = reach.get(pid);
    if (!s) reach.set(pid, (s = new Set()));
    s.add(String(r.i));
  }
  return { imps, reach };
}

/** Per-arm counts across the whole run. Derived from byDay + each agent's arm, as the
 *  card specifies. Engagement is recorded per agent-DAY (posts.json's own definition),
 *  so the rate's denominator is agent-days with at least one impression — not cards. */
function runSummary(model) {
  const armOf = (id) => {
    const a = model.agents.get(String(id));
    return a ? a.arm : undefined;
  };
  const blank = () => ({
    agents: 0, imp: 0, reach: new Set(), impDays: new Set(), engDays: new Set(),
    cmt: 0, click: 0, co: 0,
  });
  const acc = new Map(model.arms.map((a) => [a, blank()]));
  for (const a of model.agents.values()) {
    const s = acc.get(a.arm);
    if (s) s.agents++;
  }
  for (const g of model.byDay) {
    for (const r of g.imp) {
      const s = acc.get(r.arm || armOf(r.i));
      if (!s) continue;
      s.imp++;
      s.reach.add(String(r.i));
      s.impDays.add(`${g.t}|${r.i}`);
    }
    // imp for this day is already folded in above, so the membership test is exact
    for (const r of g.dec) {
      const s = acc.get(r.arm || armOf(r.i));
      if (!s) continue;
      const on = ((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0)) > 0;
      if (on && s.impDays.has(`${g.t}|${r.i}`)) s.engDays.add(`${g.t}|${r.i}`);
    }
    for (const [kind, key] of [['cmt', 'cmt'], ['click', 'click'], ['co', 'co']]) {
      for (const r of g[kind]) {
        const s = acc.get(armOf(r.i));
        if (s) s[key]++;
      }
    }
  }
  return acc;
}

/* ---------- the TV image slot ------------------------------------------ */

/** Which digest was actually shown, if any: sha_shown wins over the note's first sha. */
function shownSha(image) {
  if (!image) return null;
  const idxs = Array.isArray(image.shown_idx) ? image.shown_idx : [];
  const map = image.sha_shown || {};
  for (const i of idxs) {
    const s = map[String(i)];
    if (s && SHA64.test(String(s))) return String(s);
  }
  const s0 = image.sha;
  return s0 && SHA64.test(String(s0)) ? String(s0) : null;
}

/** The honest default: words and a digest prefix, never a broken <img>. */
function imagePlaceholder(model, post) {
  const image = post.image;
  const sha = shownSha(image);
  const shaBit = sha
    ? `摘要 <code>${esc(sha.slice(0, 8))}</code>`
    : '本次运行未记录图片摘要';

  if (!image) {
    return '<div class="placeholder">该运行未导出展示包，没有逐帖图片摘要，'
      + '无法说明 TV 臂当时附了哪张图。</div>';
  }
  if (!image.n_images) {
    return '<div class="placeholder">这条帖子在内容池里没有图片，'
      + 'TV 臂与 T 臂看到的都是纯文本卡片。</div>';
  }
  const attached = Number(image.attached_impressions || 0);
  const rootOn = !!(model.images && model.images.root);
  if (attached > 0) {
    return `<div class="placeholder">附了 ${num(image.n_images)} 张图中的 1 张，`
      + `${shaBit}，本次运行有 ${num(attached)} 次曝光真的带上了像素。`
      + '图片不随仓库分发，页面也不内联像素。</div>';
  }
  if (!rootOn) {
    return `<div class="placeholder">本次运行未配置图片库（<code>images_root</code>），`
      + `TV 臂零附图：内容池记着 ${num(image.n_images)} 张图（${shaBit}），`
      + '但没有一次曝光带上像素。图片不随仓库分发。</div>';
  }
  return `<div class="placeholder">配了图片库，但这条帖子零附图（缺文件或摘要不符）：`
    + `内容池记着 ${num(image.n_images)} 张图，${shaBit}。图片不随仓库分发。</div>`;
}

/** Upgrade a placeholder to the real file, but only on a successful load. */
function tryLocalImage(slot, sha, dead) {
  const img = new Image();
  img.decoding = 'async';
  img.alt = 'TV 臂本次曝光实际附带的图片，由本地服务按摘要校验后提供';
  img.onload = () => {
    if (dead.v || !slot.isConnected) return;
    const fig = document.createElement('figure');
    fig.className = 'modimg';
    fig.appendChild(img);
    const cap = document.createElement('figcaption');
    cap.innerHTML = '<span class="chip local">本地专用</span>'
      + `<code>${esc(sha.slice(0, 8))}</code>`
      + '<span class="faint">经本地服务按 sha256 校验后读取；图片不随仓库分发</span>';
    fig.appendChild(cap);
    slot.replaceChildren(fig);
  };
  img.onerror = () => { /* the placeholder already on screen stays */ };
  img.src = `/api/images/${encodeURIComponent(sha)}`;
}

/* ---------- rendering --------------------------------------------------- */

function armColumn(model, post, arm) {
  const slot = (post.arms || {})[arm] || null;
  const ink = armInk(model, arm);
  const isTV = arm === 'TV';
  const parts = [];

  parts.push(`<div class="armcol">
    <h3><span class="armbadge"><i style="background:${ink}"></i>${esc(arm)}</span>
      <span class="muted">${esc(armLabel(arm))}</span></h3>`);

  if (slot && slot.text != null) {
    parts.push(`<div class="txt">${esc(slot.text)}</div>`);
    if (slot.truncated) {
      parts.push('<div class="stat"><span class="faint">'
        + `文案在导出时截断至 ${num(slot.chars)} 字</span></div>`);
    }
  } else if (model.source === 'raw') {
    parts.push('<div class="txt empty-note">无卡片文案：该运行未导出展示包。</div>');
  } else if (!slot) {
    parts.push('<div class="txt empty-note">这条帖子在该臂上没有任何曝光，'
      + '因此没有渲染过卡片。</div>');
  } else {
    parts.push('<div class="txt empty-note">卡片文案不可用：'
      + '展示包导出时未能重放内容池。</div>');
  }

  if (isTV) {
    parts.push(`<div class="imgslot" data-arm="TV">${imagePlaceholder(model, post)}</div>`);
  }

  const line = (k, v) => `<span class="line"><span>${k}</span><b>${v}</b></span>`;
  if (slot) {
    parts.push('<div class="stat">'
      + line('曝光', num(slot.impressions))
      + line('触达', `${num(slot.reach)} 人`)
      + line('有互动', `${num(slot.engaged_agents)} 人`)
      + line('互动率', pct(slot.engagement_rate))
      + line('点击', num(slot.clicks))
      + line('评论', num(slot.comments))
      + line('结账', num(slot.checkouts))
      + '</div>');
  } else {
    parts.push('<div class="stat"><span class="faint">该臂无计数</span></div>');
  }
  parts.push('</div>');
  return parts.join('');
}

function armLabel(arm) {
  return ARM_ZH[arm] || '本次运行的臂';
}

/* Every figure on this page is derived from the event log; when the log was
   truncated the tallies are silently incomplete, so the arm table must say
   so. Returns '' (no empty node) when no truncation was applied. */
function truncationNote(model) {
  if (!(model.truncation && model.truncation.applied)) return '';
  return `<p class="note">事件日志已截断：${esc(model.truncation.rule || '')}，下面的触达与互动率据此不完整</p>`;
}

function compareSection(model, post, stats) {
  const arms = model.arms;
  if (!arms.length) {
    return '<section class="panel" id="modality"><h2>逐臂对照</h2>'
      + '<div class="body"><div class="empty">本次运行没有记录任何模态臂。</div></div></section>';
  }
  const reached = (stats.reach.get(post.post_id) || new Set()).size;
  const head = [
    `<span class="tag">${esc(post.post_id)}</span>`,
    `<span class="tag">${esc(post.org || '机构未记录')}</span>`,
    `<span class="tag">${esc(INTENT_ZH[post.intent] || post.intent || '意图未记录')} · ${esc(IG_ZH[post.ig] || post.ig || '组未记录')}</span>`,
    post.fund ? `<span class="tag">落地 ${esc(post.fund)}</span>`
      : '<span class="tag">无落地基金</span>',
    `<span class="tag">触达 ${num(reached)} 人</span>`,
    `<span class="tag">曝光 ${num(stats.imps.get(post.post_id) || 0)}</span>`,
  ].join(' ');

  const daysShown = Array.isArray(post.days_shown) ? post.days_shown : [];
  const meta = [
    post.published_d ? `发布于第 ${num(post.published_t)} 天（${esc(post.published_d)}）` : null,
    daysShown.length ? `出现在第 ${daysShown.map((t) => num(t)).join('、')} 天` : null,
    post.note_id ? `内容池笔记 <code>${esc(String(post.note_id))}</code>` : null,
  ].filter(Boolean).join(' · ');

  const rawNote = model.source === 'raw'
    ? '<p class="placeholder" style="margin:0 0 var(--gap)">'
      + '该运行未导出展示包，只显示触达与互动率。逐臂卡片文案要跑 '
      + '<code>flowmirror export-bundle</code> 才有。</p>'
    : '';

  const cols = arms.map((a) => armColumn(model, post, a)).join('');

  return `<section class="panel" id="modality">
    <h2>逐臂对照 <span class="note">同一条帖子，${num(arms.length)} 条臂各自看到的样子</span></h2>
    <div class="body">
      <div class="row2" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">${head}</div>
      ${meta ? `<p class="faint" style="margin:0 0 var(--gap)">${meta}</p>` : ''}
      ${rawNote}
      <div class="gridN" style="--cols:${arms.length}">${cols}</div>
      <p class="faint caveat">臂之间的差别只在卡片怎么呈现；同一条帖子的曝光人群由分配到各臂的
        投资者决定，因此各臂的曝光数与触达数本来就不相同。互动记在人日粒度（见展示包
        <code>engaged_agents</code> 的定义），不是逐卡粒度。</p>
    </div>
  </section>`;
}

function pickerRow(model, post, stats, active) {
  const pid = post.post_id;
  const reached = (stats.reach.get(pid) || new Set()).size;
  const image = post.image;
  let imgTag;
  if (!image) imgTag = '<span class="tag">图片信息缺失</span>';
  else if (Number(image.attached_impressions || 0) > 0) {
    imgTag = `<span class="tag ok">附图 ${num(image.attached_impressions)} 次曝光</span>`;
  } else if (image.n_images) imgTag = '<span class="tag">有图未附</span>';
  else imgTag = '<span class="tag">无图</span>';

  return `<a class="post" href="#/modality/${encodeURIComponent(pid)}"
      ${active ? 'aria-current="true"' : ''}>
    <span class="row1">
      <span class="org">${esc(post.org || '机构未记录')}</span>
      <span class="meta">${esc(pid)}</span>
      <span class="intent i-${esc(post.intent || '')}">${esc(INTENT_ZH[post.intent] || post.intent || '意图未记录')}</span>
      <span class="meta">${esc(IG_ZH[post.ig] || post.ig || '组未记录')}</span>
    </span>
    <span class="row2">
      <span class="tag">${post.fund ? `落地 ${esc(post.fund)}` : '无落地基金'}</span>
      <span class="tag">触达 ${num(reached)} 人</span>
      <span class="tag">曝光 ${num(stats.imps.get(pid) || 0)}</span>
      ${imgTag}
    </span>
  </a>`;
}

function summaryTable(model, summary) {
  if (!model.arms.length) return '';
  const rows = model.arms.map((arm) => {
    const s = summary.get(arm) || null;
    if (!s) return '';
    const impDays = s.impDays.size;
    const engRate = impDays ? s.engDays.size / impDays : null;
    const cmtRate = impDays ? s.cmt / impDays : null;
    return `<tr>
      <td><span class="armbadge"><i style="background:${armInk(model, arm)}"></i>${esc(arm)}</span>
        <span class="faint">${esc(armLabel(arm))}</span></td>
      <td class="num">${num(s.agents)}</td>
      <td class="num">${num(s.imp)}</td>
      <td class="num">${num(s.reach.size)}</td>
      <td class="num">${num(impDays)}</td>
      <td class="num">${num(s.engDays.size)}</td>
      <td class="num">${pct(engRate)}</td>
      <td class="num">${num(s.cmt)}</td>
      <td class="num">${pct(cmtRate)}</td>
      <td class="num">${num(s.click)}</td>
      <td class="num">${num(s.co)}</td>
    </tr>`;
  }).join('');

  return `<section class="panel">
    <h2>全运行逐臂计数 <span class="note">${num(model.arms.length)} 行，与本次运行的臂数一致</span></h2>
    <div class="body">
      <div class="scrollx"><table>
        <thead><tr>
          <th>臂</th><th class="num">人数</th><th class="num">曝光</th><th class="num">触达人数</th>
          <th class="num">有曝光人日</th><th class="num">有互动人日</th><th class="num">互动率</th>
          <th class="num">评论</th><th class="num">评论率</th><th class="num">点击</th><th class="num">结账</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <p class="muted caveat">这些是<b>单次运行的描述性计数，不是效应估计</b>：没有重复种子、没有区间、
        没有检验，臂间差异里含多少噪声这张表说不了。互动率的分母是「该臂有曝光的人日」，
        评论率同分母。点击、评论、结账按产生该行的投资者所在臂归属
        （<code>run_meta.arms</code>），只在 agent 级分臂下精确。</p>
      ${model.source === 'raw'
    ? '<p class="faint">本次运行走的是原始产物路径：以上计数由事件日志现算，'
      + '与展示包的口径一致，但没有卡片文案可比。</p>'
    : ''}
    </div>
  </section>`;
}

/* ---------- mount ------------------------------------------------------- */

/* Picker filter and scroll survive the remount that a pick triggers (the row is a real
   link, so the shell re-routes and rebuilds this page). Keyed by tag so switching runs
   does not inherit another run's filter. */
const ui = { tag: null, org: '*', q: '', sort: 'reach', scroll: 0 };

export function mountModalityPage(root, model, postId, opts = {}) {
  // The shell probes the local image service once and passes the flag down,
  // so page modules make zero fetches of their own (contract §1). Default {}
  // lets a caller that forgets opts degrade to "no local API" instead of
  // throwing on property access.
  const localApi = !!(opts && opts.localApi);
  const dead = { v: false };
  if (ui.tag !== model.tag) {
    ui.tag = model.tag; ui.org = '*'; ui.q = ''; ui.sort = 'reach'; ui.scroll = 0;
  }

  const stats = postReach(model);
  const summary = runSummary(model);
  const all = [...model.postById.values()];

  const wanted = postId != null ? String(postId) : null;
  const asked = wanted ? model.postById.get(wanted) : null;
  const missNote = (wanted && !asked)
    ? `<p class="placeholder">地址里的帖子号 <code>${esc(wanted)}</code> 不在本次运行里，`
      + '已改为默认帖子。</p>'
    : '';

  const style = document.createElement('style');
  style.textContent = CSS;
  root.appendChild(style);
  const host = document.createElement('div');
  root.appendChild(host);

  const orgs = model.orgs && model.orgs.length
    ? model.orgs
    : [...new Set(all.map((p) => p.org).filter(Boolean))].sort();

  function visible() {
    const q = ui.q.trim().toLowerCase();
    let list = all.filter((p) => {
      if (ui.org !== '*' && p.org !== ui.org) return false;
      if (!q) return true;
      const hay = [p.post_id, p.org, p.intent, p.ig, p.fund, p.note_id]
        .filter(Boolean).join(' ').toLowerCase();
      if (hay.includes(q)) return true;
      for (const s of Object.values(p.arms || {})) {
        if (s && s.text && String(s.text).toLowerCase().includes(q)) return true;
      }
      return false;
    });
    const rk = (p) => (stats.reach.get(p.post_id) || new Set()).size;
    const ik = (p) => stats.imps.get(p.post_id) || 0;
    const ak = (p) => Number((p.image || {}).attached_impressions || 0);
    if (ui.sort === 'reach') list = list.sort((a, b) => rk(b) - rk(a)
      || a.post_id.localeCompare(b.post_id));
    else if (ui.sort === 'imp') list = list.sort((a, b) => ik(b) - ik(a)
      || a.post_id.localeCompare(b.post_id));
    else if (ui.sort === 'img') list = list.sort((a, b) => ak(b) - ak(a)
      || a.post_id.localeCompare(b.post_id));
    else list = list.sort((a, b) => a.post_id.localeCompare(b.post_id));
    return list;
  }

  /* The shown post: what the route asked for, else the widest-reaching one. */
  const chosen = asked
    || [...all].sort((a, b) => (stats.reach.get(b.post_id) || new Set()).size
      - (stats.reach.get(a.post_id) || new Set()).size
      || a.post_id.localeCompare(b.post_id))[0]
    || null;

  const HEAD = `<div class="page-head">
    <h2>模态对照</h2>
    <p>同一条帖子，本次运行的每条模态臂各自看到的样子。列数就是本次运行真实出现的臂数，
      没有第三条臂时不会留空格。</p>
  </div>`;

  if (!all.length) {
    host.innerHTML = HEAD + '<div class="empty">本次运行没有任何帖子触达投资者，'
      + '没有可对照的卡片。</div>' + summaryTable(model, summary);
    return { destroy() { dead.v = true; root.replaceChildren(); } };
  }

  const list = visible();
  const orgOpts = ['<option value="*">全部机构</option>']
    .concat(orgs.map((o) => `<option value="${esc(o)}"${o === ui.org ? ' selected' : ''}>${esc(o)}</option>`))
    .join('');
  const sortOpts = [['reach', '按触达降序'], ['imp', '按曝光降序'],
    ['img', '按附图次数降序'], ['id', '按帖子号']]
    .map(([v, t]) => `<option value="${v}"${v === ui.sort ? ' selected' : ''}>${t}</option>`)
    .join('');

  host.innerHTML = HEAD + missNote + `<div class="stack">
    <section class="panel">
      <h2>选一条帖子 <span class="note">本次运行发布的帖子，共 ${num(all.length)} 条</span></h2>
      <div class="mod-tools">
        <label class="field">机构<select id="mod-org">${orgOpts}</select></label>
        <label class="field grow">搜索
          <input type="text" id="mod-q" placeholder="帖子号 / 机构 / 基金代码 / 卡片文案"
            value="${esc(ui.q)}"></label>
        <label class="field">排序<select id="mod-sort">${sortOpts}</select></label>
        <span class="muted" id="mod-count"></span>
      </div>
      <div class="picker" id="mod-picker"></div>
    </section>
    ${model.truncation && model.truncation.applied
      /* Every figure below is event-derived; a truncated log makes them
         partial, so the caveat must sit above all of them. */
      ? `<p class="muted">事件日志已截断（${esc(model.truncation.rule)}），下方触达数与互动率据此不完整。</p>`
      : ''}
    ${chosen ? compareSection(model, chosen, stats)
    : '<div class="empty">当前筛选下没有帖子。</div>'}
    ${summaryTable(model, summary)}
  </div>`;

  const picker = host.querySelector('#mod-picker');
  const count = host.querySelector('#mod-count');
  const orgSel = host.querySelector('#mod-org');
  const qBox = host.querySelector('#mod-q');
  const sortSel = host.querySelector('#mod-sort');

  function paintList() {
    const rows = visible();
    picker.innerHTML = rows.length
      ? rows.map((p) => pickerRow(model, p, stats,
        !!chosen && p.post_id === chosen.post_id)).join('')
      : '<div class="empty">没有帖子符合当前筛选。</div>';
    count.textContent = `列出 ${rows.length} / ${all.length} 条`;
  }
  paintList();
  count.textContent = `列出 ${list.length} / ${all.length} 条`;
  picker.scrollTop = ui.scroll;

  const onOrg = () => { ui.org = orgSel.value; ui.scroll = 0; paintList(); picker.scrollTop = 0; };
  const onSort = () => { ui.sort = sortSel.value; ui.scroll = 0; paintList(); picker.scrollTop = 0; };
  const onQ = () => { ui.q = qBox.value; ui.scroll = 0; paintList(); picker.scrollTop = 0; };
  const onScroll = () => { ui.scroll = picker.scrollTop; };
  orgSel.addEventListener('change', onOrg);
  sortSel.addEventListener('change', onSort);
  qBox.addEventListener('input', onQ);
  picker.addEventListener('scroll', onScroll, { passive: true });

  /* Real pixels are opt-in twice over: the local service has to be answering, and the
     run has to have been given an images root. Probe once, never per image. */
  const slot = host.querySelector('.imgslot[data-arm="TV"]');
  const sha = chosen ? shownSha(chosen.image) : null;
  const attached = Number(((chosen && chosen.image) || {}).attached_impressions || 0);
  if (localApi && slot && sha && attached > 0 && model.images && model.images.root) {
    tryLocalImage(slot, sha, dead);
  }

  return {
    destroy() {
      dead.v = true;
      orgSel.removeEventListener('change', onOrg);
      sortSel.removeEventListener('change', onSort);
      qBox.removeEventListener('input', onQ);
      picker.removeEventListener('scroll', onScroll);
      root.replaceChildren();
    },
  };
}

export default mountModalityPage;

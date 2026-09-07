/* FlowMirror viewer — the investor inspector.
   Contract: docs/WEB_CONTRACT_2026-09-07.md §2 (signature), §4 (tokens), §6 (honesty).

   The previous viewer could only show "today", so an investor looked like a dot that
   flickered. This page shows one investor's WHOLE run: the opening state the world gave
   them, the persona card the prompt actually carried, every recorded day, and how their
   familiarity with each institution moved. That is what lets a reader judge whether the
   agents are behaving coherently or just sampling noise.

   Three rules this file takes seriously:

   1. Engine facts and model utterances never look alike. `.machine` (mono, dim) is the
      engine talking; `.said` is the agent. Under a rule-based policy there ARE no agent
      utterances, so nothing on the page gets `.said` — the "reason" is a fixed engine
      string and is labelled as one.
   2. A `raw` model has no per-agent record at all. Rather than blank sections, the page
      derives what the event log does carry (`dec`/`act`/`co`/`cmt`/`st` rows filtered to
      this investor) and says on screen that the timeline is reduced.
   3. The query box is retrieval only. It filters days by substring over text ALREADY
      rendered above it. It is not an interview and must not read like one. */

/* Canonical glosses live in web/vocab.js — see the note there about the same outcome
   having read three different ways across three modules. */
import {
  ACT_ZH as KIND_ZH, OC_SHORT_ZH, OC_COLOR, STANCE_ZH, CLICK_ZH, STATUS_ZH,
} from './vocab.js';

import { esc, num } from './data.js';

/* ---------- vocabulary ------------------------------------------------- */


/* _VALID_OC in flowmirror/engine/loop.py. The three reserved colours are spent here and
   only here: --ok for a suitability match, --signal for a signed confirmation, --stop
   for a decline or a block. */
/* The tag class follows OC_COLOR so the colour and the wording cannot disagree:
   --ok -> .tag.ok, --signal -> .tag.sig, --stop -> .tag.stop. */
const OC_CLS = { '--ok': 'ok', '--signal': 'sig', '--stop': 'stop' };
const OC = Object.fromEntries(Object.keys(OC_SHORT_ZH).map((k) => [k, {
  zh: OC_SHORT_ZH[k], cls: OC_CLS[OC_COLOR[k]] || '',
}]));
const ocOf = (oc) => OC[oc] || { zh: String(oc == null ? '未记录' : oc), cls: '' };


/* ---------- small helpers ---------------------------------------------- */

const yuan = (x) => (x == null ? '—' : `${num(x, 2)} 元`);
const int = (x) => (x == null ? '—' : num(x, 0));

function fundLabel(model, code) {
  if (!code) return '—';
  const f = (model.meta && model.meta.funds && model.meta.funds[code]) || null;
  if (!f) return String(code);
  const bits = [f.org, f.r, f.qdii ? 'QDII' : ''].filter(Boolean).join(' · ');
  return bits ? `${code}（${bits}）` : String(code);
}

function armBadge(model, arm) {
  if (!arm) return '<span class="armbadge">臂未记录</span>';
  const c = (model.armColor && model.armColor[arm]) || 'var(--ink-dim)';
  return `<span class="armbadge"><i style="background:${esc(c)}"></i>${esc(arm)}</span>`;
}

/** Bare hash only. The browser preserves the current query string (?run=, ?base=)
    when resolving a relative hash, so reading location is neither needed nor
    allowed here — routing belongs to app.js alone (contract §9). */
const permalink = (id) => `#/agent/${encodeURIComponent(id)}`;

const isRuleBased = (model) => model.agentPolicy === 'null';

function truncNote(model) {
  const tr = model.truncation;
  if (!tr || !tr.applied) return '';
  return `<p class="muted">事件日志已截断：${esc(tr.rule || '未给出规则')}。`
    + '本页凡用到事件行的地方都可能少了尾部记录。</p>';
}

/* ---------- per-agent derivations -------------------------------------- */

/** Cumulative engagement per investor, for the picker table. Prefers the bundle's own
    per-day record; falls back to `dec` rows, which both loaders always carry. */
function engagementIndex(model) {
  const out = new Map();
  for (const [id, a] of model.agents) {
    const bd = a.byDay || {};
    const keys = Object.keys(bd);
    if (keys.length) {
      let sum = 0;
      for (const k of keys) {
        const r = bd[k] || {};
        sum += (r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) + (r.n_comment || 0);
      }
      out.set(id, { sum, days: keys.length, src: 'bundle' });
    } else {
      out.set(id, { sum: 0, days: 0, src: 'events' });
    }
  }
  // one pass over the stream covers every agent that only has event rows
  for (const g of model.byDay) {
    for (const r of g.dec) {
      const e = out.get(String(r.i));
      if (!e || e.src !== 'events') continue;
      e.sum += (r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) + (r.n_comment || 0);
      e.days += 1;
    }
  }
  return out;
}

/** One uniform row per recorded day, from the bundle record when there is one and from
    the event log when there is not. `reduced` is what the page must admit on screen. */
function timelineOf(model, agent) {
  const extra = new Map();
  for (let i = 0; i < model.byDay.length; i++) {
    const g = model.byDay[i];
    const e = { dec: null, clicks: [], acts: [], cos: [], cmts: [], refl: [] };
    let any = false;
    for (const r of g.dec) if (String(r.i) === agent.id) { e.dec = r; any = true; }
    for (const r of g.click) if (String(r.i) === agent.id) { e.clicks.push(r); any = true; }
    for (const r of g.act) if (String(r.i) === agent.id) { e.acts.push(r); any = true; }
    for (const r of g.co) if (String(r.i) === agent.id) { e.cos.push(r); any = true; }
    for (const r of g.cmt) if (String(r.i) === agent.id) { e.cmts.push(r); any = true; }
    for (const r of g.refl) if (String(r.i) === agent.id) { e.refl.push(r); any = true; }
    if (any) extra.set(i, e);
  }

  const bd = agent.byDay || {};
  const keys = Object.keys(bd).map(Number).filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  const dateAt = (i) => model.dateOf.get(model.days[i]) || '';

  if (keys.length) {
    return {
      reduced: false,
      rows: keys.map((i) => {
        const r = bd[i] || {};
        const e = extra.get(i) || {};
        return {
          i, d: r.d || dateAt(i),
          mood: r.mood, status: r.status, failure: r.failure_kind,
          violations: r.violations || [],
          nRead: r.n_read, nLike: r.n_like, nSave: r.n_save,
          nFollow: r.n_follow, nComment: r.n_comment, aff: r.aff_sum,
          reason: r.reason,
          comments: r.comments || [],
          trades: r.trades || [],
          checkouts: r.checkouts || [],
          clicks: e.clicks || [], refl: e.refl || [],
        };
      }),
    };
  }

  const rows = [...extra.keys()].sort((a, b) => a - b).map((i) => {
    const e = extra.get(i);
    const r = e.dec || {};
    return {
      i, d: r.d || dateAt(i),
      mood: r.mood, status: r.status, failure: r.failure_kind,
      violations: r.violations || [],
      nRead: r.n_read, nLike: r.n_like, nSave: r.n_save,
      nFollow: r.n_follow, nComment: r.n_comment, aff: r.aff_sum,
      reason: r.reason,
      comments: e.cmts.map((c) => ({ p: c.p, stance: c.stance, text: c.text })),
      trades: e.acts, checkouts: e.cos, clicks: e.clicks, refl: e.refl,
    };
  });
  return { reduced: true, rows };
}

/** Familiarity level per institution over the run. `st` is a CHANGE log (see the
    exporter's own note), so a day with no record means the level did not move: the
    series carries the last known value forward, starting from level 0. */
function familiarityOf(model, agent) {
  const n = Math.max(1, model.days.length);
  const recs = new Map();          // day index -> Map(org -> {lv, fam, aff})
  const put = (i, org, v) => {
    if (!recs.has(i)) recs.set(i, new Map());
    recs.get(i).set(org, v);
  };

  let derived = false;
  const raw = agent.familiarity || {};
  if (Object.keys(raw).length) {
    for (const [k, orgs] of Object.entries(raw)) {
      const i = Number(k);
      if (!Number.isFinite(i)) continue;
      for (const [org, v] of Object.entries(orgs || {})) {
        if (v && v.what && v.what !== 'level') continue;
        put(i, org, v || {});
      }
    }
  } else {
    derived = true;
    for (let i = 0; i < model.byDay.length; i++) {
      for (const r of model.byDay[i].st) {
        if (String(r.i) !== agent.id || r.what !== 'level') continue;
        put(i, r.org, { lv: r.lv, fam: r.fam, aff: r.aff });
      }
    }
  }

  const seen = new Set();
  for (const m of recs.values()) for (const org of m.keys()) seen.add(org);
  const orgs = [...new Set([...(model.orgs || []), ...seen])];

  let lvmax = 2;                   // engine levels are 0/1/2 (loop.py §6 gate)
  const series = orgs.map((org) => {
    const pts = [];
    let cur = 0, changes = 0, last = null;
    for (let i = 0; i < n; i++) {
      const v = recs.has(i) ? recs.get(i).get(org) : null;
      if (v && v.lv != null) { cur = Number(v.lv); changes += 1; last = v; }
      pts.push(cur);
      if (cur > lvmax) lvmax = cur;
    }
    return { org, pts, changes, last, final: pts[pts.length - 1] };
  });
  return { series, lvmax, derived, any: recs.size > 0 };
}

/* ---------- rendering: sparkline --------------------------------------- */

function sparkSvg(pts, lvmax, label) {
  const n = Math.max(1, pts.length);
  const w = 100 / n, top = 3, bot = 21;
  const y = (lv) => bot - (lvmax > 0 ? lv / lvmax : 0) * (bot - top);
  let d = '';
  for (let i = 0; i < n; i++) {
    const yy = y(pts[i]).toFixed(2);
    d += `${(i * w).toFixed(2)},${yy} ${((i + 1) * w).toFixed(2)},${yy} `;
  }
  // preserveAspectRatio="none" stretches the box to the panel; non-scaling-stroke keeps
  // the line one weight instead of smearing it.
  return `<svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none"
      role="img" aria-label="${esc(label)}">
    <line x1="0" y1="${bot}" x2="100" y2="${bot}" stroke="var(--line)"
      stroke-width="1" vector-effect="non-scaling-stroke"></line>
    <polyline points="${d.trim()}" fill="none" stroke="var(--accent-ui)"
      stroke-width="1.6" stroke-linejoin="round"
      vector-effect="non-scaling-stroke"></polyline>
  </svg>`;
}

/* ---------- rendering: the picker -------------------------------------- */

function pickerTable(model, engage) {
  const rows = [...model.agents.values()].slice().sort((a, b) => {
    const c = String(a.cell).localeCompare(String(b.cell), 'zh-Hans-CN');
    return c !== 0 ? c : String(a.id).localeCompare(String(b.id));
  });
  if (!rows.length) return '<div class="empty">这次运行没有投资者记录。</div>';

  const body = rows.map((a) => {
    const e = engage.get(a.id) || { sum: 0, days: 0 };
    return `<tr>
      <td><a href="${esc(permalink(a.id))}">${esc(a.id)}</a></td>
      <td><code>${esc(a.cell || '—')}</code></td>
      <td>${esc(a.riskClass || '—')}</td>
      <td>${armBadge(model, a.arm)}</td>
      <td class="num">${int(e.sum)}</td>
      <td class="num">${int(e.days)}</td>
    </tr>`;
  }).join('');

  return `<div class="scrollx"><table>
    <thead><tr>
      <th>投资者</th><th>人口格</th><th>报告等级</th><th>臂</th>
      <th class="num">互动次数</th><th class="num">记录天数</th>
    </tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

function selectRow(model, currentId) {
  const rows = [...model.agents.values()].slice().sort((a, b) => {
    const c = String(a.cell).localeCompare(String(b.cell), 'zh-Hans-CN');
    return c !== 0 ? c : String(a.id).localeCompare(String(b.id));
  });
  const opts = rows.map((a) => {
    const on = a.id === currentId ? ' selected' : '';
    return `<option value="${esc(a.id)}"${on}>${esc(a.id)} · ${esc(a.cell || '—')} · ${esc(a.arm || '?')}</option>`;
  }).join('');
  return `<div class="transport" style="border-top:0">
    <label class="field" style="flex:1 1 260px">换一位投资者
      <select data-role="pick">${opts}</select>
    </label>
    <a href="${esc(permalink(currentId))}">本页固定链接</a>
    <a href="${esc(permalink(''))}">回到全部投资者</a>
  </div>`;
}

/* ---------- rendering: opening state, persona -------------------------- */

function openingPanel(model, agent) {
  const ident = `<dl class="kv">
    <dt>投资者</dt><dd>${esc(agent.id)}</dd>
    <dt>人口格</dt><dd>${esc(agent.cell || '—')}</dd>
    <dt>年龄 / 资产 / 风险</dt>
    <dd>${esc(agent.age)} · ${esc(agent.asset)} · ${esc(agent.risk)}</dd>
    <dt>报告风险等级</dt><dd>${esc(agent.riskClass || '—')}</dd>
    <dt>模态臂</dt><dd>${armBadge(model, agent.arm)}</dd>
  </dl>`;

  if (model.source !== 'bundle') {
    const w = agent.wealth0 == null ? '' : `<dl class="kv">
      <dt>开局总财富</dt><dd>${yuan(agent.wealth0)}</dd></dl>
      <p class="faint">总财富来自人口队列文件的 wealth_wan，不是引擎的开局账本。</p>`;
    return `<section class="panel"><h2>开局状态 <span class="note">opening state</span></h2>
      <div class="body">${ident}
        <div class="placeholder">本次运行未导出展示包（bundle）：引擎的开局现金与开局持仓
          不可用。补齐方式是对该运行目录跑 <code>flowmirror export-bundle</code>。</div>
        ${w}</div></section>`;
  }

  let hold;
  if (agent.hold0 == null) {
    hold = '<span class="muted">展示包未记录开局持仓</span>';
  } else {
    const codes = Object.keys(agent.hold0).sort();
    hold = codes.length
      ? codes.map((c) => `${esc(fundLabel(model, c))} ${num(agent.hold0[c], 3)} 份`).join('<br>')
      : '开局空仓';
  }
  return `<section class="panel"><h2>开局状态 <span class="note">opening state</span></h2>
    <div class="body">${ident}
      <dl class="kv">
        <dt>开局现金</dt><dd>${yuan(agent.cash0)}</dd>
        <dt>开局总财富</dt><dd>${yuan(agent.wealth0)}</dd>
        <dt>开局持仓</dt><dd>${hold}</dd>
      </dl>
      <p class="faint">开局状态取自 world.init_investors 在本次配置下的结果；持仓是份额，
        不是金额。</p>
    </div></section>`;
}

function personaPanel(model, agent) {
  const raw = String(agent.persona || '').trim();
  if (!raw) {
    return `<section class="panel"><h2>人物卡 <span class="note">persona</span></h2>
      <div class="body"><div class="placeholder">本次运行没有可显示的人物卡：
        ${model.source === 'bundle' ? '展示包里这位投资者的 persona 为空。'
        : '未能载入人口队列文件，人物卡取不到。'}</div></div></section>`;
  }
  const prose = raw.split(/\n+/).map((l) => esc(l.trim())).filter(Boolean).join('<br>');
  const note = model.source === 'bundle'
    ? '<p class="faint">人物卡由导出器截为 200 字，省略了多少字写在文本末尾；本页不再截断。</p>'
    : '<p class="faint">人物卡直接来自人口队列文件（persona_card_zh_rich），未经截断。</p>';
  return `<section class="panel"><h2>人物卡 <span class="note">进入提示词的原文</span></h2>
    <div class="body"><div class="quote">${prose}</div>${note}</div></section>`;
}

/* ---------- rendering: familiarity ------------------------------------- */

function familiarityPanel(model, agent) {
  const fam = familiarityOf(model, agent);
  if (!fam.any) {
    return `<section class="panel"><h2>机构熟悉度 <span class="note">familiarity</span></h2>
      <div class="body"><div class="placeholder">这位投资者全程没有一条等级变化记录：
        对四家机构的熟悉度始终停在 0 级。</div></div></section>`;
  }
  const cards = fam.series.map((s) => {
    const lab = `${s.org} 熟悉度等级随交易日变化，末级 ${s.final}`;
    const changed = s.changes
      ? `${s.changes} 次变化`
      : '全程未变化（0 级）';
    return `<div>
      <div class="armbadge" style="justify-content:space-between;width:100%">
        <span title="${esc(s.org)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.org)}</span>
        <span>末级 ${esc(String(s.final))}</span>
      </div>
      ${sparkSvg(s.pts, fam.lvmax, lab)}
      <div class="faint">${esc(changed)}${s.last && s.last.fam != null
        ? ` · 曝光存量 ${num(s.last.fam, 2)}` : ''}${s.last && s.last.aff != null
        ? ` · 亲和存量 ${num(s.last.aff, 2)}` : ''}</div>
    </div>`;
  }).join('');

  const src = fam.derived
    ? '<p class="muted">没有展示包，这几条曲线由事件日志的 st 行（what=level）现算。</p>'
      + truncNote(model)
    : '';
  return `<section class="panel">
    <h2>机构熟悉度 <span class="note">纵轴 0–${esc(String(fam.lvmax))} 级 · 横轴交易日</span></h2>
    <div class="body">
      <div class="grid2">${cards}</div>
      <p class="faint">st 只在等级变化的那天记录，所以曲线在两次变化之间按最后已知值延续；
        0 级为不熟悉，1 级为有曝光或亲和存量，2 级为已关注。</p>
      ${src}
    </div></section>`;
}

/* ---------- rendering: the timeline ------------------------------------ */

function dayNode(model, row, ruleBased) {
  const tags = [];
  tags.push(`<span class="tag">情绪 ${esc(row.mood == null ? '—' : String(row.mood))}</span>`);
  if (row.nRead != null) tags.push(`<span class="tag">读 ${int(row.nRead)} 帖</span>`);
  for (const t of row.trades) {
    tags.push(`<span class="tag act">${esc(KIND_ZH[t.kind] || t.kind || '交易')}</span>`);
  }
  for (const c of row.checkouts) {
    const o = ocOf(c.oc);
    tags.push(`<span class="tag${o.cls ? ' ' + o.cls : ''}">${esc(o.zh)}</span>`);
  }
  if (row.status && row.status !== 'ok') {
    tags.push(`<span class="tag stop">决策行异常 · ${esc(STATUS_ZH[row.status] || row.status)}</span>`);
  }

  const lines = [];
  const eng = [
    `赞 ${int(row.nLike)}`, `收藏 ${int(row.nSave)}`,
    `关注 ${int(row.nFollow)}`, `评论 ${int(row.nComment)}`,
  ];
  if (row.aff != null) eng.push(`亲和累计 ${num(row.aff, 0)}`);
  lines.push(`<div class="machine">${esc(eng.join(' · '))}</div>`);

  if (row.clicks.length) {
    const by = row.clicks.map((c) => CLICK_ZH[c.oc] || c.oc || '未记录');
    lines.push(`<div class="machine">${esc(`点击 ${row.clicks.length} 次（${by.join('、')}）`)}</div>`);
  }

  for (const t of row.trades) {
    const bits = [
      `${KIND_ZH[t.kind] || t.kind || '交易'} ${fundLabel(model, t.fund)}`,
      `${num(t.amt, 2)} 元`,
      t.units == null ? null : `${num(t.units, 3)} 份`,
      t.nav == null ? null : `净值 ${num(t.nav, 4)}`,
      t.fee == null ? null : `手续费 ${num(t.fee, 2)} 元`,
      t.p == null ? null : `来自帖子 ${t.p}`,
    ].filter(Boolean);
    lines.push(`<div class="machine">${esc(bits.join(' · '))}</div>`);
  }

  for (const c of row.checkouts) {
    const o = ocOf(c.oc);
    const bits = [
      `结账 ${fundLabel(model, c.fund)}`,
      KIND_ZH[c.act] || c.act || '',
      c.amt == null ? null : `${num(c.amt, 2)} 元`,
      `结论 ${o.zh}`,
      c.ig ? `意图组 ${c.ig}` : null,
      (c.oc_cf != null && c.oc_cf !== c.oc) ? `反事实结论 ${ocOf(c.oc_cf).zh}` : null,
    ].filter(Boolean);
    lines.push(`<div class="machine">${esc(bits.join(' · '))}</div>`);
  }

  if (row.status && row.status !== 'ok') {
    const bits = [`status=${row.status}`];
    if (row.failure) bits.push(`failure_kind=${row.failure}`);
    if (row.violations && row.violations.length) {
      bits.push(`violations=${row.violations.join(',')}`);
    }
    lines.push(`<div class="machine">${esc(bits.join(' · '))}</div>`);
  }

  const reason = String(row.reason == null ? '' : row.reason).trim();
  if (reason) {
    lines.push(ruleBased
      ? `<div class="machine">引擎规则说明：${esc(reason)}</div>`
      : `<div class="said">「${esc(reason)}」</div>`);
  } else {
    lines.push('<div class="machine">决策行没有理由文本。</div>');
  }

  for (const c of row.comments) {
    const head = [
      '评论', STANCE_ZH[c.stance] || c.stance || '未记录立场',
      c.p ? `对帖子 ${c.p}` : null,
    ].filter(Boolean).join(' · ');
    const text = String(c.text == null ? '' : c.text).trim();
    if (ruleBased) {
      lines.push(`<div class="machine">${esc(head)}（引擎模板）：${esc(text || '无文本')}</div>`);
    } else {
      lines.push(`<div class="machine">${esc(head)}</div>`
        + `<div class="said">「${esc(text || '无文本')}」</div>`);
    }
  }

  if (row.refl.length) {
    const shas = row.refl.map((r) => String(r.summary_sha || '').slice(0, 8) || '未记录');
    lines.push(`<div class="machine">${esc(`当晚生成了记忆反思摘要（只留摘要 sha ${shas.join('、')}）`)}</div>`);
  }

  const hay = [
    reason,
    ...row.comments.map((c) => `${STANCE_ZH[c.stance] || c.stance || ''} ${c.text || ''}`),
    ...row.trades.map((t) => `${KIND_ZH[t.kind] || t.kind || ''} ${fundLabel(model, t.fund)}`),
    ...row.checkouts.map((c) => `${fundLabel(model, c.fund)} ${ocOf(c.oc).zh}`),
  ].join(' \n ').toLowerCase();

  const el = document.createElement('div');
  el.className = 'tl-day';
  el.dataset.hay = hay;
  el.innerHTML = `<div class="tl-when">第 ${row.i + 1} 天<br>${esc(row.d || '日期未记录')}</div>
    <div class="tl-what">
      <div class="tl-tags">${tags.join('')}</div>
      ${lines.join('')}
    </div>`;
  return el;
}

/* ---------- the mount -------------------------------------------------- */

export function mountAgentPage(root, model, agentId) {
  const listeners = [];
  const on = (el, ev, fn) => {
    el.addEventListener(ev, fn);
    listeners.push(() => el.removeEventListener(ev, fn));
  };
  const engage = engagementIndex(model);
  let current = agentId ? String(agentId) : null;

  function renderPicker(missing) {
    root.innerHTML = `
      <div class="page-head">
        <h2>投资者检视</h2>
        <p>选一位投资者，看他整段运行：开局状态、进入提示词的人物卡、每个交易日的阅读与
          交易、以及对各机构熟悉度的变化。</p>
      </div>
      ${missing ? `<div class="err"><h2>没有这位投资者</h2>
        <p class="muted">运行 <code>${esc(model.tag)}</code> 里没有 id 为
          <code>${esc(missing)}</code> 的记录。下面是本次运行真实存在的
          ${model.agents.size} 位。</p></div>` : ''}
      <section class="panel" style="margin-top:var(--gap)">
        <h2>全部投资者 <span class="note">按人口格排序 · 互动次数 = 赞 + 收藏 + 关注 + 评论 累计</span></h2>
        ${pickerTable(model, engage)}
      </section>`;
  }

  function renderOne(agent) {
    const ruleBased = isRuleBased(model);
    const tl = timelineOf(model, agent);
    const e = engage.get(agent.id) || { sum: 0, days: 0 };

    root.innerHTML = `
      <div class="page-head">
        <h2>投资者 ${esc(agent.id)}</h2>
        <p>${esc(agent.cell || '人口格未记录')} · 报告等级 ${esc(agent.riskClass || '—')}
          · 累计互动 ${num(e.sum, 0)} 次 · 有记录 ${num(tl.rows.length, 0)} 天（本次运行共
          ${num(model.days.length, 0)} 个交易日）。</p>
      </div>
      <section class="panel">${selectRow(model, agent.id)}</section>
      <div class="grid2" style="margin-top:var(--gap)">
        ${openingPanel(model, agent)}
        ${personaPanel(model, agent)}
      </div>
      <div style="margin-top:var(--gap)">${familiarityPanel(model, agent)}</div>
      <section class="panel" style="margin-top:var(--gap)">
        <h2>逐日时间线 <span class="note">灰色等宽是引擎记录 · 正文是 agent 自己的话</span></h2>
        <div class="body">
          ${ruleBased ? `<div class="placeholder">本次运行是规则型模拟
            （<code>agent_policy=null</code>），没有任何 LLM 调用：下面的「理由」与「评论」
            都是引擎按规则产生的固定文本，不是 agent 的自述，因此全部按引擎记录排版。</div>` : ''}
          ${tl.reduced ? `<div class="placeholder">本次运行未导出展示包（bundle）：时间线由
            事件日志的 dec / act / co / cmt 行现算，缺少展示包才有的逐日记录字段。
            ${esc('补齐方式是对该运行目录跑 flowmirror export-bundle。')}</div>` : ''}
          ${truncNote(model)}
          <label class="field" style="margin:2px 0 4px">在这位投资者的记录里检索
            <input type="text" data-role="q" placeholder="例如 观望、广发基金、588220"
              autocomplete="off" spellcheck="false">
          </label>
          <p class="faint" data-role="qnote">只在上面已经显示出来的文本里做子串匹配（理由、
            评论、基金名与代码）。这不是访谈：不会向 agent 提问，也不会生成或改写任何句子。</p>
          <p class="muted" data-role="count"></p>
          <div class="timeline" data-role="tl"></div>
          <div class="empty" data-role="none" hidden>未找到</div>
        </div>
      </section>`;

    const tlWrap = root.querySelector('[data-role="tl"]');
    const nodes = [];
    if (!tl.rows.length) {
      tlWrap.innerHTML = '<div class="empty">这位投资者在本次运行里没有任何逐日记录。</div>';
    } else {
      for (const r of tl.rows) {
        const node = dayNode(model, r, ruleBased);
        nodes.push(node);
        tlWrap.appendChild(node);
      }
    }

    const countEl = root.querySelector('[data-role="count"]');
    const noneEl = root.querySelector('[data-role="none"]');
    const input = root.querySelector('[data-role="q"]');

    function applyFilter() {
      const q = (input.value || '').trim().toLowerCase();
      let shown = 0;
      for (const node of nodes) {
        const hit = !q || (node.dataset.hay || '').includes(q);
        node.hidden = !hit;
        if (hit) shown += 1;
      }
      noneEl.hidden = !(q && shown === 0);
      countEl.textContent = q
        ? `匹配 ${shown} / ${nodes.length} 天`
        : `共 ${nodes.length} 天有记录`;
    }
    if (input) on(input, 'input', applyFilter);
    applyFilter();

    const pick = root.querySelector('[data-role="pick"]');
    if (pick) {
      on(pick, 'change', () => {
        const next = model.agents.get(pick.value);
        if (!next) return;
        // The shell owns the route, so switching here re-renders in place rather than
        // writing the hash. "本页固定链接" is the real link for a shareable URL.
        current = next.id;
        render();
        const again = root.querySelector('[data-role="pick"]');
        if (again) again.focus();
      });
    }
  }

  function render() {
    while (listeners.length) listeners.pop()();
    if (!current) { renderPicker(null); return; }
    const agent = model.agents.get(current);
    if (!agent) { renderPicker(current); return; }
    renderOne(agent);
  }

  render();

  return {
    destroy() {
      while (listeners.length) listeners.pop()();
      root.innerHTML = '';
    },
  };
}

export default mountAgentPage;

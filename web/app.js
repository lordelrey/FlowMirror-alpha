/* FlowMirror replay viewer.

   Reads a finished run directly: runs/out/<tag>/event_log.jsonl, run_meta.json and
   invariants_report.json, plus the cohort file for the population cells. Nothing is
   hard-coded to one run: arms, institutions, day count and cell axes all come from
   the data, so a two-arm run, a three-arm run and a rule-based null run all render.

   The agents have no physical location, so position encodes identity instead of space:
   a grid of tiles by age (columns) and assets (rows), each tile split into three bands
   by risk tolerance, with each investor a dot inside its own cell. A wave of exposure
   sweeping the field therefore shows WHICH KIND of investor a creative reached. */

const qs = new URLSearchParams(location.search);
const RUN = qs.get('run') || 'demo_three-arm';
const BASE = qs.get('base') || '..';

const ARM_FALLBACK = ['#5c8bb0', '#8e7bc4', '#d9a441', '#6aa8a0', '#b0705c'];
const css = (n, d) => (getComputedStyle(document.documentElement).getPropertyValue(n) || d).trim();

const AGE_ORDER = ['under_30', '30_45', '45_60', 'over_60'];
const ASSET_ORDER = ['low', 'mid', 'high'];
const RISK_ORDER = ['fragile', 'typical', 'tolerant'];
const AGE_ZH = { under_30: '30岁以下', '30_45': '30–45岁', '45_60': '45–60岁', over_60: '60岁以上' };
const ASSET_ZH = { low: '低资产', mid: '中资产', high: '高资产' };
const RISK_ZH = { fragile: '脆弱', typical: '一般', tolerant: '耐受' };
const INTENT_ZH = { I1: '品牌推广', I2: '推品转化', I3: '投资教育' };
const OC_ZH = {
  match: '风险匹配，直接成交',
  confirm_signed: '签署风险不匹配确认书后成交',
  confirm_declined: '拒签确认书，放弃',
  purchase_blocked: '暂停申购',
  no_holdings: '无持仓，赎回被拒',
  hard_block: '硬性拦截',
};
const STANCE_ZH = { bullish: '看多', bearish: '看空', watching: '观望', no_comment: '未评论' };
const STANCE_COLOR = { bullish: '--ok', bearish: '--stop', watching: '--ink-dim' };

const S = {
  meta: null, rows: [], byDay: [], days: [], agents: new Map(), arms: [], armColor: {},
  orgs: [], cells: [], axes: { age: [], asset: [] }, layout: new Map(), orgPos: new Map(),
  day: 0, playing: false, selAgent: null, selPost: null, phase: 0, raf: 0, speed: 1,
  tally: null, invariants: null, heatRows: [],
};

/* ---------- loading ---------------------------------------------------- */

async function getText(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.text();
}
const getJSON = async (p) => JSON.parse(await getText(p));

async function load() {
  const dir = `${BASE}/runs/out/${RUN}`;
  const [logText, meta] = await Promise.all([
    getText(`${dir}/event_log.jsonl`),
    getJSON(`${dir}/run_meta.json`),
  ]);
  S.meta = meta;
  S.rows = logText.split('\n').filter(Boolean).map((l) => JSON.parse(l));

  try { S.invariants = await getJSON(`${dir}/invariants_report.json`); } catch { S.invariants = null; }

  // cohort: gives every agent its population cell and reported risk class
  let cohort = [];
  try {
    const c = await getJSON(`${BASE}/data/population/agents_seed2027.json`);
    cohort = Object.values(c).find((v) => Array.isArray(v) && v.length && typeof v[0] === 'object') || [];
  } catch { /* the field falls back to a single unlabelled block */ }

  const armOf = meta.arms || {};
  const ids = Object.keys(armOf).sort();
  const byId = new Map(cohort.map((r) => [String(r.id), r]));
  ids.forEach((id) => {
    const p = byId.get(id) || {};
    const cell = String(p.cell || '|' + '|');
    const [age, asset, risk] = cell.split('|');
    S.agents.set(id, {
      id, arm: armOf[id], cell,
      age: age || '?', asset: asset || '?', risk: risk || '?',
      c: p.reported_C || '—', wealth: p.wealth_wan, persona: p.persona_card_zh_rich || '',
    });
  });

  S.arms = (meta.modality && meta.modality.arms) || [...new Set(Object.values(armOf))].sort();
  S.arms.forEach((a, i) => {
    S.armColor[a] = css(`--arm-${a}`, '') || ARM_FALLBACK[i % ARM_FALLBACK.length];
  });

  S.days = [...new Set(S.rows.map((r) => r.t))].sort((a, b) => a - b);
  S.byDay = S.days.map((t) => {
    const g = { t, d: '', post: [], imp: [], dec: [], click: [], co: [], act: [], cmt: [], clim: [], st: [] };
    for (const r of S.rows) if (r.t === t && g[r.ev]) { g[r.ev].push(r); if (!g.d && r.d) g.d = r.d; }
    return g;
  });

  S.postById = new Map(S.rows.filter((r) => r.ev === 'post').map((r) => [r.p, r]));
  S.orgs = [...new Set(S.rows.filter((r) => r.ev === 'post').map((r) => r.org))].sort();
  buildLayout();
  buildHeatRows();
}

/* ---------- layout: identity, not space -------------------------------- */

function buildLayout() {
  const present = [...S.agents.values()];
  const ages = AGE_ORDER.filter((a) => present.some((p) => p.age === a));
  const assets = ASSET_ORDER.filter((a) => present.some((p) => p.asset === a));
  S.axes = { age: ages.length ? ages : ['?'], asset: assets.length ? assets : ['?'] };

  const cv = document.getElementById('field');
  const W = cv.width, H = cv.height;
  const padL = 74, padT = 18, padR = 150, padB = 26;
  const cols = S.axes.age.length, rowsN = S.axes.asset.length;
  const tw = (W - padL - padR) / cols, th = (H - padT - padB) / rowsN;

  const groups = new Map();
  present.forEach((p) => {
    const k = `${p.age}|${p.asset}|${p.risk}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  });

  S.tileBox = { padL, padT, padR, padB, tw, th, cols, rowsN };
  S.layout.clear();
  for (const [k, list] of groups) {
    const [age, asset, risk] = k.split('|');
    const ci = Math.max(0, S.axes.age.indexOf(age));
    const ri = Math.max(0, S.axes.asset.indexOf(asset));
    const bi = Math.max(0, RISK_ORDER.indexOf(risk));
    const x0 = padL + ci * tw + 7;
    const bandH = (th - 16) / RISK_ORDER.length;
    const y0 = padT + ri * th + 9 + bi * bandH;
    const w = tw - 14, h = bandH - 3;
    list.sort((a, b) => a.id.localeCompare(b.id));
    // Pack the band from its centre so a cell holding two agents still reads as a
    // small cluster rather than a lonely dot in a large empty tile.
    const step = 13;
    const per = Math.max(1, Math.floor(w / step));
    const rowsUsed = Math.ceil(list.length / per);
    const usedW = Math.min(list.length, per) * step;
    const ox = x0 + (w - usedW) / 2 + step / 2;
    const oy = y0 + (h - (rowsUsed - 1) * 11) / 2;
    list.forEach((p, i) => {
      S.layout.set(p.id, {
        x: ox + (i % per) * step,
        y: Math.max(y0 + 4, Math.min(oy + Math.floor(i / per) * 11, y0 + h - 3)),
      });
    });
  }

  // institutions sit down the right margin, outside the tile grid
  S.orgPos.clear();
  const ox = W - padR + 54;
  S.orgs.forEach((o, i) => {
    const oy = padT + 40 + i * ((H - padT - padB - 60) / Math.max(1, S.orgs.length - 1 || 1));
    S.orgPos.set(o, { x: ox, y: S.orgs.length === 1 ? H / 2 : oy });
  });
}

function buildHeatRows() {
  const list = [...S.agents.values()].sort((a, b) => {
    const ai = AGE_ORDER.indexOf(a.age) - AGE_ORDER.indexOf(b.age);
    if (ai) return ai;
    const si = ASSET_ORDER.indexOf(a.asset) - ASSET_ORDER.indexOf(b.asset);
    if (si) return si;
    const ri = RISK_ORDER.indexOf(a.risk) - RISK_ORDER.indexOf(b.risk);
    if (ri) return ri;
    return a.id.localeCompare(b.id);
  });
  S.heatRows = list.map((p) => p.id);
}

/* ---------- per-day derived state -------------------------------------- */

function dayState(i) {
  const g = S.byDay[i];
  const st = {
    engaged: new Set(), commented: new Map(), traded: new Map(), checkout: new Map(),
    seenBy: new Map(), postOrg: new Map(), impOf: [],
  };
  for (const r of g.post) st.postOrg.set(r.p, r.org);
  for (const r of g.imp) {
    if (!st.seenBy.has(r.p)) st.seenBy.set(r.p, []);
    st.seenBy.get(r.p).push(r.i);
    st.impOf.push(r);
    // a post can be shown days after it was published, so fall back to the index
    if (!st.postOrg.has(r.p)) {
      const src = S.postById.get(r.p);
      if (src) st.postOrg.set(r.p, src.org);
    }
  }
  for (const r of g.dec) {
    if ((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) > 0) st.engaged.add(r.i);
  }
  for (const r of g.cmt) st.commented.set(r.i, r.stance);
  for (const r of g.act) st.traded.set(r.i, r.kind);
  for (const r of g.co) st.checkout.set(r.i, r.oc);
  return st;
}

function tallyUpTo(i) {
  const t = { imp: 0, eng: 0, cmt: 0, bull: 0, bear: 0, watch: 0, co: 0, signed: 0, declined: 0, sub: 0, amt: 0 };
  for (let k = 0; k <= i; k++) {
    const g = S.byDay[k];
    t.imp += g.imp.length;
    for (const r of g.dec) if ((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) > 0) t.eng++;
    t.cmt += g.cmt.length;
    for (const r of g.cmt) {
      if (r.stance === 'bullish') t.bull++;
      else if (r.stance === 'bearish') t.bear++;
      else if (r.stance === 'watching') t.watch++;
    }
    t.co += g.co.length;
    for (const r of g.co) {
      if (r.oc === 'confirm_signed') t.signed++;
      if (r.oc === 'confirm_declined') t.declined++;
    }
    for (const r of g.act) if (r.kind === 'subscribe') { t.sub++; t.amt += r.amt || 0; }
  }
  return t;
}

/* ---------- the field --------------------------------------------------- */

function drawField(progress) {
  const cv = document.getElementById('field');
  const g = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  const box = S.tileBox;
  g.clearRect(0, 0, W, H);
  g.fillStyle = css('--ground', '#0e1420');
  g.fillRect(0, 0, W, H);

  const line = css('--line', '#26314a');
  const dim = css('--ink-dim', '#8593ac');
  const ink = css('--ink', '#e6eaf2');

  // tiles + axes
  g.strokeStyle = line; g.lineWidth = 1;
  g.font = '11px system-ui, sans-serif'; g.textBaseline = 'middle';
  for (let r = 0; r < box.rowsN; r++) {
    for (let c = 0; c < box.cols; c++) {
      const x = box.padL + c * box.tw, y = box.padT + r * box.th;
      g.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5, Math.round(box.tw) - 1, Math.round(box.th) - 1);
      g.strokeStyle = line; g.globalAlpha = 0.45;
      for (let b = 1; b < RISK_ORDER.length; b++) {
        const by = y + 8 + b * ((box.th - 16) / RISK_ORDER.length);
        g.beginPath(); g.moveTo(x + 4, Math.round(by) + 0.5); g.lineTo(x + box.tw - 4, Math.round(by) + 0.5); g.stroke();
      }
      g.globalAlpha = 1;
    }
    g.fillStyle = dim; g.textAlign = 'right';
    g.fillText(ASSET_ZH[S.axes.asset[r]] || S.axes.asset[r], box.padL - 8, box.padT + r * box.th + box.th / 2);
    for (let c = 0; c < box.cols; c++) {
      const n = [...S.agents.values()].filter(
        (p) => p.age === S.axes.age[c] && p.asset === S.axes.asset[r]).length;
      g.fillStyle = dim; g.globalAlpha = 0.55; g.textAlign = 'left'; g.font = '10px var(--mono), monospace';
      g.fillText(`${n} 人`, box.padL + c * box.tw + 6, box.padT + r * box.th + 10);
      g.globalAlpha = 1; g.font = '11px system-ui, sans-serif';
    }
  }
  g.textAlign = 'center';
  for (let c = 0; c < box.cols; c++) {
    g.fillStyle = dim;
    g.fillText(AGE_ZH[S.axes.age[c]] || S.axes.age[c], box.padL + c * box.tw + box.tw / 2, H - 12);
  }
  g.save(); g.translate(14, box.padT + (H - box.padT - box.padB) / 2); g.rotate(-Math.PI / 2);
  g.fillStyle = dim; g.textAlign = 'center';
  g.fillText('每格自上而下：脆弱 / 一般 / 耐受', 0, 0);
  g.restore();

  const st = dayState(S.day);
  const focus = S.selPost ? new Set(st.seenBy.get(S.selPost) || []) : null;

  // institution nodes
  const published = new Set(S.byDay[S.day].post.map((r) => r.org));
  for (const [org, p] of S.orgPos) {
    const pulsing = published.has(org) && progress < 0.35;
    g.fillStyle = pulsing ? css('--arm-TV', '#d9a441') : css('--surface-2', '#1c2537');
    g.strokeStyle = line;
    const s = pulsing ? 15 : 12;
    g.beginPath(); g.rect(p.x - s / 2, p.y - s / 2, s, s); g.fill(); g.stroke();
    g.fillStyle = pulsing ? ink : dim; g.textAlign = 'left';
    g.fillText(org, p.x + 12, p.y);
  }

  // exposure lines: a wave sweeping the population
  const imps = S.selPost ? st.impOf.filter((r) => r.p === S.selPost) : st.impOf;
  const cut = Math.floor(imps.length * Math.min(1, Math.max(0, (progress - 0.1) / 0.55)));
  g.lineWidth = 1;
  for (let k = Math.max(0, cut - 260); k < cut; k++) {
    const r = imps[k];
    const to = S.layout.get(r.i), from = S.orgPos.get(st.postOrg.get(r.p));
    if (!to || !from) continue;
    g.globalAlpha = 0.06 + 0.3 * (1 - (cut - k) / 260);
    g.strokeStyle = S.armColor[r.arm] || dim;
    g.beginPath(); g.moveTo(from.x, from.y); g.lineTo(to.x, to.y); g.stroke();
  }
  g.globalAlpha = 1;

  // agents
  for (const [id, p] of S.layout) {
    const a = S.agents.get(id);
    const col = S.armColor[a.arm] || dim;
    const dimmed = focus && !focus.has(id);
    g.globalAlpha = dimmed ? 0.16 : 1;

    g.beginPath(); g.arc(p.x, p.y, 4.0, 0, Math.PI * 2);
    g.fillStyle = col; g.fill();

    if (progress > 0.55 && st.engaged.has(id)) {
      g.beginPath(); g.arc(p.x, p.y, 6.6, 0, Math.PI * 2);
      g.strokeStyle = col; g.lineWidth = 1.2; g.stroke();
    }
    if (progress > 0.6 && st.commented.has(id)) {
      g.fillStyle = css(STANCE_COLOR[st.commented.get(id)] || '--ink-dim', dim);
      g.fillRect(p.x - 1.2, p.y - 9.5, 2.4, 4);
    }
    if (progress > 0.75 && st.checkout.has(id)) {
      const oc = st.checkout.get(id);
      const c = oc === 'confirm_signed' ? css('--signal', '#e8823c')
        : (oc === 'confirm_declined' || oc === 'purchase_blocked') ? css('--stop', '#c9536b')
          : css('--ok', '#4fa981');
      g.beginPath(); g.arc(p.x, p.y, 9.2, 0, Math.PI * 2);
      g.strokeStyle = c; g.lineWidth = 1.8; g.stroke();
      if (oc === 'confirm_declined') {
        g.beginPath(); g.moveTo(p.x - 4, p.y - 4); g.lineTo(p.x + 4, p.y + 4);
        g.moveTo(p.x + 4, p.y - 4); g.lineTo(p.x - 4, p.y + 4); g.stroke();
      }
    }
    if (progress > 0.85 && st.traded.has(id)) {
      g.beginPath(); g.arc(p.x, p.y, 4.0, 0, Math.PI * 2);
      g.fillStyle = css('--ok', '#4fa981'); g.fill();
    }
    if (S.selAgent === id) {
      g.beginPath(); g.arc(p.x, p.y, 10, 0, Math.PI * 2);
      g.strokeStyle = ink; g.lineWidth = 1.4; g.stroke();
    }
    g.globalAlpha = 1;
  }
}

/* ---------- heatmap ---------------------------------------------------- */

function drawHeat() {
  const cv = document.getElementById('heat');
  const g = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  g.fillStyle = css('--surface', '#161e2d'); g.fillRect(0, 0, W, H);

  const padL = 8, padT = 8, padB = 20;
  const nR = S.heatRows.length, nC = S.days.length;
  if (!nR || !nC) return;
  const cw = (W - padL * 2) / nC, ch = (H - padT - padB) / nR;

  const idx = new Map(S.heatRows.map((id, i) => [id, i]));
  const dim = css('--ink-dim', '#8593ac');

  S.byDay.forEach((gday, c) => {
    const eng = new Map();
    for (const r of gday.dec) eng.set(r.i, (r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) + (r.n_comment || 0));
    for (const [id, v] of eng) {
      const r = idx.get(id); if (r === undefined) continue;
      const a = Math.min(1, v / 4);
      g.fillStyle = S.armColor[(S.agents.get(id) || {}).arm] || dim;
      g.globalAlpha = 0.12 + 0.78 * a;
      g.fillRect(padL + c * cw, padT + r * ch, Math.max(1, cw - 1), Math.max(1, ch - 0.5));
    }
    g.globalAlpha = 1;
    for (const r of gday.co) {
      const ri = idx.get(r.i); if (ri === undefined) continue;
      g.fillStyle = r.oc === 'confirm_signed' ? css('--signal', '#e8823c')
        : r.oc === 'confirm_declined' ? css('--stop', '#c9536b') : css('--ok', '#4fa981');
      g.fillRect(padL + c * cw + cw / 2 - 2, padT + ri * ch, 4, Math.max(1.5, ch));
    }
    g.fillStyle = dim; g.font = '10px system-ui, sans-serif'; g.textAlign = 'center';
    g.fillText(`D${gday.t}`, padL + c * cw + cw / 2, H - 7);
    if (c === S.day) {
      g.strokeStyle = css('--ink', '#e6eaf2'); g.lineWidth = 1;
      g.strokeRect(padL + c * cw + 0.5, padT + 0.5, cw - 1, H - padT - padB - 1);
    }
  });
}

/* ---------- panels ------------------------------------------------------ */

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function shownToday(i) {
  /* What the agents actually read today. A creative published on day t is not
     necessarily shown on day t: the ranker draws from a candidate pool that
     spans the last few days, so "published today" and "shown today" differ. */
  const st = dayState(i);
  return [...st.seenBy.keys()]
    .map((pid) => ({ post: S.postById.get(pid), reach: st.seenBy.get(pid).length }))
    .filter((x) => x.post)
    .sort((a, b) => (b.post.t - a.post.t) || (b.reach - a.reach));
}

function renderFeed() {
  const list = shownToday(S.day);
  const today = S.byDay[S.day].t;
  document.getElementById('feed-count').textContent = `（展示 ${list.length} 条）`;
  document.getElementById('feed').innerHTML = list.map(({ post: p, reach }) => {
    const age = today - p.t;
    const when = age === 0 ? '当天发布' : `${age} 天前发布`;
    return `<div class="post" data-p="${esc(p.p)}" ${S.selPost === p.p ? 'aria-current="true"' : ''}>
      <div class="org">${esc(p.org)}</div>
      <div class="intent i-${esc(p.intent)}">${esc(INTENT_ZH[p.intent] || p.intent)}${p.fund ? ` · 关联 ${esc(p.fund)}` : ''}</div>
      <div class="meta">#${esc(p.p)} · 触达 ${reach} 人 · ${when}${p.img ? ' · 有配图' : ''}</div>
    </div>`;
  }).join('') || '<div class="body muted">今天没有帖子被展示。</div>';

  document.querySelectorAll('.post').forEach((el) => {
    el.onclick = () => { S.selPost = S.selPost === el.dataset.p ? null : el.dataset.p; renderAll(); };
  });
}

function renderInspector() {
  const box = document.getElementById('inspector');
  const title = document.getElementById('insp-title');
  const g = S.byDay[S.day];

  if (!S.selAgent) {
    title.textContent = '图例与统计';
    const armRows = S.arms.map((a) => {
      const n = [...S.agents.values()].filter((p) => p.arm === a).length;
      const label = { T: '纯文本', TC: '图片文字化', TV: '真实图片' }[a] || a;
      return `<tr><td><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${S.armColor[a]}"></span> ${esc(a)}</td>
              <td>${esc(label)}</td><td class="num">${n}</td></tr>`;
    }).join('');
    box.innerHTML = `
      <table><thead><tr><th>臂</th><th>含义</th><th class="num">人数</th></tr></thead><tbody>${armRows}</tbody></table>
      <p class="muted" style="margin-top:10px">
        实心点=投资者，外环=当日有互动，上方短竖=当日评论（绿看多／红看空／灰观望），
        大圈=触发适当性结账（橙=签署确认书，红叉=拒签，绿=风险匹配）。
      </p>
      <p class="muted">点场上任意一个点，看这位投资者当天做了什么。</p>`;
    return;
  }

  const a = S.agents.get(S.selAgent);
  const dec = g.dec.find((r) => r.i === S.selAgent);
  const cmts = g.cmt.filter((r) => r.i === S.selAgent);
  const acts = g.act.filter((r) => r.i === S.selAgent);
  const cos = g.co.filter((r) => r.i === S.selAgent);
  const seen = g.imp.filter((r) => r.i === S.selAgent);

  title.textContent = a.id;
  box.innerHTML = `
    <dl class="kv">
      <dt>人口格</dt><dd>${esc(AGE_ZH[a.age] || a.age)} · ${esc(ASSET_ZH[a.asset] || a.asset)} · ${esc(RISK_ZH[a.risk] || a.risk)}</dd>
      <dt>风险测评</dt><dd>${esc(a.c)}</dd>
      <dt>模态臂</dt><dd style="color:${S.armColor[a.arm]}">${esc(a.arm)}</dd>
      <dt>当日曝光</dt><dd>${seen.length} 条</dd>
    </dl>
    ${dec ? `<dl class="kv" style="margin-top:8px">
      <dt>心情</dt><dd>${esc(dec.mood ?? '—')}</dd>
      <dt>读/赞/藏/关</dt><dd>${dec.n_read ?? '—'} / ${dec.n_like ?? '—'} / ${dec.n_save ?? '—'} / ${dec.n_follow ?? '—'}</dd>
      <dt>好感增量</dt><dd>${dec.aff_sum ?? '—'}</dd>
    </dl>${dec.reason ? `<div class="quote">${esc(dec.reason)}</div>` : ''}`
      : '<p class="muted">今天没有决策记录（未激活或解析失败）。</p>'}
    ${cmts.length ? `<p class="muted" style="margin:8px 0 2px">评论</p>` + cmts.map((c) =>
      `<div class="quote">[${esc(STANCE_ZH[c.stance] || c.stance)}] ${esc(c.text)}</div>`).join('') : ''}
    ${cos.length ? `<p class="muted" style="margin:8px 0 2px">结账</p>` + cos.map((c) =>
      `<div class="quote">${esc(c.fund)} ${esc(c.act)} → ${esc(OC_ZH[c.oc] || c.oc)}</div>`).join('') : ''}
    ${acts.length ? `<p class="muted" style="margin:8px 0 2px">成交</p>` + acts.map((r) =>
      `<div class="quote">${esc(r.kind)} ${esc(r.fund)} ${Math.round(r.amt || 0).toLocaleString('zh-CN')} 元</div>`).join('') : ''}
    <p class="muted" style="margin-top:10px"><a href="#" id="clearsel">← 返回图例</a></p>`;
  const c = document.getElementById('clearsel');
  if (c) c.onclick = (e) => { e.preventDefault(); S.selAgent = null; renderAll(); };
}

function renderTallies() {
  const t = tallyUpTo(S.day);
  const item = (k, v) => `<div class="tally"><b>${v}</b>${k}</div>`;
  document.getElementById('tallies').innerHTML = [
    item('曝光', t.imp), item('互动人次', t.eng), item('评论', t.cmt),
    item('看多/看空/观望', `${t.bull}/${t.bear}/${t.watch}`),
    item('结账', t.co), item('签确认书', t.signed), item('拒签', t.declined),
    item('申购笔数', t.sub), item('申购金额', Math.round(t.amt).toLocaleString('zh-CN')),
  ].join('');
}

function renderModality() {
  const g = S.byDay[S.day];
  const st = dayState(S.day);
  const wrap = document.getElementById('modality');
  const note = document.getElementById('mod-note');
  const shown = shownToday(S.day);
  const post = S.selPost || (shown[0] && shown[0].post.p);
  if (!post) { wrap.innerHTML = ''; note.textContent = '今天没有帖子。'; return; }

  const org = st.postOrg.get(post) || (S.postById.get(post) || {}).org || '';
  note.innerHTML = `帖子 <code>${esc(post)}</code> · ${esc(org)}。`
    + `事件日志不保存卡片正文，所以这里显示的是各臂的<strong>触达与互动</strong>；`
    + `逐字的三臂文本对照需要 <code>flowmirror export-bundle</code> 导出的展示包。`;

  wrap.innerHTML = S.arms.map((a) => {
    const seen = g.imp.filter((r) => r.p === post && r.arm === a);
    const ids = new Set(seen.map((r) => r.i));
    const eng = g.dec.filter((r) => ids.has(r.i) && (r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0) > 0).length;
    const label = { T: '纯文本（无图）', TC: '图片文字化（OCR + 冻结描述）', TV: '真实图片' }[a] || a;
    const rate = ids.size ? (100 * eng / ids.size).toFixed(0) : '—';
    return `<div class="armcol">
      <h3 style="color:${S.armColor[a]}">${esc(a)} · ${esc(label)}</h3>
      <div class="txt muted">${a === 'TV'
        ? '本臂的 agent 会收到真实图片本身。图片永不随仓库分发，因此这里不展示像素。'
        : a === 'TC' ? '本臂把图片转成文字：OCR 文本加一句冻结的中性描述，不含像素。'
          : '本臂只有文案，配图不展示。'}</div>
      <div class="stat">触达 ${ids.size} 人 · 互动 ${eng} 人 · 互动率 ${rate}%</div>
    </div>`;
  }).join('');
}

function renderCheckout() {
  const g = S.byDay[S.day];
  const box = document.getElementById('checkout');
  if (!g.co.length) { box.innerHTML = '<p class="muted">今天没有结账事件。</p>'; return; }
  box.innerHTML = `<table><thead><tr>
      <th>投资者</th><th>测评</th><th>基金</th><th>结果</th><th>反事实</th><th class="num">金额</th>
    </tr></thead><tbody>${g.co.map((r) => {
    const a = S.agents.get(r.i) || {};
    const col = r.oc === 'confirm_signed' ? 'var(--signal)' : r.oc === 'confirm_declined' ? 'var(--stop)' : 'var(--ok)';
    return `<tr><td><code>${esc(r.i)}</code></td><td>${esc(a.c || '—')}</td><td><code>${esc(r.fund)}</code></td>
      <td style="color:${col}">${esc(OC_ZH[r.oc] || r.oc)}</td>
      <td class="muted">${esc(OC_ZH[r.oc_cf] || r.oc_cf || '—')}</td>
      <td class="num">${Math.round(r.amt || 0).toLocaleString('zh-CN')}</td></tr>`;
  }).join('')}</tbody></table>`;
}

function renderInvariants() {
  const box = document.getElementById('invariants');
  const rep = S.invariants;
  if (!rep || !rep.checks) { box.innerHTML = '<p class="muted">本次运行没有不变量报告。</p>'; return; }
  const rows = Object.entries(rep.checks).map(([k, v]) => {
    const state = v.skipped ? ['跳过', 'var(--ink-dim)'] : v.pass ? ['通过', 'var(--ok)'] : ['失败', 'var(--stop)'];
    return `<tr><td><code>${esc(k)}</code></td>
      <td style="color:${state[1]}">${state[0]}</td>
      <td class="muted">${esc(v.description || '')}</td>
      <td class="muted">${esc(v.reason || '')}</td></tr>`;
  }).join('');
  const s = rep.summary || {};
  box.innerHTML = `<p class="muted">共 ${s.total ?? '—'} 条：通过 ${s.passed ?? '—'}，失败 ${s.failed ?? '—'}，跳过 ${s.skipped ?? '—'}。</p>
    <table><thead><tr><th>不变量</th><th>状态</th><th>断言</th><th>跳过原因</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderHeader() {
  const m = S.meta;
  document.getElementById('runtag').textContent =
    `${RUN} · ${S.days.length} 个交易日 · ${S.agents.size} 位投资者 · 臂 ${S.arms.join('/')}`;
  document.getElementById('chip-scale').textContent = 'demo 规模，非研究结果';
  if (m.synthetic_nav) document.getElementById('chip-nav').hidden = false;
  if ((m.cfg || {}).mock_llm) document.getElementById('chip-mock').hidden = false;
  const imgs = m.images || {};
  if (!imgs.root || !imgs.attached) document.getElementById('chip-img').hidden = false;
  const s = (S.invariants || {}).summary;
  const chip = document.getElementById('chip-inv');
  if (s) {
    chip.textContent = `不变量 ${s.passed}/${s.total} 通过${s.failed ? `，${s.failed} 失败` : ''}`;
    chip.className = 'chip ' + (s.failed ? 'bad' : 'good');
  }
}

/* ---------- transport --------------------------------------------------- */

function renderAll() {
  const g = S.byDay[S.day];
  document.getElementById('daylabel').textContent = `第 ${g.t + 1} 天 / 共 ${S.days.length} 天 · ${g.d || ''}`;
  document.getElementById('scrub').value = String(S.day);
  drawField(S.playing ? S.phase : 1);
  drawHeat();
  renderFeed(); renderInspector(); renderTallies();
  renderModality(); renderCheckout();
}

function step(delta) {
  S.day = Math.max(0, Math.min(S.byDay.length - 1, S.day + delta));
  S.phase = 1; renderAll();
}

function tick(ts) {
  if (!S.playing) return;
  if (!S.t0) S.t0 = ts;
  const dur = 3400 * S.speed;
  S.phase = Math.min(1, (ts - S.t0) / dur);
  drawField(S.phase);
  if (S.phase >= 1) {
    S.t0 = 0;
    if (S.day >= S.byDay.length - 1) { setPlaying(false); renderAll(); return; }
    S.day += 1; renderAll();
  }
  S.raf = requestAnimationFrame(tick);
}

function setPlaying(on) {
  S.playing = on; S.t0 = 0;
  document.getElementById('btn-play').textContent = on ? '⏸ 暂停' : '▶ 播放';
  if (on) S.raf = requestAnimationFrame(tick); else cancelAnimationFrame(S.raf);
}

function wire() {
  document.getElementById('btn-play').onclick = () => setPlaying(!S.playing);
  document.getElementById('btn-next').onclick = () => { setPlaying(false); step(1); };
  document.getElementById('btn-prev').onclick = () => { setPlaying(false); step(-1); };
  document.getElementById('btn-first').onclick = () => { setPlaying(false); S.day = 0; S.phase = 1; renderAll(); };
  document.getElementById('btn-last').onclick = () => { setPlaying(false); S.day = S.byDay.length - 1; S.phase = 1; renderAll(); };
  document.getElementById('scrub').oninput = (e) => { setPlaying(false); S.day = +e.target.value; S.phase = 1; renderAll(); };
  document.getElementById('speed').onchange = (e) => { S.speed = +e.target.value; };

  document.getElementById('field').onclick = (e) => {
    const cv = e.currentTarget, r = cv.getBoundingClientRect();
    const x = (e.clientX - r.left) * cv.width / r.width, y = (e.clientY - r.top) * cv.height / r.height;
    let best = null, bd = 121;
    for (const [id, p] of S.layout) {
      const d = (p.x - x) ** 2 + (p.y - y) ** 2;
      if (d < bd) { bd = d; best = id; }
    }
    S.selAgent = best && best === S.selAgent ? null : best;
    renderAll();
  };

  addEventListener('keydown', (e) => {
    if (e.code === 'Space') { e.preventDefault(); setPlaying(!S.playing); }
    if (e.code === 'ArrowRight') { setPlaying(false); step(1); }
    if (e.code === 'ArrowLeft') { setPlaying(false); step(-1); }
  });
}

/* ---------- boot -------------------------------------------------------- */

load().then(() => {
  document.getElementById('scrub').max = String(S.byDay.length - 1);
  document.getElementById('heat-note').textContent =
    `${S.heatRows.length} 位投资者 × ${S.days.length} 天。颜色深浅是当日互动强度，竖条是结账事件。`;
  ['stage', 'lower', 'heatwrap', 'invwrap'].forEach((id) => { document.getElementById(id).hidden = false; });
  renderHeader(); wire(); renderInvariants(); renderAll();
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) setPlaying(true);
}).catch((err) => {
  document.getElementById('fatal').hidden = false;
  document.getElementById('fatal-msg').textContent = String(err && err.message || err);
});

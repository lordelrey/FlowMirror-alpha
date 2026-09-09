/* FlowMirror viewer — the data layer.
   Page modules import from here and must
   not fetch anything themselves, so that "which files does the viewer read" has exactly
   one answer.

   Two loaders, one model. A finished run exports a presentation bundle; a run still in
   flight (or one made before the exporter existed) has only its raw artifacts. Both
   produce the same shape, so no page module needs to know which it got — it only reads
   `model.source` to say so honestly on screen. */

// Fallbacks must be keyed by arm name, not position: without CSS vars a 2-arm
// T/TV run would otherwise take index 1 (TC's purple), breaking "arm colors
// belong to their arm". Unknown arm names still fall back positionally below.
const ARM_FALLBACK_BY_NAME = { T: '#5c8bb0', TC: '#8e7bc4', TV: '#d9a441' };
const ARM_FALLBACK = ['#5c8bb0', '#8e7bc4', '#d9a441', '#4fa981', '#c9536b'];
const EV_KINDS = ['post', 'imp', 'dec', 'click', 'co', 'act', 'cmt', 'clim', 'st', 'refl'];

const cssVar = (name, fallback) => {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  } catch { return fallback; }
};

async function getText(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.text();
}
const getJSON = async (p) => JSON.parse(await getText(p));
const maybeJSON = async (p) => { try { return await getJSON(p); } catch { return null; } };

/* ---------- shared derivation ------------------------------------------ */

function groupByDay(rows) {
  const days = [...new Set(rows.map((r) => r.t).filter((t) => t != null))]
    .sort((a, b) => a - b);
  const index = new Map(days.map((t, i) => [t, i]));
  const byDay = days.map((t) => {
    const g = { t, d: '' };
    for (const k of EV_KINDS) g[k] = [];
    return g;
  });
  for (const r of rows) {
    const i = index.get(r.t);
    if (i == null) continue;
    const g = byDay[i];
    if (g[r.ev]) g[r.ev].push(r);
    if (!g.d && r.d) g.d = r.d;
  }
  return { days, byDay, index };
}

function armColours(arms) {
  const out = {};
  arms.forEach((a, i) => {
    out[a] = cssVar(`--arm-${a}`, '') || ARM_FALLBACK_BY_NAME[a] || ARM_FALLBACK[i % ARM_FALLBACK.length];
  });
  return out;
}

function splitCell(cell) {
  const [age, asset, risk] = String(cell || '||').split('|');
  return { age: age || '?', asset: asset || '?', risk: risk || '?' };
}

/* ---------- bundle path ------------------------------------------------ */

async function loadBundle(dir) {
  const base = `${dir}/bundle`;
  const [meta, posts, agents, events] = await Promise.all([
    getJSON(`${base}/bundle.json`),
    getJSON(`${base}/posts.json`),
    getJSON(`${base}/agents.json`),
    getJSON(`${base}/events.json`),
  ]);
  const rows = Array.isArray(events) ? events : (events.rows || []);
  const { days, byDay } = groupByDay(rows);

  const agentMap = new Map();
  for (const a of (agents.agents || [])) {
    agentMap.set(String(a.id), {
      id: String(a.id), arm: a.arm, cell: a.cell || '',
      ...splitCell(a.cell),
      riskClass: a.risk_class || '—',
      cash0: a.cash0 ?? null, hold0: a.hold0 ?? null, wealth0: a.wealth0 ?? null,
      persona: a.persona || '',
      byDay: a.by_day || {}, familiarity: a.familiarity || {},
    });
  }

  const postById = new Map();
  for (const p of (posts.posts || [])) postById.set(String(p.post_id), p);

  const arms = (posts.arms && posts.arms.length ? posts.arms
    : (meta.modality && meta.modality.arms) || [...new Set([...agentMap.values()].map((a) => a.arm))])
    .filter(Boolean).slice().sort();

  const dateOf = new Map();
  for (const g of byDay) if (g.d) dateOf.set(g.t, g.d);
  for (const [t, d] of Object.entries(agents.days || {})) dateOf.set(Number(t), d);

  const warnings = [];
  if (posts.creative_source === 'unavailable') {
    warnings.push(`帖子文案不可用：${posts.creative_source_reason || '内容池未能载入'}`);
  }
  if (agents.note) warnings.push(String(agents.note));
  // The redistribution notice travels inside the bundle itself; surface it here or readers who never see the README miss the boundary.
  if (meta._bundle && meta._bundle.redistribution) {
    warnings.push(String(meta._bundle.redistribution));
  }

  return {
    source: 'bundle', meta, rows, days, byDay, dateOf,
    agents: agentMap, postById, arms, armColor: armColours(arms),
    orgs: [...new Set((posts.posts || []).map((p) => p.org).filter(Boolean))].sort(),
    invariants: meta.invariants || null,
    truncation: meta.truncation || (events.dropped ? { applied: !!Object.keys(events.dropped).length, rule: events._what || '', dropped: events.dropped } : null),
    fidelity: (meta._bundle && meta._bundle.fidelity) || [],
    warnings,
  };
}

/* ---------- raw path --------------------------------------------------- */

async function loadRaw(dir, base) {
  const [logText, meta] = await Promise.all([
    getText(`${dir}/event_log.jsonl`),
    getJSON(`${dir}/run_meta.json`),
  ]);
  const rows = logText.split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const { days, byDay } = groupByDay(rows);
  const invariants = await maybeJSON(`${dir}/invariants_report.json`);

  // The cohort file carries each agent's population cell and reported class; without it
  // the field degrades to one unlabelled block rather than failing.
  const warnings = [];
  let cohort = [];
  const cohortDoc = await maybeJSON(`${base}/data/population/agents_seed2027.json`);
  if (cohortDoc) {
    cohort = Object.values(cohortDoc)
      .find((v) => Array.isArray(v) && v.length && typeof v[0] === 'object') || [];
  }
  if (!cohort.length) {
    warnings.push('未能载入人口队列文件，人口场退化为单一分区');
  }
  warnings.push('本次运行未导出展示包（bundle）：逐臂文案、开局持仓与逐日时间线不可用。跑 flowmirror export-bundle 补齐。');

  const byId = new Map(cohort.map((r) => [String(r.id), r]));
  const armOf = meta.arms || {};
  const agentMap = new Map();
  for (const id of Object.keys(armOf).sort()) {
    const p = byId.get(id) || {};
    agentMap.set(id, {
      id, arm: armOf[id], cell: String(p.cell || '||'),
      ...splitCell(p.cell),
      riskClass: p.reported_C || '—',
      cash0: null, hold0: null, wealth0: p.wealth_wan != null ? p.wealth_wan * 1e4 : null,
      persona: p.persona_card_zh_rich || '',
      byDay: {}, familiarity: {},
    });
  }

  const postById = new Map();
  for (const r of rows) {
    if (r.ev !== 'post') continue;
    postById.set(String(r.p), {
      post_id: String(r.p), org: r.org, intent: r.intent, ig: r.ig, fund: r.fund,
      published_t: r.t, published_d: r.d, days_shown: [], note_id: null,
      image: null, arms: {},
    });
  }
  // reach and engagement per arm are derivable from the log even without a bundle
  const engagedDay = new Set();
  for (const r of rows) {
    if (r.ev === 'dec' && ((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0)) > 0) {
      engagedDay.add(`${r.t}|${r.i}`);
    }
  }
  for (const r of rows) {
    if (r.ev !== 'imp') continue;
    const p = postById.get(String(r.p));
    if (!p) continue;
    if (!p.days_shown.includes(r.t)) p.days_shown.push(r.t);
    const slot = p.arms[r.arm] || (p.arms[r.arm] = {
      impressions: 0, reach: 0, engaged_agents: 0, engagement_rate: null,
      clicks: 0, comments: 0, checkouts: 0, text: null, chars: null, truncated: false,
      _reach: new Set(), _eng: new Set(),
    });
    slot.impressions += 1;
    slot._reach.add(r.i);
    if (engagedDay.has(`${r.t}|${r.i}`)) slot._eng.add(r.i);
  }
  for (const r of rows) {
    const p = postById.get(String(r.p));
    if (!p) continue;
    const arm = (agentMap.get(String(r.i)) || {}).arm;
    const slot = arm && p.arms[arm];
    if (!slot) continue;
    if (r.ev === 'click') slot.clicks += 1;
    else if (r.ev === 'cmt') slot.comments += 1;
    else if (r.ev === 'co') slot.checkouts += 1;
  }
  for (const p of postById.values()) {
    p.days_shown.sort((a, b) => a - b);
    for (const slot of Object.values(p.arms)) {
      slot.reach = slot._reach.size;
      slot.engaged_agents = slot._eng.size;
      slot.engagement_rate = slot.reach ? Number((slot.engaged_agents / slot.reach).toFixed(4)) : null;
      delete slot._reach; delete slot._eng;
    }
  }

  const arms = ((meta.modality && meta.modality.arms)
    || [...new Set(Object.values(armOf))]).filter(Boolean).slice().sort();
  const dateOf = new Map();
  for (const g of byDay) if (g.d) dateOf.set(g.t, g.d);

  return {
    source: 'raw', meta, rows, days, byDay, dateOf,
    agents: agentMap, postById, arms, armColor: armColours(arms),
    orgs: [...new Set(rows.filter((r) => r.ev === 'post').map((r) => r.org).filter(Boolean))].sort(),
    invariants: invariants ? {
      summary: invariants.summary || null,
      entries: Object.entries(invariants.checks || {}).map(([name, v]) => ({ name, ...v })),
    } : null,
    truncation: null, fidelity: [], warnings,
  };
}

/* ---------- the public loader ------------------------------------------ */

export async function loadRun(base, tag, opts = {}) {
  // Samples shipped with the repo live under web/samples/, not runs/out/, and
  // on GitHub Pages runs/out/ does not exist at all; let callers that already
  // know where the run lives pass opts.dir (resolved against base).
  const dir = opts.dir ? `${base}/${String(opts.dir).replace(/^\/+/, '')}` : `${base}/runs/out/${tag}`;
  let model;
  if (opts.prefer === 'raw') {
    model = await loadRaw(dir, base);
  } else {
    try {
      model = await loadBundle(dir);
    } catch (bundleErr) {
      try {
        model = await loadRaw(dir, base);
        model.warnings.unshift(`展示包读取失败，已回退到原始产物：${bundleErr.message}`);
      } catch (rawErr) {
        // The tag may name a sample shipped with the repo rather than a local
        // run: on the static site there is no runs/out/, so look the tag up in
        // web/samples/index.json and retry once inside web/<path>. The retry
        // recurses with opts.dir set, which also makes re-entry impossible.
        if (!opts.dir) {
          const sidx = await maybeJSON(`${base}/web/samples/index.json`);
          const entry = ((sidx && (sidx.runs || sidx)) || [])
            .find((x) => x && typeof x === 'object' && x.tag === tag && x.path);
          if (entry) {
            try {
              const model = await loadRun(base, tag, { ...opts, dir: `web/${entry.path}` });
              model.warnings = model.warnings || [];
              model.warnings.push('这是随仓库分发的演示样本，并非本机 runs/out/ 下的运行结果。');
              return model;
            } catch {
              /* sample also unreadable: fall through to the original error */
            }
          }
        }
        const e = new Error(`${tag}: 展示包与原始产物都读不到。bundle: ${bundleErr.message}；raw: ${rawErr.message}`);
        e.tag = tag;
        throw e;
      }
    }
  }

  const meta = model.meta || {};
  const cfg = meta.cfg || {};
  model.tag = tag;
  model.status = meta.status || 'unknown';
  model.images = meta.images || null;
  model.agentPolicy = cfg.agent_policy || meta.agent_policy || 'llm';
  model.syntheticNav = !!(meta.synthetic_nav ?? meta.nav_synthetic);
  model.mock = !!(cfg.mock_llm ?? meta.mock_llm);
  if (model.images && model.images.root && model.images.attached === 0) {
    model.warnings.push('配了图片库但本次运行零附图：检查 images_root 路径');
  }
  if (model.truncation && model.truncation.applied) {
    model.warnings.push(`事件日志已截断：${model.truncation.rule || ''}`);
  }
  return model;
}

/* ---------- derived views ---------------------------------------------- */

export function dayIndexOf(model, t) {
  return model.days.findIndex((x) => x === t);
}

/** Per-day derived state. Semantics match the pre-refactor viewer exactly, so a
 *  reader comparing the two sees the same highlighting and the same counts. */
export function stateAt(model, i) {
  const g = model.byDay[i];
  if (!g) return null;
  const st = {
    t: g.t, d: g.d,
    seenBy: new Map(), armOf: new Map(), engaged: new Set(), commented: new Map(),
    traded: new Map(), checkoutOf: new Map(), checkouts: g.co.slice(), acts: g.act.slice(),
    postOrg: new Map(), imps: g.imp.slice(), climate: new Map(), shown: [],
  };
  for (const r of g.post) st.postOrg.set(String(r.p), r.org);
  for (const r of g.clim) st.climate.set(String(r.p), r.label || r.lab || r.climate || null);
  const shownIds = new Set();
  for (const r of g.imp) {
    const pid = String(r.p), aid = String(r.i);
    shownIds.add(pid);
    if (!st.seenBy.has(aid)) st.seenBy.set(aid, new Set());
    st.seenBy.get(aid).add(pid);
    if (r.arm) st.armOf.set(aid, r.arm);
    // a post can be shown days after publication, so fall back to the index
    if (!st.postOrg.has(pid)) {
      const src = model.postById.get(pid);
      if (src) st.postOrg.set(pid, src.org);
    }
  }
  for (const r of g.dec) {
    if (((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0)) > 0) st.engaged.add(String(r.i));
    if (r.arm && !st.armOf.has(String(r.i))) st.armOf.set(String(r.i), r.arm);
  }
  for (const r of g.cmt) st.commented.set(String(r.i), { stance: r.stance, text: r.text, p: String(r.p) });
  for (const r of g.act) st.traded.set(String(r.i), r.kind);
  for (const r of g.co) st.checkoutOf.set(String(r.i), r.oc);
  st.shown = [...shownIds].sort().map((pid) => model.postById.get(pid)).filter(Boolean);
  return st;
}

/** Cumulative counts over days 0..i. Field names match the pre-refactor viewer. */
export function tallyUpTo(model, i) {
  const t = {
    imp: 0, click: 0, eng: 0, cmt: 0, bull: 0, bear: 0, watch: 0,
    co: 0, signed: 0, declined: 0, blocked: 0, sub: 0, red: 0, amt: 0, fees: 0,
    reach: new Set(), byOc: {},
  };
  for (let k = 0; k <= i && k < model.byDay.length; k++) {
    const g = model.byDay[k];
    t.imp += g.imp.length;
    // Click bridges imp and checkout in the funnel; every byDay day carries a click array like imp/cmt.
    t.click += g.click.length;
    for (const r of g.imp) t.reach.add(String(r.i));
    for (const r of g.dec) {
      if (((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0)) > 0) t.eng++;
    }
    t.cmt += g.cmt.length;
    for (const r of g.cmt) {
      if (r.stance === 'bullish') t.bull++;
      else if (r.stance === 'bearish') t.bear++;
      else if (r.stance === 'watching') t.watch++;
    }
    t.co += g.co.length;
    for (const r of g.co) {
      t.byOc[r.oc] = (t.byOc[r.oc] || 0) + 1;
      if (r.oc === 'confirm_signed') t.signed++;
      else if (r.oc === 'confirm_declined') t.declined++;
      else if (r.oc === 'hard_block' || r.oc === 'purchase_blocked') t.blocked++;
    }
    for (const r of g.act) {
      if (r.kind === 'subscribe' || r.kind === 'dca') { t.sub++; t.amt += r.amt || 0; }
      else if (r.kind === 'redeem') t.red++;
      t.fees += r.fee || 0;
    }
  }
  t.reachN = t.reach.size;
  return t;
}

/** Runs the viewer can open: the local service when present, else the shipped index. */
export async function listRuns(base) {
  try {
    const r = await fetch('/api/runs', { cache: 'no-store' });
    if (r.ok) {
      const got = await r.json();
      return (got.runs || got || []).map((x) => ({ ...x, local: true }));
    }
  } catch { /* no local service: fall through to the shipped samples */ }
  const idx = await maybeJSON(`${base}/web/samples/index.json`)
    || await maybeJSON('samples/index.json');
  return ((idx && (idx.runs || idx)) || []).map((x) => (
    typeof x === 'string' ? { tag: x, local: false } : { ...x, local: false }));
}

/** True when the stdlib service is answering, which is what gates the run controls. */
export async function probeLocalApi() {
  try {
    const r = await fetch('/api/runs', { cache: 'no-store', method: 'GET' });
    return r.ok;
  } catch { return false; }
}

export const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const pct = (x, digits = 1) =>
  (x == null || Number.isNaN(x)) ? '—' : `${(x * 100).toFixed(digits)}%`;

export const num = (x, digits = 0) =>
  (x == null || Number.isNaN(x)) ? '—' : Number(x).toLocaleString('en-US',
    { minimumFractionDigits: digits, maximumFractionDigits: digits });

/* FlowMirror viewer — the audit page and the data page.

   Two exports live in one file because they answer two halves of the same question a
   sceptical reader asks: "what is this run allowed to prove" (audit) and "what is this
   sandbox made of" (data). Neither page fetches: data.js owns every read.

   The rule this file exists to enforce: a missing field is stated on screen, never
   silently blank. Every "—" you see here is accompanied by a sentence saying which
   file did not carry it and what to run to get it. */

import { esc, num, pct, listRuns } from './data.js';

// esc() does not escape ';', so it alone cannot make a value safe inside a
// style= attribute. Whitelist colors the same way panels.js / modality.js /
// home.js already do, instead of relying on the value coming from our own data.
const SAFE_COLOR = /^[#a-zA-Z0-9(),.%\s/-]{1,64}$/;
const safeColor = (c, fallback = 'var(--ink-dim)') =>
  (typeof c === 'string' && SAFE_COLOR.test(c) && c.trim()) ? c.trim() : fallback;

/* ---------- formatting ------------------------------------------------- */

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function fmtVal(v) {
  if (v == null) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)));
  }
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch { return String(v); }
}

/** UTC, because a run directory's mtime in local time is a lie the moment it is shared. */
function fmtWhen(v) {
  if (v == null || v === '') return '—';
  const d = new Date(typeof v === 'number' ? (v > 1e12 ? v : v * 1000) : v);
  if (Number.isNaN(d.getTime())) return esc(String(v));
  return `${d.toISOString().slice(0, 16).replace('T', ' ')}Z`;
}

const cny = (x) => (x == null || Number.isNaN(Number(x)) ? '—' : `¥${num(x, 0)}`);

function tag(text, cls, title) {
  return `<span class="tag${cls ? ` ${cls}` : ''}"`
    + (title ? ` title="${esc(title)}"` : '')
    + `>${esc(text)}</span>`;
}

function panel(title, note, body, opts = {}) {
  return `<section class="panel"${opts.id ? ` id="${opts.id}"` : ''}>`
    + `<h2>${esc(title)}${note ? ` <span class="note">${esc(note)}</span>` : ''}</h2>`
    + `<div class="${opts.bodyClass || 'body'}">${body}</div></section>`;
}

/** Values must arrive already escaped or already numeric — callers do that. */
function kv(pairs) {
  const rows = pairs.filter(Boolean)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v == null || v === '' ? '—' : v}</dd>`)
    .join('');
  return `<dl class="kv">${rows}</dl>`;
}

function note(text) { return `<p class="muted">${esc(text)}</p>`; }

function placeholder(lines) {
  return `<div class="placeholder">${lines.map((l) => `<div>${esc(l)}</div>`).join('')}</div>`;
}

/* =======================================================================
   审计页
   ======================================================================= */

/* The two entries the invariant runner emits as reports rather than gates. Both always
   carry pass:true while their `reason` holds the actual finding, so a reader who sees a
   green tick next to them would draw exactly the wrong conclusion. */
const REPORT_ONLY = new Set(['m_tv_arm_carries_images', 'm_env_valence_warning']);
const VERDICT_KEYS = new Set(['name', 'description', 'pass', 'skipped', 'reason']);

function verdictOf(entry) {
  if (entry.skipped) {
    return { cls: '', label: '跳过', kind: 'skipped' };
  }
  if (REPORT_ONLY.has(entry.name)) {
    return { cls: '', label: '报告项', kind: 'report' };
  }
  if (entry.pass === true) return { cls: 'ok', label: '通过', kind: 'pass' };
  if (entry.pass === false) return { cls: 'stop', label: '未过', kind: 'fail' };
  return { cls: '', label: '无结论', kind: 'unknown' };
}

function detailTags(entry) {
  const parts = [];
  for (const [k, v] of Object.entries(entry)) {
    if (VERDICT_KEYS.has(k)) continue;
    const full = fmtVal(v);
    parts.push(tag(`${k} = ${clip(full, 72)}`, '', `${k} = ${full}`));
  }
  if (!parts.length) return '<span class="faint">这一项没有附加实测字段。</span>';
  return `<div class="tl-tags">${parts.join('')}</div>`;
}

function invariantsSection(model) {
  const inv = model.invariants;
  if (!inv || !Array.isArray(inv.entries) || !inv.entries.length) {
    return panel('不变量', '机制自检', placeholder([
      '本次运行没有不变量报告。',
      model.source === 'bundle'
        ? '展示包的 bundle.json 里没有 invariants 段。'
        : '运行目录下没有 invariants_report.json —— 跑到一半的运行还没写出这个文件。',
      '没有这份报告，就没有任何机制被机器检查过：下面所有数字都只是记账结果。',
    ]), { id: 'invariants' });
  }

  const entries = inv.entries;
  const counts = { pass: 0, fail: 0, skipped: 0, report: 0, unknown: 0 };
  const rows = entries.map((e) => {
    const v = verdictOf(e);
    counts[v.kind] += 1;
    const head = `<td><code>${esc(e.name || '（未命名）')}</code></td>`
      + `<td>${tag(v.label, v.cls)}</td>`;
    const bits = [];
    if (e.description) bits.push(`<div class="muted">${esc(e.description)}</div>`);
    if (v.kind === 'report') {
      bits.push('<div class="said">' + esc('这一项永远是报告，不是闸门：绿灯不代表任何事情通过了，'
        + '要读的是下面这句实测。') + '</div>');
    }
    if (e.reason) bits.push(`<div class="said">${esc(e.reason)}</div>`);
    if (v.kind === 'skipped' && !e.reason) {
      bits.push('<div class="said">' + esc('运行器跳过了这一项，但没写原因。跳过既不是通过也不是未过。') + '</div>');
    }
    bits.push(detailTags(e));
    return `<tr>${head}<td>${bits.join('')}</td></tr>`;
  }).join('');

  const s = inv.summary || {};
  const summaryLine = s.total != null
    ? `报告自称：共 ${num(s.total)} 项，${num(s.passed)} 通过 · ${num(s.failed)} 未过 · ${num(s.skipped)} 跳过`
    : '这份报告没有汇总段（原始产物路径可能缺 summary）。';
  const own = `本页实点：${counts.pass} 通过 · ${counts.fail} 未过 · ${counts.skipped} 跳过 · `
    + `${counts.report} 报告项${counts.unknown ? ` · ${counts.unknown} 无结论` : ''}`;
  const caveat = counts.report
    ? `汇总里的「通过」把 ${counts.report} 个报告项也算了进去；那 ${counts.report} 项本来就不会失败。`
    : '';
  const mismatch = (s.passed != null && s.passed !== counts.pass + counts.report)
    ? `汇总与逐项对不上：汇总说 ${num(s.passed)} 通过，逐项数出 ${counts.pass + counts.report}。以逐项为准。`
    : '';

  const body = `${note(summaryLine)}<p class="muted">${esc(own)}</p>`
    + (caveat ? note(caveat) : '')
    + (mismatch ? `<p>${tag(mismatch, 'stop')}</p>` : '')
    + '<table><thead><tr><th>检查</th><th>结论</th><th>说明与实测</th></tr></thead>'
    + `<tbody>${rows}</tbody></table>`
    + note('三种结论，不是两种：通过、未过、跳过。跳过的项没有被检查过，'
      + '既不能当成通过，也不能当成失败。');

  return panel('不变量', '机制自检 · 通过 / 未过 / 跳过', body,
    { id: 'invariants', bodyClass: 'body scrollx' });
}

function fidelitySection(model) {
  const lines = Array.isArray(model.fidelity) ? model.fidelity : [];
  if (!lines.length) {
    const why = model.source === 'bundle'
      ? '展示包的 _bundle.fidelity 是空的：导出器没有声明任何差异。这本身值得怀疑——'
        + '照理说逐臂卡片的渲染总会与 agent 当时所见略有出入。'
      : '原始产物路径没有保真度声明：这份声明是导出器写的，只有展示包里有。'
        + '也就是说，本页无法告诉你界面显示的内容与 agent 当时真正看到的差多少。';
    return panel('保真度声明', '导出器自己承认的差异', placeholder([why,
      '要拿到这份声明：flowmirror export-bundle runs/out/<运行标签>']));
  }
  return panel('保真度声明', `导出器自己承认的差异 · ${lines.length} 条`,
    '<p class="said">' + esc('以下每一条都是导出器自己写下的：界面上的东西与 agent 当时真正看到的东西，'
      + '在这些地方不一样。读任何图表之前先读这里。') + '</p>'
    + `<ol>${lines.map((l) => `<li class="said">${esc(l)}</li>`).join('')}</ol>`);
}

function warningsSection(model) {
  const ws = Array.isArray(model.warnings) ? model.warnings : [];
  if (!ws.length) {
    return panel('载入提示', '数据层的诚实提示',
      note('载入这次运行时数据层没有产生任何提示。'));
  }
  return panel('载入提示', `数据层在读这次运行时记下的 ${ws.length} 条`,
    `<ul>${ws.map((w) => `<li class="said">${esc(w)}</li>`).join('')}</ul>`);
}

const POLICY_TEXT = {
  llm: 'LLM agent：每位投资者每个交易日过一次模型',
  null: '规则型空策略（rule-based）：全程没有任何 LLM 调用，所有"决定"都是掷骰子',
};

function whatRunSection(model) {
  const meta = model.meta || {};
  const cfg = meta.cfg || {};
  const policy = String(model.agentPolicy || '');
  const policyText = POLICY_TEXT[policy]
    || `未知策略标记 ${policy || '（空）'}：无法判断这次运行是否调用了模型`;

  const sourceText = model.source === 'bundle'
    ? '展示包（bundle）：逐臂文案、开局持仓与逐日 agent 记录都在'
    : '原始产物（raw）：只有事件日志与 run_meta。逐臂文案、开局持仓、'
      + '逐日 agent 记录一律不可用——这三样是导出器生成的，跑到一半的运行没有。';

  const flags = [];
  if (model.mock) {
    flags.push(tag('mock_llm = true', '', '模型被替换成了确定性假回复'));
  }
  if (model.syntheticNav) {
    flags.push(tag('synthetic_nav = true', '', '净值是合成的'));
  }
  if (policy === 'null') flags.push(tag('agent_policy = null'));
  const flagLine = flags.length
    ? `<div class="tl-tags">${flags.join('')}</div>`
    + note('带上面这些标记的运行是演示规模，不是研究数据：'
      + 'mock 表示模型换成了确定性假回复，合成净值表示收益曲线不来自任何真实市场。')
    : note('没有 mock / 合成净值 / 空策略标记：这次运行的模型调用与净值都是真的。');

  const body = kv([
    ['数据来源', esc(sourceText)],
    ['agent 策略', esc(policyText)],
    ['模型调用', model.mock ? esc('模拟（mock）：确定性假回复，零真实 API 调用')
      : esc('真实调用')],
    ['净值', model.syntheticNav ? esc('合成净值（synthetic_nav），仅供演示')
      : esc('来自配置的净值文件')],
    ['规模', esc(`${model.agents.size} 人 × ${model.days.length} 日 × ${model.arms.length} 臂`)],
    ['运行状态', esc(String(model.status || 'unknown'))],
    ['臂', model.arms.length
      ? model.arms.map((a) => `<span class="armbadge"><i style="background:${safeColor(model.armColor[a])}"></i>${esc(a)}</span>`).join(' ')
      : esc('这次运行没有识别出任何臂')],
    ['随机种子', cfg.seed == null ? null : `<code>${esc(String(cfg.seed))}</code>`],
    ['窗口', cfg.window
      ? esc(`${cfg.window.start || '?'} → ${cfg.window.end || '?'} · 最多 ${cfg.window.max_trading_days ?? '?'} 个交易日`)
      : null],
  ]) + flagLine;

  return panel('这次运行是什么', '来源 · 策略 · 演示标记', body);
}

function imagesSection(model) {
  const img = model.images;
  if (!img) {
    return panel('图片', 'TV 臂的像素从哪来', placeholder([
      'run_meta 里没有 images 段：本次运行没有记录任何图片附着情况。',
      '在这种情况下 TV 臂是否真的带图无法从产物里判断——不要假设它带图。',
    ]));
  }
  const rootConfigured = img.root != null && img.root !== '';
  let head;
  if (!rootConfigured) {
    head = placeholder([
      '未配置图片库（images_root 为空）。',
      'TV 臂退化成纯文本：这次运行的模态对照没有测到"图 vs 文"。',
    ]);
  } else if (!img.attached) {
    head = placeholder([
      `配了图片库但零附图（attached = ${num(img.attached || 0)}）。`,
      '检查 images_root 路径与内容池里的 image_id 是否对得上。',
    ]);
  } else {
    head = `<p class="said">${esc(`本次运行附了 ${num(img.attached)} 张图。`)}</p>`;
  }
  const body = head + kv([
    // Local absolute paths must not reach the UI: the exporter already elides
    // them in showcase bundles, and raw-artifact runs (which read run_meta.json
    // directly) should render the same way. Show only the final directory
    // segment plus a "configured" marker.
    ['images_root', rootConfigured
      ? `<code>${esc(String(img.root).replace(/.*[\\/]/, '') || 'images')}/（已配置）</code>`
      : esc('未配置')],
    ['挑图策略 policy', esc(String(img.policy ?? '—'))],
    ['已附图 attached', num(img.attached ?? 0)],
    ['缺文件 missing', num(img.missing ?? 0)],
    ['摘要不符 sha_mismatch', num(img.sha_mismatch ?? 0)],
  ]) + note('像素永不进仓库，本页也永不内联图片；这里只报数。'
    + '真图只在本地小服务配了 images_root 时才可见。');

  return panel('图片', 'TV 臂的像素从哪来', body);
}

function truncationSection(model) {
  const tr = model.truncation;
  if (!tr) {
    return panel('事件日志截断', '展示包的字节预算', placeholder([
      '没有截断记录。',
      model.source === 'bundle'
        ? '展示包没有写 truncation 段，无法确认事件流是否完整。'
        : '原始产物就是全量事件日志，本来不经过截断；truncation 是导出器的概念。',
    ]));
  }
  const dropped = tr.dropped || {};
  const body = (tr.applied
    ? placeholder([`事件日志已截断：${tr.rule || '（未记录规则）'}`,
      '凡是按事件数算出来的数字都受此影响。'])
    : `<p class="said">${esc('未触发截断：事件流完整。')}</p>`)
    + kv([
      ['applied', esc(tr.applied ? 'true' : 'false')],
      ['规则 rule', tr.rule ? `<code>${esc(String(tr.rule))}</code>` : null],
      ...Object.entries(dropped).map(([k, v]) => [k, esc(fmtVal(v))]),
    ])
    + (dropped.personas_capped
      ? note(`另外有 ${num(dropped.personas_capped)} 份人物卡被截短——`
        + '检视页显示的人物卡不是 agent 当时读到的全文。')
      : '');
  return panel('事件日志截断', '展示包的字节预算', body);
}

function countersSection(model) {
  const c = (model.meta || {}).counters;
  if (!c) {
    return panel('决策与调用', '失败拆分', placeholder([
      'run_meta 里没有 counters 段：调用次数与决策失败数无从核对。',
    ]));
  }
  const df = c.decision_failures;
  const dfm = c.decision_failures_model;
  const dft = c.decision_failures_transport;
  const parts = (dfm || 0) + (dft || 0);
  const known = df != null && dfm != null && dft != null;
  const identity = !known
    ? tag('拆分字段不全，无法核对恒等式', '')
    : (parts === df
      ? tag(`恒等式成立：${num(dfm)} 模型 + ${num(dft)} 传输 = ${num(df)} 总失败`, 'ok')
      : tag(`恒等式不成立：${num(dfm)} + ${num(dft)} = ${num(parts)}，与总失败 ${num(df)} 不符`, 'stop'));

  const body = `<p>${identity}</p>`
    + '<p class="said">' + esc('两类失败不是一回事：传输失败（transport）是供应商侧的问题——超时、限流、'
      + '连接断开——不是模型答不出来；模型失败（model）才是模型给不出可解析的决策。'
      + '用后者判断模型能力，用前者判断当天的网络。') + '</p>'
    + kv([
      ['决策 decisions', num(c.decisions)],
      ['调用 calls', num(c.calls)],
      ['尝试 attempts', num(c.attempts)],
      ['缓存命中 cache_hits', num(c.cache_hits ?? 0)],
      ['决策失败 总数', num(df ?? 0)],
      ['其中 模型 model', num(dfm ?? 0)],
      ['其中 传输 transport', num(dft ?? 0)],
      ['失败率', c.decision_failure_rate == null ? null : pct(c.decision_failure_rate, 3)],
      ['因子切换 factor_switches', c.factor_switches == null ? null : num(c.factor_switches)],
      ['活跃人日 active_days', c.active_days == null ? null : num(c.active_days)],
      ['申购笔数 sub_n', c.sub_n == null ? null : num(c.sub_n)],
      ['申购额 sub_cny', c.sub_cny == null ? null : cny(c.sub_cny)],
      ['费用 fees_cny', c.fees_cny == null ? null : cny(c.fees_cny)],
      ['附图 images_attached', c.images_attached == null ? null : num(c.images_attached)],
    ])
    + (model.mock
      ? note('这次运行是 mock：上面的调用数是走了假回复的次数，不是真实 API 用量。')
      : '');

  return panel('决策与调用', '失败拆分：传输 vs 模型', body);
}

function metaSection(model) {
  const meta = model.meta || {};
  const cfg = meta.cfg || {};
  const llm = cfg.llm || {};
  const uni = meta.universe || {};
  const inv = meta.investors || {};
  const fees = meta.fees || cfg.fees || {};
  const oc = meta.checkout_oc || null;
  const ocLine = oc
    ? Object.entries(oc).map(([k, v]) => {
      const cls = k === 'confirm_signed' ? 'sig' : (k === 'confirm_declined' ? 'stop' : '');
      return tag(`${k} ${num(v)}`, cls);
    }).join('')
    : '';

  const body = kv([
    ['运行标签', `<code>${esc(String(model.tag || cfg.run_tag || '—'))}</code>`],
    ['引擎 engine', esc(String(meta.engine ?? '—'))],
    ['状态 status', esc(String(meta.status ?? model.status ?? '—'))],
    ['耗时 elapsed_s', meta.elapsed_s == null ? null : `${num(meta.elapsed_s, 1)} 秒`],
    ['基金池 universe', uni.size == null ? null
      : esc(`${num(uni.size)} 只（其中 deferred ${num((uni.deferred || []).length || uni.deferred || 0)}）`)],
    ['投资者 investors', inv.total == null ? null
      : esc(`共 ${num(inv.total)} 人 · 全程有过动作 ${num(inv.active_ever ?? 0)} 人`
        + ` · 开局盈亏缺失 ${num(inv.initial_pnl_misses ?? 0)}`)],
    ['费率 fees', fees.subscribe_rate == null ? null
      : esc(`申购 ${(fees.subscribe_rate * 100).toFixed(2)}% · 赎回 ${((fees.redeem_rate ?? 0) * 100).toFixed(2)}%`
        + (fees.total == null ? '' : ` · 本次共收 ¥${num(fees.total, 2)}`))],
    ['模态 modality', cfg.modality_level
      ? esc(`分臂层级 ${cfg.modality_level} · 配置臂 ${(cfg.modality_arms || []).join(' / ') || '—'}`)
      : null],
    ['机构 orgs', (cfg.orgs && cfg.orgs.length)
      ? esc(cfg.orgs.join(' · '))
      : (model.orgs.length ? esc(model.orgs.join(' · ')) : null)],
    ['模型 llm', llm.model
      ? esc(`${llm.model}${llm.text_model && llm.text_model !== llm.model ? ` / 文本 ${llm.text_model}` : ''}`
        + `${llm.workers ? ` · ${llm.workers} 并发` : ''}`
        + `${llm.temperature == null ? '' : ` · temperature ${llm.temperature}`}`)
      : null],
    ['基准 benchmark', (cfg.market && cfg.market.benchmark_label)
      ? esc(String(cfg.market.benchmark_label))
      : esc('未配置基准（对照曲线不可用）')],
    ['适当性结账', oc ? ocLine : esc('run_meta 未记录结账分布')],
  ]);
  return panel('运行元数据', 'run_meta / bundle.json 的原样字段', body);
}

/* ---- per-day aggregate ------------------------------------------------- */

/** The exporter writes `per_day`; the raw path has no such thing, so recount from the
 *  event log and say which of the two the table is. Never a silent blank. */
function perDayRows(model) {
  const given = (model.meta || {}).per_day;
  if (Array.isArray(given) && given.length) return { origin: 'exporter', rows: given };

  const rows = model.byDay.map((g) => {
    const reach = new Set();
    const byArm = {};
    const slot = (a) => (byArm[a] || (byArm[a] = {
      impressions: 0, _reach: new Set(), _eng: new Set(),
    }));
    const armOf = (id, fallback) => {
      const a = model.agents.get(String(id));
      return (a && a.arm) || fallback || null;
    };
    for (const r of g.imp) {
      reach.add(String(r.i));
      const a = armOf(r.i, r.arm);
      if (a) { const s = slot(a); s.impressions += 1; s._reach.add(String(r.i)); }
    }
    for (const r of g.dec) {
      if (((r.n_like || 0) + (r.n_save || 0) + (r.n_follow || 0)) <= 0) continue;
      const a = armOf(r.i, r.arm);
      if (a) slot(a)._eng.add(String(r.i));
    }
    let sub = 0; let fee = 0;
    for (const r of g.act) {
      if (r.kind === 'subscribe' || r.kind === 'dca') sub += r.amt || 0;
      fee += r.fee || 0;
    }
    const by_arm = {};
    for (const [a, s] of Object.entries(byArm)) {
      by_arm[a] = {
        impressions: s.impressions,
        reach_agents: s._reach.size,
        engaged_agents: s._eng.size,
      };
    }
    return {
      t: g.t, d: g.d, posts: g.post.length, impressions: g.imp.length,
      reach_agents: reach.size, decisions: g.dec.length, decisions_no_row: null,
      clicks: g.click.length, checkouts: g.co.length, comments: g.cmt.length,
      acts: g.act.length, sub_cny: sub, fees_cny: fee, by_arm,
    };
  });
  return { origin: 'computed', rows };
}

function perDaySection(model) {
  const { origin, rows } = perDayRows(model);
  if (!rows.length) {
    return panel('逐日汇总', '每个交易日发生了什么', placeholder([
      '这次运行没有任何交易日数据。',
    ]));
  }
  const arms = model.arms;
  const head = '<tr><th class="num">日</th><th>日期</th><th class="num">帖</th>'
    + '<th class="num">曝光</th><th class="num">触达</th><th class="num">决策</th>'
    + '<th class="num">点击</th><th class="num">结账</th><th class="num">评论</th>'
    + '<th class="num">交易</th><th class="num">申购额</th><th class="num">费用</th>'
    + arms.map((a) => `<th class="num" style="color:${safeColor(model.armColor[a])}">${esc(a)} 互动/触达</th>`).join('')
    + '</tr>';

  const total = {
    posts: 0, impressions: 0, decisions: 0, clicks: 0, checkouts: 0,
    comments: 0, acts: 0, sub_cny: 0, fees_cny: 0,
  };
  const body = rows.map((r) => {
    for (const k of Object.keys(total)) total[k] += Number(r[k] || 0);
    const armCells = arms.map((a) => {
      const s = (r.by_arm || {})[a];
      if (!s) return '<td class="num"><span class="faint">—</span></td>';
      const e = s.engaged_agents;
      const re = s.reach_agents ?? s.impressions;
      return `<td class="num">${e == null ? '—' : num(e)}<span class="faint">/${re == null ? '—' : num(re)}</span></td>`;
    }).join('');
    return `<tr><td class="num">${num(r.t)}</td><td>${esc(String(r.d || '—'))}</td>`
      + `<td class="num">${num(r.posts)}</td><td class="num">${num(r.impressions)}</td>`
      + `<td class="num">${num(r.reach_agents)}</td><td class="num">${num(r.decisions)}</td>`
      + `<td class="num">${num(r.clicks)}</td><td class="num">${num(r.checkouts)}</td>`
      + `<td class="num">${num(r.comments)}</td><td class="num">${num(r.acts)}</td>`
      + `<td class="num">${cny(r.sub_cny)}</td><td class="num">${cny(r.fees_cny)}</td>`
      + `${armCells}</tr>`;
  }).join('')
    + `<tr><td class="num"><b>合计</b></td><td>${esc(`${rows.length} 日`)}</td>`
    + `<td class="num">${num(total.posts)}</td><td class="num">${num(total.impressions)}</td>`
    + '<td class="num"><span class="faint">按日去重</span></td>'
    + `<td class="num">${num(total.decisions)}</td><td class="num">${num(total.clicks)}</td>`
    + `<td class="num">${num(total.checkouts)}</td><td class="num">${num(total.comments)}</td>`
    + `<td class="num">${num(total.acts)}</td><td class="num">${cny(total.sub_cny)}</td>`
    + `<td class="num">${cny(total.fees_cny)}</td>`
    + arms.map(() => '<td class="num"><span class="faint">—</span></td>').join('')
    + '</tr>';

  const noRow = rows.some((r) => Number(r.decisions_no_row || 0) > 0)
    ? note('有交易日出现 decisions_no_row > 0：那天有 agent 应当决策却没有落下决策行，'
      + '按日统计的互动率会偏低。')
    : '';

  const originNote = origin === 'exporter'
    ? note('本表直接来自导出器写下的 per_day。')
    : note('原始产物没有 per_day（那是导出器生成的）：本表由事件日志现算，'
      + '因此没有 decisions_no_row 一列，触达合计也不做跨日去重。');

  return panel('逐日汇总',
    origin === 'exporter' ? '来自 per_day' : '由事件日志现算',
    originNote + noRow
    + `<table><thead>${head}</thead><tbody>${body}</tbody></table>`,
    { bodyClass: 'body scrollx' });
}

function inputsSection(model) {
  const sha = (model.meta || {}).inputs_sha256;
  const keys = sha && typeof sha === 'object' ? Object.keys(sha) : [];
  if (!keys.length) {
    return panel('输入文件摘要', 'inputs_sha256', placeholder([
      'run_meta 里没有 inputs_sha256：无法确认这次运行读了哪些数据文件。',
      '没有摘要，就没法证明两次运行读的是同一份输入。',
    ]));
  }
  const width = Math.max(...keys.map((k) => k.length));
  const text = keys.map((k) => `${k.padEnd(width)}  ${sha[k]}`).join('\n');
  return panel('输入文件摘要', `inputs_sha256 · ${keys.length} 个文件`,
    note('这些是本次运行实际读入的数据文件的 sha256。换了任何一份输入，摘要就会变；'
      + '摘要一致才谈得上"同一个实验"。')
    + `<pre style="max-height:230px;overflow:auto">${esc(text)}</pre>`);
}

export function mountAuditPage(root, model) {
  const html = '<div class="page-head"><h2>审计</h2>'
    + `<p>${esc('这一页把本次运行的边界摊开：哪些结论有数据支撑，哪些只是演示。'
      + '先读保真度声明与载入提示，再读不变量，最后才是元数据。')}</p></div>`
    + '<div class="stack">'
    + fidelitySection(model)
    + warningsSection(model)
    + '<div class="grid2">' + whatRunSection(model) + imagesSection(model) + '</div>'
    + '<div class="grid2">' + truncationSection(model) + countersSection(model) + '</div>'
    + invariantsSection(model)
    + metaSection(model)
    + perDaySection(model)
    + inputsSection(model)
    + '</div>';
  root.innerHTML = html;
  return { destroy() { root.innerHTML = ''; } };
}

/* =======================================================================
   数据与场景页
   ======================================================================= */

function runHref(tag, base) {
  const q = new URLSearchParams();
  q.set('run', tag);
  if (base && base !== '..') q.set('base', base);
  return `?${q.toString()}#/replay`;
}

const EMPTY_RUNS_HTML = placeholder([
  '这里一个运行都没有列出来。',
  '样本包故意不进仓库：内容池里的营销文案是第三方语料，不做再分发。',
  '所以在 GitHub Pages 上这张表本来就是空的——这是设计，不是故障。',
]) + '<p class="muted">'
  + '在本地生成一个运行，再回到这一页：</p>'
  + '<pre>flowmirror demo two-arm\nflowmirror export-bundle runs/out/demo_two-arm</pre>'
  + '<p class="muted">'
  + '第一条跑一个离线演示（零 API 调用），第二条把它变成本查看器要读的四文件展示包。'
  + '想要三臂或规则型空策略，把 <code>two-arm</code> 换成 <code>three-arm</code> 或 '
  + '<code>null</code>。</p>';

function runRow(r, base) {
  const tag_ = String(r.tag || r.name || '');
  if (!tag_) return '';
  const hasBundle = r.bundle ?? r.has_bundle ?? r.hasBundle ?? null;
  const bundleCell = hasBundle == null
    ? '<span class="faint">未知</span>'
    : (hasBundle ? tag('有', '') : tag('无', ''));
  const extras = [];
  const rm = r.run_meta ?? r.has_run_meta ?? r.has_meta;
  const iv = r.invariants ?? r.has_invariants;
  if (rm === false) extras.push(tag('无 run_meta', ''));
  if (iv === false) extras.push(tag('无不变量报告', ''));
  return '<tr>'
    + `<td><a href="${esc(runHref(tag_, base))}"><code>${esc(tag_)}</code></a>`
    + (r.local ? ` <span class="chip local">本地</span>` : '')
    + (extras.length ? `<div class="tl-tags">${extras.join('')}</div>` : '')
    + '</td>'
    + `<td>${bundleCell}</td>`
    + `<td class="num">${fmtWhen(r.mtime ?? r.modified ?? r.updated_at ?? null)}</td>`
    + `<td><a href="${esc(runHref(tag_, base))}">打开回放</a></td>`
    + '</tr>';
}

function runsTable(runs, base) {
  return '<table><thead><tr><th>运行标签</th><th>展示包</th>'
    + '<th class="num">修改时间</th><th></th></tr></thead>'
    + `<tbody>${runs.map((r) => runRow(r, base)).join('')}</tbody></table>`;
}

/* ---- the four plug-in inputs ------------------------------------------ */

/* Public demo inputs mirrored from scenarios/cn_xhs_2025q4/scenario.yaml.
   Page modules do not fetch files directly; data.js owns runtime reads. */
const SCENARIO_ROWS = [
  {
    part: '人口',
    value: 'data/population/persona_grid_v3.json（人口格）+ data/population/agents_seed2027.json（队列）；'
      + '报告风险类 C2 / C3 / C4，字段 reported_C',
  },
  {
    part: '平台与创意',
    value: '中文社交信息流；四家虚构机构共用合成内容池 '
      + 'data/creatives/cn/content_pool_demo.jsonl，引擎按机构过滤；'
      + '每家 mode=rule、intent_mix=measured、cadence=1',
  },
  {
    part: '监管',
    value: '插件 cn_cxr：适当性结账；gate_redemptions=false、qdii_limits=true',
  },
  {
    part: '市场数据',
    value: 'data/funds/nav_demo_2025q4.json + fund_meta_demo.json；全部合成；日历 cn_trading、T+1；'
      + '场景费率 申购 1.20% / 赎回 0.50%',
  },
];

function riskMix(model) {
  if (!model || !model.agents || !model.agents.size) return null;
  const counts = new Map();
  for (const a of model.agents.values()) {
    const k = a.riskClass || '—';
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return [...counts.entries()].sort((x, y) => String(x[0]).localeCompare(String(y[0])))
    .map(([k, v]) => `${k} ${v}`).join(' · ');
}

function liveNumbers(model) {
  if (!model) {
    return panel('本次运行读进来的实际数字', '需要先选一个运行', placeholder([
      '还没有载入任何运行，所以这里全是"—"。',
      '在上面的表里点一个运行，或在地址里加 ?run=<运行标签>。',
    ]));
  }
  const cfg = (model.meta || {}).cfg || {};
  const uni = (model.meta || {}).universe || {};
  const fees = (model.meta || {}).fees || cfg.fees || {};
  const body = kv([
    ['运行标签', `<code>${esc(String(model.tag))}</code>`],
    ['人口', esc(`${model.agents.size} 人`
      + (riskMix(model) ? `（报告类 ${riskMix(model)}）` : ''))],
    ['队列文件', cfg.agents_file ? `<code>${esc(String(cfg.agents_file))}</code>` : null],
    ['机构', model.orgs.length ? esc(`${model.orgs.length} 家 · ${model.orgs.join(' · ')}`)
      : (cfg.orgs && cfg.orgs.length ? esc(cfg.orgs.join(' · ')) : null)],
    ['内容池', cfg.content_pool ? `<code>${esc(String(cfg.content_pool))}</code>` : null],
    ['适当性结账', cfg.suitability == null ? null
      : esc(cfg.suitability ? '开启（cn_cxr 结账在跑）' : '关闭')],
    ['基金池', uni.size == null ? null : esc(`${num(uni.size)} 只`)],
    ['净值文件', cfg.nav_cache ? `<code>${esc(String(cfg.nav_cache))}</code>` : null],
    ['本次运行费率', fees.subscribe_rate == null ? null
      : esc(`申购 ${(fees.subscribe_rate * 100).toFixed(2)}% · 赎回 ${((fees.redeem_rate ?? 0) * 100).toFixed(2)}%`)],
    ['注意力信号', cfg.guba_signal ? `<code>${esc(String(cfg.guba_signal))}</code>` : null],
    ['基金元数据', cfg.fund_meta_file ? `<code>${esc(String(cfg.fund_meta_file))}</code>` : null],
    ['窗口', cfg.window ? esc(`${cfg.window.start || '?'} → ${cfg.window.end || '?'}`) : null],
  ])
    + note('这一栏全部取自已载入运行的 run_meta / bundle.json，不是场景文件的声明值。'
      + '两者不一致时以这里为准——这才是引擎真正读进去的东西。')
    + ((fees.subscribe_rate != null && Math.abs(fees.subscribe_rate - 0.012) > 1e-9)
      ? note(`注意：场景文件写的申购费率是 1.20%，本次运行实际用的是 `
        + `${(fees.subscribe_rate * 100).toFixed(2)}%——运行配置覆盖了场景默认值。`)
      : '');
  return panel('本次运行读进来的实际数字', `${esc(model.source === 'bundle' ? '展示包' : '原始产物')}`, body);
}

function scenarioSection() {
  const rows = SCENARIO_ROWS.map((r) => `<tr><td><b>${esc(r.part)}</b></td>`
    + `<td>${esc(r.value)}</td></tr>`).join('');
  return panel('四件插件式输入', '一个场景 = 四份数据插件',
    '<table><thead><tr><th>组成部分</th>'
    + '<th>公开演示场景 · <code>cn_xhs_2025q4</code></th></tr></thead>'
    + `<tbody>${rows}</tbody></table>`
    + note('注意力输入 data/attention/attention_demo.json 是合成周度信号；'
      + 'data/flows/flow_holdout_demo.json 只是空结构示例，不包含任何观测结果。')
    + note('本表说明仓库自带的公开场景。载入某次运行后的真实配置与规模，以“本次运行读进来的实际数字”为准。'),
    { bodyClass: 'body scrollx' });
}

export function mountDataPage(root, model, opts = {}) {
  const base = (opts && opts.base) || '..';
  const localApi = !!(opts && opts.localApi);
  let dead = false;

  root.innerHTML = '<div class="page-head"><h2>数据与场景</h2>'
    + `<p>${esc('左边是能打开的运行，下面是这个沙盒由什么拼成的。'
      + '任何一条数据都指向仓库里的一个具体文件。')}</p></div>`
    + '<div class="stack">'
    + panel('可打开的运行', localApi ? '本地小服务在答话' : '静态模式',
      '<div id="data-runs" class="empty">正在列运行…</div>', { bodyClass: 'body' })
    + panel('直接按标签打开', '知道标签就不用等列表',
      '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:end">'
      + '<label class="field" style="flex:1 1 220px">运行标签'
      + '<input type="text" id="data-tag" placeholder="例如 viewer_two_arm" '
      + 'autocomplete="off" spellcheck="false"></label>'
      + '<a class="btn" id="data-open" aria-disabled="true">打开回放</a></div>'
      + note('标签就是 runs/out/ 下的目录名。'))
    + '<div id="data-live"></div>'
    + scenarioSection()
    + '</div>';

  const liveWrap = root.querySelector('#data-live');
  if (liveWrap) liveWrap.innerHTML = liveNumbers(model);

  /* the manual opener: an anchor whose href we rewrite, so this module never reads or
     writes location — the reader does the navigating by clicking */
  const input = root.querySelector('#data-tag');
  const opener = root.querySelector('#data-open');
  const sync = () => {
    const v = (input.value || '').trim();
    if (!v) {
      opener.removeAttribute('href');
      opener.setAttribute('aria-disabled', 'true');
    } else {
      opener.setAttribute('href', runHref(v, base));
      opener.removeAttribute('aria-disabled');
    }
  };
  if (input && opener) {
    input.addEventListener('input', sync);
    sync();
  }

  const slot = root.querySelector('#data-runs');
  listRuns(base).then((runs) => {
    if (dead || !slot) return;
    const list = (runs || []).filter((r) => r && (r.tag || r.name));
    if (!list.length) {
      slot.className = '';
      slot.innerHTML = EMPTY_RUNS_HTML
        + (localApi
          ? note('本地小服务在答话，但它没有列出任何运行：runs/out/ 是空的。')
          : note('没有检测到本地小服务，这张表来自随仓库发布的 web/samples/index.json。'
            + '要发起新运行，需要在本机跑 python web/server.py。'));
      return;
    }
    const nLocal = list.filter((r) => r.local).length;
    slot.className = 'scrollx';
    slot.innerHTML = runsTable(list, base)
      + note(`共 ${list.length} 个运行`
        + (nLocal ? `，其中 ${nLocal} 个由本机的小服务报出（带"本地"徽章，别人打不开）` : '')
        + '。「展示包」一列为"无"的运行仍然能打开，但只有原始产物：没有逐臂文案、'
        + '没有开局持仓、没有逐日 agent 记录。');
  }).catch((err) => {
    if (dead || !slot) return;
    slot.className = 'err';
    slot.innerHTML = '<h2>没能列出运行</h2>'
      + `<p class="muted">${esc((err && err.message) || String(err))}</p>`
      + '<p class="muted">这一页的其余部分不受影响。</p>';
  });

  return {
    destroy() {
      dead = true;
      if (input) input.removeEventListener('input', sync);
      root.innerHTML = '';
    },
  };
}

export default mountAuditPage;

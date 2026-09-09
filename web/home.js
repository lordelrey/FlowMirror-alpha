/* FlowMirror viewer — the front page.

   This is the page someone lands on knowing nothing, so it carries two warnings that
   the rest of the viewer assumes have already been read:

   1. Nothing here is a result. The cohort is synthetic, the NAVs may be synthetic, the
      scale is a demo, and no number on any page estimates an effect on a real investor.
   2. Nobody here is a person. The "investors" are agents generated from population
      cells; their words are model output.

   Everything else on this page is vocabulary — the three modality arms and the five
   suitability outcomes — because every other page uses those words without defining
   them. When a run is loaded the vocabulary is annotated with what THIS run actually
   did (which arms exist, how many people are in each, how the checkout came out), so
   the definitions and the data can never drift apart on screen.

   No listeners, no timers, no fetches: mount writes DOM once and destroy() clears it. */

import { esc, num, tallyUpTo } from './data.js';
import { ARM_ZH, ARM_LONG_ZH, OC_ZH, OC_COLOR } from './vocab.js';

/* The three arms as a DESIGN vocabulary, always all three, because the page has to
   explain what TC is even for a run that did not use it. A run's own arms are marked
   below; an unused arm is dimmed and says so in words — never an empty column. */
const DESIGN_ARMS = [
  {
    id: 'T', title: ARM_ZH.T,
    line: ARM_LONG_ZH.T,
    more: '卡片里没有图片位，agent 不知道这条笔记本来配了图。',
  },
  {
    id: 'TC', title: ARM_ZH.TC,
    line: ARM_LONG_ZH.TC,
    more: '描述文本是事先写死的，不随运行重新生成，也不带评价性措辞。',
  },
  {
    id: 'TV', title: ARM_ZH.TV,
    line: ARM_LONG_ZH.TV,
    more: '只有这一臂会把图片真的附到 agent 的输入里。',
  },
];

/* Frozen checkout vocabulary — flowmirror/regulator/base.py OUTCOMES. `tone` is the
   only place this page spends a reserved semantic colour: --signal for a signed
   mismatch confirmation, --stop for a declined one (contract §4). */
// Derived from vocab.js so this page cannot drift from the others:
// OC_ZH drives the row set, OC_COLOR drives tone.
const OC_CLS = { '--ok': 'ok', '--signal': 'sig', '--stop': 'stop' };
const OUTCOMES = Object.keys(OC_ZH).map(id => ({
  id,
  gloss: OC_ZH[id],
  tone: OC_CLS[OC_COLOR[id]] ?? '',
}));

const GO = [
  {
    hash: '#/replay', name: '回放', needsRun: true,
    line: '一天一天地看：当天有哪些帖子、谁看到了、谁动了手、结账判了什么。',
  },
  {
    hash: '#/agent', name: '投资者', needsRun: true,
    line: '单个 agent 的画像、报告风险等级、逐日的看到与做到。',
  },
  {
    hash: '#/modality', name: '模态对照', needsRun: true,
    line: '同一条笔记在三臂各是什么样子，触达与互动率差在哪。',
  },
  {
    hash: '#/audit', name: '审计', needsRun: true,
    line: '不变量逐条结论、载入过程的提示、展示包的保真度让步。',
  },
  {
    hash: '#/data', name: '数据与场景', needsRun: false,
    line: '有哪些运行可以打开，各自的规模、口径与产物是否齐全。',
  },
];

/* Only ever fed values that came from style.css or data.js's own fallback table; the
   filter is here so an unexpected string can never become CSS. */
const safeColor = (c) => (/^[#a-zA-Z0-9(),.%\s-]{1,64}$/.test(String(c || ''))
  ? String(c) : 'var(--ink-dim)');

const swatch = (arm, model) => {
  const c = model && model.armColor && model.armColor[arm]
    ? safeColor(model.armColor[arm])
    : (DESIGN_ARMS.some((a) => a.id === arm) ? `var(--arm-${arm})` : 'var(--ink-dim)');
  return `<i style="background:${c}"></i>`;
};

// Run-end states. Renamed to avoid colliding with vocab.js's same-named map
// (that one covers decision-parse states); same name, different concept.
const RUN_STATUS_ZH = {
  ok: '正常结束',
  invariant_failure: '不变量未过',
  error: '异常中止',
};

function armCounts(model) {
  const out = new Map();
  if (!model) return out;
  for (const a of model.agents.values()) {
    if (!a.arm) continue;
    out.set(a.arm, (out.get(a.arm) || 0) + 1);
  }
  return out;
}

function dateSpan(model) {
  if (!model || !model.days.length) return '—';
  const first = model.dateOf.get(model.days[0])
    || (model.byDay[0] && model.byDay[0].d) || '—';
  const lastT = model.days[model.days.length - 1];
  const last = model.dateOf.get(lastT)
    || (model.byDay[model.byDay.length - 1] && model.byDay[model.byDay.length - 1].d) || '—';
  return first === last ? first : `${first} → ${last}`;
}

/** First recorded image digest, for the honest "no pixels here" placeholder (§6). */
function firstImageSha(model) {
  if (!model) return null;
  for (const p of model.postById.values()) {
    if (p && p.image && p.image.sha) return String(p.image.sha);
  }
  return null;
}

/* ---------- sections ---------------------------------------------------- */

function sectionWhat() {
  return `
  <section class="panel">
    <h2>这是什么 <span class="note">一台可以拆开看的仪器，不是一份结论</span></h2>
    <div class="body">
      <p class="lede">一场多模态 LLM agent 模拟。真实机构在中文内容平台上真实发布过的基金营销
        笔记进入信息流，模拟出的投资者读到它们，决定点赞、收藏、关注、点进落地页还是下单；
        下单要先过一道 CSRC 式的适当性结账，由它决定这个人当天到底买得成什么。</p>
      <p class="lede">屏幕上的每一个数字都来自模拟。投资者队列是按人口格合成的，净值可能也是
        合成的，规模是演示级——几十个人、十几个交易日。这里没有任何一项在估计真实投资者受到的
        真实影响，也没有任何一位「投资者」对应现实中的某个人：他们是 agent，他们的话是模型
        生成的文本。把它当作一台能停下来逐帧检查的仪器来读。</p>
    </div>
  </section>`;
}

function sectionArms(model) {
  const counts = armCounts(model);
  const runArms = model ? model.arms : [];
  const extra = runArms.filter((a) => !DESIGN_ARMS.some((d) => d.id === a));
  const sha = firstImageSha(model);
  const img = model && model.images;

  const cols = DESIGN_ARMS.map((a) => {
    const used = !model || runArms.includes(a.id);
    const n = counts.get(a.id) || 0;
    let stat;
    if (!model) stat = '未选择运行';
    else if (!runArms.includes(a.id)) stat = '本次运行未设置该臂';
    else stat = `本次运行 ${num(n)} 人`;

    let note = '';
    if (a.id === 'TV' && model) {
      if (img && img.attached > 0) {
        note = `<p class="faint">本次运行有 ${num(img.attached)} 次曝光真的附上了像素。</p>`;
      } else {
        const why = img && img.root
          ? '配置了图片库，但本次运行零附图（检查 images_root 是否指向真实目录）'
          : '本次运行没有配置 images_root';
        const shaLine = sha
          ? `图片摘要仍在记录里，例如 <code>${esc(sha.slice(0, 8))}…</code>（仓库里永不存像素）。`
          : '本次运行连图片摘要都没有记录。';
        note = `<div class="placeholder">没有任何图片像素进入过 agent 的输入：${esc(why)}。
          ${shaLine}</div>`;
      }
    }
    return `
      <div class="armcol${used ? '' : ' off'}">
        <h3>${swatch(a.id, model)}<span>${esc(a.id)}</span>
          <span class="muted">${esc(a.title)}</span></h3>
        <div class="txt">${esc(a.line)}
<span class="faint">${esc(a.more)}</span></div>
        ${note}
        <div class="stat">${esc(stat)}</div>
      </div>`;
  }).join('');

  const extraCols = extra.map((a) => `
      <div class="armcol">
        <h3>${swatch(a, model)}<span>${esc(a)}</span>
          <span class="muted">本次运行里出现的臂</span></h3>
        <div class="txt">这个臂名不在 T / TC / TV 三个设计臂里，本页没有它的定义。</div>
        <div class="stat">本次运行 ${num(counts.get(a) || 0)} 人</div>
      </div>`).join('');

  const which = model
    ? `<p class="muted">本次运行实际用了 ${esc(runArms.join(' 、 ') || '—')}，共
        ${num(runArms.length)} 臂。</p>`
    : '<p class="muted">选定运行后，这里会标出它实际用了哪几臂。</p>';

  const rawNote = model && model.source === 'raw'
    ? `<p class="muted">本次运行未导出展示包（bundle）：逐臂的卡片文案不可用，
        「模态对照」只显示触达与互动率。</p>`
    : '';

  return `
  <section class="panel">
    <h2>三个模态臂 <span class="note">同一条笔记，三种给法</span></h2>
    <div class="body">
      ${which}
      <div class="gridN" style="--cols:3">${cols}${extraCols}</div>
      <p class="muted">中间那一臂是为了把两件事拆开：<b>TC 减 T</b> 隔离出图片「说了什么」，
        <b>TV 减 TC</b> 隔离出图片「长什么样」。少了 TC，两者就只能混在一个差值里。</p>
      ${rawNote}
    </div>
  </section>`;
}

function sectionCheckout(model) {
  const t = (model && model.days.length) ? tallyUpTo(model, model.days.length - 1) : null;
  const rows = OUTCOMES.map((o) => {
    const n = t ? (t.byOc[o.id] || 0) : null;
    return `
      <tr>
        <td><span class="tag${o.tone ? ' ' + o.tone : ''}">${esc(o.id)}</span></td>
        <td>${esc(o.gloss)}</td>
        <td class="num">${t ? num(n) : '—'}</td>
      </tr>`;
  }).join('');

  const foot = t
    ? `本次运行共 ${num(t.co)} 次结账尝试。`
    : '未选择运行，因此没有计数。';

  return `
  <section class="panel">
    <h2>结账词表 <span class="note">suitability checkout · 每页都在用这五个词</span></h2>
    <div class="body">
      <div class="scrollx">
        <table>
          <thead><tr><th>结果</th><th>含义</th><th class="num">本次计数</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="muted">${esc(foot)}
        每一行结账还同时记下反事实 <code>oc_cf</code>：闸门开着时它与实际结果一致，
        闸门关着（<code>suitability=false</code>）时它记的是同一条规则本来会怎么判。
        所以「不设这道闸会发生什么」在日志里也查得到。</p>
      <p class="faint">绿 = 风险匹配通过，橙 = 已签署不匹配确认书，红 = 被拒或被拦。
        这几种颜色在整个界面里只表示这个意思。</p>
    </div>
  </section>`;
}

function sectionGo(model) {
  const cards = GO.map((c) => {
    const off = c.needsRun && !model;
    const need = off ? '<p class="faint">需要先选一个运行。</p>' : '';
    const inner = `<h3>${esc(c.name)}</h3><p>${esc(c.line)}</p>${need}`;
    return off
      ? `<div class="gocard" aria-disabled="true">${inner}</div>`
      : `<a class="gocard" href="${esc(c.hash)}">${inner}</a>`;
  }).join('');
  return `
  <section class="panel">
    <h2>从哪儿开始看</h2>
    <div class="body">
      <div class="gridN" style="--cols:3">${cards}</div>
    </div>
  </section>`;
}

function sectionRun(model, opts) {
  if (!model) {
    const failed = opts.tag
      ? `<p class="muted">地址里的运行标签是 <code>${esc(opts.tag)}</code>，
          但它没能载入；页面顶部有具体原因。</p>`
      : '';
    return `
  <section class="panel">
    <h2>还没有选运行</h2>
    <div class="body">
      ${failed}
      <p class="muted">到 <a href="#/data">数据与场景</a> 里挑一个已有的运行，
        或者用下面的命令现做一个。页面靠地址里的
        <code>?run=&lt;运行标签&gt;</code> 认运行，标签就是
        <code>runs/out/</code> 下的目录名。</p>
      <pre>python -m flowmirror.cli demo two-arm
python -m flowmirror.cli export-bundle runs/out/demo_two-arm</pre>
      <p class="muted">跑完打开 <code>web/index.html?run=demo_two-arm</code>。
        <span class="faint">这条 demo 完全离线：合成净值、零模型调用。</span></p>
      <p class="faint">当前仓库根前缀 <code>?base=${esc(opts.base || '..')}</code>；
        目录结构不同的话用它指过去。</p>
    </div>
  </section>`;
  }

  const meta = model.meta || {};
  const cfg = meta.cfg || {};
  const level = (meta.modality && meta.modality.level) || cfg.arm_level || cfg.modality_level;
  const levelZh = level === 'agent' ? 'agent 级随机（每人固定一臂）'
    : level === 'run' ? '运行级（整次运行一臂）'
      : (level ? String(level) : '未记录');

  const inv = model.invariants && model.invariants.summary;
  const invLine = inv
    ? (inv.failed > 0
      ? `${num(inv.failed)} 项未过 · ${num(inv.passed)}/${num(inv.total)} 过 · ${num(inv.skipped)} 跳过`
      : `${num(inv.passed)}/${num(inv.total)} 过 · ${num(inv.skipped)} 跳过 · 无未过项`)
    : '本次运行没有不变量报告';

  const policy = model.agentPolicy === 'null'
    ? '规则型模拟（rule-based），无 LLM 调用'
    : `LLM agent${model.mock ? ' · mock 模型，无真实调用' : ''}`;

  const kv = [
    ['运行标签', esc(model.tag)],
    ['数据来源', model.source === 'bundle' ? '展示包（bundle）'
      : '原始产物（无展示包：逐臂文案、开局持仓、逐日 agent 记录都不可用）'],
    ['规模', `${num(model.agents.size)} 人 × ${num(model.days.length)} 交易日 ·
      ${num(model.arms.length)} 臂`],
    ['交易日区间', esc(dateSpan(model))],
    ['分臂方式', esc(levelZh)],
    ['决策策略', esc(policy)],
    ['信息流', `${num(model.orgs.length)} 家机构 · ${num(model.postById.size)} 条笔记`],
    ['结束状态', `${esc(RUN_STATUS_ZH[model.status] || model.status || '未记录')}
      <span class="faint">${esc(model.status || '')}</span>`],
    ['不变量', esc(invLine)],
  ];
  if (model.syntheticNav) kv.push(['净值', '合成净值，仅供演示，不代表任何真实基金表现']);
  if (model.warnings && model.warnings.length) {
    kv.push(['载入提示', `${num(model.warnings.length)} 条，逐条列在
      <a href="#/audit">审计</a>页`]);
  }
  if (model.fidelity && model.fidelity.length) {
    kv.push(['保真度让步', `${num(model.fidelity.length)} 条，逐条列在
      <a href="#/audit">审计</a>页`]);
  }

  return `
  <section class="panel">
    <h2>本次运行 <span class="note">明细在审计页</span></h2>
    <div class="body">
      <dl class="kv">
        ${kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}
      </dl>
      <p class="muted" style="margin-top:var(--gap)">
        <a href="#/replay">从第 1 天开始回放 →</a></p>
    </div>
  </section>`;
}

/* Scoped to #page-home and built only from tokens: the shared stylesheet is off
   limits, and the go-to cards are the one shape it does not already have. */
const CSS = `
#page-home .stack { gap: var(--gap); }
#page-home .lede { max-width: 78ch; }
#page-home .gocard {
  display: block; text-decoration: none; color: inherit;
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: var(--r); padding: 10px 12px; min-width: 0;
}
#page-home a.gocard:hover { border-color: var(--accent-ui-dim); background: var(--surface-3); }
#page-home .gocard h3 { margin: 0 0 3px; font-size: 13px; font-weight: 600; color: var(--ink); }
#page-home .gocard p { margin: 0; font-size: 12px; color: var(--ink-dim); }
#page-home .gocard[aria-disabled="true"] { opacity: .6; }
#page-home .armcol.off { opacity: .55; }
#page-home .armcol > h3 .muted { font-weight: 400; }
#page-home .armcol .placeholder { margin: 0 11px 10px; }
#page-home .kv dd a { font-family: var(--ui); }
`;

/* ---------- mount ------------------------------------------------------- */

export function mountHomePage(root, model, opts = {}) {
  const o = { base: '..', tag: null, ...opts };
  root.innerHTML = `
  <style>${CSS}</style>
  <div class="page-head">
    <h2>FlowMirror · 模拟社会回放</h2>
    <p>基金营销笔记 → 模拟投资者 → 适当性结账。全程模拟，演示规模，不是结论。</p>
  </div>
  <div class="stack">
    ${sectionWhat()}
    ${sectionArms(model)}
    ${sectionCheckout(model)}
    ${sectionGo(model)}
    ${sectionRun(model, o)}
  </div>`;

  return {
    destroy() { root.innerHTML = ''; },
  };
}

export default mountHomePage;

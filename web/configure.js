/* FlowMirror viewer — configure and run.
   Contract: docs/WEB_CONTRACT_2026-09-07.md §2, §7.

   Two modes, and the static one is the point. With no local service — GitHub Pages, or
   a plain `python -m http.server` — every control is disabled and the page shows the
   exact CLI that does the same thing. A form that silently does nothing is worse than
   no form at all, so this page would rather be honest than look capable.

   No key input exists anywhere here. Credentials resolve inside the engine from
   config/api.yaml, the environment, or the legacy key file; the service only ever
   reports a boolean saying whether they are configured. */

import { esc } from './data.js';

const STEPS = [
  { key: 'validate', label: '校验配置', detail: '按 run.schema.json 校验；additionalProperties 为 false，未登记的键会整份拒绝' },
  { key: 'load', label: '装载人口与内容池', detail: '人口队列、内容池、净值缓存、股吧信号、基金档案' },
  { key: 'loop', label: '逐日循环', detail: '发帖 → 排序 → 曝光 → 决策 → 应用 → 滞后更新' },
  { key: 'checkout', label: '结账与不变量', detail: 'C×R 适当性结账，然后 13 条不变量' },
  { key: 'bundle', label: '导出展示包', detail: 'flowmirror export-bundle，供本回放使用' },
];

/* The engine's OWN stdout prefixes. Matching what it already prints is deliberate:
   inventing engine-side progress markers would mean editing the engine to serve a
   viewer, and the markers would then be one more thing that can drift. These matches
   are best-effort — a step lighting up slightly early is fine, a fabricated precision
   is not. */
const STEP_MATCHERS = [
  { step: 'load', test: (l) => l.startsWith('[world] loaded') },
  { step: 'load', test: (l) => l.includes('initial P&L') },
  { step: 'loop', test: (l) => /^\[engine\]/.test(l) || /^\[world\] day /.test(l) },
  { step: 'checkout', test: (l) => l.includes('invariants') || l.includes('reports written') },
  { step: 'bundle', test: (l) => l.startsWith('[export]') },
];

export function mountRunPage(root, opts = {}) {
  const { base = '..', localApi = false, onStep = () => {} } = opts;
  const S = { runId: null, since: 0, timer: 0, es: null, state: 'idle', tag: null, dead: false };

  root.innerHTML = `
    <div class="page-head">
      <h2>配置与运行</h2>
      <p>从这里跑一次模拟。${localApi
        ? '本地小服务已连接，可以直接发起。'
        : '当前是静态模式，没有本地小服务，所以下面的控件全部禁用——表单点了没反应比没有表单更糟。用页面给出的命令在终端里跑同样的事。'}</p>
    </div>
    <div class="grid2">
      <section class="panel">
        <h2>参数</h2>
        <div class="body stack" style="gap:11px">
          <div class="grid2" style="gap:11px">
            <label class="field">场景
              <select id="f-scenario" ${localApi ? '' : 'disabled'}>
                <option value="cn" selected>中国 · 小红书 2025Q4</option>
                <option value="us" disabled>美国 2025（路线图）</option>
              </select>
            </label>
            <label class="field">臂
              <select id="f-arms" ${localApi ? '' : 'disabled'}>
                <option value="T,TV" selected>两臂 T / TV</option>
                <option value="T,TC,TV">三臂 T / TC / TV</option>
                <option value="T,TC">参考格 T / TC</option>
              </select>
            </label>
            <label class="field">交易日
              <input type="number" id="f-days" value="12" min="1" max="60" ${localApi ? '' : 'disabled'}>
            </label>
            <label class="field">人数
              <input type="number" id="f-agents" value="40" min="4" max="400" ${localApi ? '' : 'disabled'}>
            </label>
            <label class="field">种子
              <input type="number" id="f-seed" value="2027" ${localApi ? '' : 'disabled'}>
            </label>
            <label class="field">模型
              <select id="f-mock" ${localApi ? '' : 'disabled'}>
                <option value="1" selected>模拟模型（无真实调用）</option>
                <option value="0" id="opt-live" disabled>真实模型</option>
              </select>
            </label>
          </div>
          <p class="faint" id="crednote">本页不接受任何密钥。真实模型的凭据由引擎自己从
            <code>config/api.yaml</code>、环境变量或旧版密钥文件解析，浏览器永不经手。</p>
          <div>
            <button class="primary" id="btn-start" ${localApi ? '' : 'disabled'}>开始运行</button>
            <button id="btn-stop" disabled>停止跟踪</button>
          </div>
          <div id="cli" class="stack" style="gap:6px"></div>
        </div>
      </section>
      <section class="panel">
        <h2>进度 <span class="note">五步</span></h2>
        <div class="body">
          <ol class="ladder" id="ladder"></ol>
        </div>
      </section>
    </div>
    <section class="panel" style="margin-top:var(--gap)">
      <h2>运行日志 <span class="note">引擎 stdout 原文</span></h2>
      <div class="body">
        <div class="logstream" id="log" aria-live="polite">${localApi
          ? '（等待开始）' : '（静态模式没有日志流。上面的命令会把同样的输出打在你的终端里。）'}</div>
        <p class="muted" id="donenote"></p>
      </div>
    </section>`;

  const $ = (id) => root.querySelector('#' + id);
  const logEl = $('log');

  /* ---------- the ladder ------------------------------------------------ */

  let states = {};
  function resetLadder() {
    states = Object.fromEntries(STEPS.map((s) => [s.key, 'pending']));
    drawLadder();
  }
  function drawLadder() {
    $('ladder').innerHTML = STEPS.map((s) => `
      <li data-state="${states[s.key]}">
        <span>${esc(s.label)}<br><span class="detail">${esc(s.detail)}</span></span>
      </li>`).join('');
    const active = STEPS.find((s) => states[s.key] === 'active');
    const failed = STEPS.find((s) => states[s.key] === 'failed');
    const doneAll = STEPS.every((s) => states[s.key] === 'done');
    if (failed) onStep(`第 ${STEPS.indexOf(failed) + 1}/5 步失败`, 'bad');
    else if (doneAll) onStep('5/5 完成', 'good');
    else if (active) onStep(`第 ${STEPS.indexOf(active) + 1}/5 步 · ${active.label}`, '');
    else onStep('');
  }
  function advance(key) {
    const i = STEPS.findIndex((s) => s.key === key);
    if (i < 0) return;
    STEPS.forEach((s, j) => {
      if (j < i && states[s.key] !== 'failed') states[s.key] = 'done';
    });
    if (states[key] !== 'failed') states[key] = 'active';
    drawLadder();
  }
  resetLadder();

  /* ---------- the CLI equivalent, always shown ------------------------- */

  function cliFor() {
    const days = $('f-days').value || '12';
    const agents = $('f-agents').value || '40';
    const seed = $('f-seed').value || '2027';
    const arms = ($('f-arms').value || 'T,TV').split(',');
    const cfg = arms.length === 3 ? 'runs/demo_three_arm.json' : 'runs/demo_two_arm.json';
    const tag = `ui_${arms.length}arm_${agents}x${days}_s${seed}`;
    return [
      `python -m flowmirror.engine.loop ${cfg} --mock --days ${days} --agents ${agents} --seed ${seed} --out runs/out/${tag}`,
      `python -m flowmirror.analysis.export_bundle runs/out/${tag}`,
      `# 然后打开 web/?run=${tag}`,
    ].join('\n');
  }
  function drawCli() {
    $('cli').innerHTML = '<p class="muted">等效命令（在仓库根目录）：</p>'
      + `<pre>${esc(cliFor())}</pre>`
      + (localApi ? '' : '<p class="faint">要在页面里发起运行，用带 API 的本地小服务：'
        + '<code>python web/server.py</code></p>');
  }
  drawCli();
  for (const id of ['f-days', 'f-agents', 'f-seed', 'f-arms']) {
    const el = $(id);
    if (el) el.addEventListener('input', drawCli);
  }

  /* ---------- credentials indicator (read-only) ------------------------ */

  if (localApi) {
    fetch('/api/runs', { cache: 'no-store' })
      .then((r) => r.json())
      .then((d) => {
        if (S.dead) return;
        if (d && d.credentials_configured) {
          const o = $('opt-live');
          if (o) { o.disabled = false; o.textContent = '真实模型（凭据已配置）'; }
          $('crednote').textContent = '服务报告凭据已配置，所以「真实模型」可选。'
            + '本页仍不接受任何密钥——凭据只在引擎内部解析。';
        } else {
          $('crednote').textContent = '服务报告未检测到凭据，所以只能跑模拟模型。'
            + '配置凭据请编辑 config/api.yaml（该文件已 gitignore），本页不接受密钥。';
        }
      })
      .catch(() => {});
  }

  /* ---------- log streaming -------------------------------------------- */

  function appendLines(lines) {
    if (!lines.length) return;
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
    const html = lines.map((l) => {
      let cls = '';
      if (/FATAL|Traceback|invariant FAIL|exit code [1-9]/.test(l)) cls = 'err';
      else if (/WARNING|warn/i.test(l)) cls = 'warn';
      else if (/invariants=PASS|identical=True|\[ok\]/.test(l)) cls = 'ok';
      for (const m of STEP_MATCHERS) if (m.test(l)) advance(m.step);
      return `<span class="${cls}">${esc(l)}</span>`;
    }).join('\n');
    if (logEl.dataset.fresh !== '1') { logEl.textContent = ''; logEl.dataset.fresh = '1'; }
    logEl.insertAdjacentHTML('beforeend', html + '\n');
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  function stopTracking() {
    clearTimeout(S.timer);
    S.timer = 0;
    if (S.es) { S.es.close(); S.es = null; }
    $('btn-stop').disabled = true;
  }

  async function poll() {
    if (S.dead || !S.runId) return;
    try {
      const r = await fetch(`/api/run/${encodeURIComponent(S.runId)}/log?since=${S.since}`,
        { cache: 'no-store' });
      if (r.ok) {
        const d = await r.json();
        const lines = d.lines || [];
        S.since = d.next != null ? d.next : S.since + lines.length;
        appendLines(lines);
        S.state = d.state || S.state;
        S.tag = d.tag || S.tag;
        if (S.state === 'ok') {
          STEPS.forEach((s) => { states[s.key] = 'done'; });
          drawLadder();
          finish(true);
          return;
        }
        if (S.state === 'failed') {
          const active = STEPS.find((s) => states[s.key] === 'active') || STEPS[0];
          states[active.key] = 'failed';
          drawLadder();
          finish(false);
          return;
        }
      }
    } catch (e) { /* the service went away; the next tick reports it */ }
    S.timer = setTimeout(poll, 900);
  }

  function finish(ok) {
    stopTracking();
    $('btn-start').disabled = false;
    const note = $('donenote');
    if (ok && S.tag) {
      // Contract §9: never read location; a leading-? URL replaces the whole
      // query string, so base must ride along or it would be dropped.
      const runQ = new URLSearchParams();
      runQ.set('run', S.tag);
      if (base && base !== '..') runQ.set('base', base);
      const href = `?${runQ.toString()}#/replay`;
      note.innerHTML = `运行完成。<a href="${href}">打开回放</a>`;
    } else if (ok) {
      note.textContent = '运行完成，但服务没有报告运行标签。到「数据与场景」里挑一个。';
    } else {
      note.textContent = '运行失败。日志里的 FATAL 或 Traceback 行说明了原因。';
    }
  }

  async function start() {
    if (!localApi) return;
    stopTracking();
    resetLadder();
    logEl.dataset.fresh = '0';
    logEl.textContent = '（已提交，等待服务响应）';
    $('donenote').textContent = '';
    $('btn-start').disabled = true;
    advance('validate');
    const body = {
      scenario: $('f-scenario').value,
      days: Number($('f-days').value) || 12,
      agents: Number($('f-agents').value) || 40,
      seed: Number($('f-seed').value) || 2027,
      arms: ($('f-arms').value || 'T,TV').split(','),
      mock: $('f-mock').value !== '0',
    };
    try {
      const r = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        states.validate = 'failed';
        drawLadder();
        logEl.dataset.fresh = '0';
        appendLines([`服务拒绝了这次运行：${d.error || r.status}`]);
        $('btn-start').disabled = false;
        return;
      }
      S.runId = d.id;
      S.tag = d.tag || null;
      S.since = 0;
      S.state = 'running';
      states.validate = 'done';
      drawLadder();
      $('btn-stop').disabled = false;
      poll();
    } catch (e) {
      states.validate = 'failed';
      drawLadder();
      appendLines([`无法联系本地服务：${e.message}`]);
      $('btn-start').disabled = false;
    }
  }

  $('btn-start').addEventListener('click', start);
  $('btn-stop').addEventListener('click', () => {
    stopTracking();
    $('donenote').textContent = '已停止跟踪日志。运行本身仍在服务端继续。';
  });

  return {
    destroy() {
      S.dead = true;
      stopTracking();
      onStep('');
    },
  };
}

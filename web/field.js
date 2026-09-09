/* FlowMirror viewer — the population field.

   The agents have no physical location, so position encodes IDENTITY instead of space:
   columns are age bands, rows are asset bands, and each tile splits into three
   horizontal strips by risk tolerance. A wave of exposure sweeping the field therefore
   answers "which KIND of investor did this creative reach", which is the question a
   force-directed graph of agents cannot answer at all.

   On top of the dots, three things carry meaning and one of them is loud on purpose:
   an exposure line in the agent's ARM colour, an engagement ring, a stance tick, and
   then the suitability flash — orange where a mismatch confirmation was signed, red
   where it was refused. Those two are the only saturated marks on the canvas and they
   must stay the loudest thing on it, because they are the mechanism this sandbox
   exists to study. */

import { stateAt } from './data.js';
import {
  AGE_ORDER, ASSET_ORDER, RISK_ORDER, AGE_ZH, ASSET_ZH, RISK_ZH,
  STANCE_COLOR, STANCE_ZH, OC_SHORT_ZH, cssVar,
} from './vocab.js';

const STEP = 13;          // horizontal spacing between dots inside a strip
const ROW_STEP = 11;      // vertical spacing when a strip wraps
const PAD = { l: 74, t: 18, r: 150, b: 26 };

export function mountField(root, model) {
  const canvas = root.querySelector('#field');
  const legendEl = root.querySelector('#legend');
  if (!canvas) throw new Error('field.js: 找不到 #field 画布');

  const S = {
    day: 0, sel: null, progress: 1, raf: 0,
    w: 0, h: 0, dpr: 1,
    axes: { age: [], asset: [] },
    box: null, layout: new Map(), orgPos: new Map(), strips: [],
    pick: null, destroyed: false,
  };

  const agents = [...model.agents.values()];
  const dim = () => cssVar('--ink-dim', '#8593ac');

  /* ---------- layout ---------------------------------------------------- */

  function buildLayout() {
    const ages = AGE_ORDER.filter((a) => agents.some((p) => p.age === a));
    const assets = ASSET_ORDER.filter((a) => agents.some((p) => p.asset === a));
    S.axes = { age: ages.length ? ages : ['?'], asset: assets.length ? assets : ['?'] };

    const cols = S.axes.age.length, rowsN = S.axes.asset.length;
    const tw = (S.w - PAD.l - PAD.r) / cols;
    const th = (S.h - PAD.t - PAD.b) / rowsN;
    S.box = { ...PAD, tw, th, cols, rowsN };

    const groups = new Map();
    for (const p of agents) {
      const k = `${p.age}|${p.asset}|${p.risk}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(p);
    }

    S.layout.clear();
    S.strips = [];
    for (const [k, list] of groups) {
      const [age, asset, risk] = k.split('|');
      const ci = Math.max(0, S.axes.age.indexOf(age));
      const ri = Math.max(0, S.axes.asset.indexOf(asset));
      const bi = Math.max(0, RISK_ORDER.indexOf(risk));
      const bandH = (th - 16) / RISK_ORDER.length;
      const x0 = PAD.l + ci * tw + 7;
      const y0 = PAD.t + ri * th + 9 + bi * bandH;
      const w = tw - 14, h = bandH - 3;

      list.sort((a, b) => String(a.id).localeCompare(String(b.id)));

      // Density: a 400-agent cohort in the same tile grid as a 40-agent one needs
      // smaller dots, or the strip becomes a solid bar that shows nothing.
      const per = Math.max(1, Math.floor(w / STEP));
      const rowsUsed = Math.ceil(list.length / per);
      const fits = rowsUsed * ROW_STEP <= h + ROW_STEP;
      const radius = Math.max(1.4, Math.min(4.0, 3.2 * Math.sqrt(60 / Math.max(60, list.length))));

      if (!fits) {
        // Too many to place honestly: the strip becomes one block whose fill is the
        // share of its agents engaged that day, with the head-count centred in it.
        // A grid of overlapping dots would look like data and be none.
        S.strips.push({ x: x0, y: y0, w, h, list, risk, block: true });
        for (const p of list) S.layout.set(p.id, { x: x0 + w / 2, y: y0 + h / 2, r: 0, block: S.strips.length - 1 });
        continue;
      }
      const usedW = Math.min(list.length, per) * STEP;
      const ox = x0 + (w - usedW) / 2 + STEP / 2;
      const oy = y0 + (h - (rowsUsed - 1) * ROW_STEP) / 2;
      S.strips.push({ x: x0, y: y0, w, h, list, risk, block: false });
      list.forEach((p, i) => {
        S.layout.set(p.id, {
          x: ox + (i % per) * STEP,
          y: Math.max(y0 + 4, Math.min(oy + Math.floor(i / per) * ROW_STEP, y0 + h - 3)),
          r: radius, block: -1,
        });
      });
    }

    // institutions sit down the right margin, outside the tile grid
    S.orgPos.clear();
    const ox = S.w - PAD.r + 54;
    const n = model.orgs.length;
    const span = S.h - PAD.t - PAD.b - 60;
    model.orgs.forEach((o, i) => {
      const y = n <= 1 ? S.h / 2 : PAD.t + 40 + i * (span / (n - 1));
      S.orgPos.set(o, { x: ox, y });
    });
  }

  /* ---------- sizing --------------------------------------------------- */

  function resize() {
    const parent = canvas.parentElement;
    const cssW = Math.max(560, Math.round(parent.clientWidth || 900));
    // Aspect follows the grid so tiles stay roughly square-ish, within bounds a screen
    // can actually show without scrolling vertically.
    const rowsN = Math.max(1, ASSET_ORDER.filter((a) => agents.some((p) => p.asset === a)).length);
    const cssH = Math.max(360, Math.min(760, Math.round(cssW * (0.42 + 0.12 * rowsN))));
    S.dpr = Math.min(2.5, window.devicePixelRatio || 1);
    S.w = cssW; S.h = cssH;
    canvas.style.width = '100%';
    canvas.style.height = `${cssH}px`;
    canvas.width = Math.round(cssW * S.dpr);
    canvas.height = Math.round(cssH * S.dpr);
    buildLayout();
    draw();
  }

  /* ---------- drawing --------------------------------------------------- */

  function draw() {
    if (S.destroyed || !S.box) return;
    const g = canvas.getContext('2d');
    g.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    const { w: W, h: H, box } = S;

    g.clearRect(0, 0, W, H);
    g.fillStyle = cssVar('--ground', '#0d131f');
    g.fillRect(0, 0, W, H);

    const line = cssVar('--line', '#263149');
    const dimc = dim();
    const ink = cssVar('--ink', '#e6eaf2');
    const st = stateAt(model, S.day);
    if (!st) return;

    /* tiles, strips and axis labels */
    g.font = '11px system-ui, -apple-system, "PingFang SC", sans-serif';
    g.textBaseline = 'middle';
    for (let r = 0; r < box.rowsN; r++) {
      for (let c = 0; c < box.cols; c++) {
        const x = PAD.l + c * box.tw, y = PAD.t + r * box.th;
        g.strokeStyle = line; g.lineWidth = 1;
        g.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5,
          Math.round(box.tw) - 1, Math.round(box.th) - 1);
        g.globalAlpha = 0.45;
        for (let b = 1; b < RISK_ORDER.length; b++) {
          const by = y + 8 + b * ((box.th - 16) / RISK_ORDER.length);
          g.beginPath();
          g.moveTo(x + 4, Math.round(by) + 0.5);
          g.lineTo(x + box.tw - 4, Math.round(by) + 0.5);
          g.stroke();
        }
        g.globalAlpha = 1;
        const n = agents.filter((p) => p.age === S.axes.age[c] && p.asset === S.axes.asset[r]).length;
        g.fillStyle = dimc; g.globalAlpha = 0.55; g.textAlign = 'left';
        g.font = '10px "JetBrains Mono", monospace';
        g.fillText(`${n} 人`, x + 6, y + 10);
        g.globalAlpha = 1;
        g.font = '11px system-ui, -apple-system, "PingFang SC", sans-serif';
      }
      g.fillStyle = dimc; g.textAlign = 'right';
      g.fillText(ASSET_ZH[S.axes.asset[r]] || S.axes.asset[r], PAD.l - 8,
        PAD.t + r * box.th + box.th / 2);
    }
    g.textAlign = 'center';
    for (let c = 0; c < box.cols; c++) {
      g.fillStyle = dimc;
      g.fillText(AGE_ZH[S.axes.age[c]] || S.axes.age[c], PAD.l + c * box.tw + box.tw / 2, H - 12);
    }
    g.save();
    g.translate(14, PAD.t + (H - PAD.t - PAD.b) / 2);
    g.rotate(-Math.PI / 2);
    g.fillStyle = dimc; g.textAlign = 'center';
    g.fillText(`每格自上而下：${RISK_ZH.fragile} / ${RISK_ZH.typical} / ${RISK_ZH.tolerant}`, 0, 0);
    g.restore();

    /* which agents are in focus: a selected post dims everyone who never saw it */
    let focus = null;
    if (S.sel && S.sel.post) {
      focus = new Set();
      for (const [aid, seen] of st.seenBy) if (seen.has(String(S.sel.post))) focus.add(aid);
    }

    /* institution nodes — a published-today pulse uses the NEUTRAL emphasis, not gold:
       gold belongs to the TV arm and nothing else */
    const published = new Set(model.byDay[S.day].post.map((r) => r.org));
    for (const [org, p] of S.orgPos) {
      const pulsing = published.has(org) && S.progress < 0.35;
      g.fillStyle = pulsing ? cssVar('--accent-ui', '#cdd7e8') : cssVar('--surface-2', '#1b2435');
      g.strokeStyle = line;
      const s = pulsing ? 15 : 12;
      g.beginPath(); g.rect(p.x - s / 2, p.y - s / 2, s, s); g.fill(); g.stroke();
      g.fillStyle = pulsing ? ink : dimc;
      g.textAlign = 'left';
      // clamp the label so a long institution name cannot run off the canvas
      const room = W - (p.x + 12) - 4;
      g.fillText(clip(g, org, room), p.x + 12, p.y);
    }

    /* exposure lines, as a wave */
    const imps = S.sel && S.sel.post
      ? st.imps.filter((r) => String(r.p) === String(S.sel.post))
      : st.imps;
    const cut = Math.floor(imps.length * Math.min(1, Math.max(0, (S.progress - 0.1) / 0.55)));
    g.lineWidth = 1;
    for (let k = Math.max(0, cut - 260); k < cut; k++) {
      const r = imps[k];
      const to = S.layout.get(String(r.i));
      const from = S.orgPos.get(st.postOrg.get(String(r.p)));
      if (!to || !from) continue;
      g.globalAlpha = 0.06 + 0.3 * (1 - (cut - k) / 260);
      g.strokeStyle = model.armColor[r.arm] || dimc;
      g.beginPath(); g.moveTo(from.x, from.y); g.lineTo(to.x, to.y); g.stroke();
    }
    g.globalAlpha = 1;

    /* degraded strips first, so dots draw over their block */
    for (const strip of S.strips) {
      if (!strip.block) continue;
      const eng = strip.list.filter((p) => st.engaged.has(String(p.id))).length;
      const share = strip.list.length ? eng / strip.list.length : 0;
      g.fillStyle = cssVar('--surface-3', '#212c40');
      g.fillRect(strip.x, strip.y, strip.w, strip.h);
      if (share > 0) {
        // Density encoding, not an arm — arm colors belong to arms only (contract §4).
        g.globalAlpha = 0.25 + 0.6 * share;
        g.fillStyle = cssVar('--accent-ui-dim', '#6d7d99');
        g.fillRect(strip.x, strip.y, strip.w, strip.h);
        g.globalAlpha = 1;
      }
      g.strokeStyle = line; g.strokeRect(strip.x + 0.5, strip.y + 0.5, strip.w - 1, strip.h - 1);
      g.fillStyle = ink; g.textAlign = 'center';
      g.font = '10px "JetBrains Mono", monospace';
      g.fillText(`${strip.list.length} 人 · 互动 ${Math.round(share * 100)}%`,
        strip.x + strip.w / 2, strip.y + strip.h / 2);
      g.font = '11px system-ui, -apple-system, "PingFang SC", sans-serif';
    }

    /* agents */
    for (const [id, p] of S.layout) {
      if (p.block >= 0) continue;             // inside a degraded strip
      const a = model.agents.get(id);
      if (!a) continue;
      const col = model.armColor[a.arm] || dimc;
      g.globalAlpha = (focus && !focus.has(id)) ? 0.16 : 1;
      const R = p.r || 3.4;

      g.beginPath(); g.arc(p.x, p.y, R, 0, Math.PI * 2);
      g.fillStyle = col; g.fill();

      if (S.progress > 0.55 && st.engaged.has(id)) {
        g.beginPath(); g.arc(p.x, p.y, R + 2.6, 0, Math.PI * 2);
        g.strokeStyle = col; g.lineWidth = 1.2; g.stroke();
      }
      if (S.progress > 0.6 && st.commented.has(id)) {
        const c = st.commented.get(id);
        g.fillStyle = cssVar(STANCE_COLOR[c && c.stance] || '--ink-dim', dimc);
        g.fillRect(p.x - 1.2, p.y - (R + 5.5), 2.4, 4);
      }
      if (S.progress > 0.75 && st.checkoutOf.has(id)) {
        const oc = st.checkoutOf.get(id);
        const c = oc === 'confirm_signed' ? cssVar('--signal', '#e8823c')
          : (oc === 'confirm_declined' || oc === 'purchase_blocked' || oc === 'hard_block')
            ? cssVar('--stop', '#c9536b')
            : cssVar('--ok', '#4fa981');
        g.beginPath(); g.arc(p.x, p.y, R + 5.2, 0, Math.PI * 2);
        g.strokeStyle = c; g.lineWidth = 1.8; g.stroke();
        if (oc === 'confirm_declined') {
          const d = R;
          g.beginPath();
          g.moveTo(p.x - d, p.y - d); g.lineTo(p.x + d, p.y + d);
          g.moveTo(p.x + d, p.y - d); g.lineTo(p.x - d, p.y + d);
          g.stroke();
        }
      }
      if (S.progress > 0.85 && st.traded.has(id)) {
        g.beginPath(); g.arc(p.x, p.y, R, 0, Math.PI * 2);
        g.fillStyle = cssVar('--ok', '#4fa981'); g.fill();
      }
      if (S.sel && S.sel.agent === id) {
        g.beginPath(); g.arc(p.x, p.y, R + 6, 0, Math.PI * 2);
        g.strokeStyle = ink; g.lineWidth = 1.4; g.stroke();
      }
      g.globalAlpha = 1;
    }
  }

  function clip(g, text, room) {
    const s = String(text || '');
    if (room <= 8) return '';
    if (g.measureText(s).width <= room) return s;
    let out = s;
    while (out.length > 1 && g.measureText(out + '…').width > room) out = out.slice(0, -1);
    return out + '…';
  }

  /* ---------- the sweep ------------------------------------------------- */

  function animate() {
    cancelAnimationFrame(S.raf);
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) { S.progress = 1; draw(); return; }
    S.progress = 0;
    const t0 = performance.now();
    const stepFrame = (ts) => {
      if (S.destroyed) return;
      S.progress = Math.min(1, (ts - t0) / 700);
      draw();
      if (S.progress < 1) S.raf = requestAnimationFrame(stepFrame);
    };
    S.raf = requestAnimationFrame(stepFrame);
  }

  /* ---------- picking --------------------------------------------------- */

  function onClick(e) {
    if (!S.pick) return;
    const rect = canvas.getBoundingClientRect();
    // the backing store is DPR-scaled but the layout math is in CSS pixels, so map the
    // click through the element's own box, never through canvas.width
    const x = (e.clientX - rect.left) * (S.w / rect.width);
    const y = (e.clientY - rect.top) * (S.h / rect.height);
    let best = null, bestD = 14 * 14;
    for (const [id, p] of S.layout) {
      if (p.block >= 0) continue;
      const d = (p.x - x) ** 2 + (p.y - y) ** 2;
      if (d < bestD) { bestD = d; best = id; }
    }
    S.pick(best ? { agent: best } : null);
  }

  /* ---------- legend ---------------------------------------------------- */

  function renderLegend() {
    if (!legendEl) return;
    const sw = (color, label, cls = '') =>
      `<span class="k"><i class="${cls}" style="background:${color}"></i>${label}</span>`;
    const parts = model.arms.map((a) => sw(model.armColor[a] || dim(), `${a} 臂`));
    parts.push('<span class="k"><i style="background:transparent;'
      + `box-shadow:0 0 0 1.2px ${cssVar('--ink-dim', '#8593ac')}"></i>外圈：当日有点赞/收藏/关注</span>`);
    // Source legend wording from vocab.js so the field and the checkout tables never drift apart.
    parts.push(`<span class="k"><i class="sq" style="background:${cssVar('--ok', '#4fa981')}"></i>评论${STANCE_ZH.bullish}</span>`);
    parts.push(`<span class="k"><i class="sq" style="background:${cssVar('--stop', '#c9536b')}"></i>评论${STANCE_ZH.bearish}</span>`);
    parts.push('<span class="k"><i style="background:transparent;'
      + `box-shadow:0 0 0 1.6px ${cssVar('--signal', '#e8823c')}"></i>${OC_SHORT_ZH.confirm_signed}</span>`);
    parts.push('<span class="k"><i style="background:transparent;'
      + `box-shadow:0 0 0 1.6px ${cssVar('--stop', '#c9536b')}"></i>${OC_SHORT_ZH.confirm_declined}</span>`);
    if (S.strips.some((s) => s.block)) {
      // Swatch mirrors the canvas: mid-density UI accent veiled over surface-3.
      const veil = `color-mix(in srgb, ${cssVar('--accent-ui-dim', '#6d7d99')} 55%, transparent)`;
      parts.push(`<span class="k"><i class="sq" style="background-image:linear-gradient(${veil},${veil});background-color:${cssVar('--surface-3', '#212c40')}"></i>`
        + '人数过多的带已折叠为方块，深浅表示互动比例</span>');
    }
    legendEl.innerHTML = parts.join('');
  }

  /* ---------- wiring ---------------------------------------------------- */

  const ro = ('ResizeObserver' in window) ? new ResizeObserver(() => {
    resize();
    renderLegend();
  }) : null;
  if (ro) ro.observe(canvas.parentElement);
  else window.addEventListener('resize', resize);
  canvas.addEventListener('click', onClick);

  resize();
  renderLegend();

  return {
    setDay(i) {
      if (i === S.day && S.progress >= 1) return;
      S.day = i;
      animate();
    },
    setSelection(sel) { S.sel = sel || null; draw(); },
    render() { draw(); },
    onPick(cb) { S.pick = cb; },
    destroy() {
      S.destroyed = true;
      cancelAnimationFrame(S.raf);
      if (ro) ro.disconnect(); else window.removeEventListener('resize', resize);
      canvas.removeEventListener('click', onClick);
    },
  };
}

/* FlowMirror viewer — the shared display vocabulary.

   Every map here turns an engine value into the Chinese a reader sees. They live in one
   file for two reasons.

   The axis ORDERS are layout decisions: the population field, the heatmap's row order
   and the agent picker must sort identically or the three stop agreeing with each other.

   And a gloss that drifts between two pages is a quiet way to mislead. Before this file
   existed, `purchase_blocked` read "暂停申购" on the replay page and "申购被拦截" on the
   investor page — two different claims about the same event — and `confirm_signed` had
   three different wordings across three modules. Tables and legends do legitimately
   want different lengths, so the fix is not one string for everything: each concept has
   a SHORT label for table cells and a FULL gloss for legends and tooltips, and both are
   canonical here.

   Add a value here, never in a page module. */

/* ---------- population axes: the order IS the layout ------------------- */

export const AGE_ORDER = ['under_30', '30_45', '45_60', 'over_60'];
export const ASSET_ORDER = ['low', 'mid', 'high'];
export const RISK_ORDER = ['fragile', 'typical', 'tolerant'];

export const AGE_ZH = {
  under_30: '30 岁以下', '30_45': '30–45 岁', '45_60': '45–60 岁', over_60: '60 岁以上',
};
export const ASSET_ZH = { low: '低资产', mid: '中资产', high: '高资产' };
/** short: the field's axis caption, where the surrounding label already says 风险 */
export const RISK_ZH = { fragile: '脆弱', typical: '一般', tolerant: '耐受' };
/** long: a table cell or a chip standing on its own */
export const RISK_LONG_ZH = { fragile: '风险脆弱', typical: '风险一般', tolerant: '风险耐受' };

/* ---------- creative intent ------------------------------------------- */

export const INTENT_ZH = { I1: '品牌推广', I2: '推品转化', I3: '投资教育' };
export const IG_ZH = { I2: '推品组', nonI2: '非推品组' };

/* ---------- the modality arms ----------------------------------------- */

export const ARM_ZH = { T: '纯文本', TC: '图片文字化', TV: '真实图片' };
export const ARM_LONG_ZH = {
  T: '纯文本（配图不展示）',
  TC: '图片文字化（OCR 文本 + 冻结的中性描述，不给像素）',
  TV: '真实图片（像素直接进入 agent 的输入）',
};

/* ---------- suitability outcomes --------------------------------------
   The mechanism this sandbox exists to study, so the glosses say what happened to the
   ORDER rather than naming a rule. Keys match _VALID_OC in flowmirror/engine/loop.py. */

/** short: table cells, tags, the field legend */
export const OC_SHORT_ZH = {
  match: '适当性匹配',
  confirm_signed: '已签署确认书',
  confirm_declined: '拒签确认书',
  purchase_blocked: '当日停购',
  hard_block: '硬性拦截',
  below_min: '低于起购',
  no_holdings: '无持仓可赎',
};
/** full: legends, tooltips, the home page's vocabulary table */
export const OC_ZH = {
  match: '风险匹配，直接成交',
  confirm_signed: '风险不匹配，签署确认书后成交',
  confirm_declined: '风险不匹配，放弃申购',
  purchase_blocked: '该基金当日停购，无法申购',
  hard_block: '规则硬性拦截',
  below_min: '低于起购金额',
  no_holdings: '无持仓，赎回被拒',
};

/** Only these outcomes get a reserved colour. Everything else stays neutral. */
export const OC_COLOR = {
  match: '--ok',
  confirm_signed: '--signal',
  confirm_declined: '--stop',
  purchase_blocked: '--stop',
  hard_block: '--stop',
  below_min: '--stop',
  no_holdings: '--stop',
};

/* ---------- actions, clicks, stances, climate ------------------------- */

export const ACT_ZH = { subscribe: '申购', redeem: '赎回', dca: '定投', hold: '持有' };
export const CLICK_ZH = { to_checkout: '进入结账', click_no_landing: '无落地页' };

export const STANCE_ZH = {
  bullish: '看多', bearish: '看空', watching: '观望',
  no_comment: '未评论', neutral: '中性',
};
export const STANCE_COLOR = { bullish: '--ok', bearish: '--stop', watching: '--ink-dim' };

export const CLIM_ZH = {
  no_signal: '无口风信号',
  bullish_majority: '多数看多',
  bearish_majority: '多数看空',
  mixed: '多空混杂',
  neutral: '中性',
};

/* ---------- decision parser statuses ---------------------------------- */

export const STATUS_ZH = {
  ok: '已解析',
  no_json_object: '未返回 JSON 对象',
  schema_invalid: '结构不合规',
  empty: '空响应',
  error: '调用出错',
  exception: '调用异常',
  http_error: 'HTTP 错误',
  empty_response: '空响应',
  reasoning_salvage_rejected: '推理残片不可用',
};

/** transport vs model: the split that stops a bad key looking like a bad model */
export const FAILURE_KIND_ZH = {
  transport: '传输失败（提供方不可达、限流或凭据问题）',
  model: '模型失败（有响应但解析不出结构）',
};

/* ---------- helpers ---------------------------------------------------- */

/** Sort agents the way the field lays them out, so the heatmap and pickers agree. */
export function cellSort(a, b) {
  const ai = AGE_ORDER.indexOf(a.age) - AGE_ORDER.indexOf(b.age);
  if (ai) return ai;
  const si = ASSET_ORDER.indexOf(a.asset) - ASSET_ORDER.indexOf(b.asset);
  if (si) return si;
  const ri = RISK_ORDER.indexOf(a.risk) - RISK_ORDER.indexOf(b.risk);
  if (ri) return ri;
  return String(a.id).localeCompare(String(b.id));
}

/** A CSS custom property's current value, with a fallback for a canvas context. */
export function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  } catch { return fallback; }
}

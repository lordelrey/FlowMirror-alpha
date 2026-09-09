"""flowmirror.agents.prompt -- prompt assembly and response parsing for live LLM agents.

Pure text/JSON work: no network calls, no cache, no threading (those live in
flowmirror.agents.runtime); the only filesystem access is reading local card images.

agent_view (engine-built; every pct/return number is a FRACTION, 0.052 -> "+5.2%"):
persona_card_zh_rich, market_view/risk_mood 1-5, last_reflection, memory (list[str],
oldest first), c_class, cash, holdings ([{code,name,r,units,nav,pnl_pct}]), last_trade
(str or {d,act,code,amt}), declined_confirms (list[str]), familiarity ({org:0|1|2}),
index_5d, holdings_1d, guba ({code:{name,mult,bull_ratio}}), trend ({code:{name,ret_1w,
ret_1m,ret_3m,mdd,pos}}), direct (list[str]). feed_cards carry post_id/org/title/caption/
landing({code,name,R,ret_3m,ret_1y,min_buy}|None)/likes/arm("T"|"TC"|"TV")/image_path/
image_sha/comments_prev (<=3 x {stance,text,fam_phrase})/climate plus n_comments_prev
(int: the TOTAL day-(t-1) comment count shown in the social header; when absent the
header falls back to the excerpt count, reproducing the original bytes).  TC cards
additionally carry ocr_masked|ocr_text (the note's OCR text, hard-capped at 200 chars
in the prompt) and image_caption_frozen (str or list[str], concatenated in order).
TV cards attach image_path as a base64 data-URI; TC and T cards never attach pixels.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # console accepts CJK; printed text stays ASCII

from flowmirror.io.hashing import sha256_file, sha256_text

# ---------------------------------------------------------------------------
# VERBATIM reuse from the sim/elicit_base.py excerpt (v7 rule: copy, never import it).
# ---------------------------------------------------------------------------
FUND_TYPE_ZH = {
    "R2": "一只偏债混合型公募基金（产品风险等级 R2）",
    "R3": "一只偏股混合型公募基金（产品风险等级 R3）",
    "R4": "一只行业主题股票型公募基金（产品风险等级 R4）",
}
FUND_ZH = "基金档案：{ftype}，近3个月收益 {r3m}，近1年收益 {r1y}，规模适中，费率与同类持平。"
# GATE_MANIFEST v1.2: three familiarity levels (0 = no block; 1 = seen a few posts; 2 = follows)
FAMILIARITY_ZH = {1: "你之前在平台上刷到过这家基金公司的几条帖子，但没有关注它。",
                  2: "你之前在平台上看过这家基金公司发布的多条帖子，并关注了它的账号。"}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif"}


def image_data_url(path):
    """elicit_base image packing, logic kept verbatim: local file -> base64 data-URI."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in MIME:
        raise ValueError(f"unsupported image extension: {path}")
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{MIME[ext]};base64,{b64}"


def iter_json_objects(text):
    """Every COMPLETE top-level {...} substring, via a string-aware balanced-brace scan.

    R5.5 R2A forbids greedy first-brace-to-last-brace matching: a truncated tail must not be glued onto an earlier
    object, and a reasoning trace that merely contains `{` and `}` must not count as an answer. Unbalanced (i.e.
    truncated) trailing objects are simply never emitted."""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            if depth > 0:
                in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start:i + 1])
    return out


# ---------------------------------------------------------------------------
# Anti-priming guard and the fixed prompt texts (block order A-G is frozen).
# ---------------------------------------------------------------------------
ANTI_PRIMING_WORDS = ["适当性", "监管", "配图", "跟风", "羊群", "假设", "研究", "实验"]
# The spec mandates the literal arm-marker lines even though they contain the scanned
# word 配图: those platform-mechanic lines are sanctioned literals, stripped before
# scanning (T arm and the TC no-payload fallback are both intentional UI markers).
_SANCTIONED_LITERALS = ("配图不展示", "配图信息不可用")


def check_anti_priming(text, where="text"):
    scan = str(text)
    for lit in _SANCTIONED_LITERALS:
        scan = scan.replace(lit, "")
    for word in ANTI_PRIMING_WORDS:
        if word in scan:
            raise ValueError(f"anti-priming word {word!r} present in {where}")


PERSONA_FRAME_ZH = (
    "你在一个基金销售平台的 App 上有一个账户。平台像常见的理财社区一样：有信息流帖子、可以点赞收藏关注、"
    "可以在帖子下面留一句评论、也可以申购或赎回基金。你每天打开一次 App，看看今天刷到的内容，然后按自己的习惯"
    "处理——什么都不做也是很常见的选择。没有“正确答案”，也没有人在评价你的决定。\n\n"
    "回答时只输出一个 JSON 对象（键用英文，值中的文字用中文），不要输出其他内容。"
)

DECISION_SCHEMA_TEXT = (
    '{"reads": ["p1"], "engage": {"p1": ["like"]},\n'
    ' "comments": [{"post_id": "p3", "stance": "bullish", "text": "不超过40字"}],\n'
    ' "trade": {"action": "buy", "fund": "000001", "amount_pct": 20, "sign_mismatch_confirm": false},\n'
    ' "org_affinity_delta": {"某基金公司": 1}, "mood": 4, "reason": "不超过60字"}'
)

INSTR_V3 = """请以你的人设，按下面四个互相独立的步骤给出今天的决定；四步互不影响。

第一步（交易）：只依据基金档案、你的持仓、可投闲钱和你自己的习惯，决定是否申购或赎回基金。金额用百分比：申购=占可投现金的百分比，赎回=占该持仓的百分比；不交易则 action 填 "none"、amount_pct 填 0。若某基金的风险等级高于你测评等级的上限，平台会弹出《风险不匹配确认书》，你可以签署后继续申购，也可以放弃（sign_mismatch_confirm 相应填 true/false）。
第二步（浏览与互动）：与第一步互不影响。用 reads 列出你认真看过的帖子编号；用 engage 写出你对哪些帖子点赞、收藏或关注（取值 "like"、"save"、"follow"，可多选）。不互动完全正常。
第三步（评论）：想留言就给某条帖子写一句话评论（不超过40字，stance 用 "bullish"、"bearish" 或 "watching"）；没有想说的就 stance 填 "no_comment" 且 text 留空——大多数人大多数时候不留言。
第四步（感受）：org_affinity_delta 给出你对今天见到的各家机构好感的增减（-2 到 2 的整数）；mood 是你今天的心情（1-5）；reason 用一句话说明今天整体的想法（不超过60字）。

只输出一个 JSON 对象（键用英文，值中的文字用中文）：
""" + DECISION_SCHEMA_TEXT

# The example handle matches handle_of()'s six-hex-character shape.
INSTR_FOLLOW_ZH = (
    "\n补充：评论区的作者带有句柄（如 @u3f9a2b）。如果你想以后优先看到某个人的看法与动向，"
    "可以在 JSON 里加一个可选键 \"follow_users\"，值为句柄列表（例如 [\"@u3f9a2b\"]）；不关注任何人则不写这个键。"
)

RETRY_SUFFIX = "上次输出不是合法 JSON。请只输出一个符合下面 schema 的 JSON 对象："

REFLECTION_PROMPT_ZH = (
    "下面是你过去 5 个交易日在平台上的记录，以及你上一次的想法总结。请以这位投资者的视角，"
    "用不超过 120 字总结这 5 天你对市场和这些机构的看法有什么变化，并列出你现在相信的 1–3 条判断（每条 ≤30 字）。"
    "同时给出：你对后市的看法 1（很悲观）–5（很乐观）；你现在对亏损的承受心情 1（很怕亏）–5（不在意）。"
    "只输出 JSON：{\"summary\":\"…\",\"beliefs\":[\"…\"],\"market_view\":1-5,\"risk_mood\":1-5}"
)

# ---------------------------------------------------------------------------
# Formatting helpers and information-channel renderers (architecture 4.3). Each
# ENABLED channel's text is hashed into notes as channel_sha:<name>=<8hex>.
# ---------------------------------------------------------------------------
_MV_ZH = {1: "很悲观", 2: "偏悲观", 3: "中性", 4: "偏乐观", 5: "很乐观"}
_RM_ZH = {1: "很怕亏", 2: "比较怕亏", 3: "说不上怕不怕", 4: "比较能接受波动", 5: "不太在意短期亏损"}
_ACT_ZH = {"subscribe": "申购", "redeem": "赎回", "dca": "定投"}
_CLIMATE_ZH = {"bullish_majority": "多数看多", "mixed": "看法分歧", "bearish_majority": "多数看空"}
_STANCE_ZH = {"bullish": "看多", "bearish": "看空", "watching": "观望"}
_TREND_POS_ZH = {"high": "处于近半年高位", "mid": "处于近半年中位", "low": "处于近半年低位"}
_SENT_END = "。！？!?；;\n"


def _rlevel(v):
    s = str(v if v is not None else "").strip().upper()
    return ("R" + s) if s and not s.startswith("R") else s


def _pct(v):
    return f"{float(v or 0) * 100:+.1f}%"


def _pctu(v):
    return f"{abs(float(v or 0) * 100):.1f}%"


def _truncate_caption(text, limit=200):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    best = max((cut.rfind(ch) for ch in _SENT_END), default=-1)
    return (text[:best + 1] if best >= 0 else cut) + "…[展开]"


def render_belief(view):
    try:
        mv = max(1, min(5, int(view.get("market_view") or 3)))
        rm = max(1, min(5, int(view.get("risk_mood") or 3)))
    except (TypeError, ValueError):
        mv = rm = 3
    lines = [f"眼下你对后市的判断偏「{_MV_ZH[mv]}」；面对账户可能的亏损，你的心情是「{_RM_ZH[rm]}」。"]
    if view.get("last_reflection"):
        lines.append("你上次给自己的小结：" + str(view["last_reflection"]))
    # Beliefs from the last reflection feed into the next decision prompt only.
    # Agents that have not reflected carry no beliefs; absent, empty, or non-list
    # values render nothing so the surrounding text remains stable.
    bel = view.get("beliefs")
    if isinstance(bel, (list, tuple)):
        # parse_reflection already clamps to 3 x 30 chars; re-clamped here because
        # render_belief must hold for any other producer of the key too.
        kept = [s for s in (str(b).strip() for b in bel if isinstance(b, str)) if s][:3]
        if kept:
            # House convention for a list of sentences (render_experience / render_news):
            # one per line, no bullet or ordinal marker -- only render_social numbers items.
            lines.append("你现在相信的判断：")
            lines.extend(kept)
    return "\n".join(lines)


def render_experience(view):
    """experience channel: last trade outcome, holdings with floating P&L, declined-confirm memory."""
    lines = []
    lt = view.get("last_trade")
    if isinstance(lt, str) and lt.strip():
        lines.append("上一笔交易：" + lt)
    elif isinstance(lt, dict):
        act = _ACT_ZH.get(str(lt.get("act", "")), "交易")
        try:
            amt = f"{float(lt.get('amt') or 0):,.0f}"
        except (TypeError, ValueError):
            amt = "0"
        lines.append(f"上一笔交易：{act} {lt.get('code', '')}，金额 {amt} 元。")
    holdings = view.get("holdings") or []
    if holdings:
        for h in holdings:
            lines.append(f"{h.get('code', '?')} / {h.get('name', '?')} / {_rlevel(h.get('r'))} / "
                         f"{float(h.get('units') or 0):,.2f}份 × 净值{float(h.get('nav') or 0):.4f} / "
                         f"浮动盈亏 {_pct(h.get('pnl_pct'))}")
    else:
        lines.append("你目前没有持有任何基金。")
    declined = view.get("declined_confirms") or []
    if declined:
        lines.append(str(declined[-1]))
    return lines


def render_news(view):
    """news channel: index 5-day move, holdings 1-day move, guba exogenous lines (no coverage -> omitted).

    index_5d may come from a labelled benchmark proxy. Only its return is shown,
    never its level, and the configured label is preserved in user-facing text.

    A guba line states volume relative to the trailing baseline and adds a stance split
    ONLY when stance data exists -- which it does not until the labelling task runs.
    """
    lines = []
    # The benchmark subject comes from market.benchmark_label, never a hardcoded
    # name. loop.py requires a label whenever a benchmark is configured.
    idx_stem = str(view.get("index_label") or "").strip()
    for key, stem in ((("index_5d", idx_stem + "近五个交易日累计") if idx_stem else (None, None)),
                      ("holdings_1d", "你持有的基金昨日整体")):
        if key is None:
            continue
        v = view.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lines.append(f"{stem}{'上涨' if float(v) >= 0 else '下跌'} {_pctu(v)}。")
    guba = view.get("guba") or {}
    for code in sorted(guba):
        g = guba.get(code) or {}
        mult, ratio = g.get("mult"), g.get("bull_ratio")
        if not isinstance(mult, (int, float)) or isinstance(mult, bool):
            continue
        # mult is ratio_vs_baseline: a RATIO of this week's volume to the trailing
        # baseline, so 0.94 means slightly quieter than usual and 2.3 means busier.
        # It was rendered as "高 N 倍" (higher BY N times), which inverted the meaning
        # of every value below 1.0 -- most of the shipped signal file.
        head = f"近一周股吧里【{g.get('name') or code}】的讨论量约为上月均值的 {float(mult):.1f} 倍"
        # No stance data means no stance clause; never infer a neutral split.
        if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
            bull = int(round(float(ratio) * 10))
            lines.append(f"{head}，多空比约 {bull}:{10 - bull}。")
        else:
            lines.append(head + "。")
    return lines


def render_trend(view):
    """trend channel: one line per fund in agent_view['trend'] (engine-computed numbers only)."""
    lines = []
    trend = view.get("trend") or {}
    for code in sorted(trend):
        t = trend.get(code)
        if not isinstance(t, dict):
            continue
        pos = _TREND_POS_ZH.get(str(t.get("pos", "mid")), _TREND_POS_ZH["mid"])
        try:
            lines.append(f"【{t.get('name') or code}】近1周{_pct(t.get('ret_1w'))}、近1月{_pct(t.get('ret_1m'))}、"
                         f"近3月{_pct(t.get('ret_3m'))}，近3月最大回撤 {_pctu(t.get('mdd'))}，净值{pos}。")
        except (TypeError, ValueError):
            continue
    return lines


def render_following(agent_view):
    """block F2: what the people this agent follows did yesterday (engine-rendered
    sentences, lagged one day). '' when the social graph is off, so the prompt is
    byte-identical to a run without it."""
    lines = [str(x) for x in (agent_view.get("following_recent") or []) if str(x).strip()]
    if not lines:
        return ""
    return "你关注的人昨天：\n" + "\n".join(f"- {x}" for x in lines[:3])


def render_suggest_follow(agent_view):
    """block F3: the platform's "worth following" module -- the most-followed accounts as of t-1.

    This is the amplifier of the preferential-attachment loop: a real platform pushes accounts
    with many followers to everybody, which is how a few of them become influencers. The engine
    only fills the list when somebody actually has a follower, so the block is absent at cold
    start and the first follows must still come from the comment excerpts. No stance is shown --
    telling an agent that a popular account is bullish would prime it, which the anti-priming
    rule forbids. '' when the graph is off, so the prompt is byte-identical without it.
    """
    rows = [r for r in (agent_view.get("suggest_follow") or []) if isinstance(r, dict) and r.get("handle")]
    if not rows:
        return ""
    out = ["平台推荐关注（按粉丝数）："]
    for r in rows[:3]:
        out.append("- %s（粉丝 %d，昨日发言 %d 条）"
                   % (r["handle"], int(r.get("followers") or 0), int(r.get("n_cmt") or 0)))
    return "\n".join(out)


def render_social(card):
    """social channel (block F, per card): t-1 top comments + climate; '' when the label is no_signal.

    The header count is the TOTAL day-(t-1) comment count on the post when the card
    carries n_comments_prev (the loop supplies it from the t-1 comment list length,
    needed for comment-volume analysis); only the top-3 lines are ever shown. Cards without
    n_comments_prev (or with an inconsistent value below the excerpt count) fall
    back to the number of excerpted comments, which reproduces the original
    rendering byte-for-byte.
    """
    cps = card.get("comments_prev") or []
    label = str(card.get("climate") or card.get("climate_label") or "no_signal")
    # Authorship and the climate label are two different things. The no_signal gate exists so a
    # thin comment thread cannot manufacture a fake majority (climate_for's min_n=4 floor, kept
    # as is), but it was also swallowing the author handles that the social graph rides on: on a
    # 100-agent run only 22.4% of impressions carried this block, so agents were told to follow
    # handles they were never shown. When the graph is on (the engine attaches a handle to each
    # excerpted comment) the excerpt is rendered even without a label -- and the header then
    # states only the count, never a majority.
    has_handle = any(isinstance(c, dict) and c.get("handle") for c in cps)
    if not cps or (label == "no_signal" and not has_handle):
        return ""
    n = card.get("n_comments_prev")
    if isinstance(n, bool) or not isinstance(n, int) or n < len(cps):
        n = len(cps)
    out = [f"昨日评论（共 {n} 条）：" if label == "no_signal"
           else f"昨日评论（共 {n} 条，{_CLIMATE_ZH.get(label, '看法分歧')}）："]
    for i, c in enumerate(cps[:3]):
        c = c if isinstance(c, dict) else {}
        stance = _STANCE_ZH.get(str(c.get("stance", "")), "观望")
        who = f"{c['handle']}（粉丝 {int(c.get('followers') or 0)}）：" if c.get("handle") else ""
        # Say so when a line is here because the agent follows its author: an invisible effect is
        # no effect, and the agent needs to see that following changed what it was shown.
        tag = "（你关注的）" if c.get("followed") else ""
        out.append(f"{'①②③'[i]} {tag}{who}“{c.get('text', '')}” —{stance} · {c.get('fam_phrase', '')}")
    return "\n".join(out)


def render_direct(view):
    """direct channel stub: institution push message lines supplied verbatim by the engine."""
    return [str(x) for x in (view.get("direct") or [])]


# ---------------------------------------------------------------------------
# Feed cards (block F). Feed order is itself part of the treatment: never shuffle.
# ---------------------------------------------------------------------------
def _tc_image_text(card):
    """TC-arm text payload describing the image without pixels; None when unavailable.

    ocr_masked wins over ocr_text; whichever is used is hard-capped at 200 chars
    by design. image_caption_frozen may be a str or a list[str]; list items
    are concatenated in order.  Returns None when neither source exists, in which
    case render_card() writes 配图信息不可用。 and build_decision_messages()
    records the tc_no_caption degradation note.
    """
    ocr = ""
    for key in ("ocr_masked", "ocr_text"):
        v = card.get(key)
        if isinstance(v, str) and v.strip():
            ocr = v[:200]
            break
    cap_raw = card.get("image_caption_frozen")
    if isinstance(cap_raw, str):
        cap = cap_raw
    elif isinstance(cap_raw, (list, tuple)):
        cap = "".join(x for x in cap_raw if isinstance(x, str))
    else:
        cap = ""
    if not cap.strip():
        cap = ""
    if not ocr and not cap:
        return None
    return ocr + cap


def render_card(card, social_on=True):
    lines = [f"【{card.get('post_id', '')}】机构：{card.get('org', '')} ｜ 热度：{card.get('likes', 0)} 赞",
             "标题：" + str(card.get("title", "")),
             "正文：" + _truncate_caption(str(card.get("caption", "")))]
    landing = card.get("landing")
    if isinstance(landing, dict):
        rl = _rlevel(landing.get("R"))
        ftype = FUND_TYPE_ZH.get(rl, f"一只公募基金（产品风险等级 {rl}）")
        lines.append("关联基金：" + str(landing.get("name", "")) + "（" + str(landing.get("code", "")) + "）。"
                     + FUND_ZH.format(ftype=ftype, r3m=_pct(landing.get("ret_3m")),
                                      r1y=_pct(landing.get("ret_1y")))
                     + f"起购金额 {landing.get('min_buy', 0)} 元。")
    arm = str(card.get("arm") or "T")
    if arm == "T":
        lines.append("配图不展示。")
    elif arm == "TC":
        # No pixels: the image is conveyed by the note's OCR
        # text (ocr_masked preferred, <=200 chars) plus the frozen caption.
        payload = _tc_image_text(card)
        lines.append(("图片信息（文字）：" + payload) if payload is not None else "配图信息不可用。")
    if social_on:
        social = render_social(card)
        if social:
            lines.append(social)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decision prompt assembly (blocks A-G in FIXED order).
# ---------------------------------------------------------------------------
def build_decision_messages(agent_view, feed_cards, cfg):
    """Compose blocks A-G -> (messages, prompt_sha, image_shas, notes).

    messages is OpenAI chat format (system + one user message whose content is a list of
    text/image parts; a TV image part sits right after its card text part). prompt_sha
    hashes the concatenated TEXT of all blocks (base64 payloads excluded). notes records
    image degradations ("image_missing"/"image_unsupported"), the TC-arm degradation
    "tc_no_caption" (TC card with neither OCR text nor frozen caption),
    and one "channel_sha:<name>=<8hex>" line per ENABLED channel.  Images are attached
    ONLY for arm == "TV"; T and TC cards are text-only by construction.
    """
    cfg = cfg or {}
    channels = dict(cfg.get("channels") or {})
    feed_cards = list(feed_cards or [])
    social_on = bool(channels.get("social", True)) and bool(cfg.get("social"))
    notes, image_shas = [], []
    orgs_today = list(dict.fromkeys(str(c.get("org")) for c in feed_cards if c.get("org")))

    exp_lines = render_experience(agent_view) if channels.get("experience", True) else None
    news_lines = render_news(agent_view) if channels.get("news", True) else None
    trend_lines = render_trend(agent_view) if channels.get("trend", True) else None
    direct_lines = render_direct(agent_view) if channels.get("direct", True) else None
    social_texts = [render_social(c) for c in feed_cards] if social_on else None

    sys_text = (str(agent_view.get("persona_card_zh_rich") or "") + "\n\n" + PERSONA_FRAME_ZH).strip()
    d_lines = [f"你的风险测评等级：{agent_view.get('c_class') or 'C2'}。",
               f"账户可投闲钱：{float(agent_view.get('cash') or 0):,.0f} 元。"]
    if agent_view.get("fee_notice"):
        d_lines.append(str(agent_view["fee_notice"]))
    if exp_lines is not None:
        d_lines.extend(exp_lines)
    fam = agent_view.get("familiarity") or {}
    for org in orgs_today:
        lv = int(fam.get(org) or 0)
        if lv in FAMILIARITY_ZH:
            d_lines.append(FAMILIARITY_ZH[lv])
    mem = [str(x) for x in (agent_view.get("memory") or [])]
    c_text = "最近五个交易日的记录（从早到晚）：\n" + ("\n".join(mem) if mem else "（这几天没有特别的事。）")
    e_lines = list(news_lines or []) + list(trend_lines or [])
    head = "\n\n".join(t for t in (render_belief(agent_view), c_text, "\n".join(d_lines),
                                   "\n".join(e_lines), render_following(agent_view),
                                   render_suggest_follow(agent_view)) if t)

    check_anti_priming(sys_text, "block A (persona + frame)")   # our framing, not data
    messages = [{"role": "system", "content": sys_text}]
    parts, sha_blocks = [], [sys_text]

    def add_text(where, text, check=True):
        # Anti-priming guards OUR framing/instructions. Real creatives and real prior comments are data:
        # a marketing note may legitimately say 跟风 or 监管, and censoring it would alter the stimulus.
        if check:
            check_anti_priming(text, where)
        parts.append({"type": "text", "text": text})
        sha_blocks.append(text)

    # The anti-priming guard is for OUR words. Block B (belief: last_reflection, beliefs) and
    # block C (memory: the agent's own prior comments and reasons) are the agent's words -- an
    # investor who wrote 研究 in yesterday's comment must be allowed to read it back today; the
    # live 100x12 pilot died on exactly that on day 2. `head` bytes are untouched (prompt_sha
    # unchanged); only the scan targets move to the blocks we author.
    check_anti_priming("\n".join(d_lines), "block D (account / experience)")
    check_anti_priming("\n".join(e_lines), "block E (news / trend)")
    check_anti_priming(render_following(agent_view), "block following (social graph)")
    add_text("blocks B-E", head, check=False)
    for card in feed_cards:
        add_text("card:" + str(card.get("post_id")), render_card(card, social_on), check=False)
        arm = str(card.get("arm") or "T")
        if arm == "TC" and _tc_image_text(card) is None:
            notes.append("tc_no_caption")
        if arm != "TV":
            continue
        ipath = str(card.get("image_path") or "")
        exists = bool(ipath) and os.path.isfile(ipath)
        attached = False
        if exists and os.path.splitext(ipath)[1].lower() in MIME:
            try:
                url = image_data_url(ipath)
                attached = True
            except (OSError, ValueError):
                attached = False
        if attached:
            parts.append({"type": "image_url", "image_url": {"url": url}})
            image_shas.append(sha256_file(ipath))
        else:
            notes.append("image_unsupported" if exists else "image_missing")
    if direct_lines:
        add_text("direct", "\n".join(direct_lines))
    add_text("instr G", INSTR_V3 + (INSTR_FOLLOW_ZH if agent_view.get("social_graph_on") else ""))
    messages.append({"role": "user", "content": parts})

    for name, body in (("experience", exp_lines), ("news", news_lines), ("trend", trend_lines),
                       ("social", social_texts), ("direct", direct_lines)):
        if body is not None:
            notes.append("channel_sha:%s=%s" % (name, sha256_text("\n".join(body))[:8]))
    return messages, sha256_text("\n".join(sha_blocks)), image_shas, notes


# ---------------------------------------------------------------------------
# Decision schema + parser. The ENGINE enforces feasibility; parse_decision only
# NORMALIZES (never asks the model to self-check).
# ---------------------------------------------------------------------------
VALID_STANCES = ("bullish", "bearish", "watching", "no_comment")
VALID_ACTIONS = ("buy", "redeem", "none")
VALID_ENGAGE = ("like", "save", "follow")
_REQUIRED = ("reads", "engage", "comments", "trade", "org_affinity_delta", "mood", "reason")


def parse_decision(obj, shown_pids, shown_codes, held_codes, shown_orgs, visible_handles=()):
    """-> (normalized | None, violations). Structural failures -> (None, ["schema:<detail>"])."""
    shown_pids, shown_codes = set(shown_pids), set(shown_codes)
    held_codes, shown_orgs = set(held_codes), set(shown_orgs)
    if not isinstance(obj, dict):
        return None, ["schema:not_a_dict"]
    for key in _REQUIRED:
        if key not in obj:
            return None, ["schema:missing:" + key]
    for key, want in (("reads", list), ("engage", dict), ("comments", list), ("trade", dict),
                      ("org_affinity_delta", dict)):
        if not isinstance(obj[key], want):
            return None, ["schema:" + key]
    if not all(isinstance(x, str) for x in obj["reads"]):
        return None, ["schema:reads"]
    mood = obj["mood"]
    if isinstance(mood, bool) or not isinstance(mood, (int, float)):
        return None, ["schema:mood"]
    if not isinstance(obj["reason"], str):
        return None, ["schema:reason"]
    violations = []

    reads = [p for p in obj["reads"] if p in shown_pids]
    violations += ["unknown_pid"] * (len(obj["reads"]) - len(reads))
    reads = list(dict.fromkeys(reads))
    engage = {}
    for pid, acts in obj["engage"].items():
        if pid not in shown_pids:
            violations.append("unknown_pid")
            continue
        if isinstance(acts, str):
            acts = [acts]
        if not isinstance(acts, list):
            violations.append("schema:engage_value")
            continue
        keep = [a for a in acts if a in VALID_ENGAGE]
        if keep:
            engage[pid] = list(dict.fromkeys(keep))
    comments = []
    for c in obj["comments"]:
        if not isinstance(c, dict) or not isinstance(c.get("post_id"), str):
            violations.append("schema:comment_entry")
            continue
        pid, stance, text = c["post_id"], c.get("stance"), c.get("text", "")
        if pid not in shown_pids:
            violations.append("unknown_pid")
            continue
        if not isinstance(text, str):
            text = ""
        if stance == "no_comment":
            text = ""
        elif stance in VALID_STANCES:
            if len(text) > 40:
                text = text[:40]
                violations.append("comment_too_long")
        else:
            stance, text = "no_comment", ""
            violations.append("bad_stance")
        comments.append({"post_id": pid, "stance": stance, "text": text})
    if len(comments) > 2:
        violations.append("too_many_comments")
        comments = comments[:2]

    trade = obj["trade"]
    action, fund = trade.get("action"), trade.get("fund")
    if fund is not None and not isinstance(fund, str):
        fund = str(fund)
    if action not in VALID_ACTIONS:
        action = "none"
        violations.append("bad_action")
    amount = trade.get("amount_pct")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None, ["schema:amount_pct"]
    if "sign_mismatch_confirm" not in trade:
        return None, ["schema:missing:sign_mismatch_confirm"]
    if action == "redeem" and (fund is None or fund not in held_codes):
        action = "none"
        violations.append("nonholder_redeem")
    if action == "buy" and (fund is None or fund not in shown_codes):
        action = "none"
        violations.append("fund_not_in_scope")
    out_trade = {"action": action, "fund": fund,
                 "amount_pct": min(100.0, max(0.0, float(amount))),
                 "sign_mismatch_confirm": bool(trade["sign_mismatch_confirm"])}
    affinity = {}
    for org, delta in obj["org_affinity_delta"].items():
        if org not in shown_orgs:
            violations.append("unknown_org")
        elif isinstance(delta, bool) or not isinstance(delta, (int, float)):
            violations.append("schema:affinity_value")
        else:
            affinity[str(org)] = max(-2, min(2, int(round(float(delta)))))
    follow_users = []
    raw_fu = obj.get("follow_users")
    if raw_fu is not None:
        if not isinstance(raw_fu, list):
            violations.append("schema:follow_users")
        else:
            vis = set(visible_handles or ())
            for h in raw_fu:
                if isinstance(h, str) and h in vis:
                    if h not in follow_users:
                        follow_users.append(h)
                else:
                    violations.append("unknown_handle")
    return {"reads": reads, "engage": engage, "comments": comments, "trade": out_trade,
            "org_affinity_delta": affinity, "mood": max(1, min(5, int(round(float(mood))))),
            "reason": obj["reason"][:60], "follow_users": follow_users}, violations


def extract_decision(text, shown_pids, shown_codes, held_codes, shown_orgs, channel="content",
                     visible_handles=()):
    """-> (normalized | None, violations, parser_status in {ok, no_json_object,
    schema_invalid, ambiguous_reasoning}). channel="content" takes the FIRST complete
    object that parses; channel="reasoning" accepts ONLY a single complete object that
    parses (mirrors elicit_base.extract_answer)."""
    cands = iter_json_objects(text or "")
    if not cands:
        return None, [], "no_json_object"
    if channel == "reasoning":
        if len(cands) != 1:
            return None, ["ambiguous_reasoning"], "ambiguous_reasoning"
        try:
            obj = json.loads(cands[0])
        except Exception:
            return None, ["schema:json_loads"], "schema_invalid"
        norm, viol = parse_decision(obj, shown_pids, shown_codes, held_codes, shown_orgs,
                                    visible_handles=visible_handles)
        return (norm, viol, "ok") if norm is not None else (None, viol, "schema_invalid")
    loaded = False
    last_viol = []
    for cand in cands:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        loaded = True
        norm, viol = parse_decision(obj, shown_pids, shown_codes, held_codes, shown_orgs,
                                    visible_handles=visible_handles)
        if norm is not None:
            return norm, viol, "ok"
        last_viol = viol or last_viol
    # Surface the parser's own reason (e.g. "schema:reads") so a failed decision is diagnosable
    # in the dec event instead of collapsing every type error into one opaque label.
    if loaded:
        return None, (last_viol or ["schema:no_valid_object"]), "schema_invalid"
    return None, ["schema:json_loads"], "schema_invalid"


# ---------------------------------------------------------------------------
# Reflection (every reflection_every_days trading days).
# ---------------------------------------------------------------------------
def build_reflection_messages(agent_view):
    """-> (messages, prompt_sha) for the 5-day reflection question."""
    mem = [str(x) for x in (agent_view.get("memory") or [])][-5:]
    parts = []
    if mem:
        parts.append("你过去 5 个交易日的记录（从早到晚）：\n" + "\n".join(mem))
    if agent_view.get("last_reflection"):
        parts.append("你上一次的想法总结：" + str(agent_view["last_reflection"]))
    user = ("\n\n".join(parts) + "\n\n" if parts else "") + REFLECTION_PROMPT_ZH
    sys_text = str(agent_view.get("persona_card_zh_rich") or "")
    messages = [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]
    return messages, sha256_text(sys_text + "\n\n" + user)


def parse_reflection(obj):
    """-> (dict | None, violations); clamps views to [1,5], summary to 120, beliefs to 3 x 30 chars."""
    if not isinstance(obj, dict):
        return None, ["schema:not_a_dict"]
    for key in ("summary", "beliefs", "market_view", "risk_mood"):
        if key not in obj:
            return None, ["schema:missing:" + key]
    if not isinstance(obj["summary"], str):
        return None, ["schema:summary"]
    if not isinstance(obj["beliefs"], list) or not all(isinstance(b, str) for b in obj["beliefs"]):
        return None, ["schema:beliefs"]
    for key in ("market_view", "risk_mood"):
        if isinstance(obj[key], bool) or not isinstance(obj[key], (int, float)):
            return None, ["schema:" + key]
    return {"summary": obj["summary"][:120], "beliefs": [b[:30] for b in obj["beliefs"]][:3],
            "market_view": max(1, min(5, int(round(float(obj["market_view"]))))),
            "risk_mood": max(1, min(5, int(round(float(obj["risk_mood"])))))}, []


# ---------------------------------------------------------------------------
# Self-test (python -m flowmirror.agents.prompt --self-test) -- zero API access.
# ---------------------------------------------------------------------------
def _self_test():
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), str(detail)))

    tmp = tempfile.mkdtemp(prefix="fm_prompt_st_")
    jpg = os.path.join(tmp, "tiny.jpg")
    with open(jpg, "wb") as fh:
        fh.write(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9"))  # minimal JFIF
    landing = {"code": "000001", "name": "华夏稳健混合", "R": "R3", "ret_3m": 0.052, "ret_1y": 0.113, "min_buy": 10}
    landing2 = {"code": "000002", "name": "易方达新经济", "R": "R4", "ret_3m": -0.018, "ret_1y": 0.062, "min_buy": 1}
    long_caption = "本周市场震荡加大。" + "控回撤是当下的第一要务，组合里保留了部分短债。" * 30

    def card(pid, org, title, caption, land, likes, arm, image, cps, climate):
        return {"post_id": pid, "org": org, "title": title, "caption": caption, "landing": land,
                "likes": likes, "arm": arm, "image_path": image, "image_sha": None,
                "comments_prev": cps, "climate": climate}

    bull2 = [{"stance": "bullish", "text": "已定投两年", "fam_phrase": "老持有人"},
             {"stance": "bullish", "text": "微笑曲线", "fam_phrase": "路人"},
             {"stance": "watching", "text": "想开始", "fam_phrase": "新人"}]
    cards = [
        card("p1", "华夏基金", "震荡市里的债底仓思路", long_caption, landing, 34, "TV", jpg,
             [{"stance": "bullish", "text": "拿了一年，稳。", "fam_phrase": "老持有人"},
              {"stance": "watching", "text": "现在上车来得及吗", "fam_phrase": "新访客"}], "mixed"),
        card("p2", "易方达基金", "四季报里的几点观察", "短文本一条。", None, 12, "T", None, [], "no_signal"),
        card("p3", "华夏基金", "新发产品的定位说明", "产品定位说明文本，长度适中。", landing2, 8, "TV",
             os.path.join(tmp, "missing.jpg"),
             [{"stance": "bearish", "text": "这个主题有点拥挤", "fam_phrase": "路人"}], "bearish_majority"),
        card("p4", "易方达基金", "定投的三件小事", "坚持、分散、别盯盘。", None, 5, "T", None, bull2, "bullish_majority"),
        card("p5", "华夏基金", "月度观点小纸条", "本月观点摘要。", None, 2, "TV", None, [], "no_signal"),
        card("p6", "易方达基金", "关于回撤的一点体会", "回撤体会短文。", landing2, 2, "T", None, [], "mixed"),
    ]
    view_a = {"persona_card_zh_rich": "你是王女士，42 岁，在制造企业做行政，孩子上初中，攒下的钱想稳中求进。",
              "market_view": 4, "risk_mood": 2, "last_reflection": "上月觉得震荡市要保守一点。",
              "memory": ["周一：没怎么打开 App。", "周二：看了一条债基帖子，没操作。", "周三：申购了 2 万元华夏稳健混合。",
                         "周四：没打开 App。", "周五：给一条帖子点了赞。"],
              "c_class": "C3", "cash": 80000.0,
              "last_trade": {"d": 12, "act": "subscribe", "code": "000001", "amt": 20000},
              "holdings": [{"code": "000001", "name": "华夏稳健混合", "r": "R3", "units": 12000.5,
                            "nav": 1.2345, "pnl_pct": 0.031}],
              "declined_confirms": ["上周你曾在《风险不匹配确认书》前放弃了一次申购。"],
              "familiarity": {"华夏基金": 2, "易方达基金": 1}, "index_5d": -0.012, "holdings_1d": 0.004,
              "guba": {"000001": {"name": "华夏稳健混合", "mult": 2.3, "bull_ratio": 0.6}},
              "trend": {"000001": {"name": "华夏稳健混合", "ret_1w": 0.008, "ret_1m": -0.021,
                                   "ret_3m": 0.052, "mdd": -0.06, "pos": "low"}},
              "direct": ["【华夏基金】您关注的组合月报已生成，可到 App 查看。"]}
    view_b = {"persona_card_zh_rich": "你是小周，26 岁，刚工作两年，工资不高，想先小额试试。",
              "market_view": 3, "risk_mood": 3, "c_class": "C2", "cash": 5000.0}
    cfg = {"channels": {"feed": True, "experience": True, "trend": True, "social": True,
                        "news": True, "direct": True}, "social": True}

    messages, psha, ishas, notes = build_decision_messages(view_a, cards, cfg)
    parts = messages[1]["content"]
    img_parts = [p for p in parts if p.get("type") == "image_url"]
    all_text = messages[0]["content"] + "\n" + "\n".join(p["text"] for p in parts if p.get("type") == "text")
    check("build: shapes", isinstance(messages, list) and messages[0]["role"] == "system"
          and isinstance(parts, list) and len(psha) == 64 and isinstance(notes, list))
    check("build: one image attached", len(img_parts) == 1
          and str(img_parts[0]["image_url"]["url"]).startswith("data:image/jpeg;base64,"))
    check("build: image sha matches file", ishas == [sha256_file(jpg)])
    check("build: image_missing noted", "image_missing" in notes)
    check("build: five channel_sha notes", sum(1 for n in notes if n.startswith("channel_sha:")) == 5)
    check("build: no anti-priming word", not any(w in all_text.replace("配图不展示", "") for w in ANTI_PRIMING_WORDS))
    check("build: T marker + truncation", "配图不展示。" in all_text and "…[展开]" in all_text)
    check("build: prompt_sha stable + agent-differs", build_decision_messages(view_a, cards, cfg)[1] == psha
          and build_decision_messages(view_b, cards, cfg)[1] != psha)
    # Real creatives are data and exempt; the guard protects OUR framing (persona/belief/instruction blocks).
    poisoned_view = dict(view_a)
    poisoned_view["persona_card_zh_rich"] = str(view_a.get("persona_card_zh_rich") or "") + " 监管提示：理性投资。"
    try:
        build_decision_messages(poisoned_view, cards, cfg)
        check("build: poisoned framing raises", False, "no ValueError")
    except ValueError:
        check("build: poisoned framing raises", True)
    poisoned_cards = [dict(c) for c in cards]
    poisoned_cards[0]["title"] = "别跟风，理性投资"
    try:
        build_decision_messages(view_a, poisoned_cards, cfg)
        check("build: creative text with sensitive word is NOT censored", True)
    except ValueError:
        check("build: creative text with sensitive word is NOT censored", False, "ValueError on data")

    # --- TC arm + n_comments_prev header ----------------------------------
    tc_extra = [
        {"post_id": "p7", "org": "华夏基金", "title": "一张图看懂资产配置", "caption": "图解配置思路。",
         "landing": None, "likes": 9, "arm": "TC", "image_path": jpg, "image_sha": None,
         "ocr_masked": "横轴为风险等级，纵轴为建议仓位比例。",
         "ocr_text": "不应被采用的备用OCR文本。",
         "image_caption_frozen": ["蓝色区域代表债券部分。", "红色区域代表权益部分。"],
         "comments_prev": [], "climate": "no_signal", "n_comments_prev": 0},
        {"post_id": "p8", "org": "易方达基金", "title": "定投微笑曲线图解", "caption": "图解定投。",
         "landing": None, "likes": 6, "arm": "TC", "image_path": None, "image_sha": None,
         "ocr_masked": "曲线示意图：下跌段买入更多份额。",
         "comments_prev": [{"stance": "bullish", "text": "坚持定投第三年", "fam_phrase": "老持有人"},
                           {"stance": "watching", "text": "图里低谷期好长", "fam_phrase": "新人"},
                           {"stance": "bearish", "text": "止盈更难", "fam_phrase": "路人"},
                           {"stance": "watching", "text": "第四条不应展示", "fam_phrase": "路人"}],
         "climate": "mixed", "n_comments_prev": 11},
        {"post_id": "p9", "org": "华夏基金", "title": "图注缺失的帖子", "caption": "这条帖子的图注缺失。",
         "landing": None, "likes": 3, "arm": "TC", "image_path": None, "image_sha": None,
         "comments_prev": [], "climate": "no_signal"},
        {"post_id": "p10", "org": "易方达基金", "title": "长OCR截断测试", "caption": "OCR超长截断。",
         "landing": None, "likes": 4, "arm": "TC", "image_path": None, "image_sha": None,
         "ocr_text": "长" * 500, "comments_prev": [], "climate": "no_signal"},
    ]
    cards_tc = cards + tc_extra
    m_tc, _psha_tc, ishas_tc, notes_tc = build_decision_messages(view_a, cards_tc, cfg)
    text_tc = "\n".join(p["text"] for p in m_tc[1]["content"] if p.get("type") == "text")
    img_tc = [p for p in m_tc[1]["content"] if p.get("type") == "image_url"]
    check("TC: never attaches an image, even with a real image_path",
          len(img_tc) == 1 and ishas_tc == [sha256_file(jpg)]
          and str(img_tc[0]["image_url"]["url"]).startswith("data:image/jpeg;base64,"))
    check("TC: renders OCR + frozen caption after the text-image marker",
          "图片信息（文字）：横轴为风险等级，纵轴为建议仓位比例。蓝色区域代表债券部分。红色区域代表权益部分。" in text_tc)
    check("TC: ocr_masked preferred over ocr_text", "不应被采用的备用OCR文本。" not in text_tc)
    check("TC: no payload -> fallback line + exactly one tc_no_caption note",
          "配图信息不可用。" in text_tc and notes_tc.count("tc_no_caption") == 1)
    check("TC: ocr_text fallback capped at 200 chars",
          ("图片信息（文字）：" + "长" * 200) in text_tc and "长" * 201 not in text_tc)
    check("B2: header shows the t-1 total via n_comments_prev, top-3 kept",
          "昨日评论（共 11 条，看法分歧）：" in text_tc and "第四条不应展示" not in text_tc)
    check("B2: cards without n_comments_prev keep the old header bytes",
          "昨日评论（共 2 条，看法分歧）：" in text_tc)
    check("TC: no anti-priming word beyond the sanctioned literals",
          not any(w in text_tc.replace("配图不展示", "").replace("配图信息不可用", "")
                  for w in ANTI_PRIMING_WORDS))

    rmsg, rsha = build_reflection_messages(view_a)
    check("reflect: messages+sha", rmsg[0]["role"] == "system" and len(rsha) == 64
          and "market_view" in rmsg[1]["content"])
    robj, _ = parse_reflection({"summary": "字" * 300, "beliefs": ["b" * 50] * 5, "market_view": 9, "risk_mood": 0})
    check("reflect: clamps", robj is not None and len(robj["summary"]) == 120 and len(robj["beliefs"]) == 3
          and all(len(b) == 30 for b in robj["beliefs"]) and robj["market_view"] == 5 and robj["risk_mood"] == 1)
    robj2, _ = parse_reflection({"summary": "好", "beliefs": [], "market_view": 4})
    check("reflect: missing key -> None", robj2 is None)

    pids, shown, held, orgs = ("p1", "p2", "p3"), ("000001", "000002"), ("000001",), ("华夏基金", "易方达基金")

    def base(**kw):
        d = {"reads": [], "engage": {}, "comments": [],
             "trade": {"action": "none", "fund": None, "amount_pct": 0, "sign_mismatch_confirm": False},
             "org_affinity_delta": {}, "mood": 3, "reason": "再看看"}
        d.update(kw)
        return json.dumps(d, ensure_ascii=False)

    def trade(**kw):
        t = {"action": "buy", "fund": "000001", "amount_pct": 20, "sign_mismatch_confirm": False}
        t.update(kw)
        return t

    def one(name, text, status, must=(), val=None, channel="content"):
        norm, viol, got = extract_decision(text, pids, shown, held, orgs, channel)
        ok = got == status and all(any(v == m or (m.endswith(":") and v.startswith(m)) for v in viol) for m in must)
        if ok and val is not None:
            ok = norm is not None and bool(val(norm))
        check("extract: " + name, ok, "status=%s viol=%s" % (got, viol))

    one("valid_full", base(reads=["p1", "p2"], engage={"p2": ["like"]}, comments=[{"post_id": "p3", "stance": "bullish", "text": "看着不错"}], trade=trade(), org_affinity_delta={"华夏基金": 1}, mood=4, reason="先建一点仓"), "ok", (), lambda n: n["trade"]["action"] == "buy" and n["mood"] == 4)
    one("valid_minimal", base(), "ok", (), lambda n: n["trade"]["action"] == "none" and n["comments"] == [])
    one("two_objects_first_wins", base(reads=["p9"]) + " 说明文字 " + base(reads=["p1"]), "ok", ("unknown_pid",), lambda n: n["reads"] == [])
    one("truncated_json", '{"reads": ["p1"], "engage": {', "no_json_object")
    one("plain_text_no_braces", "今天不想交易。", "no_json_object")
    one("array_top_level", "[1, 2, 3]", "no_json_object")
    for key, bad in (("reads", "p1"), ("engage", []), ("comments", {}), ("trade", []),
                     ("org_affinity_delta", 3), ("mood", "4"), ("reason", 4)):
        d = json.loads(base())
        d[key] = bad
        one("wrong_type_" + key, json.dumps(d, ensure_ascii=False), "schema_invalid", ("schema:" + key,))
    d = json.loads(base())
    del d["mood"]
    one("missing_key_mood", json.dumps(d), "schema_invalid", ("schema:",))
    d = json.loads(base())
    d["trade"] = {"action": "buy", "fund": "000001", "amount_pct": 20}
    one("missing_sign_mismatch_confirm", json.dumps(d), "schema_invalid", ("schema:",))
    d = json.loads(base())
    d["mood"] = True
    one("bool_mood", json.dumps(d), "schema_invalid", ("schema:mood",))
    d = json.loads(base())
    d["trade"]["amount_pct"] = "20"
    one("amount_pct_not_numeric", json.dumps(d), "schema_invalid", ("schema:amount_pct",))
    one("unknown_pid_reads", base(reads=["p1", "p9", "p2"]), "ok", ("unknown_pid",), lambda n: n["reads"] == ["p1", "p2"])
    one("unknown_pid_engage", base(engage={"p9": ["like"], "p1": ["follow"]}), "ok", ("unknown_pid",), lambda n: list(n["engage"]) == ["p1"])
    one("unknown_pid_comment", base(comments=[{"post_id": "p9", "stance": "bullish", "text": "hi"}]), "ok", ("unknown_pid",), lambda n: n["comments"] == [])
    one("engage_dedup_subset", base(engage={"p1": ["like", "like", "share", "save", "follow"]}), "ok", (), lambda n: n["engage"]["p1"] == ["like", "save", "follow"])
    one("engage_str_value", base(engage={"p2": "like"}), "ok", (), lambda n: n["engage"]["p2"] == ["like"])
    one("comment_too_long", base(comments=[{"post_id": "p1", "stance": "bullish", "text": "长" * 60}]), "ok", ("comment_too_long",), lambda n: len(n["comments"][0]["text"]) == 40)
    one("no_comment_clears_text", base(comments=[{"post_id": "p1", "stance": "no_comment", "text": "废话"}]), "ok", (), lambda n: n["comments"][0]["text"] == "")
    one("bad_stance", base(comments=[{"post_id": "p1", "stance": "happy", "text": "哇"}]), "ok", ("bad_stance",), lambda n: n["comments"][0]["stance"] == "no_comment")
    one("too_many_comments", base(comments=[{"post_id": "p" + str(i), "stance": "watching", "text": "x"} for i in (1, 2, 3)]), "ok", ("too_many_comments",), lambda n: len(n["comments"]) == 2)
    one("nonholder_redeem", base(trade=trade(action="redeem", fund="000002", amount_pct=50)), "ok", ("nonholder_redeem",), lambda n: n["trade"]["action"] == "none")
    one("redeem_no_fund", base(trade=trade(action="redeem", fund=None, amount_pct=50)), "ok", ("nonholder_redeem",), lambda n: n["trade"]["action"] == "none")
    one("holder_redeem_ok", base(trade=trade(action="redeem", fund="000001", amount_pct=30)), "ok", (), lambda n: n["trade"]["action"] == "redeem")
    one("fund_not_in_scope", base(trade=trade(fund="999999")), "ok", ("fund_not_in_scope",), lambda n: n["trade"]["action"] == "none")
    one("amount_clamp_high", base(trade=trade(amount_pct=250)), "ok", (), lambda n: n["trade"]["amount_pct"] == 100.0)
    one("amount_clamp_neg", base(trade=trade(amount_pct=-5)), "ok", (), lambda n: n["trade"]["amount_pct"] == 0.0)
    one("bad_action", base(trade=trade(action="hold")), "ok", ("bad_action",), lambda n: n["trade"]["action"] == "none")
    one("affinity_clamp_round", base(org_affinity_delta={"华夏基金": 5, "易方达基金": 1.6}), "ok", (), lambda n: n["org_affinity_delta"] == {"华夏基金": 2, "易方达基金": 2})
    one("affinity_unknown_org", base(org_affinity_delta={"南方基金": 1, "华夏基金": -9}), "ok", ("unknown_org",), lambda n: n["org_affinity_delta"] == {"华夏基金": -2})
    one("mood_clamp", base(mood=9), "ok", (), lambda n: n["mood"] == 5)
    one("reason_truncated", base(reason="字" * 100), "ok", (), lambda n: len(n["reason"]) == 60)
    one("braces_inside_string", json.dumps(dict(json.loads(base()), reason="看{起来}不错"), ensure_ascii=False), "ok", (), lambda n: n["reason"] == "看{起来}不错")
    one("prose_prefix_then_json", "好的，以下是 JSON：" + base(reads=["p2"]), "ok", (), lambda n: n["reads"] == ["p2"])
    one("json_then_truncated_tail", base(reads=["p1"]) + ' 补充 {"reads": ["p2"', "ok", (), lambda n: n["reads"] == ["p1"])
    one("reasoning_unique_ok", base(mood=5), "ok", (), lambda n: n["mood"] == 5, channel="reasoning")
    one("reasoning_two_objects", base() + " " + base(), "ambiguous_reasoning", ("ambiguous_reasoning",), channel="reasoning")
    one("reasoning_invalid_unique", '{"reads": "p1"}', "schema_invalid", ("schema:",), channel="reasoning")
    norm, viol = parse_decision(["nope"], pids, shown, held, orgs)
    check("parse: non-dict -> schema", norm is None and bool(viol) and viol[0].startswith("schema:"))

    fails = [(n, d) for n, ok, d in results if not ok]
    bar = "-" * 72
    print(bar)
    for name, ok, detail in results:
        print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok or not detail else " | " + detail))
    print(bar)
    print("self-test: %d/%d checks passed, %d failed" % (len(results) - len(fails), len(results), len(fails)))
    return len(fails)


def main(argv=None):
    """Entry point; --self-test runs the zero-API self-test and returns the failure count."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" not in argv:
        print("usage: python -m flowmirror.agents.prompt --self-test")
        return 0
    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())

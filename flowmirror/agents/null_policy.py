"""Rule-based null policy used as a deterministic comparison policy.

`NullPolicyLLM(params, run_tag)` is a drop-in replacement for the `llm` callable that
flowmirror.agents.runtime.decide()/reflect() drive: same call signature
`llm(messages, max_tokens, **kw)` and the same provenance-dict shape as MockLLM
(parsed=None, raw=<json text>, response_source="null", attempts=1, parser_status="null",
raw_sha256, finish_reason, http_status, max_tokens_final, usage, attempt_log).  It READS
the very same prompt text a live model would see and decides with two canonical
behavioural-finance rules plus population-mean engagement rates:

1. Disposition effect (Odean 1998): every held fund parsed from the account block
   (`code / name / R / units份 × 净值nav / 浮动盈亏 ±x.x%` lines) is redeemed with
   probability p_redeem_gain when the floating P&L is strictly positive, else
   p_redeem_loss; the first firing fund wins and redeem_pct percent of the position is
   sold.  A firing redeem preempts rule 2 (the decision schema carries ONE trade object
   per day).  A P&L of exactly 0 falls on the loss side.
2. Return chasing (Sirsi & Tufano): among the shown cards that carry a landing fund
   (`关联基金：<name>（<code>）` plus the 近3个月收益 percentage on the same line),
   subscribe to the top-return card with probability p_sub_base * (1 + chase_slope *
   rank_quantile), capped at 0.6, buying sub_pct percent of investable cash;
   sign_mismatch_confirm is a Bernoulli(p_sign_mismatch) coin -- the null has NO notion
   of suitability, which is exactly the point of the anti-A1 baseline.
3. Engagement at population-mean rates: like/save/follow per read card, comment with a
   fixed neutral sentence and a stance drawn from stance_probs;
   org_affinity_delta = 0 for every shown org.  Zero by design: the null keeps the
   ranker/adstock channel alive ONLY through exposure and follows, so any affinity
   movement in the main grid is attributable to the LLM channel, not the plumbing.
4. Reads: every shown card (the null "reads everything").

Reflection prompts (user text contains REFLECTION_PROMPT_ZH's "过去 5 个交易日") get the
fixed payload {"summary":"（规则基线，无反思）","beliefs":[],"market_view":3,"risk_mood":3}.

Determinism: the only RNG is random.Random(rng_seed_from(run_tag, "null",
sha256(prompt_text))); the object is stateless and cheap to construct, so the loop may
build one per decision/reflection job.  Draw order is fixed: rule 1 holdings in prompt
order, rule 2 subscribe coin (then sign coin only if it fires), then per card in prompt
order like/save/follow/comment.  Sets are never iterated unsorted.

For reproducible comparisons, fix every `null_params` value before inspecting the
results and do not tune the policy between runs being compared.
"""
from __future__ import annotations

import argparse
import inspect
import json
import random
import re

from flowmirror.io.hashing import rng_seed_from, sha256_text

_REFLECTION_MARKER = "过去 5 个交易日"   # stable substring of prompt.REFLECTION_PROMPT_ZH
_STANCES = ("bullish", "bearish", "watching")
_NEUTRAL_COMMENTS = (
    "先收藏，回头再仔细看看。",
    "mark 一下，继续观察。",
    "收益数字仅供参考，先看看再说。",
    "关注了，等季报出来再判断。",
    "不动手，只记录一下。",
    "有参考价值，但先观望。",
)
_REASON = "规则基线：按持仓盈亏与展示收益机械执行，无其他考虑。"
_REFLECTION_ROW = {"summary": "（规则基线，无反思）", "beliefs": [],
                   "market_view": 3, "risk_mood": 3}

_HOLD_MARK_RE = re.compile(r"份\s*[×xX*]\s*净值")             # holdings-line marker
_HOLD_CODE_RE = re.compile(r"\d{6}")                           # first 6-digit run = code
_HOLD_PNL_RE = re.compile(r"浮动盈亏\s*([+\-−]?)\s*([\d.]+)\s*%")
_CARD_HEAD_RE = re.compile(r"^【([^】【\s]{1,64})】\s*机构[：:]")
_CARD_ORG_RE = re.compile(r"机构[：:]\s*([^\s｜|，,。;；]+)")
_LANDING_RE = re.compile(r"关联基金[：:]\s*.+?（(\d{6})）")
_RET3M_RE = re.compile(r"近3个月收益\s*([+\-−]?)\s*([\d.]+)\s*%")


def _num(mapping, key, default):
    try:
        return float(mapping.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _prob(mapping, key, default):
    return min(1.0, max(0.0, _num(mapping, key, default)))


def _signed_pct(m):
    sign = -1.0 if m.group(1) in ("-", "\u2212") else 1.0
    return sign * float(m.group(2))


def messages_text(messages):
    """Concatenate every text part of an OpenAI-style messages list (str content or
    content-block lists); image_url and other blocks contribute nothing."""
    parts = []
    for m in messages or ():
        content = (m or {}).get("content") if isinstance(m, dict) else m
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(str(blk.get("text") or ""))
                elif isinstance(blk, str):
                    parts.append(blk)
    return "\n".join(parts)


def parse_holdings(text):
    """Held funds from the account block (prompt.render_experience): lines shaped
    `code / name / R / units份 × 净值nav / 浮动盈亏 ±x.x%`; the 6-digit code is matched
    robustly anywhere in the line (leftmost run wins, which is the leading code)."""
    out = []
    for line in text.splitlines():
        if not _HOLD_MARK_RE.search(line) or "浮动盈亏" not in line:
            continue
        code = _HOLD_CODE_RE.search(line)
        pnl = _HOLD_PNL_RE.search(line)
        if code and pnl:
            out.append({"code": code.group(0), "pnl": _signed_pct(pnl)})
    return out


def parse_cards(text):
    """Impression cards as rendered by prompt.render_card: a `【post_id】机构：<org> ｜ …`
    header line, then title/caption, then (I2 posts only) a `关联基金：<name>（<code>）`
    landing line whose FUND_ZH part carries 近3个月收益 ±x.x%."""
    cards, cur = [], None
    for line in text.splitlines():
        head = _CARD_HEAD_RE.match(line)
        if head:
            org = _CARD_ORG_RE.search(line)
            cur = {"pid": head.group(1), "org": org.group(1) if org else None,
                   "code": None, "ret3m": None}
            cards.append(cur)
            continue
        if cur is None:
            continue
        landing = _LANDING_RE.search(line)
        if landing and cur["code"] is None:
            cur["code"] = landing.group(1)
            ret = _RET3M_RE.search(line)
            if ret:
                cur["ret3m"] = _signed_pct(ret)
    return cards


def _rank_quantile(ranked, chosen):
    """Quantile of the chosen card's 3m return among the shown ranked cards:
    strictly-below count / (n - 1), capped at 1; a single shown fund is trivially the top."""
    n = len(ranked)
    if n <= 1:
        return 1.0
    below = sum(1 for c in ranked if c["ret3m"] < chosen["ret3m"])
    return min(1.0, below / float(n - 1))


def draw_stance(rng, probs):
    """Stance draw over the FIXED order (bullish, bearish, watching); probs normalised
    defensively so non-summing configs stay deterministic and valid."""
    total = sum(probs)
    if total <= 0.0:
        return "watching"
    x = rng.random() * total
    for name, p in zip(_STANCES, probs):
        if x < p:
            return name
        x -= p
    return "watching"


class NullPolicyLLM:
    """Stateless rule-based stand-in for the `llm` callable (see module docstring)."""

    def __init__(self, params=None, run_tag=""):
        p = dict(params or {})
        self.run_tag = str(run_tag or "")
        self.p_redeem_gain = _prob(p, "p_redeem_gain", 0.06)
        self.p_redeem_loss = _prob(p, "p_redeem_loss", 0.03)
        self.redeem_pct = _num(p, "redeem_pct", 50.0)
        self.p_sub_base = _prob(p, "p_sub_base", 0.05)
        self.chase_slope = _num(p, "chase_slope", 2.0)
        self.sub_pct = _num(p, "sub_pct", 20.0)
        self.p_sign_mismatch = _prob(p, "p_sign_mismatch", 0.5)
        self.p_like = _prob(p, "p_like", 0.15)
        self.p_save = _prob(p, "p_save", 0.08)
        self.p_follow = _prob(p, "p_follow", 0.02)
        self.p_comment = _prob(p, "p_comment", 0.10)
        sp = p.get("stance_probs") if isinstance(p.get("stance_probs"), dict) else {}
        self.stance_probs = (_prob(sp, "bullish", 0.35), _prob(sp, "bearish", 0.30),
                             _prob(sp, "watching", 0.35))

    def __call__(self, messages, max_tokens, **kw):
        text = messages_text(messages)
        rng = random.Random(rng_seed_from(self.run_tag, "null", sha256_text(text)))
        if _REFLECTION_MARKER in text:
            raw = json.dumps(_REFLECTION_ROW, ensure_ascii=False)
        else:
            raw = json.dumps(self._decision(text, rng), ensure_ascii=False)
        return {"parsed": None, "raw": raw, "response_source": "null", "http_status": 200,
                "attempts": 1, "finish_reason": "stop", "max_tokens_final": int(max_tokens),
                "parser_status": "null", "raw_sha256": sha256_text(raw), "usage": {},
                "attempt_log": [{"i": 1, "http_status": 200, "cls": "null",
                                 "finish_reason": "stop", "max_tokens": int(max_tokens)}]}

    def _decision(self, text, rng):
        cards = parse_cards(text)
        holdings = parse_holdings(text)
        trade = {"action": "none", "fund": "", "amount_pct": 0, "sign_mismatch_confirm": False}
        for h in holdings:                       # rule 1: Odean disposition effect
            p = self.p_redeem_gain if h["pnl"] > 0.0 else self.p_redeem_loss
            if rng.random() < p:
                trade = {"action": "redeem", "fund": h["code"], "amount_pct": self.redeem_pct,
                         "sign_mismatch_confirm": False}
                break
        if trade["action"] == "none":            # rule 2: Sirsi-Tufano return chasing
            ranked = sorted((c for c in cards if c["code"] and c["ret3m"] is not None),
                            key=lambda c: (c["ret3m"], str(c["pid"])))
            if ranked:
                top = ranked[-1]
                p_sub = min(0.6, self.p_sub_base * (1.0 + self.chase_slope
                                                    * _rank_quantile(ranked, top)))
                if rng.random() < p_sub:
                    trade = {"action": "buy", "fund": top["code"], "amount_pct": self.sub_pct,
                             "sign_mismatch_confirm": rng.random() < self.p_sign_mismatch}
        engage, comments = {}, []                # rule 3: population-mean engagement
        for c in cards:
            acts = []
            if rng.random() < self.p_like:
                acts.append("like")
            if rng.random() < self.p_save:
                acts.append("save")
            if rng.random() < self.p_follow:
                acts.append("follow")
            if acts:
                engage[str(c["pid"])] = acts
            if rng.random() < self.p_comment:
                comments.append({"post_id": str(c["pid"]),
                                 "stance": draw_stance(rng, self.stance_probs),
                                 "text": rng.choice(_NEUTRAL_COMMENTS)})
        return {"reads": [str(c["pid"]) for c in cards],        # rule 4: reads everything
                "engage": engage,
                "comments": comments,
                "trade": trade,
                "org_affinity_delta": {str(c["org"]): 0 for c in cards if c.get("org")},
                "mood": 3,
                "reason": _REASON}


def _self_test():
    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + name + ((" " + extra) if extra else ""))

    def hold_line(code, pnl):
        sign = "+" if pnl >= 0 else "-"
        return (f"{code} / 测试基金{code[-2:]} / R3 / 1,000.00份 × 净值1.2000 / "
                f"浮动盈亏 {sign}{abs(pnl):.1f}%")

    def card_block(pid, org, landing=None, extra=""):
        lines = [f"【{pid}】机构：{org} ｜ 热度：3 赞",
                 "标题：组合复盘笔记" + extra,
                 "正文：一段中性的测试内容。"]
        if landing is not None:
            name, code, r3m, r1y = landing
            lines.append(f"关联基金：{name}（{code}）。基金档案：这是一只混合型基金，"
                         f"近3个月收益 {'+' if r3m >= 0 else ''}{r3m:.1f}%，"
                         f"近1年收益 {'+' if r1y >= 0 else ''}{r1y:.1f}%，起购金额 100 元。")
        lines.append("配图不展示。")
        return "\n".join(lines)

    def decision_prompt(i, holdings, cards):
        body = ["第一步（交易）：测试用缩略指令。", "当前账户："]
        body.extend(holdings)
        body.extend(cards)
        body.append(f"（变体 {i}）")
        return [{"role": "system", "content": "你是测试投资者。"},
                {"role": "user", "content": "\n".join(body)}]

    def scope_of(pids, codes, held, orgs):
        return {"pids": list(pids), "codes": list(codes), "held": list(held),
                "orgs": list(orgs)}

    def _shape_ok(row):
        return (isinstance(row, dict) and isinstance(row.get("reads"), list)
                and isinstance(row.get("engage"), dict)
                and isinstance(row.get("comments"), list)
                and isinstance(row.get("trade"), dict)
                and isinstance(row.get("org_affinity_delta"), dict)
                and row["trade"].get("action") in ("none", "buy", "redeem")
                and isinstance(row.get("mood"), int))

    def _sniff_parsed(res):
        if isinstance(res, dict):
            if isinstance(res.get("parsed"), dict):
                return res["parsed"]
            if any(k in res for k in ("reads", "engage", "trade", "comments")):
                return res
            return None
        if isinstance(res, tuple):
            for part in res:
                if isinstance(part, dict) and any(k in part for k in ("reads", "engage",
                                                                     "trade", "comments")):
                    return part
            for part in res:
                if isinstance(part, dict) and part:
                    return part
        return None

    try:
        from flowmirror.agents.prompt import extract_decision
    except Exception:
        extract_decision = None

    def _extract(raw, scope):
        """Adaptive call of prompt.extract_decision; returns (form_resolved, parsed|None)."""
        if extract_decision is None:
            return False, None
        try:
            params = list(inspect.signature(extract_decision).parameters)
        except (TypeError, ValueError):
            params = []
        forms = []
        if "scope" in params:
            forms.append(lambda: extract_decision(raw, scope=scope))
        if len(params) >= 2:
            forms.append(lambda: extract_decision(raw, scope))
        forms.append(lambda: extract_decision(raw))
        for form in forms:
            try:
                res = form()
            except TypeError:
                continue
            except Exception:
                return True, None          # the parser ran and rejected this raw
            return True, _sniff_parsed(res)
        return False, None

    # Accentuated probabilities so the deterministic direction checks cannot flip.
    accent_params = {"p_redeem_gain": 0.8, "p_redeem_loss": 0.1,
                     "p_sub_base": 0.1, "chase_slope": 4.0}
    accent = NullPolicyLLM(accent_params, run_tag="st_null")

    pairs, gain_redeems, loss_redeems = [], 0, 0
    for i in range(105):                                   # disposition: gains (cards w/o landing)
        pnl = 1.5 + (i % 17) * 0.4
        msgs = decision_prompt(i, [hold_line("000001", pnl)],
                               [card_block("p1", "机构甲", None, f"g{i}"),
                                card_block("p2", "机构乙", None, f"h{i}")])
        prov = accent(msgs, 512)
        gain_redeems += int(json.loads(prov["raw"])["trade"]["action"] == "redeem")
        pairs.append((msgs, prov, scope_of(["p1", "p2"], [], ["000001"],
                                           ["机构甲", "机构乙"])))
    for i in range(105):                                   # disposition: losses
        pnl = -(1.5 + (i % 17) * 0.4)
        msgs = decision_prompt(10000 + i, [hold_line("000002", pnl)],
                               [card_block("p1", "机构甲", None, f"g{i}"),
                                card_block("p2", "机构乙", None, f"h{i}")])
        prov = accent(msgs, 512)
        loss_redeems += int(json.loads(prov["raw"])["trade"]["action"] == "redeem")
        pairs.append((msgs, prov, scope_of(["p1", "p2"], [], ["000002"],
                                           ["机构甲", "机构乙"])))
    subs = {"q0": 0, "q5": 0, "q1": 0}                     # chasing: rank buckets
    for name, rr in (("q1", (1.0, 5.0, 9.0)), ("q5", (1.0, 9.0, 9.0)),
                     ("q0", (5.0, 5.0, 5.0))):
        for j in range(30):
            cards = [card_block(f"p{k + 1}", f"机构{chr(65 + k)}",
                                (f"基金{k + 1}", f"1100{k + 1}1", rr[k], rr[k] * 2.0),
                                f"v{name}{j}") for k in range(3)]
            msgs = decision_prompt(20000 + j, [], cards)
            prov = accent(msgs, 512)
            subs[name] += int(json.loads(prov["raw"])["trade"]["action"] == "buy")
            pairs.append((msgs, prov, scope_of(["p1", "p2", "p3"],
                                               ["110011", "110021", "110031"], [],
                                               [f"机构{chr(65 + k)}" for k in range(3)])))
    rows = [(json.loads(p["raw"]), sc) for _, p, sc in pairs]
    chk("null_300_synthetic_prompts", len(pairs) == 300, f"(n={len(pairs)})")
    chk("null_redeem_rate_gain_gt_loss", gain_redeems > loss_redeems,
        f"({gain_redeems}/105 vs {loss_redeems}/105)")
    chk("null_subscribe_rate_increases_with_rank",
        subs["q1"] > subs["q0"] and subs["q1"] >= subs["q5"] >= subs["q0"],
        f"(q0={subs['q0']}/30 q5={subs['q5']}/30 q1={subs['q1']}/30)")
    chk("null_decision_json_shape_all", all(_shape_ok(r) for r, _ in rows))
    chk("null_reads_cover_shown_and_engage_enum_valid",
        all(set(r["reads"]) == set(sc["pids"]) and set(r["engage"]) <= set(r["reads"])
            and all(set(v) <= {"like", "save", "follow"} for v in r["engage"].values())
            and all(c["stance"] in _STANCES for c in r["comments"]) for r, sc in rows))
    chk("null_org_affinity_zero_for_all_shown_orgs",
        all(r["org_affinity_delta"] == {o: 0 for o in sc["orgs"]} for r, sc in rows))
    chk("null_trade_targets_within_scope",
        all(r["trade"]["action"] == "none"
            or (r["trade"]["action"] == "redeem" and r["trade"]["fund"] in sc["held"])
            or (r["trade"]["action"] == "buy" and r["trade"]["fund"] in sc["codes"])
            for r, sc in rows))
    called = parsed_ok = 0
    for _, prov, scope in pairs:
        did, parsed = _extract(prov["raw"], scope)
        if did:
            called += 1
            parsed_ok += int(parsed is not None)
    if called == 0:
        print("NOTE: extract_decision unresolved/unimportable; structural JSON check used")
        parsed_ok = sum(1 for r, _ in rows if _shape_ok(r))
        called = len(pairs)
    chk("null_outputs_parse_with_extract_decision_ge_99pct",
        bool(called) and parsed_ok >= int(0.99 * len(pairs)),
        f"({parsed_ok}/{len(pairs)})")
    twin = NullPolicyLLM(accent_params, run_tag="st_null")
    chk("null_deterministic_same_prompt_same_json",
        all(twin(m, 512)["raw"] == p["raw"] for m, p, _ in pairs[:30]))
    s = pairs[0][1]
    chk("null_provenance_shape",
        s["parsed"] is None and s["response_source"] == "null"
        and s["parser_status"] == "null" and s["attempts"] == 1
        and s["http_status"] == 200 and s["finish_reason"] == "stop"
        and s["max_tokens_final"] == 512 and isinstance(s["usage"], dict)
        and len(s["attempt_log"]) == 1 and s["raw_sha256"] == sha256_text(s["raw"]))
    refl = [{"role": "system", "content": "你是测试投资者。"},
            {"role": "user", "content": "下面是你过去 5 个交易日在平台上的记录……只输出 JSON。"}]
    r1 = accent(refl, 333)
    chk("null_reflection_fixed_payload", json.loads(r1["raw"]) == _REFLECTION_ROW)
    chk("null_reflection_deterministic", accent(refl, 333)["raw"] == r1["raw"])
    txt = decision_prompt(7, [hold_line("000001", 3.0)],
                          [card_block("p1", "机构甲", None, "mix")])
    mixed = [{"role": "user", "content": [{"type": "text", "text": txt[1]["content"]},
                                          {"type": "image_url",
                                           "image_url": {"url": "data:image/png;base64,x"}}]}]
    chk("null_image_blocks_ignored_seed_identical",
        accent(mixed, 512)["raw"] == accent(txt, 512)["raw"])
    pc = parse_cards("【p9】机构：机构乙 ｜ 热度：0 赞\n"
                     "关联基金：名称（100099）。基金档案：近3个月收益 -1.2%，"
                     "近1年收益 +3.0%，起购金额 100 元。")
    chk("null_signed_pct_parsing",
        parse_holdings("000012 / 基金X / R2 / 10.00份 × 净值1.0000 / 浮动盈亏 -2.5%"
                       )[0]["pnl"] == -2.5
        and pc[0]["code"] == "100099" and pc[0]["ret3m"] == -1.2)
    d = NullPolicyLLM()
    chk("null_default_parameters",
        (d.p_redeem_gain, d.p_redeem_loss, d.redeem_pct, d.p_sub_base, d.chase_slope,
         d.sub_pct, d.p_sign_mismatch, d.p_like, d.p_save, d.p_follow, d.p_comment,
         d.stance_probs) ==
        (0.06, 0.03, 50.0, 0.05, 2.0, 20.0, 0.5, 0.15, 0.08, 0.02, 0.10,
         (0.35, 0.30, 0.35)))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m flowmirror.agents.null_policy",
                                 description="FlowMirror deterministic rule-based policy")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("nothing to do; use --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

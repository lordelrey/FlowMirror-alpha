# -*- coding: utf-8 -*-
"""Two-pass compliance labeller for CN fund-marketing notes (PREREG D28).
Pass 1: offline/deterministic (lexicon, regex, rule R05). Pass 2: the GLM text model
adjudicates candidate rules, one call per note. Sidecar JSONL keyed by note_id."""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
import requests
import yaml

# --- repo bootstrap (same idiom as data_pipeline/cn/caption_frozen.py): running this file
# as a plain script puts data_pipeline/cn on sys.path[0], not the repo root, so the
# flowmirror import fails with ModuleNotFoundError without an editable install. When
# imported as a module (__package__ set) the bootstrap is a no-op. ---------------------
if __package__ in (None, ""):
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

POOL_DEFAULT = "data/creatives/cn/content_pool_v1_captioned.jsonl"
RULES_DEFAULT = "config/compliance_rules_cn_v1.yaml"
OUT_DEFAULT = "data/creatives/cn/compliance_labels_v1.jsonl"
SAMPLE_PATH = "data/creatives/cn/compliance_review_sample.md"
REQUEST_TIMEOUT_S = 300
MAX_TEXT_CHARS, MAX_TOKENS_CAP = 6000, 16000
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
_SEV_NAME = {v: k for k, v in SEVERITY_ORDER.items()}
PROMPT_SYSTEM = "你是基金营销内容合规审核员。只依据给出的规则判断，不做任何投资评价。只输出一个 JSON 对象，不输出其他文字。"
PROMPT_USER_TEMPLATE = ("候选规则（只针对以下规则逐条判断）：\n{rules}\n\n待审内容：\n{text}\n\n"
                        "对每条规则输出 {{\"<id>\": {{\"hit\": true|false, \"evidence\": \"原文片段或空字串\"}}}}。")
PROMPT_SHA256 = hashlib.sha256((PROMPT_SYSTEM + "\n" + PROMPT_USER_TEMPLATE).encode("utf-8")).hexdigest()

def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def _note_text(note):
    text = "\n".join(str(note.get(k) or "") for k in ("title", "caption", "ocr_text"))
    return (text[:MAX_TEXT_CHARS], True) if len(text) > MAX_TEXT_CHARS else (text, False)

def _lex_hit(text, terms):
    for t in terms or []:
        if t and t in text:
            return t
    return None

def first_pass(note, rules):
    """Deterministic pass -> (labels, cand_ids, text, truncated)."""
    text, truncated = _note_text(note)
    labels, cand_ids = {}, []
    for r in rules:
        rid, role = r.get("id"), r.get("lexicon_role")
        frag = _lex_hit(text, r.get("lexicon"))
        m = re.search(r["lexicon_regex"], text) if r.get("lexicon_regex") else None
        if role == "hit":
            labels[rid] = ({"hit": True, "evidence": frag, "source": "lexicon"} if frag else {"hit": False, "evidence": None, "source": None})
        elif role == "candidate" and (frag or m):
            labels[rid] = {"hit": None, "evidence": frag or (m.group(0) if m else None), "source": None}
            cand_ids.append(rid)
        elif role == "none" and r.get("risk_phrases") and note.get("intent_group") == "I2" and not _lex_hit(text, r["risk_phrases"]):
            labels[rid] = {"hit": True, "evidence": "I2 且无风险提示短语", "source": "rule"}
        else:
            labels[rid] = {"hit": False, "evidence": None, "source": None}
    return labels, cand_ids, text, truncated

def format_rules(rules, ids):
    lines = []
    for r in rules:
        if r.get("id") in ids:
            lines.append("- %s %s：判定：%s；示例：%s" % (r.get("id"), r.get("name", ""),
                          r.get("decision_rule", ""), " / ".join(r.get("examples") or [])))
    return "\n".join(lines)

def extract_json(s):
    i, j = (s or "").find("{"), (s or "").rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def parse_message(msg):
    parsed = extract_json((msg or {}).get("content") or "")
    if parsed is not None:
        return parsed, "content"
    parsed = extract_json((msg or {}).get("reasoning_content") or "")
    return (parsed, "reasoning") if parsed is not None else (None, "")

def call_llm(endpoint, api_key, model, cand_ids, rules, text, max_tokens):
    """One call per note, <=3 attempts: 5s*attempt backoff on HTTP/network errors,
    max_tokens x2 (cap 16000) escalation when the reply has no parseable JSON."""
    payload = {"model": model, "temperature": 0, "max_tokens": int(max_tokens),
               "messages": [{"role": "system", "content": PROMPT_SYSTEM},
                            {"role": "user", "content": PROMPT_USER_TEMPLATE.format(
                                rules=format_rules(rules, cand_ids), text=text)}]}
    attempt = 0
    for attempt in range(1, 4):
        try:
            resp = requests.post(endpoint, headers={"Authorization": "Bearer " + api_key},
                                 json=payload, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code != 200:
                time.sleep(5 * attempt)
                continue
            parsed, channel = parse_message(resp.json()["choices"][0].get("message"))
            if parsed is not None:
                return parsed, channel, attempt
        except Exception:
            time.sleep(5 * attempt)
            continue
        payload["max_tokens"] = min(payload["max_tokens"] * 2, MAX_TOKENS_CAP)
    return None, "", attempt

def merge_labels(labels, cand_ids, llm_out):
    """Candidate rules are decided by the LLM; 'hit'-role rules only gain evidence."""
    for rid, entry in (llm_out or {}).items():
        if not isinstance(entry, dict) or rid not in labels:
            continue
        ev = str(entry.get("evidence") or "").strip()
        lab = labels[rid]
        if rid in cand_ids:
            lab["hit"] = bool(entry.get("hit"))
            lab["evidence"] = ev or lab.get("evidence")
            lab["source"] = "both" if lab["hit"] else "llm"
        elif lab.get("hit") is True and ev:
            lab["evidence"] = (lab.get("evidence") + " | " + ev) if lab.get("evidence") else ev
    return labels

def severity_max(labels, sev_by_id):
    best = 0
    for rid, lab in (labels or {}).items():
        if lab and lab.get("hit") is True:
            best = max(best, SEVERITY_ORDER.get(sev_by_id.get(rid, "low"), 1))
    return _SEV_NAME[best] if best else None

def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line.strip() or "null")
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows

def load_pool(path):
    """Captioned-pool rows; skips the {"_meta": ...} header line."""
    return [r for r in read_jsonl(path) if not r.get("_meta") and r.get("note_id")]

def is_reusable(row, model, rules_sha):
    meta = row.get("label_meta") or {}
    return bool(row.get("note_id") and not row.get("_meta")
                and meta.get("prompt_sha") == PROMPT_SHA256 and meta.get("rules_sha") == rules_sha
                and meta.get("model") == model)

def load_reusable(path, model, rules_sha):
    return {r["note_id"]: r for r in read_jsonl(path) if is_reusable(r, model, rules_sha)}

SELFTEST_RULES = [
    {"id": "R01", "severity": "high", "lexicon_role": "hit", "lexicon": ["保本", "稳赚"]},
    {"id": "R02", "severity": "high", "lexicon_role": "candidate", "lexicon": ["预期年化"]},
    {"id": "R03", "severity": "medium", "lexicon_role": "candidate", "lexicon": ["最佳"]},
    {"id": "R05", "severity": "medium", "lexicon_role": "none",
     "risk_phrases": ["风险提示", "基金有风险", "投资需谨慎"]},
    {"id": "R06", "severity": "high", "lexicon_role": "hit", "lexicon": ["躺赢"]},
]

def run_self_test():
    """Offline assertions: pass-1 logic, JSON extraction, resume filter, severity."""
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(("[pass] " if cond else "[FAIL] ") + msg)

    sev = {r["id"]: r.get("severity", "low") for r in SELFTEST_RULES}
    n1 = {"note_id": "t1", "intent_group": "nonI2", "title": "稳赚不赔的好基金", "caption": "", "ocr_text": ""}
    l1 = first_pass(n1, SELFTEST_RULES)[0]
    check(l1["R01"]["hit"] is True and l1["R01"]["source"] == "lexicon" and "稳赚" in l1["R01"]["evidence"],
          "R01：词表命中，evidence 含「稳赚」")
    n2 = dict(n1, title="好基金", intent_group="I2")
    check(first_pass(n2, SELFTEST_RULES)[0]["R05"]["hit"] is True, "R05：I2 无风险短语 → 命中")
    check(first_pass(dict(n2, caption="基金有风险，投资需谨慎"), SELFTEST_RULES)[0]["R05"]["hit"] is False,
          "R05：含风险短语 → 不命中")
    check(first_pass(dict(n2, intent_group="nonI2"), SELFTEST_RULES)[0]["R05"]["hit"] is False,
          "R05：nonI2 → 不命中")
    p3, ch3 = parse_message({"content": '好的，结果如下 {"R02": {"hit": true, "evidence": "预期年化8%"}} 谢谢'})
    check(p3 is not None and p3["R02"]["hit"] is True and ch3 == "content", "JSON 抽取：content 前后有闲话")
    p4, ch4 = parse_message({"content": "", "reasoning_content": '想了想 {"R02": {"hit": true, "evidence": "x"}}'})
    check(p4 is not None and ch4 == "reasoning", "JSON 抽取：空 content → reasoning 通道")
    meta = {"prompt_sha": PROMPT_SHA256, "rules_sha": "shaA", "model": "modelA"}
    row = {"note_id": "n1", "labels": {}, "label_meta": meta, "channel": ""}
    check(is_reusable(row, "modelA", "shaA") and not is_reusable(row, "modelB", "shaA")
          and not is_reusable(row, "modelA", "shaB"), "续跑过滤：sha/model 不匹配 → 不复用")
    labs = {"R03": {"hit": True, "evidence": "最佳", "source": "llm"}, "R06": {"hit": True, "evidence": "躺赢", "source": "lexicon"}}
    check(severity_max(labs, sev) == "high", "severity_max：medium+high → high")
    check(severity_max({"R02": {"hit": False, "evidence": None, "source": None}}, sev) is None,
          "severity_max：无命中 → None")
    print("[self-test] %s" % ("全部通过" if ok else "存在失败项"))
    sys.exit(0 if ok else 1)

def main():
    ap = argparse.ArgumentParser(description="两遍合规打标：词表/规则第一遍 + GLM 复核候选规则第二遍")
    ap.add_argument("--pool", default=POOL_DEFAULT)
    ap.add_argument("--rules", default=RULES_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--max-calls", type=int, default=300)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        run_self_test()
        return
    with open(args.rules, "rb") as f:
        rules_bytes = f.read()
    rules_sha = hashlib.sha256(rules_bytes).hexdigest()
    rules = (yaml.safe_load(rules_bytes.decode("utf-8")) or {}).get("rules") or []
    sev_by_id = {r.get("id"): r.get("severity", "low") for r in rules}
    notes = load_pool(args.pool)
    from flowmirror.agents.runtime import _load_glm_config  # same creds/endpoint as caption_frozen
    endpoint, api_key, _vision, text_model = _load_glm_config()
    model = args.model or text_model
    reusable = load_reusable(args.out, model, rules_sha)
    if args.dry_run:
        cand, reuse_cand = 0, 0
        lex_counts = {r.get("id"): 0 for r in rules}
        for note in notes:
            labels, cand_ids, _t, _tr = first_pass(note, rules)
            cand += bool(cand_ids)
            reuse_cand += 1 if (cand_ids and note.get("note_id") in reusable) else 0
            for rid, lab in labels.items():
                if lab.get("hit") is True or lab.get("hit") is None:
                    lex_counts[rid] = lex_counts.get(rid, 0) + 1
        print("[compliance] dry-run：笔记 %d | 候选笔记 %d（可复用 %d）| 将调用 %d 次（max-calls=%d）"
              % (len(notes), cand, reuse_cand, min(max(cand - reuse_cand, 0), args.max_calls), args.max_calls))
        print("  " + " | ".join("%s（role=%s, sev=%s）词表命中 %d" % (
            r.get("id"), r.get("lexicon_role"), r.get("severity"), lex_counts.get(r.get("id"), 0)) for r in rules))
        return
    stats = {"llm_calls": 0, "reused": 0, "failed": 0, "hit_notes": 0}
    per_rule_hit = {r.get("id"): 0 for r in rules}
    i2_total = i2_hit = n2_total = n2_hit = 0
    out_rows = []
    for note in notes:
        nid, ig = note.get("note_id"), note.get("intent_group") or ""
        i2_total, n2_total = i2_total + (ig == "I2"), n2_total + (ig != "I2")
        old = reusable.get(nid)
        if old is not None:
            stats["reused"] += 1
            labels, row = old.get("labels") or {}, old
        else:
            labels, cand_ids, text, truncated = first_pass(note, rules)
            channel, attempts, called = "", 0, False
            if cand_ids:
                called = stats["llm_calls"] < args.max_calls
                llm_out = None
                if called:
                    stats["llm_calls"] += 1
                    llm_out, channel, attempts = call_llm(endpoint, api_key, model, cand_ids,
                                                          rules, text, args.max_tokens)
                if llm_out is None:
                    stats["failed"] += 1
                    for rid in cand_ids:
                        labels[rid].update(hit=None, source=None)
                else:
                    labels = merge_labels(labels, cand_ids, llm_out)
            row = {"note_id": nid, "org": note.get("org"), "intent": note.get("intent"),
                   "intent_group": ig, "labels": labels, "severity_max": severity_max(labels, sev_by_id),
                   "label_meta": {"model": model, "prompt_sha": PROMPT_SHA256, "rules_sha": rules_sha,
                                  "generated_at": _now_iso(), "channel": channel, "attempts": attempts,
                                  "truncated": truncated, "llm_called": called}}
        row["n_hits"] = sum(1 for v in labels.values() if v and v.get("hit") is True)
        if row["n_hits"]:
            stats["hit_notes"] += 1
            if ig == "I2":
                i2_hit += 1
            else:
                n2_hit += 1
            for rid, lab in labels.items():
                if lab and lab.get("hit") is True and rid in per_rule_hit:
                    per_rule_hit[rid] += 1
        out_rows.append(row)
    meta_row = {"_meta": True, "prompt_sha256": PROMPT_SHA256, "rules_sha256": rules_sha,
                "model": model, "max_tokens": args.max_tokens,
                "counts": dict(stats, notes=len(notes), per_rule=per_rule_hit, i2_total=i2_total,
                               i2_hit=i2_hit, noni2_total=n2_total, noni2_hit=n2_hit),
                "updated_at": _now_iso()}
    tmp = args.out + ".tmp"
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta_row, ensure_ascii=False) + "\n")
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, args.out)
    i2_pct = 100.0 * i2_hit / i2_total if i2_total else 0.0
    n2_pct = 100.0 * n2_hit / n2_total if n2_total else 0.0
    print("[compliance] 笔记 %d | 调用 %d 次（复用 %d、失败 %d） | 命中 ≥1 条: %d 条 | %s | 按意图 I2 命中率 %.1f%% / 非 I2 %.1f%%"
          % (len(notes), stats["llm_calls"], stats["reused"], stats["failed"], stats["hit_notes"],
             " ".join("%s=%d" % (rid, per_rule_hit[rid]) for rid in sorted(per_rule_hit)), i2_pct, n2_pct))
    if args.sample > 0 and out_rows:
        write_sample(args.sample, notes, out_rows)

def write_sample(n, notes, out_rows):
    """Spot-check markdown for the owner (random.Random(2027) draw)."""
    pool_by_id = {x.get("note_id"): x for x in notes}
    picked = random.Random(2027).sample(out_rows, min(n, len(out_rows)))
    lines = ["# 合规抽检样本（random.Random(2027)，共 %d 条）" % len(picked), ""]
    for row in picked:
        text, _ = _note_text(pool_by_id.get(row.get("note_id")) or {})
        hits = [(rid, lab) for rid, lab in (row.get("labels") or {}).items() if lab and lab.get("hit")]
        lines.append("## %s（%s / %s / n_hits=%s）" % (row.get("note_id"), row.get("org"), row.get("intent"), row.get("n_hits")))
        lines += ["- %s 命中，evidence：%s" % (rid, lab.get("evidence")) for rid, lab in hits] or ["- 无命中规则"]
        lines += ["", "> " + text[:200].replace("\n", " "), ""]
    os.makedirs(os.path.dirname(SAMPLE_PATH) or ".", exist_ok=True)
    with open(SAMPLE_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("[compliance] 抽检样本已写入 %s" % SAMPLE_PATH)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never crash the pipeline; exit 0 per card
        print("[compliance] 运行出错：%r" % (exc,))

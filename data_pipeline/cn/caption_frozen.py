#!/usr/bin/env python
"""data_pipeline/cn/caption_frozen.py -- frozen image captions for the TC arm.

Card D (DECISIONS #10, PREREG v1.5 draft section A1). Offline artefact builder:

  python data_pipeline/cn/caption_frozen.py \
      --pool data/creatives/cn/content_pool_v1_masked.jsonl \
      --images-root <dir with one file per image_id> \
      --out data/creatives/cn/content_pool_v1_captioned.jsonl

or with a research-repo pool whose rows already carry `image_paths_resized`
(those paths are used as-is; --images-root is then not needed).

For every pool row the script sends each image ONCE to the GLM vision model
with a FROZEN neutral-description prompt (temperature 0, 1 image per call,
3-attempt retry ladder, --max-calls budget guard) and writes the SAME rows
plus:
  image_caption_frozen: list[str] -- one <=40-char Chinese sentence per
      image, same order as the row's images ("" when the file is missing or
      the budget ran out; re-running resumes those)
  caption_meta: {model, prompt_sha, generated_at, leak_flags}

The first line of --out is a `_meta` object with prompt_sha256, model and
counts. The pool file is never modified; --out is rewritten atomically and is
resume-safe: images already captioned in --out with the SAME prompt_sha and
model are reused with zero new calls. A leak audit flags captions containing
any word from the fixed LEAK_WORDS list; --strict exits 1 on any flag.

Pure stdlib + requests + yaml, plus the installed flowmirror package for the
GLM config loader, the MIME/data-URI packing and the seeded RNG. Console
output is ASCII-only (Chinese lives in the frozen prompt constants, the
audit list and the UTF-8 output files). No PIL is allowed here, so the
"768 px max" contract is met by feeding pre-resized images
(image_paths_resized / a resized images-root); an oversized payload only
produces a console warning.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests

from flowmirror.agents.prompt import image_data_url
from flowmirror.agents.runtime import _load_glm_config
from flowmirror.io.hashing import rng_seed_from, sha256_text

# ---------------------------------------------------------------------------
# Frozen prompt (verbatim from Card D; NEVER edit -- prompt_sha256 is pinned).
# PROMPT_SHA256 hashes the exact system + blank line + user-text bytes; every
# "was this caption made with the frozen prompt?" check uses it.
# ---------------------------------------------------------------------------
PROMPT_SYSTEM = ("你是一个只描述画面内容的助手。只写画面上有什么（文字、数字、图表类型、"
                 "人物或物体），不评价好坏，不推测意图，不使用颜色、表情、情绪和美感词汇。")
PROMPT_USER_TEXT = "用不超过40个字描述这张图的内容。只输出这一句话。"
PROMPT_SHA256 = sha256_text(PROMPT_SYSTEM + "\n\n" + PROMPT_USER_TEXT)

MAX_CAPTION_CHARS = 40
MAX_ATTEMPTS = 3            # retry ladder per image
BACKOFF_ERROR_S = 5.0       # multiplied by the attempt index (5s, 10s)
BACKOFF_EMPTY_S = 15.0
REQUEST_TIMEOUT_S = 300
MAX_TOKENS = 96             # 40 Chinese chars fit comfortably; guards rambling
BIG_DATAURI_LEN = 4_000_000  # > ~3MB image: probably not a 768px resize
DEFAULT_MODEL = "glm-4.6v"
DEFAULT_ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"

# Fixed leak-audit list (Card D). Single-character colour words match any
# occurrence (e.g. 金 inside 基金) -- conservative by design; the audit only
# flags, it never edits the real creative captions.
LEAK_CATEGORIES = {
    "colour": ["红", "橙", "黄", "绿", "蓝", "紫", "黑", "白", "灰", "金", "粉", "彩"],
    "affect": ["漂亮", "好看", "精美", "高级", "温馨", "喜悦", "焦虑", "恐慌",
               "诱人", "吸引", "美观", "大气"],
    "eval": ["优秀", "出色", "不错", "很好", "差", "糟糕", "值得", "推荐", "建议"],
}
LEAK_WORDS = sorted({w for words in LEAK_CATEGORIES.values() for w in words})
_WORD_CATEGORY = {w: cat for cat, words in LEAK_CATEGORIES.items() for w in words}


def audit_caption(caption):
    """-> list of (word, category) for every LEAK word contained in caption."""
    flags = []
    for word in LEAK_WORDS:  # sorted -> deterministic flag order
        if word in caption:
            flags.append((word, _WORD_CATEGORY[word]))
    return flags


def normalize_caption(raw):
    """First non-empty line, stripped of wrapping quotes and whitespace."""
    text = (raw or "").strip()
    for a, b in (("「", "」"), ("『", "』"), ("\u201c", "\u201d"), ('"', '"'), ("'", "'")):
        if len(text) >= 2 and text.startswith(a) and text.endswith(b):
            text = text[1:-1].strip()
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def fit_caption(raw):
    """-> (caption <= MAX_CAPTION_CHARS, truncated_flag)."""
    text = normalize_caption(raw)
    if len(text) > MAX_CAPTION_CHARS:
        return text[:MAX_CAPTION_CHARS], True
    return text, False


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def glm_caption_image(path, model=None, endpoint=None, api_key=None,
                      max_attempts=MAX_ATTEMPTS):
    """One GLM vision call for one local image. -> (caption, calls_made).

    caption is "" when every attempt failed; calls_made counts HTTP attempts
    (the budget guard debits all of them, including failures)."""
    model = model or DEFAULT_MODEL
    endpoint = endpoint or DEFAULT_ENDPOINT
    if not api_key:
        print("[error] no GLM api key: set config/api.yaml, FLOWMIRROR_GLM_KEY "
              "or the legacy key file (see flowmirror.agents.runtime)")
        return "", 0
    try:
        data_uri = image_data_url(path)  # verbatim MIME/data-URI packing
    except (OSError, ValueError) as exc:
        print(f"[error] {os.path.basename(path)}: cannot pack image ({exc})")
        return "", 1
    if len(data_uri) > BIG_DATAURI_LEN:
        print(f"[warn] {os.path.basename(path)}: large data-URI; expected "
              "pre-resized <=768px images")
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT_USER_TEXT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ],
    }
    headers = {"Authorization": "Bearer " + api_key}
    calls = 0
    for attempt in range(1, max_attempts + 1):
        calls += 1
        try:
            resp = requests.post(endpoint, json=payload, headers=headers,
                                 timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            print(f"[warn] {os.path.basename(path)}: attempt {attempt}/{max_attempts} "
                  f"network error: {type(exc).__name__}")
            if attempt < max_attempts:
                time.sleep(BACKOFF_ERROR_S * attempt)
            continue
        if resp.status_code != 200:
            print(f"[warn] {os.path.basename(path)}: attempt {attempt}/{max_attempts} "
                  f"HTTP {resp.status_code}")
            if attempt < max_attempts:
                time.sleep(BACKOFF_ERROR_S * attempt)
            continue
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            content = ""
        text = normalize_caption(content)
        if not text:
            if attempt < max_attempts:
                time.sleep(BACKOFF_EMPTY_S)
            continue
        return text, calls
    print(f"[fail] {os.path.basename(path)}: no caption after {max_attempts} attempts")
    return "", calls


def row_images(row, images_root):
    """Image resolution per Card D. -> list of (key, path_or_None).

    `image_paths_resized` (research-repo pool) wins when present and is used
    as-is; otherwise files resolve to <images-root>/<image_id>. The key is
    the image_id (or the path string) and is stable across runs, which is
    what resume matching relies on."""
    out = []
    for p in row.get("image_paths_resized") or []:
        out.append((str(p), str(p)))
    if out:
        return out
    for iid in row.get("image_ids") or []:
        iid = str(iid)
        out.append((iid, os.path.join(images_root, iid) if images_root else None))
    return out


def read_pool(pool_path):
    rows = []
    with open(pool_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                print(f"[warn] pool line {lineno}: not JSON, skipped")
                continue
            if isinstance(obj, dict) and not obj.get("_meta"):
                rows.append(obj)
            else:
                print(f"[warn] pool line {lineno}: not a content row, skipped")
    return rows


def load_resume_state(out_path, model):
    """image key -> {'caption','generated_at'} for images already captioned in
    out_path with the SAME frozen prompt_sha and model (everything else
    regenerates)."""
    state = {}
    if not out_path or not os.path.isfile(out_path):
        return state
    with open(out_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("_meta"):
                continue
            meta = row.get("caption_meta") or {}
            if meta.get("prompt_sha") != PROMPT_SHA256 or meta.get("model") != model:
                continue
            caps = row.get("image_caption_frozen") or []
            gen = str(meta.get("generated_at") or "")
            for (key, _path), cap in zip(row_images(row, None), caps):
                if isinstance(cap, str) and cap.strip():
                    state[key] = {"caption": cap, "generated_at": gen}
    return state


def process(pool_path, out_path, images_root, model, captioner, max_calls, dry_run):
    """Caption every pool image (resume-aware, budget-guarded) and rewrite out.
    -> (counts, results) with results = [{'key','caption'}] for sampling."""
    rows = read_pool(pool_path)
    resume = load_resume_state(out_path, model)

    plan, needs_root = [], False
    for ri, row in enumerate(rows):
        if (row.get("image_ids") or []) and not (row.get("image_paths_resized") or []) \
                and not images_root:
            needs_root = True
        for key, path in row_images(row, images_root):
            plan.append((ri, key, path))
    found = sum(1 for _ri, _k, p in plan if p and os.path.isfile(p))
    missing_list = [k for _ri, k, p in plan if not (p and os.path.isfile(p))]
    reusable = sum(1 for _ri, k, _p in plan if k in resume)
    todo = sum(1 for _ri, k, p in plan
               if k not in resume and p and os.path.isfile(p))
    print(f"[plan] pool={pool_path} rows={len(rows)} images={len(plan)} "
          f"found={found} missing={len(missing_list)}")
    print(f"[plan] out={out_path} already-captioned={reusable} todo={todo} "
          f"planned-calls={min(todo, max_calls)} max-calls={max_calls} model={model}")
    if needs_root:
        print("[plan] hint: pool rows use image_ids but --images-root is not set; "
              "those images count as missing")
    if missing_list:
        shown = " ".join(missing_list[:10])
        more = " ..." if len(missing_list) > 10 else ""
        print(f"[plan] missing/unresolved ({len(missing_list)}): {shown}{more}")

    counts = {"rows": len(rows), "images": len(plan), "found": found,
              "missing": len(missing_list), "reused": 0, "generated": 0,
              "failed": 0, "budget_exhausted": 0, "truncated": 0, "calls": 0,
              "max_calls": max_calls, "leak_flagged": 0}
    leak_categories = {"colour": 0, "affect": 0, "eval": 0}
    if dry_run:
        print("[dry-run] zero API calls made; nothing written")
        counts["leak_categories"] = leak_categories
        results = [{"key": k, "caption": v["caption"]}
                   for k, v in sorted(resume.items())]
        return counts, results

    cache = dict(resume)  # in-run memo also covers images shared by rows
    calls_left = max_calls
    out_rows, results = [], []
    for row in rows:
        caps, times, flags, keys = [], [], [], []
        for key, path in row_images(row, images_root):
            keys.append(key)
            raw, gen = "", ""
            if key in cache:
                raw = cache[key]["caption"]
                gen = cache[key]["generated_at"]
                counts["reused"] += 1
            elif path and os.path.isfile(path):
                if calls_left > 0:
                    raw, used = captioner(path)
                    calls_left -= used
                    counts["calls"] += used
                    if raw:
                        counts["generated"] += 1
                        gen = _now_iso()
                    else:
                        counts["failed"] += 1
                else:
                    counts["budget_exhausted"] += 1
            cap, truncated = fit_caption(raw)
            if truncated:
                counts["truncated"] += 1
            caps.append(cap)
            if cap:
                if gen:
                    times.append(gen)
                if key not in cache:
                    cache[key] = {"caption": cap, "generated_at": gen}
                results.append({"key": key, "caption": cap})
        row_flags = 0
        for idx, cap in enumerate(caps):
            if not cap:
                continue
            hits = audit_caption(cap)
            if hits:
                row_flags += 1
            for word, cat in hits:
                flags.append({"image": keys[idx], "index": idx,
                              "word": word, "category": cat})
                leak_categories[cat] += 1
        counts["leak_flagged"] += row_flags
        row_out = dict(row)
        row_out["image_caption_frozen"] = caps
        row_out["caption_meta"] = {
            "model": model,
            "prompt_sha": PROMPT_SHA256,
            "generated_at": (max(times) if times else ""),
            "leak_flags": flags,
        }
        out_rows.append(row_out)

    print(f"[leak-audit] flagged captions: {counts['leak_flagged']}/{len(results)} "
          f"(colour={leak_categories['colour']} affect={leak_categories['affect']} "
          f"eval={leak_categories['eval']})")
    meta = {"_meta": True, "prompt_sha256": PROMPT_SHA256, "model": model,
            "counts": dict(counts, leak_categories=leak_categories),
            "updated_at": _now_iso()}
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for row_out in out_rows:
            fh.write(json.dumps(row_out, ensure_ascii=False) + "\n")
    os.replace(tmp, out_path)
    print(f"[out] wrote {out_path} (prompt_sha256={PROMPT_SHA256[:12]}..., "
          f"generated={counts['generated']} reused={counts['reused']} "
          f"failed={counts['failed']} budget_exhausted={counts['budget_exhausted']})")
    counts["leak_categories"] = leak_categories
    return counts, results


def write_sample(results, n, out_path):
    """caption_review_sample.md next to --out: N seeded-random images with
    captions for the owner's manual fidelity check (PREREG: mean >= 8/10)."""
    rng = random.Random(rng_seed_from("caption_frozen", "sample", str(n)))
    results = list(results)
    picks = rng.sample(results, min(n, len(results))) if results else []
    directory = os.path.dirname(os.path.abspath(out_path)) or "."
    path = os.path.join(directory, "caption_review_sample.md")
    lines = [
        "# caption review sample",
        "",
        f"images: {len(picks)} of {len(results)} captioned "
        "(seed tag: caption_frozen/sample/%d)." % n,
        "Manual fidelity check per PREREG v1.5 draft A1: score each caption",
        "0-10; average >= 8/10 required for the frozen captions to ship.",
        "",
        "| # | image | caption | score (0-10) |",
        "|---|-------|---------|--------------|",
    ]
    for i, item in enumerate(picks, 1):
        cap = str(item["caption"]).replace("|", "/").replace("\n", " ")
        key = str(item["key"]).replace("|", "/")
        lines.append(f"| {i} | {key} | {cap} | |")
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Self-test (offline: no network, no GLM key, no sleeps).
# ---------------------------------------------------------------------------
_SELFTEST_CAPTIONS = [
    ("画面是一张折线图，横轴写着1月至6月，纵轴标有数字刻度。", set()),
    ("图中有一位穿西装的人物，手里拿着一张卡片，卡片上写着三个数字。", set()),
    ("这是一张柱状图，共五根柱子，每根柱子顶部标有一个百分数。", set()),
    ("红色曲线从左下角延伸到右上角，旁边标注了两个数字。", {"colour"}),
    ("背景是金色文字，写着基金名称和一行小字。", {"colour"}),
    ("整体排版精美，画面看起来很温馨。", {"affect"}),
    ("这张图做得不错，值得推荐给客户。", {"eval"}),
    ("人物表情喜悦，正在看向一台笔记本电脑。", {"affect"}),
    ("黄金价格走势图，图例位于右上角。", {"colour"}),
    ("深蓝色背景上有一张表格，表格共三列。", {"colour"}),
]


def _read_jsonl_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict) and not obj.get("_meta"):
                rows.append(obj)
    return rows


def _read_meta(path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                obj = json.loads(line)
                return obj if isinstance(obj, dict) and obj.get("_meta") else None
    return None


def _rewrite_rows_keep_meta(path, rows):
    """Self-test helper: rewrite out keeping the leading _meta line intact."""
    meta_line = ""
    with open(path, "r", encoding="utf-8") as fh:
        first = fh.readline().strip()
        if first:
            obj = json.loads(first)
            if isinstance(obj, dict) and obj.get("_meta"):
                meta_line = first
    with open(path, "w", encoding="utf-8") as fh:
        if meta_line:
            fh.write(meta_line + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def self_test():
    failures = []
    # 1) leak audit on 10 hand-written captions
    for i, (cap, expected) in enumerate(_SELFTEST_CAPTIONS, 1):
        got = {cat for _w, cat in audit_caption(cap)}
        if got != expected:
            failures.append(f"leak-audit #{i}: expected {sorted(expected)} got {sorted(got)}")
    # 2) caption fitting / normalisation
    text, truncated = fit_caption("  「" + "图" * 45 + "」  ")
    if len(text) != 40 or text != "图" * 40 or not truncated:
        failures.append("fit_caption: >40 chars must truncate to 40")
    text, truncated = fit_caption("画面上有一行文字。")
    if text != "画面上有一行文字。" or truncated:
        failures.append("fit_caption: clean caption must pass through")
    if fit_caption("第一行\n第二行")[0] != "第一行":
        failures.append("normalize_caption: must keep only the first line")
    # 3) resume logic on temp files (stub captioner, zero network calls)
    with tempfile.TemporaryDirectory() as td:
        imgdir = os.path.join(td, "imgs")
        os.makedirs(imgdir)
        ids = [f"IMG_{i:03d}.png" for i in range(4)]
        for iid in ids:
            with open(os.path.join(imgdir, iid), "wb") as fh:
                fh.write(b"\x89PNG-fake-bytes")
        pool = os.path.join(td, "pool.jsonl")
        with open(pool, "w", encoding="utf-8") as fh:
            for cid, chunk in (("c1", ids[:2]), ("c2", ids[2:])):
                fh.write(json.dumps({"content_id": cid, "image_ids": chunk},
                                    ensure_ascii=False) + "\n")
        out = os.path.join(td, "out.jsonl")
        state = {"n": 0}

        def stub(path):
            state["n"] += 1
            return f"stub-caption-{state['n']}", 1

        counts, _res = process(pool, out, imgdir, "stub-model", stub, 2, False)
        if state["n"] != 2 or counts["generated"] != 2 or counts["budget_exhausted"] != 2:
            failures.append("resume: first pass must stop at --max-calls 2")
        rows = _read_jsonl_rows(out)
        if rows[0]["image_caption_frozen"] != ["stub-caption-1", "stub-caption-2"]:
            failures.append("resume: first pass row 1 captions wrong")
        if rows[1]["image_caption_frozen"] != ["", ""]:
            failures.append("resume: first pass row 2 must be empty")
        counts, _res = process(pool, out, imgdir, "stub-model", stub, 10, False)
        if state["n"] != 4:
            failures.append("resume: second pass must reuse the 2 done images")
        rows = _read_jsonl_rows(out)
        if rows[0]["image_caption_frozen"] != ["stub-caption-1", "stub-caption-2"]:
            failures.append("resume: reused captions must be byte-identical")
        if rows[1]["image_caption_frozen"] != ["stub-caption-3", "stub-caption-4"]:
            failures.append("resume: second pass must caption the remaining 2")
        # a stale prompt_sha invalidates the cache for that row only
        rows[1]["caption_meta"]["prompt_sha"] = "0" * 64
        _rewrite_rows_keep_meta(out, rows)
        counts, _res = process(pool, out, imgdir, "stub-model", stub, 10, False)
        if state["n"] != 6:
            failures.append("resume: stale prompt_sha must force regeneration")
        rows = _read_jsonl_rows(out)
        if rows[0]["image_caption_frozen"] != ["stub-caption-1", "stub-caption-2"]:
            failures.append("resume: valid row must stay cached")
        if rows[0].get("content_id") != "c1" or rows[0].get("image_ids") != ids[:2]:
            failures.append("out: original pool fields must be preserved")
        meta = _read_meta(out)
        if not meta or meta.get("prompt_sha256") != PROMPT_SHA256 or "counts" not in meta:
            failures.append("out: _meta line must carry prompt_sha256 and counts")
        for row in rows:
            if set(row["caption_meta"]) != {"model", "prompt_sha",
                                            "generated_at", "leak_flags"}:
                failures.append("out: caption_meta keys must be exactly the frozen four")
        # 4) dry-run: zero calls, nothing written
        out2 = os.path.join(td, "out2.jsonl")
        counts, _res = process(pool, out2, imgdir, "stub-model", stub, 10, True)
        if os.path.exists(out2) or counts["calls"] != 0 or state["n"] != 6:
            failures.append("dry-run: must make zero calls and write nothing")
    if failures:
        for f in failures:
            print(f"[self-test] FAIL {f}")
        return 1
    print("[self-test] ok (leak audit x10, fit/normalize, resume, dry-run, meta)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Frozen neutral image captions for the TC arm (Card D). "
                    "Standalone; use --self-test for offline checks.")
    ap.add_argument("--pool", help="content-pool JSONL (image_ids or image_paths_resized)")
    ap.add_argument("--out", help="output JSONL (same rows + image_caption_frozen + caption_meta)")
    ap.add_argument("--images-root", default=None,
                    help="dir with one image file per image_id (for image_ids pools)")
    ap.add_argument("--model", default=None,
                    help="vision model (default: config vision model, glm-4.6v)")
    ap.add_argument("--max-calls", type=int, default=500,
                    help="budget guard: max GLM HTTP attempts, failures included (default 500)")
    ap.add_argument("--sample", type=int, default=0, metavar="N",
                    help="also write caption_review_sample.md with N seeded-random captions")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any caption is flagged by the leak audit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (images found/missing) with zero calls; write nothing")
    ap.add_argument("--self-test", action="store_true", help="offline self-test (no network)")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.pool or not args.out:
        ap.error("--pool and --out are required unless --self-test is used")

    endpoint, api_key, vision_model, _text = _load_glm_config()
    model = args.model or vision_model or DEFAULT_MODEL
    captioner = functools.partial(glm_caption_image, model=model,
                                  endpoint=endpoint, api_key=api_key)
    counts, results = process(args.pool, args.out, args.images_root, model,
                              captioner, args.max_calls, args.dry_run)
    if args.sample > 0:
        path = write_sample(results, args.sample, args.out)
        print(f"[sample] wrote {path} ({args.sample} requested; "
              "manual check: mean score >= 8/10)")
    incomplete = counts["missing"] + counts["failed"] + counts["budget_exhausted"]
    if incomplete:
        print(f"[warn] {incomplete} image(s) without captions "
              f"(missing={counts['missing']} failed={counts['failed']} "
              f"budget_exhausted={counts['budget_exhausted']}); re-run to resume")
    if args.strict and counts["leak_flagged"]:
        print(f"[strict] {counts['leak_flagged']} caption(s) flagged by the "
              "leak audit -> exit 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

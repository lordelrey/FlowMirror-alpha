#!/usr/bin/env python
"""data_pipeline/cn/caption_frozen.py -- frozen image captions for the TC arm.

Card D (DECISIONS #10, PREREG v1.5 draft section A1) + Card CAP (make the
captioner actually produce captions against reasoning-class vision models).
Offline artefact builder:

  python data_pipeline/cn/caption_frozen.py \
      --pool data/creatives/cn/content_pool_v1_masked.jsonl \
      --images-root "D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images" \
      --out data/creatives/cn/content_pool_v1_captioned.jsonl \
      --model glm-5.3-flash

or with a research-repo pool whose rows already carry `image_paths_resized`
(those paths are used as-is, sha-verified when the row carries
`image_sha256`; --images-root is then not needed).

For every pool row the script sends each image ONCE to the GLM vision model
with a FROZEN neutral-description prompt (temperature 0, 1 image per call,
3-attempt retry ladder, --max-calls budget guard) and writes the SAME rows
plus:
  image_caption_frozen: list[str] -- one <=40-char Chinese sentence per
      image, same order as the row's images ("" when the file is missing,
      fails its sha256 check, or the budget ran out; re-running resumes)
  caption_meta: {model, prompt_sha, generated_at, leak_flags, channels,
      token_budgets} -- channels[i] is "content" or "reasoning" (which
      transport produced caption i) and token_budgets[i] the final
      max_tokens budget spent on that image (Card CAP provenance).

Card CAP #1 -- reasoning-model transport. glm-4.6v and glm-5.3-flash are
reasoning models on the coding endpoint: a small max_tokens budget is spent
in message.reasoning_content and message.content comes back "" (measured on
a real 95KB pool image: glm-5.3-flash max_tokens=300 -> finish=length,
content=''; max_tokens=2500 -> finish=stop at 1196 tokens -- and glm-5.3-
flash is the model that noticed the risk-disclaimer line glm-4v-flash
invented away, so it is the model TC must use: the caption IS the
stimulus). DEFAULT_MODEL is therefore glm-5.3-flash, the default
--max-tokens is 2500 (~2x the measured 1196), and the ladder adds the
engine's two recoveries: (a) reasoning-channel salvage -- empty content +
a usable final-answer sentence inside reasoning_content -> caption with
channel="reasoning"; (b) the escalate_tokens idiom from
flowmirror.agents.runtime -- when NEITHER channel yields anything usable,
the next attempt runs with a doubled (hard-capped) max_tokens instead of
burning all three attempts at the same budget. Only the transport changed;
the prompt bytes and PROMPT_SHA256 are untouched (pre-registered).

Card CAP #2 -- resolution with sha256 parity. image_ids resolve against
--images-root: flat <root>/<image_id> first (the pre-resized 768px store
the TV arm itself displays), then a recursive search under the root as a
fallback; a candidate is accepted ONLY when its sha256 equals the row's
image_sha256 at that index, which guarantees the TC caption describes the
exact bytes the TV arm displays (a pre-registration requirement, not a
nicety). Found-but-wrong files are refused and counted as sha_mismatch;
the console prints the resolution summary
`resolved=N missing=N sha_mismatch=N`. Pool rows WITHOUT image_sha256
cannot be verified and accept a name match as-is (legacy behaviour).

The >40-char rule is the pre-registered Card-D one and is UNCHANGED:
normalize_caption keeps the first non-empty line and strips wrapping
quotes; fit_caption then hard-truncates to 40 chars and FLAGS it
(counts['truncated'], visible in the console and the _meta line) -- a
counted, visible truncation, never a silent mid-sentence cut introduced
by this card.

The first line of --out is a `_meta` object with prompt_sha256, model,
max_tokens and counts. The pool file is never modified; --out is rewritten
atomically and is resume-safe: images already captioned in --out with the
SAME prompt_sha and model are reused with zero new calls (their recorded
channel/token-budget provenance is carried over; '' / 0 for pre-CAP files).
A leak audit flags captions containing any word from the fixed LEAK_WORDS
list; --strict exits 1 on any flag.

Pure stdlib + requests + yaml, plus the installed flowmirror package for
the GLM config loader, the MIME/data-URI packing and the seeded RNG.
Console output is ASCII-only (Chinese lives in the frozen prompt
constants, the audit list, the salvage heuristics and the UTF-8 output
files; main() additionally reconfigures stdout/stderr with
errors="replace" as a belt-and-braces guard). No PIL is allowed here, so
the "768 px max" contract is met by feeding pre-resized images
(image_paths_resized / the resized images-root); an oversized payload only
produces a console warning.

The repo bootstrap below the imports lets the documented plain-script
invocation run from any working directory, with or without an editable
install (same idiom as make_demo_nav.py).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests

# --- repo bootstrap (same idiom as data_pipeline/cn/make_demo_nav.py):
# running this file as a plain script ("python data_pipeline/cn/
# caption_frozen.py ...") puts data_pipeline/cn on sys.path[0], not the
# repo root, so the flowmirror imports below fail with ModuleNotFoundError
# without an editable install.  When imported as a module (__package__ is
# set) the bootstrap is a no-op. ---------------------------------------------
if __package__ in (None, ""):
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

from flowmirror.agents.prompt import image_data_url
from flowmirror.agents.runtime import _load_glm_config
from flowmirror.io.hashing import rng_seed_from, sha256_text

# ---------------------------------------------------------------------------
# Frozen prompt (verbatim from Card D; NEVER edit -- prompt_sha256 is pinned).
# PROMPT_SHA256 hashes the exact system + blank line + user-text bytes; every
# "was this caption made with the frozen prompt?" check uses it.
# ---------------------------------------------------------------------------
PROMPT_SYSTEM = ("你是一个只描述画面内容的助手。只写画面上有什么（文字、数字、图表类型、人物或物体），"
                 "不评价好坏，不推测意图，不使用颜色、表情、情绪和美感词汇。")
PROMPT_USER_TEXT = "用不超过40个字描述这张图的内容。只输出这一句话。"
PROMPT_SHA256 = sha256_text(PROMPT_SYSTEM + "\n\n" + PROMPT_USER_TEXT)

MAX_CAPTION_CHARS = 40
MAX_ATTEMPTS = 3            # retry ladder per image
BACKOFF_ERROR_S = 5.0       # multiplied by the attempt index (5s, 10s)
BACKOFF_EMPTY_S = 15.0
REQUEST_TIMEOUT_S = 300
BIG_DATAURI_LEN = 4_000_000  # > ~3MB image: probably not a 768px resize
# Card CAP #1: glm-4.6v and glm-5.3-flash are REASONING models on the coding
# endpoint -- the old MAX_TOKENS=96 (and even 300) is spent inside
# message.reasoning_content and message.content returns '' with
# finish_reason='length'.  Measured on a real 95KB pool image:
#   glm-5.3-flash max_tokens=300  -> finish=length, content=''
#   glm-5.3-flash max_tokens=2500 -> finish=stop at 1196 tokens, caption
#       INCLUDING the risk-disclaimer line glm-4v-flash missed
# Default therefore 2500: ~2x headroom over the observed 1196.
DEFAULT_MAX_TOKENS = 2500
TOKEN_ESCALATE_FACTOR = 2   # escalate_tokens idiom (flowmirror.agents.runtime)
MAX_TOKENS_CAP = 8192       # hard ceiling for the escalation ladder
# Card CAP #1: glm-5.3-flash (reasoning, but reads the risk-disclaimer lines
# glm-4v-flash invents away -- an information-content difference between TC
# and TV that the experiment must not have).
DEFAULT_MODEL = "glm-5.3-flash"
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

# Card CAP #1: reasoning-salvage heuristics (transport only; deterministic
# and offline-testable).  An explicit answer marker wins; otherwise the last
# sentence of the last non-empty line is tried.  Text that still reads like
# planning/first-person reasoning, has no CJK at all, or an implausible
# length is rejected so the caller escalates max_tokens instead.
_ANSWER_MARKERS = ("最终答案", "答案", "一句话描述", "描述如下", "输出如下", "回复如下", "答复如下")
_REASONING_PREFIXES = ("我", "需要", "应该", "用户", "题", "首先", "然后", "接下来",
                       "好的", "那么")
_CJK_RE = re.compile("[\u4e00-\u9fff]")
_SALVAGE_MIN_CHARS = 4
_SALVAGE_MAX_CHARS = 2 * MAX_CAPTION_CHARS


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
    """-> (caption <= MAX_CAPTION_CHARS, truncated_flag).

    The pre-registered Card-D rule, UNCHANGED by Card CAP: normalize_caption
    takes the first non-empty line (wrapping quotes stripped); if it is
    longer than 40 chars it is HARD-TRUNCATED to the first 40 chars with
    truncated=True -- a flagged, counted truncation (counts['truncated']),
    never a silent mid-sentence cut, and never a re-ask (the call budget is
    already spent)."""
    text = normalize_caption(raw)
    if len(text) > MAX_CAPTION_CHARS:
        return text[:MAX_CAPTION_CHARS], True
    return text, False


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _looks_like_caption(text):
    """Salvage gate: plausible final-answer sentence (CJK, sane length, not
    obviously first-person planning prose)."""
    text = (text or "").strip()
    if len(text) < _SALVAGE_MIN_CHARS or len(text) > _SALVAGE_MAX_CHARS:
        return False
    if not _CJK_RE.search(text):  # the frozen prompt demands a Chinese sentence
        return False
    return not text.startswith(_REASONING_PREFIXES)


def salvage_reasoning(reasoning):
    """Recover the final answer sentence from the reasoning channel (Card CAP
    #1; the reasoning-salvage path the engine already uses).  (1) an explicit
    answer marker ('...最终答案：X') in the last three non-empty lines -> the
    text after the LAST marker occurrence; else (2) the last sentence of the
    last non-empty line.  Anything that still reads like reasoning is
    rejected -> "" so the caller escalates max_tokens on the next attempt."""
    text = (reasoning or "").strip()
    if not text:
        return ""
    lines = [ln.strip(" \t-*>") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    for line in reversed(lines[-3:]):
        for marker in _ANSWER_MARKERS:
            idx = line.rfind(marker)
            if idx >= 0:
                tail = line[idx + len(marker):].strip("：:，,、 ")
                if _looks_like_caption(tail):
                    return tail
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;.])", lines[-1])
                 if s.strip()]
    for sent in reversed(sentences):
        if _looks_like_caption(sent):
            return sent
    return ""


def escalate_tokens(current):
    """Token-budget escalation for empty reasoning-model responses (the
    escalate_tokens idiom from flowmirror.agents.runtime.call_glm: multiply
    the budget, hard cap).  Returns the budget for the NEXT attempt."""
    return min(int(current) * TOKEN_ESCALATE_FACTOR, MAX_TOKENS_CAP)


def glm_caption_image(path, model=None, endpoint=None, api_key=None,
                      max_attempts=MAX_ATTEMPTS, max_tokens=None):
    """One GLM vision call ladder for one local image.

    -> (caption, calls_made, channel, max_tokens_final).  caption is "" when
    every attempt failed; calls_made counts HTTP attempts (the budget guard
    debits all of them, including failures); channel is "content" or
    "reasoning" for the channel the returned caption came from ("" on
    failure); max_tokens_final is the budget of the last attempt.  The
    ladder per attempt (Card CAP #1): usable content -> return; else usable
    reasoning salvage -> return (channel="reasoning"); else escalate
    max_tokens (escalate_tokens idiom) for the next attempt instead of
    retrying the same budget three times."""
    model = model or DEFAULT_MODEL
    endpoint = endpoint or DEFAULT_ENDPOINT
    budget = int(max_tokens) if max_tokens else DEFAULT_MAX_TOKENS
    if not api_key:
        print("[error] no GLM api key: set config/api.yaml, FLOWMIRROR_GLM_KEY "
              "or the legacy key file (see flowmirror.agents.runtime)")
        return "", 0, "", 0
    try:
        data_uri = image_data_url(path)  # verbatim MIME/data-URI packing
    except (OSError, ValueError) as exc:
        print(f"[error] {os.path.basename(path)}: cannot pack image ({exc})")
        return "", 1, "", 0
    if len(data_uri) > BIG_DATAURI_LEN:
        print(f"[warn] {os.path.basename(path)}: large data-URI; expected "
              "pre-resized <=768px images")
    headers = {"Authorization": "Bearer " + api_key}
    calls = 0
    for attempt in range(1, max_attempts + 1):
        calls += 1
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": budget,
            "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": PROMPT_USER_TEXT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
        }
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
        content, reasoning, finish = "", "", None
        try:
            choice = resp.json()["choices"][0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError):
            content, reasoning = "", ""
        text = normalize_caption(content)
        if text:
            return text, calls, "content", budget
        salvaged = salvage_reasoning(reasoning)
        if salvaged:
            print(f"[salvage] {os.path.basename(path)}: empty content, recovered "
                  f"the final answer from reasoning_content (attempt {attempt}, "
                  f"max_tokens={budget})")
            return salvaged, calls, "reasoning", budget
        if attempt < max_attempts:
            new_budget = escalate_tokens(budget)
            print(f"[warn] {os.path.basename(path)}: attempt {attempt}/{max_attempts} "
                  f"no usable content (finish={finish}); escalate max_tokens "
                  f"{budget} -> {new_budget}")
            budget = new_budget
            time.sleep(BACKOFF_EMPTY_S)
    print(f"[fail] {os.path.basename(path)}: no caption after {max_attempts} "
          f"attempts (final max_tokens={budget})")
    return "", calls, "", budget


# ---------------------------------------------------------------------------
# Image resolution (Card CAP #2): sha256 parity with the pool.  A candidate
# file is accepted ONLY when its sha256 equals the row's image_sha256 at that
# index, so the TC caption is guaranteed to describe the exact bytes the TV
# arm displays.  Resolution order: flat <root>/<image_id> first (the 768px
# store), then a recursive search under --images-root as fallback.
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def _cached_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@functools.lru_cache(maxsize=None)
def _recursive_find_all(root, filename):
    """Every file named `filename` under root (recursive fallback), in walk
    order.  Returns a tuple so the lru_cache stays immutable."""
    hits = []
    for dirpath, _dirnames, files in os.walk(root):
        if filename in files:
            hits.append(os.path.join(dirpath, filename))
    return tuple(hits)


def _verify_hash(path, expected_sha):
    """True iff the file's sha256 equals the pool's expected 64-hex value.
    A pool without image_sha256 at this index cannot be verified and accepts
    by name (legacy behaviour); an unreadable file counts as a mismatch
    (refuse to caption)."""
    expected = (expected_sha or "").strip().lower()
    if not expected:
        return True
    try:
        return _cached_sha256(path) == expected
    except OSError:
        return False


def _resolve_by_id(image_id, expected_sha, images_root):
    """-> (key, path_or_None, status) with status in resolved/missing/
    sha_mismatch.  Flat candidate first; only on flat miss/failure does the
    (more expensive) recursive fallback run.  Only hash-matching files are
    accepted."""
    if not images_root:
        return (image_id, None, "missing")
    flat = os.path.join(images_root, image_id)
    if os.path.isfile(flat) and _verify_hash(flat, expected_sha):
        return (image_id, flat, "resolved")
    candidates = [flat] if os.path.isfile(flat) else []
    candidates += [p for p in _recursive_find_all(images_root, image_id)
                   if p != flat]
    for cand in candidates:
        if _verify_hash(cand, expected_sha):
            return (image_id, cand, "resolved")
    if candidates:
        return (image_id, None, "sha_mismatch")
    return (image_id, None, "missing")


def _resolve_explicit(path, expected_sha):
    """Resolution for image_paths_resized pools: the path is used as-is and
    sha-verified when the row carries a hash at that index."""
    if os.path.isfile(path) and _verify_hash(path, expected_sha):
        return (str(path), path, "resolved")
    if os.path.isfile(path):
        return (str(path), None, "sha_mismatch")
    return (str(path), None, "missing")


def _row_expected_shas(row):
    """The row's image_sha256 list, index-aligned with image_ids /
    image_paths_resized (entries may be '' when the pool omits a hash)."""
    return [str(s) for s in row.get("image_sha256") or []]


def row_image_keys(row):
    """Stable per-image keys (the image_id, or the resized-path string for
    research-repo pools); resume matching relies on them being stable."""
    resized = [str(p) for p in row.get("image_paths_resized") or []]
    if resized:
        return resized
    return [str(i) for i in row.get("image_ids") or []]


def resolve_row_images(row, images_root):
    """Image resolution per Card CAP #2. -> list of (key, path_or_None,
    status) in row order, keys identical to row_image_keys."""
    shas = _row_expected_shas(row)
    resized = [str(p) for p in row.get("image_paths_resized") or []]
    if resized:
        return [_resolve_explicit(p, shas[i] if i < len(shas) else "")
                for i, p in enumerate(resized)]
    out = []
    for i, iid in enumerate(row.get("image_ids") or []):
        out.append(_resolve_by_id(str(iid), shas[i] if i < len(shas) else "",
                                  images_root))
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
    """image key -> {'caption','generated_at','channel','max_tokens'} for
    images already captioned in out_path with the SAME frozen prompt_sha and
    model (everything else regenerates).  channel/max_tokens come from the
    previous run's caption_meta ('' / 0 for pre-CAP output files)."""
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
            channels = meta.get("channels") or []
            budgets = meta.get("token_budgets") or []
            gen = str(meta.get("generated_at") or "")
            keys = row_image_keys(row)
            for idx in range(min(len(keys), len(caps))):
                cap = caps[idx]
                if isinstance(cap, str) and cap.strip():
                    state[keys[idx]] = {
                        "caption": cap,
                        "generated_at": gen,
                        "channel": str(channels[idx]) if idx < len(channels) else "",
                        "max_tokens": _to_int(budgets[idx]) if idx < len(budgets) else 0,
                    }
    return state


def process(pool_path, out_path, images_root, model, captioner, max_calls,
            dry_run, max_tokens=None):
    """Caption every pool image (resolution-checked, resume-aware,
    budget-guarded) and rewrite out. -> (counts, results) with results =
    [{'key','caption'}] for sampling."""
    rows = read_pool(pool_path)
    resume = load_resume_state(out_path, model)

    row_plans = []
    resolve_counts = {"resolved": 0, "missing": 0, "sha_mismatch": 0}
    missing_keys, mismatch_keys, needs_root = [], [], False
    for row in rows:
        if (row.get("image_ids") or []) and not (row.get("image_paths_resized") or []) \
                and not images_root:
            needs_root = True
        entries = resolve_row_images(row, images_root)
        row_plans.append(entries)
        for key, _path, status in entries:
            resolve_counts[status] += 1
            if status == "missing":
                missing_keys.append(key)
            elif status == "sha_mismatch":
                mismatch_keys.append(key)
    plan = [(ri, key, path, status)
            for ri, entries in enumerate(row_plans)
            for (key, path, status) in entries]
    # Resume is honoured only for images that STILL resolve (and still hash
    # OK): a cached caption for a file that no longer matches the pool's
    # sha256 must not ship.
    reusable = sum(1 for _ri, k, _p, s in plan if s == "resolved" and k in resume)
    todo = sum(1 for _ri, k, _p, s in plan if s == "resolved" and k not in resume)
    print(f"[plan] pool={pool_path} rows={len(rows)} images={len(plan)} model={model}")
    print(f"[resolve] resolved={resolve_counts['resolved']} "
          f"missing={resolve_counts['missing']} "
          f"sha_mismatch={resolve_counts['sha_mismatch']} "
          f"(accepted files sha256-match the pool image_sha256 at that "
          f"index; recursive fallback under --images-root)")
    print(f"[plan] out={out_path} already-captioned={reusable} todo={todo} "
          f"planned-calls={min(todo, max_calls)} max-calls={max_calls} "
          f"max-tokens={int(max_tokens) if max_tokens else DEFAULT_MAX_TOKENS}")
    if needs_root:
        print("[plan] hint: pool rows use image_ids but --images-root is not set; "
              "those images count as missing")
    for label, keys in (("missing", missing_keys), ("sha_mismatch", mismatch_keys)):
        if keys:
            shown = " ".join(keys[:10])
            more = " ..." if len(keys) > 10 else ""
            print(f"[resolve] {label} ({len(keys)}): {shown}{more}")

    counts = {"rows": len(rows), "images": len(plan),
              "resolved": resolve_counts["resolved"],
              "missing": resolve_counts["missing"],
              "sha_mismatch": resolve_counts["sha_mismatch"],
              "reused": 0, "generated": 0, "failed": 0,
              "budget_exhausted": 0, "truncated": 0, "calls": 0,
              "max_calls": max_calls, "leak_flagged": 0}
    leak_categories = {"colour": 0, "affect": 0, "eval": 0}
    if dry_run:
        print("[dry-run] zero API calls made; nothing written")
        counts["leak_categories"] = leak_categories
        results = [{"key": k, "caption": resume[k]["caption"]}
                   for _ri, k, _p, s in plan if s == "resolved" and k in resume]
        return counts, results

    cache = dict(resume)  # in-run memo also covers images shared by rows
    calls_left = max_calls
    out_rows, results = [], []
    for ri, row in enumerate(rows):
        caps, times, flags, keys = [], [], [], []
        channels, budgets = [], []
        for key, path, status in row_plans[ri]:
            keys.append(key)
            raw, gen, channel, budget = "", "", "", 0
            if status != "resolved":
                pass  # missing / sha_mismatch: never captioned, never reused
            elif key in cache:
                raw = cache[key]["caption"]
                gen = cache[key]["generated_at"]
                channel = str(cache[key].get("channel") or "")
                budget = _to_int(cache[key].get("max_tokens"))
                counts["reused"] += 1
            elif calls_left > 0:
                raw, used, channel, budget = captioner(path)
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
            channels.append(channel)
            budgets.append(budget)
            if cap:
                if gen:
                    times.append(gen)
                if key not in cache:
                    cache[key] = {"caption": cap, "generated_at": gen,
                                  "channel": channel, "max_tokens": budget}
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
            "channels": channels,         # Card CAP: per-image transport
            "token_budgets": budgets,     # Card CAP: final budget per image
        }
        out_rows.append(row_out)

    print(f"[leak-audit] flagged captions: {counts['leak_flagged']}/{len(results)} "
          f"(colour={leak_categories['colour']} affect={leak_categories['affect']} "
          f"eval={leak_categories['eval']})")
    meta = {"_meta": True, "prompt_sha256": PROMPT_SHA256, "model": model,
            "max_tokens": (int(max_tokens) if max_tokens else DEFAULT_MAX_TOKENS),
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
          f"failed={counts['failed']} sha_mismatch={counts['sha_mismatch']} "
          f"budget_exhausted={counts['budget_exhausted']})")
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
# Self-test (offline: no network, no GLM key, no real sleeps).  The ladder
# tests script the HTTP layer via module-global stubs restored in finally.
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


class _FakeResp:
    """Scripted HTTP-200 response for the offline ladder tests."""

    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRequests:
    """Stand-in for the requests module: pops scripted responses in order and
    records every payload sent (zero real network calls)."""

    RequestException = requests.RequestException  # keeps the except valid

    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, endpoint, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return self.responses.pop(0)


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

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    # 1) leak audit on 10 hand-written captions
    for i, (cap, expected) in enumerate(_SELFTEST_CAPTIONS, 1):
        got = {cat for _w, cat in audit_caption(cap)}
        check(got == expected,
              f"leak-audit #{i}: expected {sorted(expected)} got {sorted(got)}")
    # 2) caption fitting / normalisation (the pre-registered >40-char rule:
    #    first non-empty line, quotes stripped, hard truncate to 40 + flag)
    text, truncated = fit_caption("  「" + "图" * 45 + "」  ")
    check(len(text) == 40 and text == "图" * 40 and truncated,
          "fit_caption: >40 chars must truncate to 40")
    text, truncated = fit_caption("画面上有一行文字。")
    check(text == "画面上有一行文字。" and not truncated,
          "fit_caption: clean caption must pass through")
    check(fit_caption("第一行\n第二行")[0] == "第一行",
          "normalize_caption: must keep only the first line")
    # 3) reasoning salvage + token escalation (Card CAP #1)
    check(salvage_reasoning("先分析构图。最终答案：图片底部有一行风险提示文字，上方是机构标志。")
          == "图片底部有一行风险提示文字，上方是机构标志。",
          "salvage: must recover the sentence after an explicit answer marker")
    check(salvage_reasoning("The user wants a neutral description.\n画面中有一条折线和两个数字。")
          == "画面中有一条折线和两个数字。",
          "salvage: no marker -> last sentence of the last line")
    check(salvage_reasoning("我需要再看看图片的底部。") == "",
          "salvage: first-person reasoning must be rejected")
    check(salvage_reasoning("Let me think.") == "" and salvage_reasoning("") == "",
          "salvage: non-CJK / empty reasoning must be rejected")
    check(escalate_tokens(DEFAULT_MAX_TOKENS) == DEFAULT_MAX_TOKENS * 2
          and escalate_tokens(MAX_TOKENS_CAP * 4) == MAX_TOKENS_CAP,
          "escalate_tokens: doubling with a hard cap")
    # 4) the GLM ladder against scripted responses (offline: network, image
    #    packing and sleeps are stubbed as module globals, restored in finally)
    def run_ladder(responses):
        fake = _FakeRequests(responses)
        g = globals()
        saved = (g["requests"], g["image_data_url"], time.sleep)
        g["requests"] = fake
        g["image_data_url"] = lambda path: "data:image/png;base64," + "A" * 64
        time.sleep = lambda _s: None
        try:
            out = glm_caption_image("fake.png", model="glm-5.3-flash",
                                    api_key="test-key")
        finally:
            g["requests"], g["image_data_url"] = saved[0], saved[1]
            time.sleep = saved[2]
        return out, fake.payloads

    ans = "图片底部有一行风险提示文字，上方是机构标志。"
    (cap, calls, channel, budget), payloads = run_ladder([
        _FakeResp({"choices": [{"finish_reason": "length",
                                "message": {"content": "",
                                            "reasoning_content": "先分析构图。最终答案：" + ans}}]})])
    check(cap == ans and calls == 1 and channel == "reasoning"
          and budget == DEFAULT_MAX_TOKENS,
          "ladder: empty content + usable reasoning -> channel='reasoning', no escalation")
    check(bool(payloads) and payloads[0]["max_tokens"] == DEFAULT_MAX_TOKENS
          and payloads[0]["temperature"] == 0
          and payloads[0]["messages"][0]["content"] == PROMPT_SYSTEM
          and payloads[0]["messages"][1]["content"][0]["text"] == PROMPT_USER_TEXT,
          "ladder: attempt 1 must use the --max-tokens budget and the frozen prompt")
    (cap, calls, channel, budget), payloads = run_ladder([
        _FakeResp({"choices": [{"finish_reason": "length",
                                "message": {"content": "",
                                            "reasoning_content": "我还需要检查图片底部是否有文字。"}}]}),
        _FakeResp({"choices": [{"finish_reason": "stop",
                                "message": {"content": "图片展示了一个城市天际线和一行文字。",
                                            "reasoning_content": "ok"}}]})])
    check(cap == "图片展示了一个城市天际线和一行文字。" and calls == 2
          and channel == "content",
          "ladder: unusable reasoning must not be returned as a caption")
    check([p["max_tokens"] for p in payloads] ==
          [DEFAULT_MAX_TOKENS, DEFAULT_MAX_TOKENS * 2],
          "ladder: an empty attempt must escalate the next attempt's budget")
    bad = _FakeResp({"choices": [{"finish_reason": "length",
                                  "message": {"content": "",
                                              "reasoning_content": "用户想要客观描述。"}}]})
    (cap, calls, channel, budget), payloads = run_ladder([bad, bad, bad])
    check(cap == "" and calls == MAX_ATTEMPTS and channel == "",
          "ladder: all attempts empty -> empty caption, no channel")
    check([p["max_tokens"] for p in payloads] ==
          [DEFAULT_MAX_TOKENS, DEFAULT_MAX_TOKENS * 2, MAX_TOKENS_CAP]
          and budget == MAX_TOKENS_CAP,
          "ladder: budgets must escalate default -> x2 -> cap across 3 attempts")
    # 5) resolution (sha parity + recursive fallback) and resume on temp
    #    files (stub captioner, zero network calls)
    with tempfile.TemporaryDirectory() as td:
        imgdir = os.path.join(td, "imgs")
        subdir = os.path.join(imgdir, "nested")
        os.makedirs(subdir)
        ids = [f"IMG_{i:03d}.png" for i in range(4)]
        blobs = [b"png-fake-bytes-" + str(i).encode() for i in range(4)]
        for i, iid in enumerate(ids):
            with open(os.path.join(imgdir, iid), "wb") as fh:
                fh.write(blobs[i])
        os.remove(os.path.join(imgdir, ids[3]))     # only in the nested dir
        with open(os.path.join(subdir, ids[3]), "wb") as fh:
            fh.write(blobs[3])
        shas = [hashlib.sha256(b).hexdigest() for b in blobs]
        wrong = "0" * 64
        pool = os.path.join(td, "pool.jsonl")
        with open(pool, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"content_id": "c1", "image_ids": ids[:2],
                                 "image_sha256": shas[:2]},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"content_id": "c2", "image_ids": ids[2:],
                                 "image_sha256": [wrong, shas[3]]},
                                ensure_ascii=False) + "\n")
        out = os.path.join(td, "out.jsonl")
        state = {"n": 0}

        def stub(path):
            state["n"] += 1
            return f"stub-caption-{state['n']}", 1, "content", DEFAULT_MAX_TOKENS

        counts, _res = process(pool, out, imgdir, "stub-model", stub, 2, False)
        check(counts["resolved"] == 3 and counts["missing"] == 0
              and counts["sha_mismatch"] == 1,
              "resolve: 3 resolved (1 via recursive fallback), 1 sha_mismatch")
        check(state["n"] == 2 and counts["generated"] == 2
              and counts["budget_exhausted"] == 1,
              "resume: first pass must stop at --max-calls 2; the sha_mismatch image is never captioned")
        rows = _read_jsonl_rows(out)
        check(rows[0]["image_caption_frozen"] == ["stub-caption-1", "stub-caption-2"],
              "resume: first pass row 1 captions wrong")
        check(rows[1]["image_caption_frozen"] == ["", ""],
              "resume: first pass row 2 must be empty")
        check(rows[0]["caption_meta"]["channels"] == ["content", "content"]
              and rows[0]["caption_meta"]["token_budgets"] ==
              [DEFAULT_MAX_TOKENS, DEFAULT_MAX_TOKENS],
              "caption_meta: must record channel and token budget per image")
        counts, _res = process(pool, out, imgdir, "stub-model", stub, 10, False)
        check(state["n"] == 3,
              "resume: second pass must reuse the 2 done images")
        rows = _read_jsonl_rows(out)
        check(rows[0]["image_caption_frozen"] == ["stub-caption-1", "stub-caption-2"],
              "resume: reused captions must be byte-identical")
        check(rows[0]["caption_meta"]["channels"] == ["content", "content"]
              and rows[0]["caption_meta"]["token_budgets"] ==
              [DEFAULT_MAX_TOKENS, DEFAULT_MAX_TOKENS],
              "resume: channel/token provenance must survive reuse")
        check(rows[1]["image_caption_frozen"] == ["", "stub-caption-3"],
              "resume: second pass must caption the remaining resolved image")
        # a stale prompt_sha invalidates the cache for that row only
        rows[1]["caption_meta"]["prompt_sha"] = "0" * 64
        _rewrite_rows_keep_meta(out, rows)
        counts, _res = process(pool, out, imgdir, "stub-model", stub, 10, False)
        check(state["n"] == 4,
              "resume: stale prompt_sha must force regeneration")
        rows = _read_jsonl_rows(out)
        check(rows[0]["image_caption_frozen"] == ["stub-caption-1", "stub-caption-2"],
              "resume: valid row must stay cached")
        check(rows[1]["image_caption_frozen"] == ["", "stub-caption-4"],
              "resume: regenerated row must re-caption its resolved image")
        check(rows[0].get("content_id") == "c1" and rows[0].get("image_ids") == ids[:2]
              and rows[0].get("image_sha256") == shas[:2],
              "out: original pool fields must be preserved")
        meta = _read_meta(out)
        check(bool(meta) and meta.get("prompt_sha256") == PROMPT_SHA256
              and "counts" in meta and meta.get("max_tokens") == DEFAULT_MAX_TOKENS,
              "out: _meta line must carry prompt_sha256, counts and the token budget")
        for row in rows:
            cm = row["caption_meta"]
            if set(cm) != {"model", "prompt_sha", "generated_at", "leak_flags",
                           "channels", "token_budgets"}:
                failures.append("out: caption_meta keys must be exactly the frozen six")
                break
            if len(cm["channels"]) != len(row["image_caption_frozen"]) \
                    or len(cm["token_budgets"]) != len(row["image_caption_frozen"]):
                failures.append("out: channels/token_budgets must align with captions")
                break
        # resolution edge cases
        ent = resolve_row_images({"image_ids": [ids[0]]}, imgdir)
        check(bool(ent) and ent[0][2] == "resolved"
              and ent[0][1] == os.path.join(imgdir, ids[0]),
              "resolve: pool rows without image_sha256 accept the flat file by name")
        ent = resolve_row_images({"image_ids": ["nope.png"],
                                  "image_sha256": ["f" * 64]}, imgdir)
        check(bool(ent) and ent[0][2] == "missing",
              "resolve: unknown id must be missing")
        ent = resolve_row_images({"image_paths_resized": [os.path.join(imgdir, ids[1])],
                                  "image_sha256": [wrong]}, imgdir)
        check(bool(ent) and ent[0][2] == "sha_mismatch",
              "resolve: resized-path pools are sha-verified too")
        ent = resolve_row_images({"image_ids": [ids[3]],
                                  "image_sha256": [shas[3]]}, imgdir)
        check(bool(ent) and ent[0][2] == "resolved"
              and ent[0][1] == os.path.join(subdir, ids[3]),
              "resolve: recursive fallback must find the nested file")
        # 6) dry-run: zero calls, nothing written
        out2 = os.path.join(td, "out2.jsonl")
        counts, _res = process(pool, out2, imgdir, "stub-model", stub, 10, True)
        check(not os.path.exists(out2) and counts["calls"] == 0 and state["n"] == 4,
              "dry-run: must make zero calls and write nothing")
    if failures:
        for f in failures:
            print(f"[self-test] FAIL {f}")
        return 1
    print("[self-test] ok (leak audit x10, fit/normalize, salvage+escalation, "
          "scripted GLM ladder x3, resolve+sha parity, resume+provenance, "
          "dry-run, meta)")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # ASCII console; never die on a
        if hasattr(stream, "reconfigure"):   # stray non-ASCII print
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    ap = argparse.ArgumentParser(
        description="Frozen neutral image captions for the TC arm (Card D + "
                    "Card CAP). Standalone; use --self-test for offline checks.")
    ap.add_argument("--pool", help="content-pool JSONL (image_ids or image_paths_resized)")
    ap.add_argument("--out", help="output JSONL (same rows + image_caption_frozen + caption_meta)")
    ap.add_argument("--images-root", default=None,
                    help="image store, e.g. the TV arm's 768px store "
                         "fundmarket-sim/sim/content_pool_v1/images: flat "
                         "<root>/<image_id> first, recursive fallback second; "
                         "files are sha256-verified against the pool's "
                         "image_sha256 (mismatch -> refused)")
    ap.add_argument("--model", default=None,
                    help="vision model (default: glm-5.3-flash -- the reasoning "
                         "model that reads the risk-disclaimer lines glm-4v-flash "
                         "misses; the config vision model is deliberately not "
                         "the default since glm-4.6v also reasons)")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="max completion tokens per attempt (default 2500: "
                         "glm-5.3-flash spent 1196 tokens on a real pool image "
                         "and finished with stop; 96 or 300 return empty content "
                         "with finish=length). The ladder escalates this budget "
                         "when the reasoning channel eats it")
    ap.add_argument("--max-calls", type=int, default=500,
                    help="budget guard: max GLM HTTP attempts, failures included (default 500)")
    ap.add_argument("--sample", type=int, default=0, metavar="N",
                    help="also write caption_review_sample.md with N seeded-random captions")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any caption is flagged by the leak audit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and resolution summary (resolved/missing/"
                         "sha_mismatch) with zero calls; write nothing")
    ap.add_argument("--self-test", action="store_true", help="offline self-test (no network)")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.pool or not args.out:
        ap.error("--pool and --out are required unless --self-test is used")
    if args.max_tokens < 1:
        ap.error("--max-tokens must be >= 1")

    endpoint, api_key, _vision_model, _text = _load_glm_config()
    # Card CAP: the captioner's frozen default is glm-5.3-flash; the config
    # vision model (glm-4.6v) is intentionally NOT the default any more --
    # it is a reasoning model that returns content='' under small budgets.
    model = args.model or DEFAULT_MODEL
    captioner = functools.partial(glm_caption_image, model=model,
                                  endpoint=endpoint, api_key=api_key,
                                  max_tokens=args.max_tokens)
    counts, results = process(args.pool, args.out, args.images_root, model,
                              captioner, args.max_calls, args.dry_run,
                              max_tokens=args.max_tokens)
    if args.sample > 0:
        path = write_sample(results, args.sample, args.out)
        print(f"[sample] wrote {path} ({args.sample} requested; "
              "manual check: mean score >= 8/10)")
    incomplete = (counts["missing"] + counts["sha_mismatch"]
                  + counts["failed"] + counts["budget_exhausted"])
    if incomplete:
        print(f"[warn] {incomplete} image(s) without captions "
              f"(missing={counts['missing']} sha_mismatch={counts['sha_mismatch']} "
              f"failed={counts['failed']} "
              f"budget_exhausted={counts['budget_exhausted']}); re-run to resume")
    if args.strict and counts["leak_flagged"]:
        print(f"[strict] {counts['leak_flagged']} caption(s) flagged by the "
              "leak audit -> exit 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

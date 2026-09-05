"""flowmirror.agents.runtime -- the runtime around one live LLM agent decision.

Content-addressed response cache (LLMCache), a pre-authorization budget governor
(BudgetGovernor), the provider call reused verbatim from sim/elicit_base.py
(call_glm, CapStop, iter_json_objects -- only the key/endpoint loading adapted),
a deterministic MockLLM for zero-API dry runs, thin decide()/reflect() entry
points for flowmirror.engine.loop, and an order-preserving run_parallel().
Prompt assembly and parsing live in flowmirror.agents.prompt, never here.
"""
from __future__ import annotations

import inspect
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError

import requests
import yaml

from flowmirror.agents.prompt import (RETRY_SUFFIX, build_decision_messages,
                                      build_reflection_messages, extract_decision, parse_reflection)
from flowmirror.io.hashing import rng_seed_from, sha256_text

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_YAML = os.path.join(REPO_ROOT, "config", "api.yaml")
KEY_PATH = r"D:\Desktop\ABM paper\tools\.glm_key"
GLM_EP_DEFAULT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
MODEL = "glm-4.6v"
TEXT_MODEL = "glm-4.6"
TEMP = 0.3
MAX_ATTEMPTS = 5
BACKOFF_EMPTY_S = 15.0
BACKOFF_ERROR_S = 5.0
TOKEN_LADDER = (6144, 12288, 16384)
SCHEMA_VERSION = "v7"


def _load_glm_config():
    """config/api.yaml -> env FLOWMIRROR_GLM_KEY -> the legacy one-key file (the ONLY adapted part)."""
    ep, key, vis, txt = GLM_EP_DEFAULT, "", MODEL, TEXT_MODEL
    if os.path.isfile(API_YAML):
        try:
            with open(API_YAML, "r", encoding="utf-8") as fh:
                obj = yaml.safe_load(fh) or {}
            return (str(obj.get("endpoint") or ep), str(obj.get("api_key") or key),
                    str(obj.get("vision_model") or vis), str(obj.get("text_model") or txt))
        except Exception:
            pass
    if os.environ.get("FLOWMIRROR_GLM_KEY", "").strip():
        return ep, os.environ["FLOWMIRROR_GLM_KEY"].strip(), vis, txt
    try:
        if os.path.isfile(KEY_PATH):
            with open(KEY_PATH, "r", encoding="utf-8") as fh:
                legacy = fh.read().strip()
            if legacy:
                return ep, legacy, vis, txt
    except Exception:
        pass
    return ep, key, vis, txt


GLM_EP, GLM_KEY, MODEL, TEXT_MODEL = _load_glm_config()


def escalate_tokens(current):
    for nxt in TOKEN_LADDER:
        if nxt > int(current):
            return nxt
    return int(current)


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


class CapStop(BaseException):
    """Raised when the NEXT provider attempt would breach the reserve or the absolute all-elicitation cap.

    Deliberately a BaseException: call_glm() classifies provider failures with a broad `except Exception`, and a
    budget stop must never be swallowed and retried as if it were a provider error."""


class BudgetGovernor:
    """Pre-attempt authority for every provider attempt, simplified from elicit_base.AttemptGovernor:
    one lock, one hard cap, plain counters -- no elicitation-table bookkeeping.  authorize() runs
    BEFORE the attempt, so the attempt that would breach the cap is refused, never spent (B3/B4)."""

    def __init__(self, hard_cap_attempts):
        self.hard_cap = int(hard_cap_attempts)
        self.attempts = self.first_open = self.retries = 0
        self.failures = self.cache_hits = 0
        self.stopped, self.stop_msg = False, None
        self.lock = threading.Lock()

    def authorize(self, is_first_open):
        """Charge ONE provider attempt; raises CapStop before the attempt if it would breach the cap."""
        with self.lock:
            if self.attempts + 1 > self.hard_cap:
                if not self.stopped:
                    self.stopped = True
                    self.stop_msg = (f"hard_cap_attempts={self.hard_cap} would be exceeded "
                                     f"(attempts={self.attempts}); the next provider attempt is refused")
                raise CapStop(self.stop_msg)
            self.attempts += 1
            if is_first_open:
                self.first_open += 1
            else:
                self.retries += 1
            return True

    def record_failure(self):
        with self.lock:
            self.failures += 1

    def record_cache_hit(self):
        with self.lock:
            self.cache_hits += 1

    def state(self):
        with self.lock:
            return {"attempts": self.attempts, "first_open": self.first_open, "retries": self.retries,
                    "failures": self.failures, "cache_hits": self.cache_hits,
                    "hard_cap_attempts": self.hard_cap, "cap_remaining": self.hard_cap - self.attempts,
                    "stopped": self.stopped}


class LLMCache:
    """Append-only JSONL response cache.  Key = sha256(model|temp|schema_version|prompt_sha|image_shas).
    A cached FAILURE (parsed is None) is terminal and replays as a failure -- this is what makes a
    full re-run reproduce the event log byte-for-byte with zero API calls."""

    def __init__(self, path):
        self.path = path
        self.rows = {}
        self.hits = self.misses = 0
        self.lock = threading.Lock()
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(row, dict) and row.get("key"):
                        self.rows[row["key"]] = row          # last write wins

    @staticmethod
    def key_for(model, temp, prompt_sha, image_shas):
        return sha256_text(f"{model}|{temp}|{SCHEMA_VERSION}|{prompt_sha}|"
                           + ",".join(image_shas or []))

    def get(self, key):
        with self.lock:
            row = self.rows.get(key)
            if row is None:
                self.misses += 1
            else:
                self.hits += 1
        return row

    def put(self, key, row):
        with self.lock:
            self.rows[key] = row
            if self.path:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def call_glm(messages, max_tokens, model=None, parser=None, governor=None, first_open=False):
    """One initial attempt + at most 4 retries (<= 5 physical provider attempts) — R5.5 R2A frozen schedule.

    `parser(text, channel)` must return (obj, matched_text); a call is a SUCCESS only when it returns a complete
    schema-valid object (R5.5 must-fix 4/5: nonempty raw text is NOT success and must consume a retry).
    Every attempt is classified independently — no sticky empty flag can leak the 15s path into a later HTTP
    error (must-fix 7). Returns a provenance dict and never raises, EXCEPT CapStop: when a `governor` is supplied
    every single attempt — initial, retry and repair — is authorized BEFORE it is made (B3/B4), and the attempt
    that would breach the reserve or the absolute cap is refused instead of being spent."""
    headers = {"Authorization": f"Bearer {GLM_KEY}", "Content-Type": "application/json"}
    payload = {"model": model or MODEL, "messages": messages, "temperature": TEMP, "max_tokens": int(max_tokens)}
    prov = {"parsed": None, "raw": None, "response_source": None, "http_status": None, "attempts": 0,
            "finish_reason": None, "max_tokens_final": int(max_tokens), "parser_status": None,
            "raw_sha256": None, "usage": {}, "attempt_log": []}
    for k in range(1, MAX_ATTEMPTS + 1):                      # k = physical attempt index (1..5)
        if governor is not None:
            # B2/B3/B4: only attempt 1 on a first-time-opened prespecified ID is charged to the 15,120 base;
            # every retry, duplicate execution and repair-pass attempt is an EXTRA attempt against the reserve.
            governor.authorize(bool(first_open) and k == 1)
        prov["attempts"], prov["max_tokens_final"] = k, int(payload["max_tokens"])
        status, finish, empty = None, None, False
        parsed, raw_store, chan_text, source, cls = None, None, None, None, None
        try:
            resp = requests.post(GLM_EP, headers=headers, json=payload, timeout=180)
            status = resp.status_code
            if status == 200:
                data = resp.json()
                ch = data.get("choices") or []
                msg = (ch[0].get("message") or {}) if ch else {}
                finish = ch[0].get("finish_reason") if ch else None
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                prov["usage"] = dict(data.get("usage") or {}) or prov["usage"]
                if content.strip():
                    parsed, _m = parser(content, "content") if parser else (None, None)
                    chan_text, raw_store, source = content, content, "content"
                    cls = ("ok" if parsed is not None else
                           "truncated_length" if finish == "length" else
                           "schema_invalid" if iter_json_objects(content) else "no_json_object")
                else:
                    # HTTP-200 with empty content: FIRST test the reasoning channel for exactly one complete,
                    # schema-valid object (R2A salvage). Only a failed salvage takes the 15s / escalation path.
                    parsed, matched = parser(reasoning, "reasoning") if (parser and reasoning.strip()) else (None, None)
                    chan_text = reasoning
                    if parsed is not None:
                        raw_store, source, cls = matched, "reasoning_content", "ok"   # store only the answer JSON
                    else:
                        empty = True
                        cls = "reasoning_salvage_rejected" if reasoning.strip() else "empty_response"
            else:
                cls = "http_error"
        except Exception:
            cls = "exception"
        prov["http_status"], prov["finish_reason"], prov["parser_status"] = status, finish, cls
        prov["attempt_log"].append({"i": k, "http_status": status, "cls": cls, "finish_reason": finish,
                                    "max_tokens": int(payload["max_tokens"])})
        if cls == "ok":
            prov.update(parsed=parsed, raw=raw_store, response_source=source, raw_sha256=sha256_text(chan_text))
            return prov
        prov["raw"], prov["raw_sha256"], prov["response_source"] = raw_store, sha256_text(chan_text), None
        if empty or finish == "length":                       # budget exhaustion / truncation -> escalate tokens
            payload["max_tokens"] = escalate_tokens(payload["max_tokens"])
        if k < MAX_ATTEMPTS:                                  # no sleep after the 5th (terminal) failure
            time.sleep((BACKOFF_EMPTY_S if empty else BACKOFF_ERROR_S) * k)
    return prov                                               # terminal: parsed is None -> caller writes a hole row


def _iter_message_parts(messages):
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            yield "text", content
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    yield "text", part["text"]
                elif part.get("type") == "image_url":
                    yield "image", str((part.get("image_url") or {}).get("url") or "")


def _messages_text(messages):
    return "\n".join(t for kind, t in _iter_message_parts(messages) if kind == "text")


def _image_shas(messages):
    return [sha256_text(u) for kind, u in _iter_message_parts(messages) if kind == "image"]


_PARAM_CACHE = {}


def _call_prompt(fn, kwargs):
    """Call a flowmirror.agents.prompt builder with whichever of our kwargs its signature names."""
    params = _PARAM_CACHE.get(fn)
    if params is None:
        try:
            params = tuple(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            params = ()
        _PARAM_CACHE[fn] = params
    return fn(**{k: v for k, v in kwargs.items() if k in params}) if params else fn(**kwargs)


def _parse_pair(fn, txt):
    """Normalize a prompt-module parser to the (parsed, matched) pair call_glm's parser contract uses."""
    if not txt:
        return None, None
    for args in ((txt,), (txt, "content")):
        try:
            res = fn(*args)
        except TypeError:
            continue
        except Exception:
            return None, None
        if isinstance(res, tuple):
            return res[0], (res[1] if len(res) > 1 else None)
        return res, None
    return None, None


_VALID_ACTIONS = ("buy", "sell", "subscribe", "redeem", "hold", "ignore", "dca", "none")


def _decision_violations(parsed, shown):
    if parsed is None:
        return ["parse_failure"]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("trade"), dict):
        return ["trade_missing"]
    out, trade = [], parsed["trade"]
    if trade.get("action") not in _VALID_ACTIONS:
        out.append("bad_action")
    code, amt = trade.get("code"), trade.get("amount_pct")
    if trade.get("action") in ("buy", "sell", "subscribe", "redeem") and not code:
        out.append("code_missing")
    if not isinstance(amt, (int, float)) or isinstance(amt, bool):
        out.append("amount_not_numeric")
    if shown and code:
        valid = set(shown.get("codes") or []) | set(shown.get("held") or [])
        if valid and code not in valid:
            out.append("code_not_shown")
    return out


_SCHEMA_ECHO = ('Output ONLY one JSON object: {"mood": "..", "reason": "..", "trade": {"action": '
                '"buy|sell|hold", "code": "<6-digit code or null>", "amount_pct": 0-100, '
                '"sign_mismatch_confirm": true|false}, "comment": {"stance": "positive|neutral|negative", '
                '"text": ".."} or null}')


def _append_retry_suffix(messages):
    out = [dict(m) if isinstance(m, dict) else m for m in messages]
    extra = RETRY_SUFFIX + "\n" + _SCHEMA_ECHO
    for m in reversed(out):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                m["content"] = list(content) + [{"type": "text", "text": extra}]
            else:
                m["content"] = (content or "") + "\n\n" + extra
            break
    return out


def _llm_cfg(cfg):
    llm = dict((cfg or {}).get("llm") or {})
    return (str(llm.get("model") or MODEL), str(llm.get("text_model") or TEXT_MODEL),
            int(llm.get("max_tokens_start") or 6144), int(llm.get("max_attempts") or MAX_ATTEMPTS))


def _record_from_row(row, prompt_sha, img_shas, violations):
    prov = row.get("provenance") or {}
    parsed = row.get("parsed")          # a cached FAILURE replays as a failure -- terminal
    return {"parsed": parsed, "violations": violations,
            "parser_status": "ok" if parsed is not None else (prov.get("parser_status") or "unparsed"),
            "prompt_sha": prompt_sha, "raw_sha": prov.get("raw_sha256"), "image_shas": img_shas,
            "cache_hit": True, "attempts": int(prov.get("attempts") or 0), "notes": {"replay": True}}


def decide(agent_view, feed_cards, cfg, cache, governor, llm, shown):
    """One agent decision: cache lookup, else one authorized provider call plus at most one
    schema-echo retry; the outcome (success OR failure) is always written to the cache."""
    messages = _call_prompt(build_decision_messages,
                            {"agent_view": agent_view, "feed_cards": feed_cards, "cfg": cfg, "shown": shown})
    prompt_sha = sha256_text(_messages_text(messages))
    img_shas = _image_shas(messages)
    model, _txt, max_tokens, max_attempts = _llm_cfg(cfg)
    key = cache.key_for(model, TEMP, prompt_sha, img_shas)
    row = cache.get(key)
    if row is not None:
        governor.record_cache_hit()
        return _record_from_row(row, prompt_sha, img_shas, _decision_violations(row.get("parsed"), shown))
    parser = lambda txt, channel="content": _parse_pair(extract_decision, txt)
    notes = {"mode": "decision", "model": model, "retried": False}
    governor.authorize(True)
    prov = llm(messages, max_tokens=max_tokens, model=model, parser=parser, governor=governor, first_open=True)
    parsed = prov.get("parsed")
    if parsed is None:
        parsed, _m = _parse_pair(extract_decision, prov.get("raw") or "")
    total_attempts = int(prov.get("attempts") or 0)
    if parsed is None and max_attempts > 1:
        notes["retried"] = True
        governor.authorize(False)                              # retries += 1
        prov = llm(_append_retry_suffix(messages), max_tokens=max_tokens, model=model, parser=parser,
                   governor=governor, first_open=False)
        parsed = prov.get("parsed")
        if parsed is None:
            parsed, _m = _parse_pair(extract_decision, prov.get("raw") or "")
        total_attempts += int(prov.get("attempts") or 0)
    if parsed is None:
        governor.record_failure()
    raw = prov.get("raw") or ""
    rec = {"parsed": parsed, "violations": _decision_violations(parsed, shown),
           "parser_status": "ok" if parsed is not None else (prov.get("parser_status") or "unparsed"),
           "prompt_sha": prompt_sha,
           "raw_sha": prov.get("raw_sha256") or (sha256_text(raw) if raw else None),
           "image_shas": img_shas, "cache_hit": False, "attempts": total_attempts, "notes": notes}
    cache.put(key, {"key": key, "provenance": prov, "parsed": parsed, "raw": prov.get("raw"),
                    "ts": round(time.time(), 3)})
    return rec


def reflect(agent_view, cfg, cache, governor, llm):
    """One agent reflection (no retry): the same cache/governor pattern around
    build_reflection_messages / parse_reflection."""
    messages = _call_prompt(build_reflection_messages, {"agent_view": agent_view, "cfg": cfg})
    prompt_sha = sha256_text(_messages_text(messages))
    img_shas = _image_shas(messages)
    vis, text_model, max_tokens, _ma = _llm_cfg(cfg)
    model = text_model or vis
    key = cache.key_for(model, TEMP, prompt_sha, img_shas)
    row = cache.get(key)
    if row is not None:
        governor.record_cache_hit()
        return _record_from_row(row, prompt_sha, img_shas,
                                [] if row.get("parsed") is not None else ["parse_failure"])
    parser = lambda txt, channel="content": _parse_pair(parse_reflection, txt)
    governor.authorize(True)
    prov = llm(messages, max_tokens=max_tokens, model=model, parser=parser, governor=governor, first_open=True)
    parsed = prov.get("parsed")
    if parsed is None:
        parsed, _m = _parse_pair(parse_reflection, prov.get("raw") or "")
    if parsed is None:
        governor.record_failure()
    raw = prov.get("raw") or ""
    rec = {"parsed": parsed, "violations": [] if parsed is not None else ["parse_failure"],
           "parser_status": "ok" if parsed is not None else (prov.get("parser_status") or "unparsed"),
           "prompt_sha": prompt_sha,
           "raw_sha": prov.get("raw_sha256") or (sha256_text(raw) if raw else None),
           "image_shas": img_shas, "cache_hit": False, "attempts": int(prov.get("attempts") or 0),
           "notes": {"mode": "reflection", "model": model}}
    cache.put(key, {"key": key, "provenance": prov, "parsed": parsed, "raw": prov.get("raw"),
                    "ts": round(time.time(), 3)})
    return rec


def run_parallel(jobs, fn, workers):
    """fn(job) over ThreadPoolExecutor(workers); results in job order regardless of completion order.
    If any worker raised CapStop, cancel the rest and re-raise it after the pool has drained."""
    jobs = list(jobs)
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    results, cap = [None] * len(jobs), None
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futs = {ex.submit(fn, j): i for i, j in enumerate(jobs)}
        for fut in as_completed(futs):
            try:
                results[futs[fut]] = fut.result()
            except CapStop as e:
                if cap is None:
                    cap = e
                for other in futs:                      # cancel the not-yet-started rest
                    if other is not fut:
                        other.cancel()
            except CancelledError:
                continue
    if cap is not None:
        raise cap
    return results


_MOCK_REASONS = ("近期波动加大，先控制仓位。", "帖子有道理，但金额要保守。", "已持有类似方向，暂不加仓。",
                 "回撤超出预期，先观望。", "定投思路合适，小额参与。", "没有看懂逻辑，不动。")
_MOCK_COMMENTS = ("写得挺实在，赞一个。", "标题有点夸张了。", "关注了，等回调再考虑。", "风险提示得很及时。")
_MOCK_SUMMARIES = ("本周整体偏谨慎，减少了追高操作。", "市场震荡，保持既定仓位。", "情绪受帖子影响，略偏乐观。")
_MOCK_BELIEFS = ("高收益宣传需要打折看待。", "平台推送频率影响注意力。", "分散配置比押注单主题稳。")


def _codes_in(text):
    return sorted({m for m in re.findall(r"(?<!\d)(\d{6})(?!\d)", text or "")})


def _held_codes(text):
    out = set()
    for line in (text or "").splitlines():
        if "held:" in line.lower() or "held：" in line or line.strip().startswith("持仓"):
            out.update(_codes_in(line))
    return sorted(out)


def _account_level(text):
    m = re.search(r"C([1-5])", text or "")
    return "C" + m.group(1) if m else None


def _first_shown_r4(text, codes):
    for line in (text or "").splitlines():
        if "R4" in line:
            line_codes = _codes_in(line)
            if line_codes:
                return line_codes[0]
    return codes[0] if codes and "R4" in (text or "") else None


def _looks_like_reflection(text):
    head = (text or "")[:512]
    return "复盘" in (text or "") or "反思" in head or "reflection" in head.lower()


class MockLLM:
    """Deterministic zero-API LLM: random.Random seeded from the sha256 of the prompt's text parts,
    so the same prompt always produces the same raw output (and the same cached row)."""

    def __init__(self, force_c2_r4=False, malformed_rate=0.05):
        self.force_c2_r4 = bool(force_c2_r4)
        self.malformed_rate = float(malformed_rate)

    def __call__(self, messages, max_tokens, **kw):
        text = _messages_text(messages)
        rng = random.Random(rng_seed_from("mockllm", text))
        raw = self._render_reflection(rng) if _looks_like_reflection(text) else self._render_decision(text, rng)
        return {"parsed": None, "raw": raw, "response_source": "mock", "http_status": 200,
                "attempts": 1, "finish_reason": "stop", "max_tokens_final": int(max_tokens),
                "parser_status": "mock", "raw_sha256": sha256_text(raw), "usage": {},
                "attempt_log": [{"i": 1, "http_status": 200, "cls": "mock", "finish_reason": "stop",
                                 "max_tokens": int(max_tokens)}]}

    def _render_reflection(self, rng):
        return json.dumps({"summary": rng.choice(_MOCK_SUMMARIES),
                           "beliefs": [rng.choice(_MOCK_BELIEFS), rng.choice(_MOCK_BELIEFS)],
                           "market_view": rng.choice(("bullish", "neutral", "bearish")),
                           "risk_mood": rng.choice(("cautious", "stable", "aggressive"))},
                          ensure_ascii=False)

    def _render_decision(self, text, rng):
        obj = {"mood": rng.choice(("neutral", "cautious", "curious", "greedy", "fearful")),
               "reason": rng.choice(_MOCK_REASONS),
               "trade": {"action": "hold", "code": None, "amount_pct": 0.0,
                         "sign_mismatch_confirm": False},
               "comment": None}
        codes, held = _codes_in(text), _held_codes(text)
        if self.force_c2_r4 and _account_level(text) == "C2":
            r4 = _first_shown_r4(text, codes)
            if r4:
                obj["trade"] = {"action": "buy", "code": r4, "amount_pct": float(rng.choice((10, 20, 30))),
                                "sign_mismatch_confirm": False}
        else:
            roll = rng.random()
            if roll < 0.35 and codes:
                obj["trade"] = {"action": "buy", "code": rng.choice(codes),
                                "amount_pct": float(rng.randint(5, 50)), "sign_mismatch_confirm": False}
            elif roll < 0.60 and held:
                obj["trade"] = {"action": "sell", "code": rng.choice(held),
                                "amount_pct": float(rng.randint(10, 80)), "sign_mismatch_confirm": False}
        if rng.random() < 0.30:
            obj["comment"] = {"stance": rng.choice(("positive", "neutral", "negative")),
                              "text": rng.choice(_MOCK_COMMENTS)}
        out = json.dumps(obj, ensure_ascii=False)
        if rng.random() < self.malformed_rate:          # truncate so no complete {...} survives
            core = out.rstrip()
            out = core[: max(0, len(core) - rng.randint(1, 6))]
        return out


def _synth_agent(i, held_codes):
    return {"agent_id": f"A{i:03d}", "arm": "T" if i % 2 == 0 else "TV",
            "risk_level": "C2" if i % 3 else "C3", "persona": f"投资者{1000 + i}号，风格稳健，关注回撤。",
            "mood": ("neutral", "cautious", "curious", "greedy", "fearful")[i % 5],
            "cash_pct": 40.0, "day": 10 + (i % 5),
            "holdings": [{"code": c, "units": 800.0, "nav": 1.42, "cost": 1.35, "pnl_pct": 5.2,
                          "last": "redeem_declined"} for c in held_codes],
            "familiarity": {"ORG01": 1 + (i % 2)}, "follows": ["ORG01"], "memory": [], "experience": [],
            "news": {"index": "沪深300本周+1.2%", "guba": "情绪偏谨慎"}, "trend": {}, "direct": [],
            "social": {"climate": "neutral", "top": []}}


def _synth_cards(levels):
    out = []
    for j, r in enumerate(levels):
        out.append({"pid": f"P{j:03d}", "org": "ORG01", "intent": "push" if j % 3 == 0 else "edu",
                    "text": f"第{j}条帖子：近期市场波动与配置思路。",
                    "fund": {"code": f"{100001 + j * 11:06d}", "name": f"示例基金{j}", "r": r,
                             "family": "示例家族", "org": "ORG01", "r3m": 2.3, "r1y": 8.9,
                             "nav": 1.21 + j * 0.01},
                    "img": False, "likes": 10 + j, "comments": 2 + j})
    return out


def _ascii(s):
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def _self_test():
    tmp = tempfile.mkdtemp(prefix="flowmirror_runtime_st_")
    cfg = {"run_tag": "selftest", "llm": {"model": "mock-v", "text_model": "mock-t",
                                          "max_tokens_start": 6144, "max_attempts": 2},
           "social": True, "channels": {"feed": True, "experience": True, "trend": True, "social": True}}
    cache, gov = LLMCache(os.path.join(tmp, "cache.jsonl")), BudgetGovernor(10 ** 7)
    llm = MockLLM(malformed_rate=0.05)
    jobs, state = [], {}
    for i in range(200):
        held = [f"{200000 + (i % 20) * 13:06d}"] if i % 4 == 0 else []
        cards = _synth_cards([("R2", "R3", "R4")[i % 3] for _ in range(1 + i % 3)])
        shown = {"pids": [c["pid"] for c in cards], "codes": [c["fund"]["code"] for c in cards],
                 "held": list(held), "orgs": ["ORG01"]}
        jobs.append((_synth_agent(i, held), cards, cfg, cache, gov, llm, shown))
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"exception: {exc!r}"
        results.append((name, bool(ok), str(detail)))

    def first():
        recs = [decide(*j) for j in jobs]
        state["recs"] = recs
        req = ("parsed", "violations", "parser_status", "prompt_sha", "raw_sha", "image_shas",
               "cache_hit", "attempts", "notes")
        bad = sum(1 for r in recs if any(k not in r for k in req))
        okn = sum(1 for r in recs if r["parsed"] is not None)
        return okn >= 186 and bad == 0 and (200 - okn) <= 14, \
            f"parse_rate={okn}/200 failures={200 - okn} missing_keys={bad}"

    def replay():
        att0, hits0 = gov.attempts, cache.hits
        recs = [decide(*j) for j in jobs]
        all_hit = all(r["cache_hit"] for r in recs)
        same = sum(1 for a, b in zip(state["recs"], recs) if a["parsed"] == b["parsed"])
        return gov.attempts == att0 and cache.hits == hits0 + 200 and all_hit and same == 200, \
            f"attempts {att0}->{gov.attempts} hits {hits0}->{cache.hits} all_hit={all_hit} identical={same}/200"

    def forced():
        c2c, c2g, c2l = LLMCache(os.path.join(tmp, "c2.jsonl")), BudgetGovernor(10 ** 6), MockLLM(True, 0.0)
        n = 0
        for i in range(10):
            cards = _synth_cards(["R4", "R2"])
            view = _synth_agent(i, [])
            view["risk_level"] = "C2"
            shown = {"pids": [c["pid"] for c in cards], "codes": [c["fund"]["code"] for c in cards],
                     "held": [], "orgs": ["ORG01"]}
            rec = decide(view, cards, cfg, c2c, c2g, c2l, shown)
            tr = ((rec["parsed"] or {}).get("trade") or {})
            if tr.get("action") == "buy" and tr.get("code") == cards[0]["fund"]["code"]:
                n += 1
        return n >= 1, f"forced C2 x R4 buys={n}/10"

    def cap():
        g5 = BudgetGovernor(hard_cap_attempts=5)
        for _ in range(5):
            g5.authorize(True)
        try:
            g5.authorize(True)
            raised = False
        except CapStop:
            raised = True
        return raised and g5.attempts == 5, f"raised={raised} attempts={g5.attempts}"

    def par():
        def boom(j):
            if j == 30:
                raise CapStop("selftest stop")
            return j * 2

        def slow(j):
            time.sleep(0.002 * (5 - j % 5))     # reversed sleeps -> completion order != job order
            return j * 3

        raised = False
        try:
            run_parallel(list(range(60)), boom, 8)
        except CapStop:
            raised = True
        out = run_parallel(list(range(40)), slow, 6)
        order_ok = out == [j * 3 for j in range(40)]
        return raised and order_ok, f"capstop_raised={raised} order_preserved={order_ok}"

    check("decide_200_mock_first_pass", first)
    check("replay_second_pass_free", replay)
    check("force_c2_r4_buy", forced)
    check("budget_governor_cap", cap)
    check("run_parallel_order_and_capstop", par)
    width = max(len(n) for n, _, _ in results)
    fails = 0
    print(f"{'check':<{width}}  result  detail")
    print("-" * (width + 48))
    for name, ok, detail in results:
        print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}  {_ascii(detail)}")
        fails += 0 if ok else 1
    print(f"self-test: {len(results) - fails} passed, {fails} failed")
    return fails


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in argv:
        return _self_test()
    print("usage: python -m flowmirror.agents.runtime --self-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""flowmirror.agents.runtime -- the runtime around one live LLM agent decision.

Content-addressed response cache (LLMCache), a pre-authorization budget governor
(BudgetGovernor), the provider call reused verbatim from sim/elicit_base.py
(call_glm, CapStop, iter_json_objects -- only the credential loading adapted:
config/api.yaml, then env FLOWMIRROR_GLM_KEY, then the OPT-IN env-selected
legacy key file FLOWMIRROR_LEGACY_KEY_FILE; never a hard-coded path, and a key
is never printed or logged), a deterministic MockLLM for zero-API dry runs,
thin decide()/reflect() entry points for flowmirror.engine.loop, and an
order-preserving run_parallel().  Prompt assembly and parsing live in
flowmirror.agents.prompt, never here.

Every decide()/reflect() record carries failure_kind (contract 2.4): "transport" when the
provider never returned a model response, "model" when a response arrived but would not
parse, None when nothing failed.  Splitting the two is diagnosis only -- the
decision_failure_halt gate keeps the same threshold and the same condition (decision 9).
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
import threading
import time
_CLOCK = time.monotonic   # bound at import: tests stub `rt.time` with a sleep-only object
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError

import requests
import yaml

from flowmirror.agents.prompt import (REFLECTION_PROMPT_ZH, RETRY_SUFFIX,
                                      build_decision_messages, build_reflection_messages,
                                      extract_decision, parse_reflection)
from flowmirror.io.hashing import rng_seed_from, sha256_text

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_YAML = os.path.join(REPO_ROOT, "config", "api.yaml")
LEGACY_KEY_FILE_ENV = "FLOWMIRROR_LEGACY_KEY_FILE"
GLM_EP_DEFAULT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
MODEL = "glm-4.6v"
TEXT_MODEL = "glm-4.6"
# E9 / card RT2: these are now DEFAULTS behind config keys, not the effective values --
# a published experiment has to record its own sampling temperature, and a module
# constant never reaches run_meta.  The old names were TEMP and MAX_ATTEMPTS;
# MAX_ATTEMPTS read exactly like cfg["llm"]["max_attempts"], which is a DIFFERENT
# mechanism (the decision-level re-ask switch), and _llm_cfg below was already using the
# physical-retry constant as that macro switch's fallback.  Three distinct names so no
# reader can conflate them again, each spelled like the config key it backs.
TEMP_DEFAULT = 0.3                      # llm.temperature -- per-request sampling temperature
MAX_PROVIDER_ATTEMPTS_DEFAULT = 5       # llm.max_provider_attempts -- PHYSICAL HTTP ladder in call_glm
MAX_DECISION_ATTEMPTS_FALLBACK = 5      # llm.max_attempts fallback -- MACRO re-ask in decide(); that key
                                        # is required by run.schema.json, so only a hand-built
                                        # cfg dict (a test, the self-test) ever reaches this
BACKOFF_EMPTY_S = 15.0
BACKOFF_ERROR_S = 5.0
RATE_LIMIT_CAP_S_DEFAULT = 120.0        # one 429 wait never exceeds this, however long the provider asks
RATE_LIMIT_MAX_WAIT_S_DEFAULT = 600.0   # cumulative 429 waiting tolerated before the rung counts as failed
GATE_RECOVER_AFTER = 20                 # consecutive 200s that win one shrunk permit back
TOKEN_LADDER = (6144, 12288, 16384)
SCHEMA_VERSION = "v7"

# Contract 2.4 / audit E12: the call_glm `cls` values that mean NO model response was
# ever produced -- the provider was unreachable, refused the request, or answered with
# nothing.  Counting these as model failures is what made three rate-limit responses look
# like model instability while the parse rate was 77/77, and what would present a bad API
# key as "the model is unstable".  reasoning_salvage_rejected sits here by owner decision
# (contract 2.4): the content channel came back empty, which is provider-side degradation
# rather than a model that cannot produce parseable output.
TRANSPORT_FAILURE_CLASSES = ("exception", "http_error", "empty_response",
                             "reasoning_salvage_rejected", "rate_limited")


class AdaptiveGate:
    """Process-wide concurrency gate for provider calls. Starts at `permits`;
    every 429 halves the live permits (floor 1); after GATE_RECOVER_AFTER
    consecutive 200s one permit is restored (ceiling = initial). Why: the
    provider enforces a concurrency cap we cannot read; the thread pool would
    otherwise keep hammering at full width and turn one 429 into a storm."""

    def __init__(self, permits):
        self._initial = max(1, int(permits))
        self._live = self._initial    # current admitted width; in-flight calls beyond a shrink finish naturally
        self._in_use = 0
        self._shrinks = 0
        self._streak = 0
        self._cond = threading.Condition()

    def acquire(self):
        with self._cond:
            while self._in_use >= self._live:
                self._cond.wait()
            self._in_use += 1

    def release(self):
        with self._cond:
            self._in_use = max(0, self._in_use - 1)
            self._cond.notify()

    def on_rate_limited(self):
        # Halve the width we admit, not the calls already running: the provider
        # meters admissions, and aborting in-flight requests wastes paid tokens.
        with self._cond:
            new_live = max(1, self._live // 2)
            if new_live < self._live:
                self._shrinks += 1
            self._live = new_live
            self._streak = 0
            self._cond.notify_all()

    def on_success(self):
        with self._cond:
            self._streak += 1
            if self._streak >= GATE_RECOVER_AFTER and self._live < self._initial:
                self._live += 1
                self._streak = 0
                self._cond.notify()

    def snapshot(self):
        with self._cond:
            return {"permits": self._live, "initial": self._initial, "shrinks": self._shrinks}


_GATE = None


def gate_for(workers):
    """Create the process-wide gate on first use, sized to the worker pool;
    every later call returns the same instance regardless of `workers` (only
    one pool runs per process, so the first sizing wins)."""
    global _GATE
    if _GATE is None:
        _GATE = AdaptiveGate(workers)
    return _GATE


def _load_glm_config():
    """Credential resolution -- first non-empty key wins; nothing is ever printed:

    1. config/api.yaml                 flat keys endpoint / api_key / vision_model / text_model
                                       (template: config/api_example.yaml; config/api.yaml is
                                       git-ignored)
    2. env FLOWMIRROR_ENDPOINT /       override the endpoint / vision_model / text_model
       FLOWMIRROR_VISION_MODEL /       read from api.yaml; lets CI and containers point
       FLOWMIRROR_TEXT_MODEL           at a provider without editing the yaml (no key here)
    3. env FLOWMIRROR_API_KEY          key only; recommended environment variable name
       env FLOWMIRROR_GLM_KEY          legacy alias, consulted only if API_KEY is empty
    4. env FLOWMIRROR_LEGACY_KEY_FILE  OPT-IN legacy fallback: path to a one-line key file
    5. none of the above               empty key; call_glm() then fails fast with an
                                       actionable error naming all the ways above
    """
    ep, key, vis, txt = GLM_EP_DEFAULT, "", MODEL, TEXT_MODEL
    if os.path.isfile(API_YAML):
        try:
            with open(API_YAML, "r", encoding="utf-8") as fh:
                obj = yaml.safe_load(fh) or {}
            if isinstance(obj, dict):
                ep = str(obj.get("endpoint") or ep)
                vis = str(obj.get("vision_model") or vis)
                txt = str(obj.get("text_model") or txt)
                key = str(obj.get("api_key") or "").strip()
        except Exception:
            pass
    if not key and os.environ.get("FLOWMIRROR_GLM_KEY", "").strip():
        key = os.environ["FLOWMIRROR_GLM_KEY"].strip()
    if not key:
        legacy_path = os.environ.get(LEGACY_KEY_FILE_ENV, "").strip()
        if legacy_path:
            try:
                if os.path.isfile(legacy_path):
                    with open(legacy_path, "r", encoding="utf-8") as fh:
                        key = fh.read().strip()
            except Exception:
                key = ""
    return ep, key, vis, txt


def _no_credentials_error():
    """Actionable RuntimeError for a live call with no configured credentials (echoes no key material)."""
    return RuntimeError(
        "FlowMirror live-LLM credentials not found. Supply a key in ONE of these three ways:\n"
        "  1. create config/api.yaml (copy config/api_example.yaml) and set api_key there\n"
        "     (config/api.yaml is git-ignored; never commit real keys);\n"
        "  2. set the environment variable FLOWMIRROR_GLM_KEY to your key;\n"
        "  3. set the environment variable FLOWMIRROR_LEGACY_KEY_FILE to the path of a one-line\n"
        "     key file (opt-in legacy fallback).\n"
        "Mock runs (mock_llm: true) and agent_policy 'null' need no key at all. No key is ever printed.")


GLM_EP, GLM_KEY, MODEL, TEXT_MODEL = _load_glm_config()


def escalate_tokens(current):
    for nxt in TOKEN_LADDER:
        if nxt > int(current):
            return nxt
    return int(current)


def iter_json_objects(text):
    """Every COMPLETE top-level {...} substring, via a string-aware balanced-brace scan.

    R5.5 R2A forbids greedy first-brace-to-last-brace matching: a truncated tail must not be glued onto an earlier
    object, and a reasoning trace that merely contains `{` and `}` must not count as an answer. Unbalanced (i.e
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

    def __init__(self, path, retry_transport_holes=False):
        self.path = path
        self.rows = {}
        self.hits = self.misses = 0
        self.lock = threading.Lock()
        # Off by default: a cached run must replay byte-for-byte, and cached
        # failure rows are part of that transcript. Enable only when the holes
        # came from transport noise (a rate-limited session's 429s, timeouts,
        # empty responses) and the operator explicitly wants those turns
        # re-asked rather than replayed.
        self.retry_transport_holes = bool(retry_transport_holes)
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
            # A transport hole is infrastructure noise, not the model's
            # answer, so operators may opt into treating just those rows as
            # misses. Model-side failures (schema_invalid etc.) are real
            # verdicts and stay terminal even when the flag is on.
            if (
                row is not None
                and self.retry_transport_holes
                and row.get("parsed") is None
                and ((row.get("provenance") or {}).get("parser_status")) in TRANSPORT_FAILURE_CLASSES
            ):
                row = None
            if row is None:
                self.misses += 1
            else:
                self.hits += 1
        return row

    _OPEN_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 4.0)

    def _open_append(self):
        """Open the cache file in append mode, retrying transient PermissionError.

        Only the open() call is retried; write failures propagate immediately
        so a partial write can never be duplicated or corrupted by a retry.
        """
        delays = self._OPEN_RETRY_DELAYS
        attempt = 0
        while True:
            try:
                return open(self.path, "a", encoding="utf-8")
            except PermissionError:
                if attempt >= len(delays):
                    raise
                time.sleep(delays[attempt])
                attempt += 1

    def put(self, key, row):
        with self.lock:
            self.rows[key] = row
            if self.path:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with self._open_append() as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def call_glm(messages, max_tokens, model=None, parser=None, governor=None, first_open=False,
             temperature=None, max_provider_attempts=None, gate=None,
             rate_limit_cap_s=None, rate_limit_max_wait_s=None):
    """One initial attempt + at most 4 retries (<= 5 physical provider attempts) — R5.5 R2A frozen schedule.

    `parser(text, channel)` must return (obj, matched_text); a call is a SUCCESS only when it returns a complete
    schema-valid object (R5.5 must-fix 4/5: nonempty raw text is NOT success and must consume a retry).
    Every attempt is classified independently — no sticky empty flag can leak the 15s path into a later HTTP
    error (must-fix 7).  A failed attempt that produced NO channel text (transport exception, non-200) records
    raw=None / raw_sha256=None and the ladder CONTINUES — one transient provider error must never kill a run
    (E3 fix: the old post-attempt bookkeeping hashed the missing chan_text unconditionally and died with
    AttributeError, leaving the whole retry ladder as dead code).  Returns a provenance dict and never raises,
    EXCEPT CapStop (when a `governor` is supplied every single attempt — initial, retry and repair — is
    authorized BEFORE it is made, B3/B4, and the breaching attempt is refused instead of spent) and
    RuntimeError when no live credentials are configured (fail fast: a credential-less attempt must not be
    spent, retried, or cached as a hole row).

    E9 / card RT2: `temperature` and `max_provider_attempts` default to TEMP_DEFAULT and
    MAX_PROVIDER_ATTEMPTS_DEFAULT -- today's constants -- so an unpassed call behaves exactly as before;
    engine/loop.py's live wrapper supplies both from cfg["llm"] so the run's own sampling parameters, not
    a module constant, drive the request and reach run_meta.  `max_provider_attempts` is the PHYSICAL
    ladder length here and is NOT cfg["llm"]["max_attempts"] (the macro re-ask switch in decide())."""
    if not GLM_KEY:
        # Fail fast, BEFORE any attempt: without a key every request is a guaranteed 401, so running
        # the ladder would only burn 5 attempts x backoff and then CACHE the terminal failure, which
        # replays as a failure forever.  The message names every supported way to supply a key.
        raise _no_credentials_error()
    headers = {"Authorization": f"Bearer {GLM_KEY}", "Content-Type": "application/json"}
    temp = TEMP_DEFAULT if temperature is None else float(temperature)
    # max(1, ...): a zero-length ladder would skip the loop entirely and return a TERMINAL failure
    # without ever contacting the provider -- a fabricated failure, which decide() would then cache
    # forever.  run.schema.json already pins minimum 1; this is the guard for hand-built cfg dicts.
    n_attempts = max(1, int(MAX_PROVIDER_ATTEMPTS_DEFAULT if max_provider_attempts is None
                            else max_provider_attempts))
    payload = {"model": model or MODEL, "messages": messages, "temperature": temp, "max_tokens": int(max_tokens)}
    prov = {"parsed": None, "raw": None, "response_source": None, "http_status": None, "attempts": 0,
            "finish_reason": None, "max_tokens_final": int(max_tokens), "parser_status": None,
            "raw_sha256": None, "usage": {}, "attempt_log": []}
    rate_cap = rate_limit_cap_s if rate_limit_cap_s is not None else RATE_LIMIT_CAP_S_DEFAULT
    rate_max_wait = rate_limit_max_wait_s if rate_limit_max_wait_s is not None else RATE_LIMIT_MAX_WAIT_S_DEFAULT
    waited_rl = 0.0
    prov["rate_limit_waits"] = []        # seconds actually slept on 429s during this call, for the probe logs
    k = 1
    while k <= n_attempts:               # manual rung counter: a retried 429 re-runs the same k (see continue)                        # k = physical attempt index (1..n_attempts)
        if governor is not None:
            # B2/B3/B4: only attempt 1 on a first-time-opened prespecified ID is charged to the 15,120 base;
            # every retry, duplicate execution and repair-pass attempt is an EXTRA attempt against the reserve.
            governor.authorize(bool(first_open) and k == 1)
        prov["attempts"], prov["max_tokens_final"] = k, int(payload["max_tokens"])
        status, finish, empty = None, None, False
        parsed, raw_store, chan_text, source, cls = None, None, None, None, None
        if gate is not None:
            gate.acquire()               # admission control before every physical attempt (429 retries included)
        t0 = _CLOCK()            # per-attempt wall time, recorded in the attempt log
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
                # 429 -- or Zhipu's JSON business codes 1302/1305 (concurrency /
                # frequency cap) on a non-200 body -- is transport pressure, not a
                # model verdict. Classify it apart so it neither burns the retry
                # ladder nor reaches the cache as a terminal model failure.
                rl_code = False
                try:
                    rl_code = str((resp.json() or {}).get("error", {}).get("code")) in ("1302", "1305")
                except Exception:
                    pass                                      # unreadable body: let the HTTP status alone decide
                cls = "rate_limited" if status == 429 or rl_code else "http_error"
        except Exception:
            cls = "exception"
        finally:
            if gate is not None:
                gate.release()                                # free the slot on every path, raises included
        elapsed_s = round(_CLOCK() - t0, 2)
        prov["http_status"], prov["finish_reason"], prov["parser_status"] = status, finish, cls
        prov["attempt_log"].append({"i": k, "http_status": status, "cls": cls, "finish_reason": finish,
                                    "max_tokens": int(payload["max_tokens"]), "elapsed_s": elapsed_s})
        if cls == "ok":
            if gate is not None:
                gate.on_success()                             # a streak of 200s slowly wins shrunk permits back
            prov.update(parsed=parsed, raw=raw_store, response_source=source, raw_sha256=sha256_text(chan_text))
            return prov
        if cls == "rate_limited":
            if gate is not None:
                gate.on_rate_limited()                        # shrink process-wide width: stop feeding the storm
            # No Retry-After from this provider (probe 0c): 30*k stalled the whole pool and
            # throughput fell below the 4-worker baseline. 10*k plus the adaptive gate's width
            # cut is enough back-off; rate_cap still bounds it (PREREG D24).
            retry_after = 10.0 * k
            try:
                retry_after = float(resp.headers.get("Retry-After"))
            except Exception:
                pass                                          # header missing / not plain seconds / absent headers
            wait_s = min(max(retry_after, 1.0), rate_cap)     # floor 1s prevents a Retry-After: 0 spin; cap bounds the stall
            time.sleep(wait_s)                                # honor the provider's pause before asking again
            waited_rl += wait_s
            prov["rate_limit_waits"].append(wait_s)
            if waited_rl <= rate_max_wait:
                continue                                      # 429 is not a model failure: re-run rung k unchanged
            # cumulative 429 waiting blew the budget: this attempt now counts as an
            # ordinary transport failure and consumes the rung like any other error
        # Failed attempt: keep whatever channel text existed (schema_invalid etc. still stores the raw text
        # and its sha). An attempt with NO channel text (exception / http_error / rate_limited) records
        # raw=None / raw_sha256=None instead of crashing, and the ladder proceeds to the next attempt (E3 fix).
        prov["raw"], prov["response_source"] = raw_store, None
        prov["raw_sha256"] = sha256_text(chan_text) if chan_text else None
        if empty or finish == "length":                       # budget exhaustion / truncation -> escalate tokens
            payload["max_tokens"] = escalate_tokens(payload["max_tokens"])
        if k < n_attempts and cls != "rate_limited":          # a 429 already slept its Retry-After above; never
            time.sleep((BACKOFF_EMPTY_S if empty else BACKOFF_ERROR_S) * k)  # sleep after the terminal failure
        k += 1                                                # advance the rung (budget-blowing 429s land here too)
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


_SCHEMA_ECHO = json.dumps(
    {"reads": ["p1"], "engage": {"p1": ["like"]},
     "comments": [{"post_id": "p3", "stance": "bullish", "text": "..."}],
     "trade": {"action": "buy", "fund": "000001", "amount_pct": 20, "sign_mismatch_confirm": False},
     "org_affinity_delta": {"<org>": 1}, "mood": 4, "reason": "..."},
    separators=(",", ":"))


def _decision_schema_text():
    """Retry-suffix schema payload: prompt.DECISION_SCHEMA_TEXT when the module defines it (lazy import +
    getattr fallback), else the local compact-JSON echo of the normalized decision schema."""
    try:
        import flowmirror.agents.prompt as _prompt
    except Exception:
        return _SCHEMA_ECHO
    return getattr(_prompt, "DECISION_SCHEMA_TEXT", None) or _SCHEMA_ECHO


def _append_retry_suffix(messages):
    out = [dict(m) if isinstance(m, dict) else m for m in messages]
    extra = RETRY_SUFFIX + "\n" + _decision_schema_text()
    for m in reversed(out):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                parts = [dict(p) if isinstance(p, dict) else p for p in content]
                for p in reversed(parts):                    # append to the LAST text part
                    if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                        p["text"] = p["text"] + "\n\n" + extra
                        break
                else:
                    parts.append({"type": "text", "text": extra})
                m["content"] = parts
            else:
                m["content"] = (content or "") + "\n\n" + extra
            break
    return out


def _reflection_pair(txt):
    """First COMPLETE JSON object in txt through parse_reflection(obj), as call_glm's (parsed, matched) pair."""
    objs = iter_json_objects(txt or "")
    if not objs:
        return None, None
    try:
        obj = json.loads(objs[0])
    except Exception:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    parsed, _violations = parse_reflection(obj)
    return parsed, objs[0]


def _llm_cfg(cfg):
    """-> (vision model, text model, max_tokens_start, MACRO re-ask limit).

    The 4-tuple shape is load-bearing: tests/unit/test_live_path.py unpacks exactly four names
    from this helper, so card RT2's two sampling parameters live in _llm_sampling() beside it
    instead of lengthening this tuple.  The last element is cfg["llm"]["max_attempts"], the
    decision-level re-ask switch -- NOT call_glm's physical HTTP ladder."""
    llm = dict((cfg or {}).get("llm") or {})
    return (str(llm.get("model") or MODEL), str(llm.get("text_model") or TEXT_MODEL),
            int(llm.get("max_tokens_start") or 6144),
            int(llm.get("max_attempts") or MAX_DECISION_ATTEMPTS_FALLBACK))


def _llm_sampling(cfg):
    """-> (temperature, max_provider_attempts) from cfg["llm"] (E9 / card RT2).

    `or`-style defaulting is wrong for temperature: 0.0 is a legitimate (greedy) setting and
    `x or TEMP_DEFAULT` would silently turn it into 0.3, so both keys test for None instead.
    Both are coerced (float / int) because the value goes into the LLM cache key: two spellings
    of one temperature must not open two cache namespaces for the same experiment."""
    llm = dict((cfg or {}).get("llm") or {})
    temp, mpa = llm.get("temperature"), llm.get("max_provider_attempts")
    return (TEMP_DEFAULT if temp is None else float(temp),
            MAX_PROVIDER_ATTEMPTS_DEFAULT if mpa is None else max(1, int(mpa)))


def _failure_kind(parser_status, parsed):
    """Contract 2.4: "transport" | "model" | None -- the honest diagnosis of ONE call (audit E12).

    `parser_status` is call_glm's own per-attempt class from prov["parser_status"], never the status
    decide() finally reports: a transport failure produced no model output, so the decision extractor
    is not consulted about it at all.  Derived from the provenance on BOTH the live and the replay
    path, so a warm replay reproduces the cold run's kind and invariant (l) byte-identical logs
    survive card L3 writing this into dec.failure_kind."""
    if parsed is not None:
        return None
    return "transport" if parser_status in TRANSPORT_FAILURE_CLASSES else "model"


def _decision_outcome(prov, shown):
    """-> (parsed, violations, parser_status) for ONE call_glm result on the decision path.

    E12: on a transport class the old code re-fed prov["raw"] -- None on every one of those four
    branches -- into extract_decision, which dutifully reported "no_json_object" on the empty
    string.  That manufactured a parse verdict out of a network problem.  A transport class is
    now the status itself and the extractor never sees it."""
    parsed = prov.get("parsed")
    if parsed is not None:
        return parsed, [], None
    cls = prov.get("parser_status")
    if cls in TRANSPORT_FAILURE_CLASSES:
        return None, [], cls
    return extract_decision(prov.get("raw") or "", shown["pids"], shown["codes"],
                            shown["held"], shown["orgs"],
                            visible_handles=shown.get("handles", ()))


def _record_from_row(row, prompt_sha, img_shas, violations, parser_status=None):
    prov = row.get("provenance") or {}
    parsed = row.get("parsed")          # a cached FAILURE replays as a failure -- terminal
    return {"parsed": parsed, "violations": violations,
            "parser_status": ("ok" if parsed is not None
                              else (parser_status or prov.get("parser_status") or "unparsed")),
            "failure_kind": _failure_kind(prov.get("parser_status"), parsed),
            "prompt_sha": prompt_sha, "raw_sha256": prov.get("raw_sha256"), "image_shas": img_shas,
            "cache_hit": True, "attempts": int(prov.get("attempts") or 0), "notes": {"replay": True}}


def decide(agent_view, feed_cards, cfg, cache, governor, llm, shown):
    """One agent decision: cache lookup, else one authorized provider call plus at most one
    schema-echo retry; the outcome (success OR failure) is always written to the cache."""
    messages, prompt_sha, img_shas, prompt_notes = build_decision_messages(agent_view, feed_cards, cfg)
    model, _txt, max_tokens, max_attempts = _llm_cfg(cfg)
    temperature, max_provider_attempts = _llm_sampling(cfg)
    # E9: the cache key has always carried the temperature; it now carries the RUN's temperature
    # instead of a module constant, so two temperatures can never share a cached response.  At the
    # default the key is byte-identical to the old one (f"{0.3}" either way), so every existing
    # cache entry -- all of them produced at 0.3 -- stays valid.
    key = cache.key_for(model, temperature, prompt_sha, img_shas)
    parser = lambda txt, channel="content": extract_decision(
        txt, shown["pids"], shown["codes"], shown["held"], shown["orgs"], channel,
        visible_handles=shown.get("handles", ()))[:2]
    row = cache.get(key)
    if row is not None:
        governor.record_cache_hit()
        viol, status = [], None
        if row.get("parsed") is None:                        # replay derives exactly as the live path did
            cls = (row.get("provenance") or {}).get("parser_status")
            if cls in TRANSPORT_FAILURE_CLASSES:
                # E12: the cold run reported the transport class itself, so the replay must too --
                # otherwise a warm replay would disagree with the log it is supposed to reproduce.
                status = cls
            else:
                _norm, viol, status = extract_decision(row.get("raw") or "", shown["pids"], shown["codes"],
                                                       shown["held"], shown["orgs"],
                                                       visible_handles=shown.get("handles", ()))
        return _record_from_row(row, prompt_sha, img_shas, viol, status)
    notes = {"mode": "decision", "model": model, "retried": False, "prompt_notes": list(prompt_notes or [])}
    governor.authorize(True)
    prov = llm(messages, max_tokens=max_tokens, model=model, parser=parser, governor=governor,
               first_open=True, temperature=temperature, max_provider_attempts=max_provider_attempts)
    parsed, viol, status = _decision_outcome(prov, shown)
    total_attempts = int(prov.get("attempts") or 0)
    if parsed is None and max_attempts > 1:
        notes["retried"] = True
        governor.authorize(False)                              # retries += 1
        prov = llm(_append_retry_suffix(messages), max_tokens=max_tokens, model=model, parser=parser,
                   governor=governor, first_open=False, temperature=temperature,
                   max_provider_attempts=max_provider_attempts)
        parsed, viol, status = _decision_outcome(prov, shown)
        total_attempts += int(prov.get("attempts") or 0)
    if parsed is None:
        governor.record_failure()
    raw = prov.get("raw") or ""
    rec = {"parsed": parsed, "violations": [] if parsed is not None else viol,
           "parser_status": "ok" if parsed is not None else (status or "unparsed"),
           "failure_kind": _failure_kind(prov.get("parser_status"), parsed),
           "prompt_sha": prompt_sha,
           "raw_sha256": prov.get("raw_sha256") or (sha256_text(raw) if raw else None),
           "image_shas": img_shas, "cache_hit": False, "attempts": total_attempts, "notes": notes}
    cache.put(key, {"key": key, "provenance": prov, "parsed": parsed, "raw": prov.get("raw"),
                    "ts": round(time.time(), 3)})
    return rec


def reflect(agent_view, cfg, cache, governor, llm):
    """One agent reflection (no retry): the same cache/governor pattern around
    build_reflection_messages / parse_reflection."""
    messages, prompt_sha = build_reflection_messages(agent_view)
    img_shas = _image_shas(messages)
    vis, text_model, max_tokens, _ma = _llm_cfg(cfg)
    temperature, max_provider_attempts = _llm_sampling(cfg)
    model = text_model or vis
    key = cache.key_for(model, temperature, prompt_sha, img_shas)   # E9: see the note in decide()
    row = cache.get(key)
    if row is not None:
        governor.record_cache_hit()
        return _record_from_row(row, prompt_sha, img_shas,
                                [] if row.get("parsed") is not None else ["parse_failure"])
    parser = lambda txt, channel="content": _reflection_pair(txt)
    governor.authorize(True)
    prov = llm(messages, max_tokens=max_tokens, model=model, parser=parser, governor=governor,
               first_open=True, temperature=temperature, max_provider_attempts=max_provider_attempts)
    parsed = prov.get("parsed")
    if parsed is None and prov.get("parser_status") not in TRANSPORT_FAILURE_CLASSES:
        # E12: on a transport class prov["raw"] is None on all four branches, so re-parsing it only
        # asked the reflection parser to fail on the empty string.  parser_status already carries the
        # transport class straight through to rec below, which is the honest report.
        parsed, _m = _reflection_pair(prov.get("raw") or "")
    if parsed is None:
        governor.record_failure()
    raw = prov.get("raw") or ""
    rec = {"parsed": parsed, "violations": [] if parsed is not None else ["parse_failure"],
           "parser_status": "ok" if parsed is not None else (prov.get("parser_status") or "unparsed"),
           "failure_kind": _failure_kind(prov.get("parser_status"), parsed),
           "prompt_sha": prompt_sha,
           "raw_sha256": prov.get("raw_sha256") or (sha256_text(raw) if raw else None),
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
        # Holding lines rendered by prompt.py carry NO prefix marker; they look
        # like "003142 / ... / R3 / 73,845.06份 × 净值0.9600 / 浮动盈亏 -4.1%".
        # The legacy markers above ("held:", "held：", lines starting with
        # "持仓") are never emitted by the prompt, so this parser always
        # returned [] and the mock redeem branch in _trade has been dead code
        # since the project started. Match on the "份 × 净值" + "浮动盈亏"
        # pair instead: the generator guarantees both fragments on every
        # holding line, and no other prompt line contains them. The
        # empty-holdings line "你目前没有持有任何基金。" matches neither
        # fragment and would yield no codes anyway.
        elif "份 × 净值" in line and "浮动盈亏" in line:
            out.update(_codes_in(line))
    return sorted(out)


def _account_level(text):
    m = re.search(r"C([1-5])", text or "")
    return "C" + m.group(1) if m else None


def _first_shown_r4(text, codes):
    """First shown R4 fund code: the shown 6-digit code sharing the R4 card line, else the nearest
    one in a +/-160-char window (multi-line cards), else the first shown code as last resort."""
    text = text or ""
    shown = set(codes or [])
    for line in text.splitlines():
        if "R4" in line:
            near = [c for c in _codes_in(line) if c in shown]
            if near:
                return near[0]
    for m in re.finditer(r"R4", text):
        window = text[max(0, m.start() - 160): m.end() + 160]
        near = [c for c in _codes_in(window) if c in shown]
        if near:
            return near[0]
    return sorted(shown)[0] if shown and "R4" in text else None


# Reflection prompts are recognized by a verbatim slice of REFLECTION_PROMPT_ZH, the fixed
# instruction block that build_reflection_messages always ends with. Never sniff for words
# like "复盘"/"反思"/"reflection": creatives OCR copy contains them (e.g. "理财笔记 复盘7"),
# and only the TC arm embeds OCR text into decision prompts, so word sniffing silently reroutes
# TC decisions into reflection JSON (schema:missing:reads) -- a material-dependent, non-random
# loss that also counts toward decision_failure_halt.
_REFLECTION_MARKER = REFLECTION_PROMPT_ZH[:40]


def _looks_like_reflection(text):
    if not text:
        return False
    return _REFLECTION_MARKER in text


class MockLLM:
    """Deterministic zero-API LLM: random.Random seeded from the sha256 of the prompt's text parts,
    so the same prompt always produces the same raw output (and the same cached row).  Decision
    prompts emit the REAL decision schema (reads/engage/comments/trade/org_affinity_delta) so a
    zero-API dry run exercises extract_decision and the whole downstream engine."""

    _ACTIONS = ("like", "save", "follow")
    _STANCES = ("bullish", "bearish", "watching")
    _COMMENTS = _MOCK_COMMENTS + ("内容有参考价值，先收藏。", "希望补充数据来源再判断。")
    _PID_RE = re.compile(r"[pP](\d+)")
    _ORG_RE = re.compile(r"机构[：:]\s*([^\s，,。;；】]+)")

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
                           "beliefs": rng.sample(_MOCK_BELIEFS, rng.randint(1, min(3, len(_MOCK_BELIEFS)))),
                           "market_view": rng.randint(1, 5), "risk_mood": rng.randint(1, 5)},
                          ensure_ascii=False)

    def _render_decision(self, text, rng):
        reads = self._reads(text, rng)
        obj = {"reads": reads,
               "engage": self._engage(reads, rng),
               "comments": self._comments(reads, rng),
               "trade": self._trade(text, rng),
               "org_affinity_delta": self._org_delta(text, rng),
               "mood": rng.randint(1, 5),
               "reason": rng.choice(_MOCK_REASONS)}
        out = json.dumps(obj, ensure_ascii=False)
        if rng.random() < self.malformed_rate:          # cut at 60% -> no complete {...} survives
            out = out[: int(len(out) * 0.6)]
        return out

    _CARD_RE = re.compile(r"【([^】\n]{1,24})】机构[：:]")   # the card header renders the REAL post id

    def _reads(self, text, rng):
        ids = []
        for m in self._CARD_RE.finditer(text or ""):     # engine post ids as shown on the cards
            pid = m.group(1).strip()
            if pid not in ids:
                ids.append(pid)
        if not ids:                                       # synthetic self-test cards use p1..pK labels
            for m in self._PID_RE.finditer(text or ""):
                pid = "p" + m.group(1)
                if pid not in ids:
                    ids.append(pid)
        if not ids:
            return []
        k = rng.randint(1, min(4, len(ids)))
        return [ids[i] for i in sorted(rng.sample(range(len(ids)), k))]

    def _engage(self, reads, rng):
        engage = {}
        if reads:
            k = rng.randint(0, min(2, len(reads)))
            for i in sorted(rng.sample(range(len(reads)), k)):
                engage[reads[i]] = list(rng.sample(self._ACTIONS, rng.randint(1, len(self._ACTIONS))))
        return engage

    def _comments(self, reads, rng):
        if reads and rng.random() < 0.30:
            return [{"post_id": rng.choice(reads), "stance": rng.choice(self._STANCES),
                     "text": rng.choice(self._COMMENTS)}]
        return []

    def _trade(self, text, rng):
        codes, held = _codes_in(text), _held_codes(text)
        if self.force_c2_r4 and _account_level(text) == "C2":
            r4 = _first_shown_r4(text, codes)
            if r4:
                return {"action": "buy", "fund": r4, "amount_pct": 20, "sign_mismatch_confirm": False}
        roll = rng.random()
        if codes and roll < 0.25:
            return {"action": "buy", "fund": rng.choice(codes), "amount_pct": int(rng.randint(5, 40)),
                    "sign_mismatch_confirm": bool(rng.random() < 0.3)}
        if held and roll < 0.35:
            return {"action": "redeem", "fund": rng.choice(held), "amount_pct": int(rng.randint(20, 100)),
                    "sign_mismatch_confirm": False}
        return {"action": "none", "fund": None, "amount_pct": 0, "sign_mismatch_confirm": False}

    def _org_delta(self, text, rng):
        orgs = []
        for m in self._ORG_RE.finditer(text or ""):
            if m.group(1) not in orgs:
                orgs.append(m.group(1))
        if not orgs:
            return {}
        k = rng.randint(0, min(2, len(orgs)))
        return {orgs[i]: rng.randint(-2, 2) for i in sorted(rng.sample(range(len(orgs)), k))}


def _synth_agent(i, held_codes):
    # Mirrors the agent_view contract consumed by flowmirror.agents.prompt.build_decision_messages
    # (persona_card_zh_rich, c_class, cash, holdings{code,name,r,units,nav,pnl_pct}, familiarity, memory, ...).
    return {"agent_id": f"A{i:03d}", "arm": "T" if i % 2 == 0 else "TV",
            "c_class": "C2" if i % 3 else "C3", "cash": 10000.0 + 500.0 * (i % 7),
            "persona_card_zh_rich": f"我是投资者{1000 + i}号，风格稳健，关注回撤，买基金前会先看一段时间。",
            "market_view": 3, "risk_mood": 3, "day": 10 + (i % 5),
            "holdings": [{"code": c, "name": f"持有基金{c[-2:]}", "r": "R3", "units": 800.0, "nav": 1.42,
                          "pnl_pct": 5.2} for c in held_codes],
            "familiarity": {"ORG01": 1 + (i % 2)}, "follows": ["ORG01"], "memory": [], "last_trade": None,
            "declined_confirms": [], "guba": {}, "trend": {}, "direct": []}


def _synth_cards(levels):
    # Mirrors the feed_card contract of flowmirror.agents.prompt (post_id, landing{code,name,R,...}, arm, ...).
    out = []
    for j, r in enumerate(levels):
        out.append({"post_id": f"p{j + 1}", "org": "ORG01", "intent": "I2" if j % 3 == 0 else "I3",
                    "title": f"第{j}条帖子", "caption": f"第{j}条帖子：近期市场波动与配置思路。",
                    "landing": {"code": f"{100001 + j * 11:06d}", "name": f"示例基金{j}", "R": r,
                                "ret_3m": 2.3, "ret_1y": 8.9, "min_buy": 10},
                    "likes": 10 + j, "arm": "T", "image_path": None, "image_sha": None,
                    "comments_prev": [], "climate_label": "no_signal"})
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
        shown = {"pids": [c["post_id"] for c in cards], "codes": [c["landing"]["code"] for c in cards],
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
        req = ("parsed", "violations", "parser_status", "failure_kind", "prompt_sha", "raw_sha256",
               "image_shas", "cache_hit", "attempts", "notes")
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
            shown = {"pids": [c["post_id"] for c in cards], "codes": [c["landing"]["code"] for c in cards],
                     "held": [], "orgs": ["ORG01"]}
            rec = decide(view, cards, cfg, c2c, c2g, c2l, shown)
            tr = ((rec["parsed"] or {}).get("trade") or {})
            if tr.get("action") == "buy" and tr.get("fund") == cards[0]["landing"]["code"]:
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

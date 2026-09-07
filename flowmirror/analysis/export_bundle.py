# -*- coding: utf-8 -*-
"""Presentation bundle exporter: one finished run directory -> four reader-facing files.

`web/app.js` has always told its reader that the verbatim per-arm card texts come from
`flowmirror export-bundle` (the event log deliberately stores no card bodies); this
module is what that sentence points at.  Everything lands under <out>/:

    bundle.json  every key of run_meta.json, plus `images`, plus `invariants` as
                 {summary, entries}, plus a `per_day` aggregate, plus `truncation`
    posts.json   per post that ACTUALLY appeared in the run, the text each arm
                 rendered -- produced by CALLING engine.loop._feed_card and
                 agents.prompt.render_card, never reimplemented, so the bundle
                 cannot drift from what the agents saw -- plus image digests
                 (never pixels) and per-arm reach / engagement
    agents.json  per investor: cell, risk class, arm, opening cash and holdings, a
                 per-day record, and familiarity as it moved
    events.json  the event stream with the truncation rule applied

DEMO scale by design: 4 MB for the whole bundle, 1200 chars per arm text, 200 chars
per persona.  Every cut is written down (`truncation`, or a `*_truncated` flag next to
the value) -- a silent truncation in a presentation artifact is a lie about the run.

Three standing prohibitions, each of which has already cost this project a day: no
absolute filesystem path, no key-shaped string, no image byte may leave here.  scrub()
enforces the first two over every string AND every dict key and reports what it
touched in `_bundle.redactions`; the third is structural, because nothing in this
module ever opens an image file.

The card texts need the creative bodies, which the event log does not carry (a `post`
row names its org and intent, not its note).  They are recovered by replaying
world.publish_day against random.Random(cfg["seed"]) -- in the engine that generator
feeds publish_day and nothing else, so the replay is exact -- and then CHECKED field by
field against the logged post rows.  A mismatch degrades to "no creative text" with the
reason recorded; it never fails the export, because an export is not a release
boundary.

Usage:
  flowmirror export-bundle <run_dir> [--out DIR] [--anonymise-orgs] [--max-bytes N]
  python -m flowmirror.analysis.export_bundle <run_dir> [same flags]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .common import load_run_meta

MAX_BYTES = 4 * 1024 * 1024          # whole-bundle byte budget (demo scale, deliberately small)
ARM_TEXT_CHARS = 1200                # one arm's rendered card text
PERSONA_CHARS = 200                  # one investor's persona summary
DROP_ORDER = ("imp", "st")           # the two high-volume, low-narrative event kinds
_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# scrubbing: absolute paths and key-shaped strings never leave this module
# ---------------------------------------------------------------------------
# POSIX roots are enumerated rather than matching a bare leading "/" on purpose: real
# creative copy is full of "0-4年/5年" and "看多/看空", and a greedy path detector would
# mangle the very stimulus this bundle exists to preserve.
_ABS_TOKEN = re.compile(
    r"^(?:[A-Za-z]:[\\/]"
    r"|\\\\[^\\/]+[\\/]"
    r"|/(?:home|Users|usr|var|tmp|mnt|opt|etc|root|private|Volumes)/)")
# A bare 40/64-hex digest is NOT key material: prompt_sha, raw_sha and image_sha256 are
# the whole provenance story of a run, so they must survive the key detector below.
_DIGEST = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_KEY_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                       # OpenAI-style
    re.compile(r"\b[0-9a-fA-F]{32}\.[A-Za-z0-9]{8,}\b"),         # GLM / Zhipu style
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                         # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),               # GitHub token
    re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}"),
    re.compile(r"[A-Za-z0-9+/]{60,}={0,2}"),                     # long base64 / blob run
)
# Redaction by FIELD NAME is the belt to the detector's braces: a credential shape we
# fail to recognise still must not be copied out of a field that says what it holds.
_SECRET_NAME = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?key|private[_-]?key|secret|passwd|password"
    r"|credential|bearer|\btoken\b)")


def looks_absolute_path(text) -> bool:
    """True when `text`, or any whitespace-delimited token in it, reads as an absolute path."""
    if not isinstance(text, str) or not text:
        return False
    if _ABS_TOKEN.match(text):
        return True
    return any(_ABS_TOKEN.match(tok) for tok in text.split())


def looks_key_shaped(text) -> bool:
    """True when `text` looks like credential material. A bare sha digest is exempt."""
    if not isinstance(text, str) or len(text) < 16:
        return False
    if _DIGEST.fullmatch(text):
        return False
    return any(rx.search(text) for rx in _KEY_SHAPES)


def _strip_root(text: str) -> str:
    """Cut the repo root off a path so what remains is repo-relative and machine-free."""
    low = text.lower()
    for variant in (str(_ROOT), str(_ROOT).replace("\\", "/")):
        idx = low.find(variant.lower())
        if idx >= 0:
            return (text[:idx] + text[idx + len(variant):]).lstrip("\\/")
    return text


def _scrub_str(text: str, counts: dict) -> str:
    if looks_key_shaped(text):
        counts["keys"] += 1
        return "<redacted:key-shaped>"
    if not looks_absolute_path(text):
        return text
    counts["paths"] += 1
    inner = _strip_root(text)
    if not looks_absolute_path(inner):
        return inner.replace("\\", "/")
    # Outside this repo: keep the file name, drop the machine's directory layout.
    return "<elided>/" + (re.split(r"[\\/]", inner.strip())[-1] or "path")


def scrub(obj, counts=None):
    """Recursively strip absolute paths and key material from values AND dict keys.

    Returns (clean, counts) where counts is {"paths", "keys", "named"}, so the bundle
    can state what was touched instead of quietly differing from the run directory.
    """
    counts = counts if counts is not None else {"paths": 0, "keys": 0, "named": 0}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = _scrub_str(k, counts) if isinstance(k, str) else k
            if isinstance(key, str) and _SECRET_NAME.search(key):
                counts["named"] += 1
                out[key] = "<redacted:field-name>"
                continue
            out[key] = scrub(v, counts)[0]
        return out, counts
    if isinstance(obj, list):
        return [scrub(v, counts)[0] for v in obj], counts
    if isinstance(obj, str):
        return _scrub_str(obj, counts), counts
    return obj, counts


# ---------------------------------------------------------------------------
# org anonymisation (double-blind submission)
# ---------------------------------------------------------------------------
_ORG_SUFFIX = re.compile(r"(基金管理股份有限公司|基金管理有限公司|基金|资管|证券|银行)$")


def org_pseudonyms(orgs) -> dict:
    """{real org: "ORG_A", ...} assigned in sorted order, so the map is stable per org set."""
    out = {}
    for n, name in enumerate(sorted({str(o) for o in orgs if o})):
        out[name] = "ORG_%s" % chr(ord("A") + n) if n < 26 else "ORG_%02d" % n
    return out


def _org_patterns(mapping: dict):
    """[(needle, pseudonym)] longest-first: the full institution name AND its stem.

    A fund name carries the stem, not the full name ("鹏华中证国防指数"), so replacing
    only full names would leave the sponsor legible in every landing block.  Sorting
    longest-first keeps a full name from being half-replaced by its own stem.
    """
    pairs = []
    for name, label in mapping.items():
        pairs.append((name, label))
        stem = _ORG_SUFFIX.sub("", name)
        if len(stem) >= 2 and stem != name:
            pairs.append((stem, label))
    return sorted(pairs, key=lambda p: -len(p[0]))


def anonymise(obj, patterns):
    """Recursively replace institution names in every string and every dict key."""
    if isinstance(obj, dict):
        return {anonymise(k, patterns): anonymise(v, patterns) for k, v in obj.items()}
    if isinstance(obj, list):
        return [anonymise(v, patterns) for v in obj]
    if isinstance(obj, str):
        for needle, label in patterns:
            if needle in obj:
                obj = obj.replace(needle, label)
        return obj
    return obj


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def _rows(run_dir) -> list:
    path = os.path.join(str(run_dir), "event_log.jsonl")
    if not os.path.isfile(path):
        raise FileNotFoundError("event_log.jsonl not found in %s" % run_dir)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _cap(text, limit):
    """(text, was_truncated) with the omission stated inside the value itself."""
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[truncated: %d of %d chars omitted]" % (
        len(text) - limit, len(text)), True


def resolve_creatives(cfg, logged, days):
    """Recover post -> pool note; -> (world, {pid: post}, {note_id: note}, reason).

    reason is None on success and a human sentence on every degradation -- the only
    thing that makes missing card bodies visible to a reader of the bundle.
    """
    if not cfg:
        return None, {}, {}, "run_meta.json carries no cfg block"
    from ..engine import world as W
    try:
        world = W.load_world(cfg)
        rng, recent, posts = random.Random(cfg["seed"]), {}, {}
        for t in days:
            for post in W.publish_day(world, cfg, t, recent, rng):
                posts[post["post_id"]] = post
    except (SystemExit, KeyError, OSError, ValueError, TypeError) as exc:
        return None, {}, {}, "world replay unavailable (%s: %s)" % (type(exc).__name__, exc)
    # The replay is worth nothing unless it lands on the same posts the run logged, so
    # every recovered post is checked against its `post` row before any text is exported.
    for pid, row in sorted(logged.items()):
        got = posts.get(pid)
        if got is None:
            return world, {}, {}, "replay produced no post %s" % pid
        if (got.get("org") != row.get("org") or got.get("intent") != row.get("intent")
                or got.get("intent_group") != row.get("ig")
                or got.get("code") != row.get("fund")):
            return world, {}, {}, "replay disagrees with the logged post row at %s" % pid
    notes = {str(W._note_id(n)): n for org in sorted(world.pool) for n in world.pool[org]}
    return world, posts, notes, None


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def _engaged(dec) -> bool:
    return any(float(dec.get(k) or 0) > 0 for k in ("n_like", "n_save", "n_follow"))


def build_posts(by_ev, meta, cfg, dates, world, replay, notes, reason):
    """posts.json -- only the posts that actually reached an agent. -> (payload, n_capped)."""
    from ..agents import prompt as P
    from ..engine import loop as L

    logged = {r["p"]: r for r in by_ev.get("post", []) if r.get("p")}
    imps_by_post = defaultdict(list)
    for r in by_ev.get("imp", []):
        imps_by_post[r.get("p")].append(r)
    arms = list(meta.get("modality", {}).get("arms") or sorted(
        {r.get("arm") for r in by_ev.get("imp", []) if r.get("arm")}))
    arm_of = dict(meta.get("arms") or {})
    clim = {(r.get("t"), r.get("p")): r for r in by_ev.get("clim", [])}
    cmt_n = defaultdict(int)                      # (t, pid) -> comments published that day
    for r in by_ev.get("cmt", []):
        cmt_n[(r.get("t"), r.get("p"))] += 1
    eng_day = {(r.get("t"), r.get("i")) for r in by_ev.get("dec", []) if _engaged(r)}
    social_on = bool((cfg or {}).get("social", True))

    capped, out = 0, []
    for pid in sorted(imps_by_post):
        imps = imps_by_post[pid]
        row = logged.get(pid, {})
        dshown = sorted({r.get("t") for r in imps if r.get("t") is not None})
        t0 = dshown[0] if dshown else row.get("t")
        note_id = (replay.get(pid) or {}).get("note")
        note = notes.get(str(note_id)) if note_id else None
        ids = L._parse_id_list((note or {}).get("image_ids"))
        shas = L._parse_id_list((note or {}).get("image_sha256"))
        idxs = sorted({r.get("img_idx") for r in imps if isinstance(r.get("img_idx"), int)})
        entry = {"post_id": pid, "org": row.get("org"), "intent": row.get("intent"),
                 "ig": row.get("ig"), "fund": row.get("fund"),
                 "published_t": row.get("t"), "published_d": row.get("d"),
                 "days_shown": dshown, "rendered_on": {"t": t0, "d": dates.get(t0)},
                 "note_id": note_id,
                 # digests and indices only; this module never opens an image file
                 "image": {"n_images": len(ids), "sha": shas[0] if shas else None,
                           "shown_idx": idxs,
                           "sha_shown": {str(i): (shas[i] if i < len(shas) else None)
                                         for i in idxs},
                           "attached_impressions": sum(
                               1 for r in imps if isinstance(r.get("img_idx"), int))},
                 "arms": {}}
        cr = clim.get((t0, pid)) or {}
        # fam_phrase is the one card input the log drops (see _bundle.fidelity).
        top = {pid: [{"stance": c.get("stance"), "text": c.get("text"), "fam_phrase": ""}
                     for c in (cr.get("top") or []) if isinstance(c, dict)]}
        try:
            dt = date.fromisoformat(dates[t0]) if dates.get(t0) else None
        except (ValueError, KeyError):
            dt = None
        for arm in arms:
            seen = {(r.get("i"), r.get("t")) for r in imps if r.get("arm") == arm}
            reach = {i for i, _t in seen}
            eng = len({i for i, t in seen if (t, i) in eng_day})
            slot = {"impressions": sum(1 for r in imps if r.get("arm") == arm),
                    "reach": len(reach), "engaged_agents": eng,
                    "engagement_rate": (round(eng / len(reach), 4) if reach else None),
                    "clicks": sum(1 for r in by_ev.get("click", [])
                                  if r.get("p") == pid and arm_of.get(r.get("i")) == arm),
                    "comments": sum(1 for r in by_ev.get("cmt", [])
                                    if r.get("p") == pid and arm_of.get(r.get("i")) == arm),
                    "checkouts": sum(1 for r in by_ev.get("co", [])
                                     if r.get("p") == pid and arm_of.get(r.get("i")) == arm),
                    "text": None, "chars": None, "truncated": False}
            if note is not None and world is not None and dt is not None:
                card = L._feed_card(world, replay[pid], notes, arm, {},
                                    {pid: cr.get("label")}, top, dt,
                                    {pid: cmt_n.get((t0 - 1, pid), 0)})
                text, cut = _cap(P.render_card(card, social_on), ARM_TEXT_CHARS)
                slot.update({"text": text, "chars": len(text), "truncated": cut})
                capped += 1 if cut else 0
            entry["arms"][arm] = slot
        out.append(entry)
    return {"_what": "one entry per post that reached at least one agent",
            "_definitions": {
                "text": "engine.loop._feed_card -> agents.prompt.render_card, the exact "
                        "renderer the agents' prompts use, in the social context of "
                        "rendered_on",
                "engaged_agents": "impressed agents whose decision row on a day they saw "
                                  "this post reports any like/save/follow; the log records "
                                  "engagement per agent-day, not per card",
                "image": "digests and indices only -- no pixels, by construction"},
            "creative_source": "unavailable" if reason else "engine_replay",
            "creative_source_reason": reason,
            "arms": arms, "posts": out}, capped


def build_agents(meta, cfg, world, by_ev, dates):
    """agents.json -- opening state, a per-day record, familiarity over time."""
    invs, note = [], None
    if world is not None:
        try:
            from ..engine import world as W
            invs = W.init_investors(world, cfg)
        except (SystemExit, KeyError, OSError, ValueError, TypeError) as exc:
            invs, note = [], "opening state unavailable (%s)" % type(exc).__name__
    else:
        note = "opening state unavailable (the run's world could not be rebuilt)"
    persona = {}
    if cfg and cfg.get("agents_file"):
        p = Path(cfg["agents_file"])
        try:
            with open(p if p.is_absolute() else (_ROOT / p), encoding="utf-8") as fh:
                blob = json.load(fh)
            rowset = blob.get("agents") if isinstance(blob, dict) else blob
            persona = {str(r.get("id")): r for r in (rowset or []) if isinstance(r, dict)}
        except (OSError, ValueError, AttributeError):
            persona = {}

    arm_of, open_of = dict(meta.get("arms") or {}), {i.id: i for i in invs}
    per_day = defaultdict(lambda: defaultdict(dict))
    fam = defaultdict(lambda: defaultdict(dict))
    for r in by_ev.get("dec", []):
        slot = per_day[r.get("i")][r.get("t")]
        slot.update({"d": r.get("d"), "mood": r.get("mood"), "status": r.get("status"),
                     "reason": r.get("reason"), "failure_kind": r.get("failure_kind"),
                     "violations": r.get("violations") or []})
        for k in ("n_read", "n_like", "n_save", "n_follow", "n_comment", "aff_sum"):
            slot[k] = r.get(k)
    for ev, key, fields in (("cmt", "comments", ("p", "stance", "text")),
                            ("act", "trades", ("p", "kind", "fund", "amt", "units", "nav", "fee")),
                            ("co", "checkouts", ("p", "fund", "act", "oc", "oc_cf", "amt"))):
        for r in by_ev.get(ev, []):
            slot = per_day[r.get("i")][r.get("t")]
            slot.setdefault("d", r.get("d"))
            slot.setdefault(key, []).append({f: r.get(f) for f in fields})
    for r in by_ev.get("st", []):
        fam[r.get("i")][r.get("t")][r.get("org")] = {
            "what": r.get("what"), "lv": r.get("lv"), "fam": r.get("fam"), "aff": r.get("aff")}

    truncated, out = 0, []
    for aid in sorted(set(arm_of) | set(open_of) | set(per_day)):
        inv, rec = open_of.get(aid), persona.get(aid) or {}
        text, cut = _cap(rec.get("persona_card_zh_rich") or "", PERSONA_CHARS)
        truncated += 1 if cut else 0
        out.append({
            "id": aid,
            "cell": getattr(inv, "cell", None) or rec.get("cell"),
            "risk_class": getattr(inv, "rc", None) or rec.get("reported_C"),
            "arm": arm_of.get(aid) or getattr(inv, "arm", None),
            "cash0": (round(float(inv.cash), 2) if inv is not None else None),
            "hold0": ({c: round(float(u), 6) for c, u in sorted(inv.hold.items())}
                      if inv is not None else None),
            "wealth0": (round(float(inv.w0), 2) if inv is not None else None),
            "persona": text, "persona_truncated": cut,
            "by_day": {str(t): per_day[aid][t] for t in sorted(per_day.get(aid, {}))},
            "familiarity": {str(t): fam[aid][t] for t in sorted(fam.get(aid, {}))}})
    return {"_what": "one entry per investor; by_day and familiarity keyed by trading day index",
            "_definitions": {
                "cash0/hold0/wealth0": "opening state from world.init_investors on this cfg",
                "familiarity": "only the days an st row fired -- st is a CHANGE log, so a "
                               "missing day means level and affinity did not move",
                "persona": "capped at %d chars; the omission is stated in the value"
                           % PERSONA_CHARS},
            "note": note, "days": {str(t): d for t, d in sorted(dates.items())},
            "agents": out}, truncated


def build_per_day(by_ev, dates, arm_of, arms):
    """The day-by-day aggregate carried in bundle.json."""
    out = []
    for t in sorted(dates):
        decs = [r for r in by_ev.get("dec", []) if r.get("t") == t]
        imps = [r for r in by_ev.get("imp", []) if r.get("t") == t]
        acts = [r for r in by_ev.get("act", []) if r.get("t") == t]
        row = {"t": t, "d": dates.get(t),
               "posts": sum(1 for r in by_ev.get("post", []) if r.get("t") == t),
               "impressions": len(imps), "reach_agents": len({r.get("i") for r in imps}),
               "decisions": len(decs),
               # a decision row with no mood is one whose response never became a row
               "decisions_no_row": sum(1 for r in decs if r.get("mood") is None),
               "clicks": sum(1 for r in by_ev.get("click", []) if r.get("t") == t),
               "checkouts": sum(1 for r in by_ev.get("co", []) if r.get("t") == t),
               "comments": sum(1 for r in by_ev.get("cmt", []) if r.get("t") == t),
               "acts": len(acts),
               "sub_cny": round(sum(float(r.get("amt") or 0) for r in acts
                                    if r.get("kind") == "subscribe"), 2),
               "fees_cny": round(sum(float(r.get("fee") or 0) for r in acts), 2),
               "by_arm": {}}
        for arm in arms:
            a_imps = [r for r in imps if r.get("arm") == arm]
            a_decs = [r for r in decs if (r.get("arm") or arm_of.get(r.get("i"))) == arm]
            row["by_arm"][arm] = {
                "impressions": len(a_imps), "reach_agents": len({r.get("i") for r in a_imps}),
                "decisions": len(a_decs),
                "engaged_agents": len({r.get("i") for r in a_decs if _engaged(r)})}
        out.append(row)
    return out


def build_invariants(run_dir):
    """invariants_report.json -> {summary, entries}; entries sorted by name."""
    try:
        with open(os.path.join(str(run_dir), "invariants_report.json"), encoding="utf-8") as fh:
            rep = json.load(fh) or {}
    except (OSError, ValueError):
        return {"summary": None, "entries": []}
    checks = rep.get("checks") or {}
    return {"summary": rep.get("summary"),
            "entries": [dict(body, name=name) if isinstance(body, dict)
                        else {"name": name, "value": body}
                        for name, body in sorted(checks.items())]}


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------
def _blob(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")


def _total(files) -> int:
    return sum(len(_blob(v)) for v in files.values())


def apply_budget(files: dict, max_bytes: int) -> dict:
    """Shrink `files` in place until it fits; return the truncation record.

    The ladder is fixed and stated in the record: `imp` rows, then `st` rows (the two
    high-volume kinds that carry no narrative), then a tail trim of whatever remains.
    Nothing is dropped without a number written next to it.
    """
    rule = ("total <= %d bytes; drop event rows of kind %s in that order, then trim the "
            "tail of the remaining stream; card text capped at %d chars, persona at %d"
            % (max_bytes, "/".join(DROP_ORDER), ARM_TEXT_CHARS, PERSONA_CHARS))
    before = _total(files)
    rec = {"applied": False, "rule": rule,
           "dropped": {"bytes_before": before, "bytes_after": before}}
    ev = files.get("events.json") or {}
    if before > max_bytes:
        for kind in DROP_ORDER:
            keep = [r for r in ev.get("rows", []) if r.get("ev") != kind]
            n = len(ev.get("rows", [])) - len(keep)
            if n:
                ev["rows"], rec["dropped"][kind], rec["applied"] = keep, n, True
            if _total(files) <= max_bytes:
                break
        rows, tail = ev.get("rows", []), 0
        # Last resort so the bundle still opens rather than blowing the stated budget.
        while rows and _total(files) > max_bytes:
            step = max(1, len(rows) // 10)
            del rows[-step:]
            tail += step
        if tail:
            rec["dropped"]["tail_rows"], rec["applied"] = tail, True
    ev["n_rows"] = len(ev.get("rows", []))
    ev["dropped"] = {k: v for k, v in rec["dropped"].items()
                     if k in DROP_ORDER or k == "tail_rows"}
    rec["dropped"]["bytes_after"] = _total(files)
    return rec


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def export_bundle(run_dir, out_dir=None, anonymise_orgs=False, max_bytes=MAX_BYTES) -> dict:
    """Write the four bundle files under out_dir; -> a summary dict for the CLI."""
    run_dir = str(run_dir)
    rows = _rows(run_dir)                                   # raises when the run has no log
    meta = load_run_meta(run_dir)
    cfg = meta.get("cfg") if isinstance(meta.get("cfg"), dict) else None
    by_ev = defaultdict(list)
    for r in rows:
        by_ev[r.get("ev")].append(r)
    dates = {}
    for r in rows:
        if r.get("t") is not None and r.get("d") and r["t"] not in dates:
            dates[r["t"]] = r["d"]
    arm_of = dict(meta.get("arms") or {})
    arms = list(meta.get("modality", {}).get("arms") or sorted(set(arm_of.values())))

    logged = {r["p"]: r for r in by_ev.get("post", []) if r.get("p")}
    world, replay, notes, reason = resolve_creatives(cfg, logged, sorted(dates))
    posts, arm_caps = build_posts(by_ev, meta, cfg, dates, world, replay, notes, reason)
    agents, persona_caps = build_agents(meta, cfg, world, by_ev, dates)

    bundle = dict(meta)                                     # every existing run_meta key
    bundle.setdefault("images", {"root": None,
                                 "policy": (cfg or {}).get("image_pick", "first"),
                                 "attached": 0, "missing": 0, "sha_mismatch": 0})
    bundle["invariants"] = build_invariants(run_dir)
    bundle["per_day"] = build_per_day(by_ev, dates, arm_of, arms)
    events = {"_what": "the event stream, in file order, with the truncation rule applied",
              "n_rows": len(rows), "dropped": {}, "rows": rows}

    counts = {"paths": 0, "keys": 0, "named": 0}
    files = {"bundle.json": scrub(bundle, counts)[0], "posts.json": scrub(posts, counts)[0],
             "agents.json": scrub(agents, counts)[0], "events.json": scrub(events, counts)[0]}
    pseudo = org_pseudonyms(list((cfg or {}).get("orgs") or [])
                            + [r.get("org") for r in by_ev.get("post", [])])
    if anonymise_orgs and pseudo:
        pats = _org_patterns(pseudo)
        files = {k: anonymise(v, pats) for k, v in files.items()}
    # Both blocks go in BEFORE the budget is measured, so the stated cap covers the
    # bytes actually written rather than everything except the last two keys.
    files["bundle.json"]["_bundle"] = {
        "generator": "flowmirror.analysis.export_bundle",
        "run_dir": _scrub_str(os.path.abspath(run_dir), counts),
        "anonymised_orgs": bool(anonymise_orgs),
        "pseudonyms": sorted(pseudo.values()) if anonymise_orgs else [],
        "redactions": dict(counts),
        "fidelity": [
            "card `likes` is the day-(t-1) heat score, which the event log does not carry "
            "(per-post likes are never logged), so every exported card renders it as 0",
            "comment `fam_phrase` is not logged either, so excerpt lines carry an empty one",
            "each post is rendered once, in the social context of `rendered_on`; a post "
            "shown on several days looked slightly different on the other days",
            "arm-level click/comment/checkout counts attribute a row to the arm of the "
            "agent that produced it (run_meta.arms), which is exact only at agent level"]}
    files["bundle.json"]["truncation"] = {"applied": False, "rule": "", "dropped": {}}
    trunc = apply_budget(files, int(max_bytes))
    trunc["dropped"]["arm_texts_capped"] = arm_caps
    trunc["dropped"]["personas_capped"] = persona_caps
    files["bundle.json"]["truncation"] = trunc

    out = Path(out_dir) if out_dir else Path(run_dir) / "bundle"
    out.mkdir(parents=True, exist_ok=True)
    for name in sorted(files):
        (out / name).write_bytes(_blob(files[name]))
    return {"out_dir": str(out), "files": sorted(files),
            "bytes": {n: len(_blob(files[n])) for n in sorted(files)},
            "truncation": trunc, "posts": len(files["posts.json"]["posts"]),
            "agents": len(files["agents.json"]["agents"]),
            "creative_source": files["posts.json"]["creative_source"],
            "creative_source_reason": files["posts.json"]["creative_source_reason"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m flowmirror.analysis.export_bundle")
    ap.add_argument("run_dir", help="finished run output directory (holds event_log.jsonl)")
    ap.add_argument("--out", help="bundle output directory (default: <run_dir>/bundle)")
    ap.add_argument("--anonymise-orgs", dest="anonymise_orgs", action="store_true",
                    help="replace institution names with stable pseudonyms (double-blind)")
    ap.add_argument("--max-bytes", dest="max_bytes", type=int, default=MAX_BYTES,
                    help="whole-bundle byte budget (default %d)" % MAX_BYTES)
    a = ap.parse_args(argv)
    try:
        res = export_bundle(a.run_dir, a.out, a.anonymise_orgs, a.max_bytes)
    except (FileNotFoundError, ValueError) as exc:
        print("[export] FAIL %s: %s" % (a.run_dir, exc), file=sys.stderr)
        return 1
    print("[export] %s -> %s" % (a.run_dir, res["out_dir"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

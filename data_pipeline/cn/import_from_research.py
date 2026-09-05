#!/usr/bin/env python3
"""Populate the open repo's data/ layer L1 from the private research repo.

Reads ONLY from --research (never modifies it) and writes ONLY under
<root>/data/, following the distribution policy in data/DATA.md:
copies the allowed files, strips non-distributable bits (local image paths,
post-id lists, NAV series), and rewrites data/MANIFEST.sha256 + MANIFEST.json.

Usage:
    python data_pipeline/cn/import_from_research.py [--dry-run] [--force]
        [--research "D:/Desktop/ABM paper/fundmarket-sim"]
        [--root "D:/Desktop/ABM paper/flowmirror_v7"]
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FROZEN_AGENTS_SHA_PREFIX = "ddae7d79e0d835c7"
FLOWMIRROR_POPULATION = Path(
    "D:/Desktop/ABM paper/flowmirror/flowmirror/data/population_10k_v3.json"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEP = "  "  # manifest column separator (two spaces, matches header)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def basename_no_dir(p) -> str:
    s = str(p).replace("\\", "/").rstrip("/")
    tail = s.rsplit("/", 1)[-1]
    return tail if tail else s


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: Path) -> str:
    # utf-8-sig transparently drops a BOM if one exists
    return path.read_text(encoding="utf-8-sig")


def iter_string_values(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from iter_string_values(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from iter_string_values(v)


# --------------------------------------------------------------------------
# plan (source -> destination, transformation)
# --------------------------------------------------------------------------

def build_plan(research: Path):
    return [
        {"dest": "data/population/persona_grid_v3.json",
         "src": research / "data" / "persona_grid_v3.json",
         "srcref": "research:data/persona_grid_v3.json",
         "mode": "verbatim", "xf": "verbatim", "required": True},
        {"dest": "data/population/population_10k_v3.json",
         "src": FLOWMIRROR_POPULATION,
         "srcref": "external:flowmirror/flowmirror/data/population_10k_v3.json",
         "mode": "verbatim", "xf": "verbatim", "required": False},
        {"dest": "data/population/agents_seed2027.json",
         "src": research / "sim" / "agents_seed2027.json",
         "srcref": "research:sim/agents_seed2027.json",
         "mode": "verbatim", "xf": "verbatim (agents_sha256 verified)",
         "required": True, "agents_check": True},
        {"dest": "data/creatives/cn/content_pool_v1_masked.jsonl",
         "src": research / "sim" / "content_pool_v1_masked.jsonl",
         "srcref": "research:sim/content_pool_v1_masked.jsonl",
         "mode": "strip_images",
         "xf": "images stripped (image_paths_resized -> image_ids; path keys dropped)",
         "required": True},
        {"dest": "data/attention/guba_signal_v1.json",
         "src": research / "sim" / "guba_signal_v1.json",
         "srcref": "research:sim/guba_signal_v1.json",
         "mode": "strip_stance", "xf": "stance_jobs stripped (aggregates only)",
         "required": True},
        {"dest": "data/attention/elasticity_real_v1.json",
         "src": research / "sim" / "elasticity_real_v1.json",
         "srcref": "research:sim/elasticity_real_v1.json",
         "mode": "verbatim", "xf": "verbatim", "required": True},
        {"dest": "data/flows/flow_panel_v2.json",
         "src": research / "sim" / "flow_panel_v2.json",
         "srcref": "research:sim/flow_panel_v2.json",
         "mode": "verbatim", "xf": "verbatim (public quarterly disclosures)",
         "required": True},
        {"dest": "data/flows/stylized_facts_real.json",
         "src": research / "sim" / "stylized_facts.json",
         "srcref": "research:sim/stylized_facts.json",
         "mode": "verbatim", "xf": "verbatim", "required": False},
        {"dest": "data/funds/nav_codes.txt",
         "src": research / "sim" / "nav_cache.json",
         "srcref": "research:sim/nav_cache.json",
         "mode": "nav_codes", "xf": "derived: sorted fund codes only",
         "required": True},
        {"dest": "data/funds/README.md",
         "src": research / "sim" / "nav_cache.json",
         "srcref": "research:sim/nav_cache.json",
         "mode": "nav_readme", "xf": "derived: regeneration readme",
         "required": True},
        {"dest": "data/creatives/cn/tables_frozen_meta.json",
         "src": research / "paper" / "figs" / "tables_frozen_meta.json",
         "srcref": "research:paper/figs/tables_frozen_meta.json",
         "mode": "verbatim", "xf": "verbatim", "required": False},
    ]


# --------------------------------------------------------------------------
# integrity check: frozen agents hash
# --------------------------------------------------------------------------

def check_agents(src: Path):
    try:
        obj = json.loads(read_text(src))
        agents = obj["agents"]
        meta = obj.get("_meta") or {}
        expected = meta.get("agents_sha256")
        raw = [a["id"] for a in agents]
        if all(isinstance(x, int) and not isinstance(x, bool) for x in raw):
            ids = [str(x) for x in sorted(raw)]
        else:
            ids = sorted(str(x) for x in raw)
        computed = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    except Exception as e:
        return False, f"[agents_sha256] FAIL (cannot compute: {e})"
    ok = (expected == computed)
    if ok:
        msg = (f"[agents_sha256] PASS computed={computed[:16]}... "
               f"n_agents={len(ids)}")
    else:
        msg = (f"[agents_sha256] FAIL computed={computed[:16]}... "
               f"expected={str(expected)[:16]}... (frozen value is known to "
               f"start with {FROZEN_AGENTS_SHA_PREFIX})")
    if expected and not str(expected).startswith(FROZEN_AGENTS_SHA_PREFIX):
        msg += (f" [warn] _meta.agents_sha256 does not start with known "
                f"prefix {FROZEN_AGENTS_SHA_PREFIX}")
    return ok, msg


# --------------------------------------------------------------------------
# transforms
# --------------------------------------------------------------------------

def gen_strip_images(src: Path) -> bytes:
    lines = [ln for ln in read_text(src).split("\n") if ln.strip()]
    if not lines:
        raise ValueError("content pool file is empty")
    out = []
    dropped = replaced = notes = 0
    for i, ln in enumerate(lines):
        obj = json.loads(ln)
        if i == 0 and isinstance(obj, dict) and "_meta" in obj:
            meta = obj["_meta"]
            if isinstance(meta, dict):
                meta["images_stripped"] = True
                meta["images_location"] = (
                    "not redistributed; see data/DATA.md")
            out.append(json.dumps(obj, ensure_ascii=False,
                                  separators=(",", ":")))
            continue
        notes += 1
        new = {}
        for k, v in obj.items():
            if "path" in k.lower():
                if k == "image_paths_resized" and isinstance(v, list):
                    new["image_ids"] = [basename_no_dir(p) for p in v]
                    replaced += 1
                else:
                    dropped += 1  # any other path-bearing key is dropped
            else:
                new[k] = v  # keeps note_id, image_sha256, n_images, ...
        out.append(json.dumps(new, ensure_ascii=False, separators=(",", ":")))
    print(f"[content_pool] notes={notes} image_paths_resized->image_ids "
          f"records={replaced} dropped_path_keys={dropped}")
    return ("\n".join(out) + "\n").encode("utf-8")


def gen_strip_stance(src: Path) -> bytes:
    obj = json.loads(read_text(src))
    out = {}
    dropped = []
    for k, v in obj.items():
        if k == "_meta":
            m = dict(v) if isinstance(v, dict) else v
            if isinstance(m, dict):
                m["stance_jobs_stripped"] = True
            out["_meta"] = m
        elif k == "signal":
            out["signal"] = v
        else:
            dropped.append(k)
    print("[guba_signal] kept=_meta,signal dropped="
          + (",".join(dropped) if dropped else "(none)"))
    return (json.dumps(out, ensure_ascii=False, indent=2)
            + "\n").encode("utf-8")


def _collect_dates(v, out, depth=0):
    if depth > 6:
        return
    if isinstance(v, str):
        if DATE_RE.match(v):
            out.append(v)
    elif isinstance(v, dict):
        for k, vv in v.items():
            if isinstance(k, str) and DATE_RE.match(k):
                out.append(k)
            else:
                _collect_dates(vv, out, depth + 1)
    elif isinstance(v, (list, tuple)):
        for item in v:
            if isinstance(item, str) and DATE_RE.match(item):
                out.append(item)
            elif isinstance(item, dict):
                hit = False
                for dk in ("date", "nav_date", "trade_date", "ds", "day"):
                    dv = item.get(dk)
                    if isinstance(dv, str) and DATE_RE.match(dv):
                        out.append(dv)
                        hit = True
                        break
                if not hit:
                    _collect_dates(item, out, depth + 1)
            elif (isinstance(item, (list, tuple)) and item
                  and isinstance(item[0], str) and DATE_RE.match(item[0])):
                out.append(item[0])


def parse_nav(src: Path):
    """Return (sorted fund codes, min_date, max_date) from nav_cache.json."""
    obj = json.loads(read_text(src))
    codes = sorted(str(k) for k in obj if not str(k).startswith("_"))
    dates = []
    for k in codes:
        _collect_dates(obj[k], dates)
    dmin = min(dates) if dates else None
    dmax = max(dates) if dates else None
    return codes, dmin, dmax


def gen_nav_readme(codes, dmin, dmax) -> bytes:
    rng = f"{dmin} to {dmax}" if (dmin and dmax) else "see nav_cache _meta"
    lines = [
        "NAV series are not redistributed (provider ToS unclear); regenerate "
        "locally with `python data_pipeline/cn/fill_nav.py` using your own "
        "credentials (see data/DATA.md).",
        f"Covers {len(codes)} fund codes (sorted, one per line in "
        f"nav_codes.txt).",
        f"Date range covered by the source cache: {rng}.",
    ]
    return "".join(l + "\n" for l in lines).encode("utf-8")


def generate(op, nav_info) -> bytes:
    mode, src = op["mode"], op["src"]
    if mode == "verbatim":
        return src.read_bytes()
    if mode == "strip_images":
        return gen_strip_images(src)
    if mode == "strip_stance":
        return gen_strip_stance(src)
    if mode == "nav_codes":
        codes = nav_info[0]
        return ("\n".join(codes) + ("\n" if codes else "")).encode("utf-8")
    if mode == "nav_readme":
        codes, dmin, dmax = nav_info
        return gen_nav_readme(codes, dmin, dmax)
    raise ValueError(f"unknown mode: {mode}")


# --------------------------------------------------------------------------
# manifests + selfcheck
# --------------------------------------------------------------------------

def write_manifests(root: Path, rows) -> None:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# sha256" + SEP + "path" + SEP + "bytes" + SEP + "source"
             + SEP + "generated_at(UTC)"]
    for r in rows:
        lines.append(SEP.join([r["sha256"], r["path"], str(r["bytes"]),
                               r["source"], r["generated_at"]]))
    (data_dir / "MANIFEST.sha256").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8"))
    mdoc = {
        "generated_at_utc": rows[0]["generated_at"] if rows else utc_now(),
        "tool": "data_pipeline/cn/import_from_research.py",
        "note": ("Rows cover files written in this run only; the MANIFEST "
                 "files exclude themselves. nav_cache.json is never copied "
                 "(derived codes/readme only)."),
        "files": rows,
    }
    (data_dir / "MANIFEST.json").write_bytes(
        (json.dumps(mdoc, ensure_ascii=False, indent=2)
         + "\n").encode("utf-8"))
    print(f"[manifest] rewrote data/MANIFEST.sha256 and data/MANIFEST.json "
          f"({len(rows)} file rows)")


def selfcheck(root: Path, rows) -> bool:
    all_ok = True
    for r in rows:
        p = root / r["path"]
        problems = []
        parsed = None
        if not p.is_file():
            problems.append("missing on disk")
        else:
            b = p.read_bytes()
            if sha256_bytes(b) != r["sha256"]:
                problems.append("sha256 mismatch vs manifest")
            if len(b) != r["bytes"]:
                problems.append("byte count mismatch vs manifest")
            try:
                if r["path"].endswith(".jsonl"):
                    parsed = [json.loads(ln) for ln in
                              b.decode("utf-8-sig").splitlines() if ln.strip()]
                elif r["path"].endswith(".json"):
                    parsed = json.loads(b.decode("utf-8-sig"))
            except Exception as e:
                problems.append(f"invalid JSON/JSONL: {e}")
            if parsed is not None and r["path"].startswith("data/creatives/"):
                leaks = [s for s in iter_string_values(parsed)
                         if ("D:\\" in s or "/Desktop/" in s)]
                if leaks:
                    problems.append(f"local path leak x{len(leaks)} "
                                    f"e.g. {leaks[0][:60]!r}")
        if problems:
            all_ok = False
            print(f"[selfcheck] FAIL {r['path']}: " + "; ".join(problems))
        else:
            print(f"[selfcheck] PASS {r['path']}")
    return all_ok


# --------------------------------------------------------------------------
# dry-run / run / main
# --------------------------------------------------------------------------

def dry_run(plan, root: Path, research: Path, force: bool) -> int:
    print(f"[dry-run] research : {research}")
    print(f"[dry-run] root     : {root}")
    print(f"[dry-run] force    : {force} (informational; nothing is written)")
    total = missing = 0
    for i, op in enumerate(plan, 1):
        src, dest = op["src"], root / op["dest"]
        if src.is_file():
            size = src.stat().st_size
            total += size
            size_s = f"{size} bytes"
        else:
            missing += 1
            size_s = "MISSING" + ("" if op["required"] else " (optional)")
        print(f"[plan] {i:2d}. {op['dest']}")
        print(f"[plan]      src={op['srcref']} [{size_s}] mode={op['mode']} "
              f"required={'yes' if op['required'] else 'no'} "
              f"dest_exists={'yes' if dest.exists() else 'no'}")
    print("[plan] note: sim/nav_cache.json itself is NOT copied (provider "
          "ToS unclear); only data/funds/nav_codes.txt + README.md derived")
    print(f"[plan] {len(plan)} destinations, {missing} missing sources, "
          f"{total} source bytes total")
    print("[dry-run] no files written")
    return 0


def run(plan, root: Path, force: bool) -> int:
    failures = 0
    missing_req = [op for op in plan
                   if op["required"] and not op["src"].is_file()]
    for op in missing_req:
        print(f"[error] missing required source: {op['src']}")
    if missing_req:
        return 1

    # frozen agents hash gate: abort before any write on FAIL
    aop = next((op for op in plan if op.get("agents_check")), None)
    if aop:
        ok, msg = check_agents(aop["src"])
        print(msg)
        if not ok:
            print("[error] agents_seed2027.json failed its frozen "
                  "_meta.agents_sha256 check; aborting before any write")
            return 1

    # parse nav_cache once (shared by the two derived outputs)
    nav_info = None
    nav_ops = [op for op in plan if op["mode"].startswith("nav_")]
    if nav_ops:
        try:
            nav_info = parse_nav(nav_ops[0]["src"])
            codes, dmin, dmax = nav_info
            rng = f"{dmin}..{dmax}" if (dmin and dmax) else "unknown"
            print(f"[nav_cache] codes={len(codes)} date_range={rng} "
                  f"(cache itself NOT copied)")
        except Exception as e:
            print(f"[error] cannot parse nav_cache.json: {e}")
            return 1

    ts = utc_now()
    written = []
    for op in plan:
        dest = root / op["dest"]
        if not op["src"].is_file():
            print(f"[warn] optional source missing, skipped: {op['src']}")
            continue
        if dest.exists() and not force:
            print(f"[skip] destination exists (use --force): {op['dest']}")
            continue
        existed = dest.exists()
        try:
            content = generate(op, nav_info)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        except Exception as e:
            print(f"[error] transform/write failed for {op['dest']}: {e}")
            failures += 1
            continue
        print(f"[write] {'OVERWRITE' if existed else 'NEW'} "
              f"{op['dest']} ({len(content)} bytes)")
        written.append(op)

    rows = []
    for op in written:
        b = (root / op["dest"]).read_bytes()  # hash what is actually on disk
        rows.append({"sha256": sha256_bytes(b), "path": op["dest"],
                     "bytes": len(b), "source": op["srcref"],
                     "generated_at": ts, "transformation": op["xf"]})
    rows.sort(key=lambda r: r["path"])

    write_manifests(root, rows)
    if not rows:
        print("[manifest] note: no data files were written this run "
              "(everything skipped or missing)")

    ok = selfcheck(root, rows)
    if failures:
        print(f"[done] FAIL ({failures} transform/write errors)")
        return 1
    if not ok:
        print("[done] FAIL (selfcheck)")
        return 1
    print(f"[done] PASS ({len(rows)} files written and verified)")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    ap = argparse.ArgumentParser(
        description="Import distributable data from the research repo into "
                    "<root>/data/ (layer L1). Research repo is read-only.")
    ap.add_argument("--research", default="D:/Desktop/ABM paper/fundmarket-sim",
                    help="research repo root (read-only; default: %(default)s)")
    ap.add_argument("--root", default=None,
                    help="open repo root to write under (default: two levels "
                         "up from this script)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing destination files")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan with source sizes; write nothing")
    args = ap.parse_args(argv)

    research = Path(args.research).expanduser().resolve()
    root = (Path(args.root).expanduser().resolve() if args.root
            else Path(__file__).resolve().parents[2])

    if not research.is_dir():
        print(f"[error] research repo not found: {research}")
        return 2
    if not root.is_dir():
        print(f"[error] root not found: {root}")
        return 2
    if root == research or is_under(root, research):
        print("[error] root must not equal or sit inside the research repo; "
              "refusing to write")
        return 2
    if is_under(research, root):
        print("[warn] research repo is inside root; writes remain restricted "
              "to <root>/data/...")

    plan = build_plan(research)
    data_root = (root / "data").resolve()
    for op in plan:
        if not is_under((root / op["dest"]).resolve(), data_root):
            print(f"[error] destination escapes <root>/data/: {op['dest']}")
            return 2

    if args.dry_run:
        return dry_run(plan, root, research, args.force)
    return run(plan, root, args.force)


if __name__ == "__main__":
    sys.exit(main())
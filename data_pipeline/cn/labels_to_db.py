"""labels_to_db.py -- write paper-side artifacts (content-pool snapshot,
frozen image captions, compliance labels) into NEW tables of the research
database so other sessions can join/query them with plain SQL.

Iron rules: (1) only create and only write the three NEW tables (pool_note,
pool_image_caption, note_compliance_label) via CREATE TABLE IF NOT EXISTS +
INSERT OR REPLACE; never ALTER/DROP/UPDATE/DELETE any pre-existing table
(asset, asset_ocr, xhs_note, fund_nav, guba_post, ...). (2) Never connect
to xhs_data.db / xhs_images.db (crawler stores, red line); only the --db
research db is opened. (3) The db is WAL and other processes may write:
PRAGMA busy_timeout = 30000 right after connect; journal_mode is never
touched and VACUUM is never run. (4) Idempotent: identical primary keys
overwrite; re-runs add no duplicates. (5) Nothing raises to the top level,
no gating; exit code 0 (1 only when --self-test fails). Stdlib only.
"""

import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

POOL_NOTE_DDL = """CREATE TABLE IF NOT EXISTS pool_note (
  note_id TEXT NOT NULL, pool_version TEXT NOT NULL, org_name TEXT, intent TEXT,
  intent_group TEXT, title TEXT, caption TEXT, caption_masked TEXT, ocr_text TEXT,
  ocr_masked TEXT, n_images INTEGER, primary_label TEXT, submit_time TEXT,
  fund_codes_valid TEXT, common_support_codes TEXT, likes INTEGER, collects INTEGER,
  comments INTEGER, shares INTEGER, source_file TEXT, written_at TEXT,
  PRIMARY KEY (note_id, pool_version))"""

CAPTION_DDL = """CREATE TABLE IF NOT EXISTS pool_image_caption (
  note_id TEXT NOT NULL, img_index INTEGER NOT NULL, pool_version TEXT NOT NULL,
  image_id TEXT, image_sha256 TEXT, caption TEXT, caption_len INTEGER, at_cap INTEGER,
  model TEXT, prompt_sha TEXT, channel TEXT, token_budget INTEGER, leak_flags TEXT,
  generated_at TEXT, source_file TEXT, written_at TEXT,
  PRIMARY KEY (note_id, img_index, pool_version))"""

LABEL_DDL = """CREATE TABLE IF NOT EXISTS note_compliance_label (
  note_id TEXT NOT NULL, rule_id TEXT NOT NULL, labels_version TEXT NOT NULL,
  hit INTEGER, evidence TEXT, source TEXT, severity TEXT, n_hits INTEGER,
  severity_max TEXT, model TEXT, prompt_sha TEXT, rules_sha TEXT, generated_at TEXT,
  llm_called INTEGER, truncated INTEGER, source_file TEXT, written_at TEXT,
  PRIMARY KEY (note_id, rule_id, labels_version))"""

TABLES = (("pool_note", POOL_NOTE_DDL, 21),
          ("pool_image_caption", CAPTION_DDL, 16),
          ("note_compliance_label", LABEL_DDL, 17))

def _b01(v):
    # True/False -> 1/0; None/missing -> None (stored as NULL)
    return None if v is None else int(bool(v))

def _jdump(v):
    # list field -> JSON text; None becomes "[]"
    return json.dumps(v if v is not None else [], ensure_ascii=False)

def _version_from_path(path, override):
    return override or os.path.splitext(os.path.basename(path))[0]

def _connect(db_path, read_only=False):
    # busy_timeout only; WAL/journal_mode untouched, no VACUUM
    if read_only:
        con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    else:
        con = sqlite3.connect(db_path)
    con.execute("PRAGMA busy_timeout = 30000")
    return con

def _iter_data_rows(path):
    # yield JSONL rows, skipping _meta header rows and blank lines
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                if not (isinstance(obj, dict) and obj.get("_meta")):
                    yield obj

def _pool_note_row(r, ver, src, wt):
    return (r.get("note_id"), ver, r.get("org"), r.get("intent"),
            r.get("intent_group"), r.get("title"), r.get("caption"),
            r.get("caption_masked"), r.get("ocr_text"), r.get("ocr_masked"),
            r.get("n_images"), r.get("primary_label"), r.get("submit_time"),
            _jdump(r.get("fund_codes_valid")),
            _jdump(r.get("common_support_codes")), r.get("likes"),
            r.get("collects"), r.get("comments"), r.get("shares"), src, wt)

def _caption_rows(r, ver, src, wt):
    # one row per image; returns (rows, no_caption count)
    ids, shas = r.get("image_ids") or [], r.get("image_sha256") or []
    caps = r.get("image_caption_frozen") or []
    meta = r.get("caption_meta") or {}
    chs, tbs = meta.get("channels") or [], meta.get("token_budgets") or []
    leaks = meta.get("leak_flags") or []
    rows, n_no = [], 0
    for i, img_id in enumerate(ids):
        cap = caps[i] if i < len(caps) else None
        if not cap:  # missing/empty caption: skip row, count it
            n_no += 1
            continue
        rows.append((r.get("note_id"), i, ver, img_id,
                     shas[i] if i < len(shas) else None, cap, len(cap),
                     1 if len(cap) >= 40 else 0, meta.get("model"),
                     meta.get("prompt_sha"), chs[i] if i < len(chs) else None,
                     tbs[i] if i < len(tbs) else None,
                     json.dumps([x for x in leaks if x.get("index") == i],
                                ensure_ascii=False),
                     meta.get("generated_at"), src, wt))
    return rows, n_no

def _label_rows(r, ver, src, wt):
    # one row per (note, rule); rule ids written in sorted order
    meta, labels = r.get("label_meta") or {}, r.get("labels") or {}
    tail = (r.get("n_hits"), r.get("severity_max"), meta.get("model"),
            meta.get("prompt_sha"), meta.get("rules_sha"),
            meta.get("generated_at"), _b01(meta.get("llm_called")),
            _b01(meta.get("truncated")), src, wt)
    return [(r.get("note_id"), rid, ver,
             _b01((labels.get(rid) or {}).get("hit")),
             (labels.get(rid) or {}).get("evidence"),
             (labels.get(rid) or {}).get("source"), None) + tail
            for rid in sorted(labels)]

def _write_all(con, note_rows, cap_rows, lab_rows):
    # create the three NEW tables and write all rows in ONE transaction
    with con:
        for _, ddl, _ in TABLES:
            con.execute(ddl)
        for (name, _, n_cols), rows in zip(TABLES, (note_rows, cap_rows, lab_rows)):
            con.executemany("INSERT OR REPLACE INTO %s VALUES (%s)"
                            % (name, ",".join("?" * n_cols)), rows)

def run(db_path, pool_path, labels_path, pool_ver, labels_ver, dry_run=False):
    # parse inputs, print stats, write the three new tables; never raises
    wt = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    note_rows, cap_rows, lab_rows = [], [], []
    n_notes = n_img = n_no_cap = n_lab = 0
    if os.path.exists(pool_path):
        for r in _iter_data_rows(pool_path):
            n_notes += 1
            note_rows.append(_pool_note_row(r, pool_ver, pool_path, wt))
            rows, n_no = _caption_rows(r, pool_ver, pool_path, wt)
            cap_rows.extend(rows); n_no_cap += n_no
            n_img += len(r.get("image_ids") or [])
        print("[pool] 笔记 %d → pool_note 写入 %d 行（版本 %s）" % (n_notes, len(note_rows), pool_ver))
        print("[caption] 图片 %d → pool_image_caption 写入 %d 行（无描述 %d | 40 字上限 %d）"
              % (n_img, len(cap_rows), n_no_cap, sum(1 for x in cap_rows if x[7] == 1)))
    else:
        print("[pool] 文件不存在，跳过：%s" % pool_path)
    if os.path.exists(labels_path):
        for r in _iter_data_rows(labels_path):
            n_lab += 1
            lab_rows.extend(_label_rows(r, labels_ver, labels_path, wt))
        print("[labels] 笔记 %d → note_compliance_label 写入 %d 行（版本 %s）" % (n_lab, len(lab_rows), labels_ver))
    else:
        print("[labels] 文件不存在，跳过：%s" % labels_path)
    if dry_run:
        try:
            _connect(db_path, read_only=True).close()
            print("[db] dry-run：目标库可只读打开；仅统计，未建表、未写入")
        except sqlite3.Error:
            print("[db] dry-run：目标库不存在或无法只读打开；仅统计，未写入")
        return 0
    con = None
    try:
        con = _connect(db_path)
        _write_all(con, note_rows, cap_rows, lab_rows)
    except Exception as e:  # transaction rolled back; never propagate
        print("[db] 写入失败（事务已回滚，退出码仍为 0）：%s" % e)
        return 0
    finally:
        if con is not None:
            con.close()
    print("[db] 完成；既有表未改动（本脚本只写 pool_note / pool_image_caption / note_compliance_label）")
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(description="write pool/caption/label artifacts into NEW research-db tables")
    ap.add_argument("--db", default="D:/Desktop/ABM paper/fundmarket-sim/flowmirror.db")
    ap.add_argument("--pool", default="data/creatives/cn/content_pool_v1_captioned.jsonl")
    ap.add_argument("--labels", default="data/creatives/cn/compliance_labels_v1.jsonl")
    ap.add_argument("--pool-version", default=None, help="pool file basename w/o ext")
    ap.add_argument("--labels-version", default=None, help="labels file basename w/o ext")
    ap.add_argument("--dry-run", action="store_true", help="stats only; read-only open; no writes")
    ap.add_argument("--self-test", action="store_true", help="offline test on a temp sqlite file")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    print("[db] 目标 %s（WAL，busy_timeout=30s）" % a.db)
    return run(a.db, a.pool, a.labels, _version_from_path(a.pool, a.pool_version),
               _version_from_path(a.labels, a.labels_version), a.dry_run)

def _expect(name, exp, act):
    # assert that reports expected vs actual on failure
    assert exp == act, "%s 期望 %r，实际 %r" % (name, exp, act)

def _self_test():
    # offline test on a throwaway temp db; returns 0 ok / 1 fail
    con = None
    try:
        # ignore_cleanup_errors: on Windows an open sqlite handle makes the temp-dir removal
        # raise PermissionError, which would mask the real assertion failure below.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            con = sqlite3.connect(os.path.join(td, "t.db"))
            con.execute("CREATE TABLE legacy(a INTEGER)")
            con.execute("INSERT INTO legacy VALUES (1)")
            con.commit()
            legacy = con.execute("PRAGMA table_info(legacy)").fetchall()
            cm = dict(model="m", prompt_sha="ps", generated_at="g")

            def mk(**kw):
                base = dict(title="t", caption="c", caption_masked="cm", ocr_text="o",
                            ocr_masked="om", likes=0, collects=0, comments=0,
                            shares=0, image_caption_frozen=[], caption_meta=cm)
                base.update(kw)
                return base
            pool = [
                mk(note_id="n1", org="A", intent="I2", intent_group="I2",
                   n_images=2, image_ids=["i0", "i1"], image_sha256=[],
                   primary_label="P", submit_time="2024-01-01",
                   fund_codes_valid=["F1"], common_support_codes=[],
                   image_caption_frozen=["x" * 40, ""],
                   caption_meta=dict(cm, channels=["A"], token_budgets=[50],
                                     leak_flags=[dict(image="i0", index=0, word="w",
                                                      category="c")])),
                mk(note_id="n2", org="B", intent="I1", intent_group="nonI2",
                   n_images=1, image_ids=["j0"], image_sha256=["s1"],
                   primary_label="Q", submit_time="2024-01-02",
                   fund_codes_valid=[], common_support_codes=["F2"],
                   image_caption_frozen=["y" * 39],
                   caption_meta=dict(cm, channels=["B"], token_budgets=[60]))]
            lab = [dict(note_id="n1", n_hits=1, severity_max="high",
                        labels=dict(R02=dict(hit=None, evidence=None, source=None),
                                    R01=dict(hit=True, evidence="e", source="ocr")),
                        label_meta=dict(model="m", prompt_sha="ps", rules_sha="rs",
                                        generated_at="g", channel="B", attempts=1,
                                        truncated=False, llm_called=True))]
            wt = "2024-01-01T00:00:00+08:00"
            nr = [_pool_note_row(r, "vT", "poolT", wt) for r in pool]
            cr, n_no = [], 0
            for r in pool:
                rows, k = _caption_rows(r, "vT", "poolT", wt)
                cr += rows; n_no += k
            lr = [x for r in lab for x in _label_rows(r, "LT", "labT", wt)]
            _write_all(con, nr, cr, lr)
            _write_all(con, nr, cr, lr)  # second identical run: idempotency
            q = lambda s: con.execute(s).fetchall()
            _expect("pool_note 行数", 2, q("select count(*) from pool_note")[0][0])
            _expect("caption 行数", 2, q("select count(*) from pool_image_caption")[0][0])
            _expect("label 行数", 2, q("select count(*) from note_compliance_label")[0][0])
            _expect("规则 id 排序", [("R01",), ("R02",)], q("select rule_id from note_compliance_label order by rowid"))
            _expect("legacy 行数不变", 1, q("select count(*) from legacy")[0][0])
            _expect("legacy 结构不变", legacy, q("pragma table_info(legacy)"))
            _expect("无描述计数", 1, n_no)
            _expect("空描述图不写行", [], q("select 1 from pool_image_caption where note_id='n1' and img_index=1"))
            # q() returns fetchall(): a list of row tuples, so expectations are lists too.
            _expect("hit=None 存 NULL", [(None,)], q("select hit from note_compliance_label where rule_id='R02'"))
            _expect("hit=True 存 1", [(1,)], q("select hit from note_compliance_label where rule_id='R01'"))
            _expect("40 字 at_cap=1", [(1,)], q("select at_cap from pool_image_caption where note_id='n1' and img_index=0"))
            _expect("39 字 at_cap=0", [(0,)], q("select at_cap from pool_image_caption where note_id='n2'"))
            _expect("越界 sha 为 NULL", [(None,)], q("select image_sha256 from pool_image_caption where note_id='n1' and img_index=0"))
            _expect("leak_flags 仅含本图", '[{"image": "i0", "index": 0, "word": "w", "category": "c"}]',
                    q("select leak_flags from pool_image_caption where note_id='n1' and img_index=0")[0][0])
            con.close()
            con = None
        print("[self-test] 全部通过")
        return 0
    except AssertionError as e:
        print("[self-test] 失败：%s" % e)
        return 1
    except Exception as e:  # self-test must not raise either
        print("[self-test] 异常：%r" % e)
        return 1
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

if __name__ == "__main__":
    try:
        _code = main()
    except Exception as e:  # nothing escapes to the top level; exit 0
        print("[error] 未预期错误（已吞掉，退出码 0）：%r" % e)
        _code = 0
    raise SystemExit(_code)

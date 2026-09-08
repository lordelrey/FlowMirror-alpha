# 交接单 · 论文派生数据入库（flowmirror.db）

给正在写 `flowmirror.db` 的那个会话。**本单只涉及三张新表，既有表一行不动。**
2026-09-08 20:44 已由本会话写入一次（20:38 第一次尝试撞上你的写锁，20:44 重试成功）。之后每次内容池或标签更新，重跑同一条命令即可。

## 一句话

```bash
cd "D:/Desktop/ABM paper/flowmirror_v7" && python data_pipeline/cn/labels_to_db.py
```

幂等（主键相同则覆盖），随时可重跑。加 `--dry-run` 只统计不写，用只读连接打开库。

## 它写什么

| 表 | 行数（20:44） | 一行是什么 | 主键 |
|---|---|---|---|
| `pool_note` | 200 | 论文用内容池的一条笔记（含**池版 OCR 文本** `ocr_text`/`ocr_masked`、标题、正文、意图、互动数） | (note_id, pool_version) |
| `pool_image_caption` | 800 | 一张图的**冻结中文描述**（TC 处理组的刺激），带模型、prompt_sha、生成时间、是否触到 40 字上限 | (note_id, img_index, pool_version) |
| `note_compliance_label` | 0（标签还在生成） | 一条笔记 × 一条合规规则的判定（hit / evidence / source） | (note_id, rule_id, labels_version) |

`pool_version` 取源文件名去扩展名，例如 `content_pool_v1_captioned`；描述重跑出 v2 后是 `content_pool_v1_captioned_v2`，**两版共存不互相覆盖**。

## 它不做什么（脚本层面保证）

- 只 `CREATE TABLE IF NOT EXISTS` + `INSERT OR REPLACE`；没有 `ALTER`／`DROP`／`UPDATE`／`DELETE`。
- `asset`、`asset_ocr`、`xhs_note`、`fund_nav`、`guba_post` 等既有表不读也不写（只有联表查询时才读）。20:44 写入前后行数完全一致：asset 43,571 / asset_ocr 3,431 / xhs_note 9,708 / fund_nav 276,243 / guba_post 774,131。
- 不连 `xhs_data.db`、`xhs_images.db`（爬虫库，只读红线）。
- 不改 `journal_mode`（保持 WAL）、不 `VACUUM`、不建索引以外的东西。
- 连接后 `PRAGMA busy_timeout = 30000`；拿不到写锁就整体回滚并打印中文提示，退出码仍为 0，库里不留半成品。

回退方式：`DROP TABLE pool_note; DROP TABLE pool_image_caption; DROP TABLE note_compliance_label;`

## 还差一步：合规标签

`data/creatives/cn/compliance_labels_v1.jsonl` 正在本会话的 key1 上生成（200 条笔记里 108 条需要模型裁定）。生成完我会自己重跑一次入库；**如果你先看到该文件出现而我没跑**，请执行上面那条命令，`note_compliance_label` 会补上约 1,400 行（200 × 7 条规则）。

规则清单在 `config/compliance_rules_cn_v1.yaml`（R01 保本承诺 / R02 预测业绩 / R03 极端表述 / R04 业绩展示无区间 / R05 推品帖无风险提示 / R06 诱导性用语 / R07 未经核实数据），每行标签带 `prompt_sha` 与 `rules_sha`。

## 与你正在写的 OCR 的关系

两份 OCR 不是同一批，**不要互相覆盖**：

- 你写的 `asset_ocr`：对全部资产做的 OCR（3,431 行，glm-4.5v，batch1–3），键 `asset_id` + `ocr_key = <note_id>:<序号>`，图片是 `xhs_covers\<机构>\...` 那一套命名。
- 我写的 `pool_note.ocr_text`：论文冻结内容池自带的 OCR，200 条笔记全覆盖、对应 801 张图，图片在 `fundmarket-sim/sim/content_pool_v1/images`，命名 `<note_id>_<序号>.jpg`。这一份是**实际喂给 agent 的字节**，冻结在 JSONL 里不可变。

论文的 200 条笔记里有 132 条在 `asset_ocr` 里也有 OCR，可以这样对照两份文本：

```sql
SELECT p.note_id, p.org_name, length(p.ocr_text) AS pool_chars,
       count(o.asset_id) AS db_ocr_rows, sum(o.n_chars) AS db_chars
FROM pool_note p
LEFT JOIN asset_ocr o ON o.ocr_key LIKE p.note_id || ':%'
GROUP BY p.note_id
ORDER BY db_ocr_rows DESC;
```

## 几个能直接跑的查询

```sql
-- 描述质量：40 字上限处被硬截断的比例（v1 是 550/800，v2 重跑后应显著下降）
SELECT pool_version, at_cap, count(*) FROM pool_image_caption GROUP BY 1, 2;

-- 池笔记 ⋈ 爬虫笔记表（200/200 命中）
SELECT p.note_id, p.org_name, p.intent, x.submit_time, x.note_type
FROM pool_note p JOIN xhs_note x USING (note_id) LIMIT 20;

-- 合规标签出来之后：不合规笔记的机构分布
SELECT p.org_name, count(DISTINCT l.note_id) AS bad_notes
FROM note_compliance_label l JOIN pool_note p USING (note_id)
WHERE l.hit = 1 GROUP BY 1 ORDER BY 2 DESC;
```

## 如果又撞上写锁

脚本会打印 `[db] 写入失败（事务已回滚…）：database is locked`。这不是数据损坏，重跑即可。20:38 那次就是这样：你的连接持着写事务（最后一次实际写入是 20:21，WAL 16.9 MB），20:44 锁一放开重试就成功了。长事务写完记得 `commit()` 或关连接，别让它跨小时挂着。

代码：`data_pipeline/cn/labels_to_db.py`（277 行，只标准库，提交 `0f1d1aa`）。有 `--self-test`（离线，用临时库，含"既有表不变"与幂等断言）。

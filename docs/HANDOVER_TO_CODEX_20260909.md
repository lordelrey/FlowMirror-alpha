# FlowMirror 项目交接文档 · 给 Codex

生成时间：2026-09-09 11:40（北京时间）。前任操作方：Claude Code（Opus 5 / Fable 5.1）。
**读完本文即可接手。** 本文只写事实与位置，设计推理见 `docs/PLAN_v2_20260908.md`，预注册见 `docs/PREREG_v1.5_DRAFT.md`。

---

## 0. 一分钟摘要

FlowMirror 是一个用 LLM agent 模拟中国基金营销平台的社会模拟项目，目标 **WWW 2027 第二赛道，正文 8 页，截稿 2026-10-25**（还剩 46 天）。

论文定位是**社会涌现：微观行为如何长成宏观结构**。头条实验是**多模态对比**（同一条营销笔记以纯文本 / 文字描述 / 真图三种形态呈现，agent 级随机化），主终点是三组的点击率与申购转化率差异。

**现在正在跑**：主网格第一个种子 `main_ref_s2027`（400 人 × 12 交易日，真模型 glm-4.6v），进度约 1,500 / 5,600 次调用（本文生成时），第 3 个交易日；**实时数字用 §4.2 的命令查**。**这个进程不能丢，丢了也不致命**——缓存可复用，重启会免费重放已完成的调用。

**接手第一件事**：确认这个进程还活着（见 §4）。如果死了，用 §4 的命令重启。

---

## 1. 目标与硬约束

| 项 | 值 |
|---|---|
| 投稿目标 | WWW 2027 第二赛道 |
| 正文页数 | 8 页 |
| 截稿 | 2026-10-25（计划 10-23 提交，留两天） |
| 论文定位 | 社会涌现，微观 → 宏观；三层叙事（微观真实 / 宏观真实 / 涌现） |
| 头条实验 | 三组模态对比（T 纯文本 / TC 文字描述 / TV 真图），agent 级随机化 |
| 作者信息 | **只保留业主（aitool@ledao.ai / GitHub lordelrey），不要写任何 AI 的名字** |

---

## 2. 位置地图

### 2.1 代码与文档

| 内容 | 绝对路径 |
|---|---|
| 主仓库 | `D:\Desktop\ABM paper\flowmirror_v7` |
| GitHub | 私有仓库 `lordelrey/flowmirror`，分支 `main`。本文正文首次写成时 HEAD 为 `adb151c`；本文自身的提交是 `7ec7421`，**以 §13 的核验结果为准** |
| 设计方案（必读） | `docs/PLAN_v2_20260908.md` |
| 预注册（必读） | `docs/PREREG_v1.5_DRAFT.md`（决策 D20–D33 + 十六份配置 sha 表） |
| 操作手册 | `docs/RUNBOOK.md`（§6 凭证、§6.1 探针实测、§15 机制开关总表） |
| 审计交接单 | `docs/AUDIT_BRIEF_2026-09-08.md`（给独立审计上下文用，列了建议攻击点） |
| 数据库交接 | `docs/DB_HANDOVER_20260908.md` |
| 论文初稿 | `paper/DRAFT_S3_S5_20260909.md`（§3–§6，177 行，数字都标了来源） |

### 2.2 工具与凭证（**都在仓库外，不要提交进仓库**）

| 内容 | 绝对路径 | 说明 |
|---|---|---|
| 发卡工具目录 | `D:\Desktop\ABM paper\tools\` | 不是 git 仓库 |
| 发卡脚本 | `tools/glm_run.py` | 把一张「卡」发给 GLM 5.3，产出 `<卡名>.md.response.md` |
| 落地补丁 | `tools\apply_patch.py` | 应用 `### PATCH:` 格式的 OLD/NEW 替换块 |
| 落地整文件 | `tools/land_multi.py` | 应用 `### FILE:` 格式的整文件 |
| 已写的卡 | `tools\cards\`（94 张） | 命名如 `A9_workers_cli_override.md` |
| 引擎用 key | `tools\.glm_key` | 与 `config/api.yaml` 里的 `api_key` **是同一把** |
| 发卡用 key | `tools\.glm_key2` | `tools/glm_run.py` 的默认 key |
| autodl 中转 key | `tools\.glm_key3` | 见 §2.4，实测比直连慢，目前不用 |

**引擎凭证配置**：`config/api.yaml`（已被 gitignore，绝不入库）。也支持环境变量覆盖：`FLOWMIRROR_API_KEY` / `FLOWMIRROR_ENDPOINT` / `FLOWMIRROR_VISION_MODEL` / `FLOWMIRROR_TEXT_MODEL`。

### 2.3 数据与图片

| 内容 | 绝对路径 | 说明 |
|---|---|---|
| 内容池（论文用，带 v2 描述） | `data/creatives/cn/content_pool_v1_captioned_v2.jsonl` | 200 条笔记，801 张图，**十六份配置都指向它** |
| 内容池 v1（已废弃） | `data/creatives/cn/content_pool_v1_captioned.jsonl` | 描述被 40 字截断，保留作对比 |
| 内容池母本 | `data/creatives/cn/content_pool_v1_masked.jsonl` | 无描述，脱敏后 |
| 合规标签 | `data/creatives/cn/compliance_labels_v1.jsonl` | 200 条 × 7 条规则 |
| 抽检样本（**业主待看**） | `data/creatives/cn/compliance_review_sample.md`、`data/creatives/cn/caption_review_sample.md` | 各 30 条 |
| 图片库 | `D:\Desktop\ABM paper\fundmarket-sim\sim\content_pool_v1\images` | 801 张，**永不入库** |
| 真实净值 | `data/funds/nav_cache.json` | 已 gitignore |
| 校验和清单 | `data/MANIFEST.sha256` | 每个数据产物一行 |

### 2.4 数据库

| 数据库 | 路径 | 权限 |
|---|---|---|
| 研究库 | `D:\Desktop\ABM paper\fundmarket-sim\flowmirror.db`（289 MB，WAL 模式） | 可读写，但**只准新增表** |
| 爬虫库 | `D:\Desktop\定时脚本\lesu_spider-master (2)\lesu_spider-master\xhs_data.db` | **只读红线** |
| 爬虫图库 | 同目录 `xhs_images.db` | **只读红线** |

研究库里的相关表：

- `asset_ocr`（3,431 行）：**OCR 打标结果在这里**，模型 glm-4.5v，分 batch1/2/3，键为 `asset_id` + `ocr_key`（形如 `<note_id>:<图序号>`）。另一个会话在往里写，不要重复做。
- `pool_note` / `pool_image_caption` / `note_compliance_label`：**本项目新增的三张表**，由 `data_pipeline/cn/labels_to_db.py` 写入，只用 `CREATE TABLE IF NOT EXISTS` + `INSERT OR REPLACE`，从不碰既有表。

---

## 3. 红线（业主明确要求，不可违反）

1. **爬虫库 `xhs_data.db` / `xhs_images.db` 只读**，任何写操作都是红线。
2. **key 绝不进版本库**，`config/api.yaml` 已 gitignore。
3. **图片绝不进版本库**。
4. **`runs/out/` 下已有目录一律不删**。
5. **绝不碰 `D:\Desktop\4.15论文\greenarena\`**（另一个项目）。
6. **作者信息只写业主，不写 AI 的名字。**
7. **代码由 GLM 5.3 写**（业主的分工要求，见 §5）；打标、OCR、爬虫、监控这类后台任务只用 GLM 或 Haiku。
8. **默认不新增 hash / 冻结 / 基线 / 门禁**。要新增必须能说出一个具体失败场景，并说明 Git、版本号、主键、事务、唯一约束、类型系统、普通测试这七项为什么不够。门禁只放在不可逆操作、跨系统操作、安全边界、正式发布边界这四类地方。已存在的不变量与重放校验一律保留，不许为"简化"而删。

---

## 4. 现在正在跑什么，以及怎么接手

### 4.1 状态

```
运行：main_ref_s2027（主网格参考格第一个种子）
配置：runs/main_ref_s2027.json（400 人 × 12 日，三组模态，glm-4.6v，6 并发）
输出：runs/out/main_ref_s2027/
进度：约 1,500 / 5,600 次调用（本文生成时刻），第 3/12 个交易日
```

### 4.2 检查是否还活着

```bash
cd "D:/Desktop/ABM paper/flowmirror_v7"
python -c "
import json,io,time,os
rows=[]
for l in io.open('runs/out/main_ref_s2027/llm_cache.jsonl',encoding='utf-8'):
    l=l.strip()
    if l:
        try: rows.append(json.loads(l))
        except ValueError: pass
print('%d/5600 | 最后一次调用 %.0f 秒前 | 已完成: %s'%(len(rows), time.time()-rows[-1]['ts'], os.path.exists('runs/out/main_ref_s2027/run_meta.json')))"
```

最后一次调用超过 5 分钟且没有 `run_meta.json`，就是死了。

### 4.3 重启（缓存复用，不丢进度）

```bash
cd "D:/Desktop/ABM paper/flowmirror_v7"
python -m flowmirror.engine.loop runs/main_ref_s2027.json --workers 6 --out runs/out/main_ref_s2027
```

**不要改 `--workers`。** 实测：6 并发 367 次/小时；10 并发反而掉到 91 次/小时并触发限流（账户级速率限制）。

### 4.4 一次运行跑完后的固定动作

```bash
# 1) 免费回放校验（走缓存，不花钱），必须 identical=True
python -m flowmirror.engine.loop runs/main_ref_s2027.json --out runs/out/main_ref_s2027 --replay-check

# 2) 跑全部分析模块
# 注意：modality 不给 --out 就只打印不落盘，其余七个模块默认写 <run_dir>/analysis/<模块>.json
python -m flowmirror.analysis.modality runs/out/main_ref_s2027 --out runs/out/main_ref_s2027/analysis/modality.json
for m in society_metrics sell_side disposition influence institutions social_proof compliance; do
  python -m flowmirror.analysis.$m runs/out/main_ref_s2027
done

# 3) 把数字填进 paper/DRAFT_S3_S5_20260909.md 里标了【待填】的地方
```

### 4.5 运行顺序（方案已定，按此执行）

1. `main_ref_s{2027,3031,4049,5057,6071}` —— 参考格 5 种子，**跑完这 5 个就能算模态主终点（df=4）**，是论文的头条结果
2. `main_nosuit_s{2027,3031,4049,5057,6071}` —— 适当性关对照
3. `heat_seed_s{2027,3031,4049}` —— 种子热度实验
4. `emerge_s{2027,3031,4049}` —— 涌现运行（150 人 × 40 日，大 V + 机构适应 + 种子热度全开）

十六次运行合计约 94,400 次调用。按 367 次/小时不间断，全部跑完约在 09-20；按 250 次/小时约在 09-25。参考格 5 种子在 09-12 到 09-13 出齐。

**砍单顺序（落后时从上往下砍）**：D 过滤臂运行 → B2 披露臂（已撤）→ 涌现种子 5→3 → 涌现天数 40→30 → 适当性关 5→3 种子 → 涌现运行整体。
**永不砍**：参考格 5 种子、卖出侧分析、限流加固、探针、X1。

---

## 5. 分工与发卡工作流

业主定的分工：**设计由 Fable 5.1，代码由 GLM 5.3 写，操作与审计由 Opus 5**。Codex 接手后请沿用「代码由 GLM 写」这一条，除非业主改口。

### 5.1 发一张卡

```bash
cd "D:/Desktop/ABM paper/tools"
python glm_run.py cards/<卡名>.md          # 默认用 .glm_key2，约 1–5 分钟返回
# 产出 cards/<卡名>.md.response.md
cd "D:/Desktop/ABM paper/flowmirror_v7"
python "D:/Desktop/ABM paper/tools/apply_patch.py" "D:/Desktop/ABM paper/tools/cards/<卡名>.md.response.md"   # PATCH 型
python "D:/Desktop/ABM paper/tools/land_multi.py"  "D:/Desktop/ABM paper/tools/cards/<卡名>.md.response.md"   # FILE 型
```

### 5.2 写卡的九条硬规矩（都是踩坑换来的）

1. **每一个锚点必须逐字符从文件里 grep 出来贴进卡**。凡是写「你自己去找那一行」，GLM 一定自编锚点，整个文件被跳过。今天因此踩坑 6 次。
2. 卡的第一行必须是 `你的回复第一行必须是 ### PATCH: <路径>` 或 `### FILE: <路径>`，并明确「不要复述、不要推理」。
3. 单张卡规格要窄。**≥300 行、5 个测度的大卡会让 GLM 推理几小时不出码**，实测发生 3 次；拆成 ≤220 行 + 后续 PATCH 卡就顺利。
4. **同一把 key 同一时刻只发一张卡**，并发第二张直接 429（业务码 1302）。杀掉挂死的卡后 key 还要约 20 分钟才恢复。
5. GLM 有时不写代码围栏直接输出整个文件，`land_multi` 会报 "no complete fenced block"，此时手工去掉首行标题与末尾 bullet 原样落盘即可。
6. 卡里凡涉及 `common.load_events`，必须写明它返回**按 ev 分组的字典**；GLM 两次把它当扁平列表（连 `list()` 都会把它变成键列表）。
7. `unittest.TestCase` 的方法**不能接 pytest 夹具**（`tmp_path`），要用 `tempfile.TemporaryDirectory()`。
8. 一致性检查条目在 `<run_dir>/invariants_report.json` 的 `checks`，**不在** `run_meta.json`。
9. 验收链里 `--self-test | tail` 会吞掉退出码，必须单独 `; echo exit=$?`。

### 5.3 落地后的固定验收

```bash
python -c "import ast;ast.parse(open('<改过的文件>',encoding='utf-8').read())"
python -m flowmirror.engine.loop --self-test; echo exit=$?     # 必须 exit=0
python -m flowmirror.cli run runs/demo_three_arm.json --mock --days 3 --agents 12 --out runs/out/acc_<卡名> --replay-check
cmp runs/out/acc_<卡名>/event_log.jsonl runs/out/<上一个基线>/event_log.jsonl   # 机制关闭时必须逐字节相同
python -m pytest -q                                             # 当前 482 项，必须全绿才提交
```

**提交前必须确认测试全绿**：曾经因为 `tail` 掩盖了 pytest 退出码而带着红测试提交过一次。

---

## 6. 已完成的工作清单

本轮会话共 51 次提交（`5d6dba9` 到 `adb151c`）。按模块列，附提交号与文件位置。

### 6.1 引擎机制（都默认关闭，关闭时事件日志逐字节不变）

| 编号 | 内容 | 文件 | 提交 |
|---|---|---|---|
| X1 | 卡片显示真实点赞数；`dec.p_like/p_save`；`post.note`；`run_meta.openings` | `flowmirror/engine/loop.py`、`flowmirror/engine/world.py` | `a7dffee` `ce06d18` |
| A1 | 429 归为传输失败、自适应闸门、传输洞可重试 | `flowmirror/agents/runtime.py` | `a654169` `58fd5e3` `4a4b3a7` |
| A2 | 任意 OpenAI 兼容端点、env 覆盖、`run_meta.provider` 只写主机名 | `flowmirror/agents/runtime.py` | `6b04194` `cc8dab4` |
| A5 | `llm.decision_failure_min_calls`（默认 100）停机样本下限 | `flowmirror/engine/loop.py` | `13a622b` |
| A6 | 反诱导检查只扫我们写的块，不扫 agent 自己的记忆与反思 | `flowmirror/agents/prompt.py` | `3ae5120` |
| A7 | 单模态组运行时 `h_arm_balance` 记为跳过而非失败 | `flowmirror/engine/world.py` | `0e17d91` `ccfa8e8` |
| A8 | 无 `Retry-After` 时默认等待 30·k → 10·k | `flowmirror/agents/runtime.py` | `0c3d27d` |
| A9 | `--workers N` 命令行并发覆盖（不动已冻结配置） | `flowmirror/engine/loop.py`、`flowmirror/cli.py` | `8c01ac2` |
| B1 | 买入批次 `inv.lots`、FIFO 赎回、`act.pnl/hold_days` | `flowmirror/engine/loop.py`、`flowmirror/engine/world.py` | `01103e8` |
| B2 | 证监会 2017 七日惩罚性赎回费（默认关，反事实始终记录） | `flowmirror/regulator/cn_redeem.py` | `7bcdb43` |
| E1/E2 | 大 V 层：句柄、关注边、粉丝加权热评、关注者动态 | `flowmirror/agents/prompt.py`、`flowmirror/engine/loop.py`、`flowmirror/channels/feed.py` | `430a62a` `0b45eb9` `6b10983` |
| E6 | 句柄从 5 位十六进制改 6 位（400 人撞名概率 7.4% → 0.48%） | `flowmirror/engine/loop.py`、`flowmirror/analysis/influence.py` | `80b8db8` |
| F1 | 候选笔记抽样改派生流（一次性日志哈希变更，预注册 D32 允许） | `flowmirror/engine/world.py` | `13a622b` |
| F2 | 机构适应：按响应重配意图权重，`inst` 事件 | `flowmirror/engine/loop.py` | `13a622b` |
| C1 | 种子热度实验（Muchnik 2013 设计）：发布时分组、首日卡片 +k | `flowmirror/engine/world.py`、`flowmirror/engine/loop.py` | `9ef7187` |

### 6.2 分析层（都在 `flowmirror/analysis/`，纯标准库，样本不足写 None + note）

| 模块 | 算什么 | 提交 |
|---|---|---|
| `flowmirror/analysis/sell_side.py` | 赎回概况、持有期分布、七日费暴露面 | `4befee7` |
| `flowmirror/analysis/disposition.py` | Odean PGR/PLR 处置效应 | `0ce7b0b` |
| `flowmirror/analysis/influence.py` | 粉丝分布 Gini、关注图同质性、跟单、影响 vs 同质性分解 | `fde06ce` `7940edb` `4d29f31` |
| `flowmirror/analysis/institutions.py` | 平台/机构 I2 占比路径、意图熵、权重两两 L1 收敛 | `b22b0b3` |
| `flowmirror/analysis/social_proof.py` | 首日点赞率差、级联乘数与路径判定、帖级自助 | `1026c36` |
| `flowmirror/analysis/compliance.py` | 合规 vs 不合规笔记的漏斗对比 | `d610048` |
| `flowmirror/analysis/society_metrics.py` | 集中度、LSV 羊群、立场熵、评论爆发（上一轮已有） | — |
| `flowmirror/analysis/modality.py` | 三组模态两两对比（上一轮已有） | — |

### 6.3 数据管线（`data_pipeline/cn/`）

| 脚本 | 作用 | 状态 |
|---|---|---|
| `data_pipeline/cn/caption_frozen.py` | TC 组冻结描述生成（冻结提示 sha、断点续跑、原子写出） | v2 已跑完 800/801 |
| `data_pipeline/cn/compliance_label.py` | 两遍合规打标（词表 + GLM 裁定） | 已跑完，108 次裁定 0 失败 |
| `data_pipeline/cn/labels_to_db.py` | 把内容池快照、描述、合规标签写进研究库三张新表 | 已写入 |

### 6.4 测试

当前 **482 项，全绿**。本轮新增：`tests/unit/test_social_graph.py`(8)、`tests/unit/test_institution_adaptation.py`(7)、`tests/unit/test_heat_seed.py`(5)、`tests/unit/test_halt_min_calls.py`(2)、`tests/unit/test_anti_priming_scope.py`(4)、`tests/unit/test_single_arm_invariant.py`(2)、`tests/unit/test_workers_override.py`(4)、`tests/unit/test_lots_and_short_term_fee.py`(11) 等。

---

## 7. 已经测到的科学结论（可直接写进论文）

全部来自 100 人 × 12 日真模型试跑 `runs/out/live_pilot_100x12`（glm-4.6v，1,400 次调用，终态失败 0，回放逐字节相同）。

### 7.1 微观

- **决策可靠性**：1,400 次调用零终态失败；2,376 张图零缺失零摘要不匹配；一致性检查 13 项零失败；回放逐字节相同。首次尝试失败率约 15%（全部在重试梯内救回）。
- **模态方向**（n=1，仅描述）：点击率 T 0.396 < TC 0.429 < TV 0.437；转化率 0.391 < 0.419 < 0.434，单调且与预注册方向一致。
- **零卖出（重要边界）**：1,200 次决策里模型自报动作只有 `none` 829 与 `buy` 670，**`redeem` 0 次**。已排除机制原因：解析零违规；同一引擎规则型基线有 26 笔赎回、DE = +0.056；窗口内 390 只基金有 293 只下跌；运行期买入 498 笔到末日 134 笔浮亏；后三日 300 次决策中 158 次由持有浮亏仓位者做出，无一卖出，理由集中在"坚持定投/长期看好"。
- **开局浮亏上限是真实净值史给的**：即使按目标模式要 50%，实际只能做到 6.6%（578 笔里 38 笔）。

### 7.2 宏观

- **漏斗在点击环节坍缩**（本文最强的一条）：96 条帖子里曝光铺到 92 条、Gini 0.592、头部单帖仅占 3.9%；但点击与申购只落在 **5 条**帖子上，点击 Gini 0.972，头部单帖独占 41.4% 的点击与 41.8% 的申购。
- **情绪单极化**：1,082 条评论中看多 841、观望 241、**看空 0**，立场熵 0.765 bit。
- **评论重尾**：单帖最高 218 条，Gini 0.893，96 条帖里只有 40 条收到评论。
- **LSV 羊群指标无定义**：零卖出导致所有格子只有买方，照实报告。
- **机构意图漂移的非适应基线**：I2 占比 0.375 → 0.425 → 0.438，意图熵 1.579 → 1.505。

### 7.3 内容侧

合规打标（glm-4.6，108 次裁定 0 失败）：200 条笔记中 64 条命中至少一条规则。R07 未核实数据 31、R01 保本 15、R06 诱导 10、R03 极端表述 7、R04 业绩无区间 4、R05 推品无风险提示 3、**R02 预测业绩 0**（待业主抽检核对是否漏判）。推品帖命中率 40%，非推品 27.7%。

### 7.4 工程实测（写进方法与附录）

| 配置 | 吞吐 | 备注 |
|---|---|---|
| key1 + glm-4.6v，6 并发 | 367 次/小时，单次中位 53 秒 | **目前最优** |
| key1 + glm-4.6v，10 并发 | 91 次/小时 | 账户级限流，并发上限在 6 附近 |
| key1 + GLM-5.3-flash | 单次 51 秒，输出 token 与 4.6v 相同 | flash 不更快 |
| autodl 中转 + flash，12 并发 | 110 次/小时 | 比直连慢 |

autodl 中转的可用信息：OpenAI 兼容路径 `https://www.autodl.art/api/v1/chat/completions`，Bearer 认证，40 个模型，GLM 系列只有 `GLM-5.3-flash` 支持图片，**没有 glm-4.6v**。key 在 `tools\.glm_key3`。

---

## 8. 预注册要点（`docs/PREREG_v1.5_DRAFT.md` 里 D20–D33）

写作与运行时必须遵守的几条：

- **D22**：TC 组定义已定稿为描述 v2（`data/creatives/cn/content_pool_v1_captioned_v2.jsonl`，prompt_sha `dea5cb49039d…`）。v1 有 550/800 被 40 字截断、425 条抄写图内文字，已废弃。v2 触上限仅 6 条、抄写 0 条，代价是 57 条（7%）带出颜色词，已披露并预注册稳健性检查：剔除这 57 张图所属笔记后重算 TV−TC。
- **D23**：十六份配置的 sha256 已冻结，表在预注册文末。**改配置就要更新那张表。**
- **D24**：429 归传输失败、不计入模型侧停机率；停机样本下限 100 次；模型失败阈值维持 2%。
- **D25**：七日费的行为披露臂**已撤下**（零卖出导致暴露面恒为 0）。
- **D26**：处置效应改为**零结果报告**，只在规则型基线与 40 日涌现运行上给。
- **D27/D30**：种子热度实验 k=10、p_treat=0.5；主因变量是级联乘数（到 t+2 累计多出的互动 ÷ k），带 amplifying / damped / none 判定。k 的定标依据：试跑每帖首日曝光中位 5 次、首日点赞中位 0（均值 2.6）。
- **D29**：所有真模型运行用同一个 vision model 做三组决策（隔离模态）。**中途换模型会让模型与种子混淆，禁止。**
- **D31**：句柄 6 位十六进制；大 V 层开启时 `prompt_sha` 会变。
- **D32**：机构适应 η=0.5、下限 0.05、周期 5 日；候选笔记抽样改派生流是一次性日志哈希变更，已在网格前完成。
- **D33**：涌现运行 150 人 × 40 日，模态固定单组；集体层结论措辞档 `collective_qualitative`（Wu & Peng 2025）。

---

## 9. 待办清单（按优先级）

### P0 · 不做就没论文

1. **看住主网格运行**，一次跑完做一次 §4.4 的固定动作，然后手工启动下一个种子。
2. 参考格 5 种子齐了之后跑 `python -m flowmirror.analysis.modality runs/out/main_ref_s2027 ... runs/out/main_ref_s6071`，得到三组两两对比与种子级 t 区间，填进 `paper/DRAFT_S3_S5_20260909.md` 的 §4.2。
3. 把 §5 的全部宏观量从单次试跑换成 5 种子的均值与区间。

### P1 · 业主要做的人工动作

4. **抽检 30 条合规样本**（`data/creatives/cn/compliance_review_sample.md`），重点看 R02 零命中是否漏判。
5. **抽检 30 张描述样本**（`data/creatives/cn/caption_review_sample.md`）。

### P2 · 论文其余部分

6. 写 §1 引言、§2 相关工作（文献锚清单在 `docs/PLAN_v2_20260908.md` 末尾，已核实出处，直接搬）。
7. 写 §6 涌现（等种子热度与涌现运行结果）。
8. §7 讨论与局限：零卖出、情绪单极化、单一模型族这三条边界必须写。

### P3 · 有余力再做

9. 描述 v2 的 1 张失败图（`69aac1c1000000001a0267a5_2.jpg`，两版都 HTTP 400）可重跑补齐。
10. 平台内容过滤臂（预注册里标为可选）。
11. 独立审计：把 `docs/AUDIT_BRIEF_2026-09-08.md` 交给一个干净上下文，按它列的攻击点查。

---

## 10. 会话中做过的关键操作与决策记录

按时间顺序，只列改变了项目状态的：

1. 跑通阶段 0 探针 0a/0b/0c，量出真实吞吐，确认 09-07 全 429 的原因是引擎与发卡共用同一把 key。
2. 落地 X1、A1、A2、B1、B2 五组引擎改动，全部带测试。
3. 跑完 TC 描述批量 v1（801 张），发现 550 张被截断、425 张抄写图内文字。
4. 落地大 V 层（E1/E2a/E2b）、机构适应（F1/F2）、种子热度（C1），各自带分析模块与测试。
5. 跑 100×12 真模型试跑，中途因反诱导误判崩溃一次（A6 修复后重跑），最终 1,400 次调用零失败跑完。
6. 从试跑数据发现**零卖出**，据此修订预注册 D25/D26。
7. 跑完合规打标（108 次裁定），产出标签与抽检样本。
8. 按业主决定重跑描述 v2，触上限从 550 降到 6、抄写从 425 降到 0。
9. 生成并冻结十六份网格配置，sha 表写进预注册 D23。
10. 把内容池快照、描述、合规标签写进研究库三张新表。
11. 启动主网格第一个种子；期间电脑重启一次，用缓存无损恢复。
12. 按业主要求测试 flash 与 autodl 中转，实测都不更快，按业主指示恢复原配置。
13. 写出论文 §3–§6 初稿。

---

## 11. 常用命令速查

```bash
# 进仓库
cd "D:/Desktop/ABM paper/flowmirror_v7"

# 跑一次真模型运行
python -m flowmirror.engine.loop runs/<配置>.json --workers 6 --out runs/out/<tag>

# 替身模型快速验证（不花钱）
python -m flowmirror.cli run runs/demo_three_arm.json --mock --days 3 --agents 12 --out runs/out/acc_x --replay-check

# 三个自检（改完引擎必须跑，都要 exit=0）
python -m flowmirror.engine.world --self-test; echo exit=$?
python -m flowmirror.engine.loop  --self-test; echo exit=$?
python -m flowmirror.channels.feed

# 全部测试
python -m pytest -q

# 校验配置
python -m flowmirror.cli validate runs/<配置>.json

# 分析
python -m flowmirror.analysis.<模块> runs/out/<tag>

# 只读查研究库
python -c "import sqlite3;con=sqlite3.connect('file:D:/Desktop/ABM paper/fundmarket-sim/flowmirror.db?mode=ro',uri=True);print(con.execute('select count(*) from pool_note').fetchone())"
```

---

## 12. 给 Codex 的几句提醒

- 本项目的价值集中在**已经跑出来的真实数据**和**十六份冻结配置**上。代码可以重写，那些运行结果重跑要花几天机器时间，`runs/out/` 千万别动。
- 遇到"要不要加一层校验/哈希/门禁"的念头，先读 §3 第 8 条。业主对这件事有明确态度。
- 引擎里每一个机制开关都有"关闭时逐字节相同"的保证，这是整套设计的地基。任何改动如果破坏了它，就是 bug，不是新特性。
- 论文初稿里每个数字都标了来源文件，填新数字时请保持这个习惯，审稿人问起来能直接指过去。

---

## 13. 交接验收（2026-09-09 12:18–12:35 实测，只读核验）

本节以**当前工作树、当前进程、当前缓存、当前结果文件**为准，不复述旧摘要。核验期间未重跑任何付费实验、未启停主网格、未改数据库、未动 `runs/out/`。

### 13.1 Git

| 项 | 实测 |
|---|---|
| 分支 | `main` |
| HEAD | `7ec7421f759ee4e738f0511ca5732b5598a41547` |
| 工作树 | 干净（`git status --porcelain` 无输出） |
| `7ec7421` | 存在，类型 commit，含 `docs/HANDOVER_TO_CODEX_20260909.md`（398 行）与 `docs/PLAN_v2_20260908.md`（451 行），共 849 行新增 |
| 本地 vs `origin/main` | `git rev-list --left-right --count HEAD...origin/main` → `0 0`，完全一致 |
| 最近 5 提交 | `7ec7421` 交接文档 / `adb151c` 论文初稿 / `8c01ac2` A9 并发开关 / `c056a9f` 描述 v2 与十六份配置 / `c8a8111` 数据库交接 |

**文档内 HEAD 与实际不一致的原因**：正文 §2.1 写作时 HEAD 是 `adb151c`，随后该文档自身被提交为 `7ec7421`，即文档记录的是其父提交。已在 §2.1 就地注明，未为此另开提交。

### 13.2 主网格实时状态 —— 发现一个必须处理的问题

**同一个输出目录上有两个引擎进程在跑**（只读观测，未做任何处置）：

| PID | 启动时间 | 命令行 |
|---|---|---|
| 15924（shim 29380） | 2026-09-09 09:46:24 | `-m flowmirror.engine.loop runs/main_ref_s2027.json --workers 10 --out runs/out/main_ref_s2027` |
| 28152（shim 30856） | 2026-09-09 09:54:35 | `-m flowmirror.engine.loop runs/main_ref_s2027.json --workers 6 --out runs/out/main_ref_s2027` |

成因：前任操作方在 09:54 与 11:00 两次用 `taskkill` 停旧进程，**两次都没真正杀掉**，于是 10 并发那次一直活着。

已量出的损害（全部只读统计）：

| 项 | 实测 |
|---|---|
| `llm_cache.jsonl` 行数 | 1,580（核验时刻），解析状态 100% `ok` |
| **重复缓存键** | 174 个，即 **174 次白花的付费调用（约 11%）** |
| `event_log.jsonl` | 15,572 行，**损坏 1 行**（第 15571 行被截断），完整行级重复 2 行，均在末尾同一 agent `inv_09784` 上 |
| `st` 行按 (t, i, org, what) 去重 | 重复 0，说明没有系统性双写 |
| 每日决策数 | t=0/1/2 各 400，无翻倍 |
| 累计限流等待 | 40 次 |
| 最近 30 分钟吞吐 | 298 次/小时 |
| 最后一条缓存 | 核验时距今 6 秒 |
| 当前模拟交易日 | t=3（共 12） |
| `run_meta.json` / `invariants_report.json` | 均不存在（运行未完成，属正常） |

**判定：正常推进，但结果不可信。** 两个进程都在为同一次运行付费，且事件日志已出现交错写入的痕迹（1 行损坏 + 2 行重复）。这次运行结束时的 `--replay-check` 很可能不通过。

**处置（需业主授权，本次核验未执行）**：把两个 PID 都停掉，然后只启动一个。缓存可复用，1,580 次已完成调用不会丢；引擎重启时会把现有事件日志备份并从头重写，所以损坏与重复会自愈。停进程用 PowerShell 更可靠（`taskkill` 在本机已两次失效）：按 `Get-CimInstance Win32_Process` 过滤 `CommandLine` 含 `main_ref_s2027`，再 `Stop-Process -Id <PID> -Force`；确认进程数归零后，只执行一次 §4.3 的重启命令。跑完后务必执行 `--replay-check`。

### 13.3 十六次运行状态矩阵（按实际文件，不按计划推断）

| 配置 | 人 | 日 | 模型 | 并发 | cache 行 | run_meta | event_log 行 | 分析产物 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| `runs/main_ref_s2027.json` | 400 | 12 | glm-4.6v | 6 | 1,580 | 无 | 15,572 | 0/8 | 推进中（见 13.2 的双进程问题） |
| `runs/main_ref_s3031.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始 |
| `runs/main_ref_s4049.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始 |
| `runs/main_ref_s5057.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始 |
| `runs/main_ref_s6071.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始 |
| `runs/main_nosuit_s{2027,3031,4049,5057,6071}.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始（5 份） |
| `runs/heat_seed_s{2027,3031,4049}.json` | 400 | 12 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始（3 份） |
| `runs/emerge_s{2027,3031,4049}.json` | 150 | 40 | glm-4.6v | 6 | 0 | 无 | 0 | 0/8 | 未开始（3 份） |

- 输出目录一律为 `runs/out/<run_tag>/`，与 `run_tag` 同名。
- 十六份配置的 `content_pool` 全部指向 `data/creatives/cn/content_pool_v1_captioned_v2.jsonl`（已核实文件存在）。
- **`--replay-check` 迄今只在 `runs/out/live_pilot_100x12` 上做过，结果 `identical=True`，但该结果只存在于当时的终端输出，仓库内没有持久化产物。** Codex 可用缓存免费重跑一次自证。
- 十六次运行的分析产物目前全部为 0/8；唯一有分析产物的是试跑 `runs/out/live_pilot_100x12/analysis/`（7 个 JSON，缺 `modality.json`，原因见 13.5 第 1 行注）。

### 13.4 代码与测试验收（均不产生付费调用）

| 命令 | 结果 | 时间 |
|---|---|---|
| `python -m pytest -q` | **482 passed**，用时 84.95s | 12:22:24 起 |
| `python -m flowmirror.engine.world --self-test` | exit=0，PASS 50，FAIL 0 | 12:23:51 |
| `python -m flowmirror.engine.loop --self-test` | exit=0，PASS 42，FAIL 0 | 12:23:53 |
| `python -m flowmirror.channels.feed` | exit=0，无 FAIL | 12:23:55 |
| 十六份配置逐个 `flowmirror.cli validate` | OK=16，FAIL=0 | 12:23:56 |

数据资产（12:30 实测）：

| 项 | 实测 |
|---|---|
| 图片库 `fundmarket-sim/sim/content_pool_v1/images` | 801 个图片文件 |
| `data/creatives/cn/content_pool_v1_captioned_v2.jsonl` | 存在，2,442,697 字节，800 条描述 |
| `data/creatives/cn/compliance_labels_v1.jsonl` | 存在，182,595 字节 |
| `data/funds/nav_cache.json` | 存在，4,394,511 字节 |
| MANIFEST 校验和 | v2 记录 `a98519674b47…` = 实际；v1 记录 `a17007dd169b…` = 实际，两者均一致 |
| 研究库（只读打开） | `pool_note` 400、`pool_image_caption` 1,600、`note_compliance_label` 1,400；既有表 `asset_ocr` 3,431、`asset` 29,208、`xhs_note` 9,708 |
| `pool_image_caption` 版本分布 | `content_pool_v1_captioned` 800 + `content_pool_v1_captioned_v2` 800（v1/v2 共存，按 `pool_version` 区分，符合设计） |

注：`pool_note` 400 行 = 200 条笔记 × 2 个池版本，主键是 (note_id, pool_version)，不是重复写入。

### 13.5 七个未决问题的实测判定

| # | 问题 | 判定 | 证据 |
|---|---|---|---|
| 1 | `flowmirror/analysis/modality.py` 的真实日志 I2 识别 | **已修复** | 模块文档字符串第 11–14 行：以 `post` 行的 `ig` 字段（值域 `{I2, nonI2}`，由 `world.publish_day` 写入）判定 I2，仅当日志完全没有 `ig` 时才回退旧启发式 `_is_i2`。真实日志携带 `ig`。另注：该模块**不给 `--out` 就只打印不落盘**，这是试跑目录缺 `modality.json` 的原因，已在 §4.4 修正命令 |
| 2 | `guba_signal` 的真实 bull/bear 字段覆盖 | **未修复** | `data/attention/guba_signal_v1.json`：442 条基金-周记录中带 `bull_ratio` 的为 **0 条（0.0%）**。引擎每次启动打印 `guba stance seed unavailable ... stance labelling has not been run`，评论区舆论标签退回由 agent 评论自举。影响：Environment 环少一条外生立场输入 |
| 3 | TV 图片是否在真实提示中传到模型 | **已修复，三重证据** | (a) `run_meta.images = {attached: 2376, missing: 0, sha_mismatch: 0}`；(b) 事件日志里带 `img_idx` 的 `imp` 行恰为 2,376 条，等于 TV 臂 imp 行数；(c) 缓存中 1,699 条有 usage 的调用里，495 条（29%）`prompt_tokens` 中位 5,500，其余 1,204 条中位 2,440，差约 3,060 token，与"每图约 500 token、最多 6 图"一致。TV 臂占 agent 的 33%，反思调用无图故略低 |
| 4 | TC v2 描述是否仍有截断或颜色词 | **部分修复** | 当前被十六份配置引用的 v2 文件：800 条描述，长度中位 29，触到 40 字上限 **6 条（0.8%）**、含引号抄写图内文字 **0 条**（v1 分别为 550 与 425）；但含颜色词 **57 条（7.1%）**，与冻结提示"不使用颜色词汇"不符。已在预注册 D22 披露并预注册稳健性检查（剔除这 57 张图所属笔记后重算 TV−TC）。**该稳健性检查的代码尚未实现** |
| 5 | 真模型 12 日窗口零赎回及处置效应的解释边界 | **已定性并写入预注册，但尚无主网格证据** | `runs/out/live_pilot_100x12/analysis/sell_side.json` 的 `n_redeem: 0`；`disposition.json` 全 None 带 note。预注册 D25 已撤下披露臂、D26 改为零结果报告；论文初稿 §4.3 有四条证据链。**目前只有一次 100 人试跑支撑，主网格未验证** |
| 6 | 100×12 试跑数字是否只是探索性结果 | **是，文档已正确标注** | `paper/DRAFT_S3_S5_20260909.md` 第 54 行声明数字来自单次试跑；第 77 行标注"（n=1 次运行，仅作描述，不做推断）"；第 85 行声明主终点以 5 种子的种子级 t 区间为准并标 `【待填】` |
| 7 | 五个参考种子是否已足够计算种子级模态主终点 | **设计上足够，但目前一个都没跑完** | `flowmirror/analysis/common.py:55` 的 `seed_t_interval` 给出 df = n−1，5 种子即 df=4；`modality.py` 在 n_runs < 2 时输出 `n_runs=<n>: descriptive only` 并 withhold 区间。当前 `main_ref_s*` 中仅 `s2027` 推进到 t=3，其余四份 cache 为 0。**主终点仍需 5 次完整运行** |

### 13.6 结论分级（写作时按此措辞）

**A 类 · 已验证的工程事实**（可直接写进方法与附录）

| 数字 | 来源文件 | 样本单位 | 真实模型 | 可否直写 |
|---|---|---|---|---|
| 1,400 次调用、终态失败 0、传输/模型侧失败各 0 | `runs/out/live_pilot_100x12/run_meta.json` → `counters` | 一次运行的全部调用 | 是 | 可 |
| 图片附着 2,376、缺失 0、摘要不匹配 0 | 同上 → `images` | 同上 | 是 | 可 |
| 一致性检查 13 项、失败 0、跳过 3 | `runs/out/live_pilot_100x12/invariants_report.json` → `summary` | 同上 | 是 | 可 |
| 缓存重放逐字节相同 | 会话终端输出，**仓库无持久化产物** | 同上 | 是 | 免费重跑留档后可 |
| 六并发 367 次/小时；十并发 91；flash 单次 51s；中转十二并发 110 | 本文 §7.4（本会话实测） | 多次窗口采样 | 是 | 可（工程可行性） |
| 描述 v2：触上限 6/800、抄写 0/800、颜色词 57/800 | `data/creatives/cn/content_pool_v1_captioned_v2.jsonl` | 800 条描述 | 是（glm-5.3-flash 生成） | 可 |
| 合规打标 108 次裁定 0 失败、64/200 命中 | `data/creatives/cn/compliance_labels_v1.jsonl` → `_meta.counts` | 200 条笔记 | 是（glm-4.6） | 可，措辞为描述性（D28） |

**B 类 · 单次真模型试跑的探索性结果**（样本单位 = 1 次运行，不可作为论文主张）

| 数字 | 来源文件 | 应有措辞 |
|---|---|---|
| 三组点击率 0.396 / 0.429 / 0.437；转化率 0.391 / 0.419 / 0.434 | 试跑 `event_log.jsonl` 按臂聚合 | "单次试跑呈现与假设一致的单调序，无推断力；主终点见 5 种子结果" |
| 曝光 Gini 0.592、点击 Gini 0.972、5/96 帖获得全部点击与申购 | `analysis/society.json` → `concentration` | "单次运行观察到的漏斗坍缩模式，待 5 种子确认" |
| 立场熵 0.765 bit、看空 0 条 | `analysis/society.json` → `stance_entropy` | 同上 |
| 评论 Gini 0.893、单帖最高 218 条 | `analysis/society.json` → `comment_burst` | 同上 |
| 零赎回（1,200 次决策 redeem 0 次） | `analysis/sell_side.json` + 缓存动作统计 | "在这一次 100 人 × 12 日运行中未出现赎回"，主网格验证后再升格 |
| 机构 I2 占比 0.375 → 0.438 | `analysis/institutions.json` → `platform.periods` | 同上 |

**C 类 · 替身模型或规则基线结果**（只作阳性对照与机制验证，绝不可当作 agent 行为发现）

| 数字 | 来源文件 | 用途 |
|---|---|---|
| 规则基线 26 笔赎回、PGR 0.293 / PLR 0.237 / DE +0.056 | `runs/out/acc_b2_off/analysis/disposition.json` | 证明卖出通路与处置效应测度可用 |
| 替身种子热度乘数 0.201、判定 damped | `runs/out/acc_c1_on/analysis/social_proof.json` | 只证明模块能算，数字无科学含义 |
| 替身机构权重 L1 0.30 → 0.56 | `runs/out/acc_f2_on/analysis/institutions.json` | 同上 |

**D 类 · 尚未获得**：主网格 5 种子的模态主终点与全部宏观量区间、适当性开关对照、种子热度级联乘数、大 V 层影响分析、机构规范收敛、40 日涌现运行的处置效应。论文初稿中已用 `【待填】` 标注。

### 13.7 Codex 接手后的第一条安全动作

**先只做一件事**：按 §4.2 的命令读一次主网格状态，并用 PowerShell 的 `Get-CimInstance Win32_Process` 过滤 `CommandLine` 含 `main_ref_s2027`，确认当前有几个进程。

- 若为 **1 个**：什么都不用做，继续观察；跑完后按 §4.4 执行（注意 `modality` 必须带 `--out`）。
- 若为 **2 个或更多**：即 §13.2 描述的问题，**先向业主报告并取得授权**，再停进程并只重启一个。缓存可复用，不会丢已完成的调用。

任何情况下都不要删除 `runs/out/` 下的文件，不要改数据库，不要改十六份配置（sha256 已冻结在预注册 D23）。

---

## 14. 2026-09-09 14:20 复核：机制可行性与"大 V 层饥饿"问题

业主指示"不用跑整体，只验证项目没问题并同步 GitHub"。本节记录停机点、逐机制可行性判定，以及一个必须在涌现运行前决定的问题。

### 14.1 停机点（全部只读记录，未删除任何文件）

| 运行 | cache 行 | run_meta | 说明 |
|---|---|---|---|
| `runs/out/main_ref_s2027` | 1,713 | 无 | 主网格种子 1，按业主指示停止；缓存可复用，将来重启不丢已完成调用 |
| `runs/out/smoke_social_20x5` | 120 | 无 | 大 V 层冒烟探针，跑到第 2 日停 |
| `runs/out/smoke_inst_20x6` | 140 | 无 | 机构适应探针 |
| `runs/out/smoke_heat_20x5` | 68 | 无 | 种子热度探针 |

引擎进程已确认 0 个，四个缓存文件 15 秒采样零增长，不再产生费用。

### 14.2 逐机制可行性判定

| 机制 | 判定 | 依据 |
|---|---|---|
| 多模态对比（论文头条） | **可行，已证明** | 100 人 × 12 日真跑：1,400 次调用终态失败 0；2,376 张真图三重证据送达；一致性检查 13 项 0 失败；缓存重放逐字节相同。三组点击率与转化率呈 T < TC < TV 单调序 |
| 机构适应 | **可行，与模型意愿无关** | `inst` 事件由交易日计时器触发，权重更新是 `_inst_update` 纯函数。替身运行 `runs/out/acc_f2_on` 已产出 8 条 `inst` 行，权重和为 1、均不低于下限。真模型下必然照样触发，未知的只是效应量 |
| 种子热度 | **可行，与模型意愿无关** | 分组在发帖时由派生流 `rng_for(run_tag,"heat_seed",pid)` 决定，卡片点赞数由引擎加 k。替身运行 `runs/out/acc_c1_on` 已验证 13 plus / 11 ctrl，且首日 `imp` 行与关闭态完全相同。未知的只是级联乘数大小 |
| 七日惩罚性赎回费 | **真模型下无法验证** | 12 日窗口内 agent 零赎回，规则暴露面恒为 0。已按零结果写入预注册 D25/D26 |
| **大 V 层（社交图）** | **结构性受限，需业主决定** | 见 14.3 |

### 14.3 大 V 层饥饿问题（今日新发现，必须在涌现运行前定）

**现象**：`runs/out/smoke_social_20x5`（`social_graph.enabled: true`，真模型，跑到第 2 日、40 次决策）产生关注边 **0 条**，`dec` 行带 `p_follow_users` 的 0 条。

**根因（已用免费替身运行 + 提示转储定位，不是模型不愿意关注）**：

1. 句柄与粉丝数只渲染在"昨日评论"块里（`flowmirror/agents/prompt.py` 的 `render_social`）。
2. 该函数在舆论标签为 `no_signal` 时**整块返回空串**，句柄随之消失。
3. 舆论标签要求该帖前一日至少 4 条评论（`flowmirror/channels/feed.py` 的 `climate_for(min_n=4)`，这个下限是早前刻意冻结的，防止稀薄评论伪造社会证明）。
4. 于是提示里同时出现两件互相矛盾的事：**指令说"评论区的作者带有句柄（如 @u3f9a），可以用 `follow_users` 关注"，而卡片里一个真实句柄都没有**。免费替身转储 `runs/out/mockcheck_social/prompts/inv_00012_d1.txt` 实测：`follow_users` 指令存在，真实 `@u` 句柄出现 **0 次**（唯一的 `@u3f9a` 是指令里的举例，且是 E6 改成 6 位之前的旧格式，属笔误级问题）。

**100 人 × 12 日试跑上的定量事实**（`runs/out/live_pilot_100x12`）：

| 项 | 实测 |
|---|---|
| 舆论标签分布 | `no_signal` 244 / `bullish_majority` 18 / `mixed` 2 |
| 清过 4 条评论门槛的（日, 帖）对 | 20 个，占 8% |
| 卡片带评论块的曝光 | 1,613 / 7,200 次（22.4%） |
| 承载评论块的帖子 | 96 个帖里只有 11 个 |
| 每个 agent 平均看到评论块 | 16.1 次（12 天内） |

**推论**：20 人探针把"看到句柄"的机会压到接近 0，所以探针的零关注边**不能证明模型不愿关注**——探针规模选错了。100 人规模下每人有约 16 次机会；150 人 × 40 日会更多，但仍集中在评论量最大的少数帖上。

**两个选项（需业主定）**：

- **选项一 · 保持现状**：直接跑 150 人 × 40 日涌现运行。关注边会集中在少数高评论帖的评论者身上，这本身正是优先连接的故事；风险是边数太少，粉丝分布与同质性算不出有意义的结果。
- **选项二 · 解耦（前任操作方的建议）**：让句柄与粉丝数在"有评论就渲染"，不再受舆论标签门槛约束。改动量是 `render_social` 一处判断；句柄曝光面从 22.4% 升到接近 100%。代价是提示字节变化、`prompt_sha` 变动，属预注册 D31 范围内的机制细化，**必须在网格与涌现运行开跑前完成**，否则会造成跨运行不可比。

另有一处笔误建议随同修正：`INSTR_FOLLOW_ZH` 里的举例句柄 `@u3f9a` 是 5 位十六进制，E6 已把真实句柄改为 6 位，举例应同步改成 6 位以免误导模型。

### 14.4 本次复核的验收结果（全部不产生付费调用）

| 检查 | 命令 | 结果 |
|---|---|---|
| 全套测试 | `python -m pytest -q` | **482 passed**（36.93s） |
| 世界自检 | `python -m flowmirror.engine.world --self-test` | exit=0，FAIL 0 |
| 主循环自检 | `python -m flowmirror.engine.loop --self-test` | exit=0，FAIL 0 |
| 信息流自检 | `python -m flowmirror.channels.feed` | exit=0，FAIL 0 |
| 十六份配置 | `flowmirror.cli validate` 逐个 | OK 16/16 |
| 分析模块可导入 | 逐个 `importlib.import_module` | 10/10 |
| 数据管线自检 | `caption_frozen` / `compliance_label` / `labels_to_db` 各 `--self-test` | 三个均 exit=0 |

### 14.5 GitHub 同步与安全审计

| 项 | 实测 |
|---|---|
| 远端 | `https://github.com/lordelrey/flowmirror.git` |
| `origin/main` | 与本地 HEAD 一致，`git rev-list --left-right --count HEAD...origin/main` → `0 0` |
| 工作树 | 干净 |
| CI（GitHub Actions） | main 最近三次推送全部 `success` |
| 历史中被跟踪过的 key 或 `api.yaml` | **无**（`git log --all --name-only` 全历史检索为空） |
| 全历史 diff 中的密钥样式串 | **0 处** |
| `config/api.yaml` | 已被 `.gitignore` 覆盖 |
| 已跟踪的图片文件 | 0 |
| 已跟踪的 `.db` 文件 | 0 |
| 已跟踪的 `runs/out/` 文件 | 0 |
| 跟踪文件总数 / `.git` 体积 | 223 个 / 9.1 MB |

远端另有一个分支 `copilot/fix-failing-github-actions-job-test`（GitHub Copilot 早前创建），与 `main` 无关，未合并，不影响交接。

---

## 15. 2026-09-09 15:40 收尾：大 V 层三项改动已落地，交由 Codex 接续

业主指示"三个都可以做"，指 §14.3 里列出的三个选项。三项已全部实现、提交、推送。本节是本会话的最终状态与 Codex 的接续清单。

### 15.1 已实现的三项（提交 `69f6da2`）

| 卡 | 改的是什么 | 为什么 | 实测效果 |
|---|---|---|---|
| **E7** | 句柄与粉丝数不再受舆论标签约束；只要该帖 t−1 有评论就渲染摘录，`no_signal` 时表头只报条数、不断言舆论倾向。`climate_for(min_n=4)` 门槛保留不动 | `render_social` 在 `no_signal` 时整块返回空串，而句柄只住在这个块里，于是 100 人真跑只有 22.4% 的曝光带句柄，模型被要求关注它从未看见的人 | 替身 100 人复测：句柄曝光 18.5% → **49.4%**；转储实测单次提示里出现 **6 个真实句柄**，其中两个来自过去被吞掉的 `no_signal` 帖 |
| **E7**（同卡第二项） | 热评平手规则由 `agent_id` 升序改为 `sha256(run_tag\|t\|post_id\|agent_id)`，粉丝数仍是主键 | 粉丝全为 0 时 id 成了唯一有效键，实测只有 id 最小的 **60/100** 人可能被摘录，"谁成名"由编号决定，不是涌现 | 改后 **78/100** 人有机会；实测无 salt 保持 `a1,a2,a3`，`salt="s\|2\|P1"` 变为 `a2,a1,a3`，粉丝 5 的那条在任意 salt 下仍第一 |
| **E8A** | 卡片摘录改为**每 agent 各自**：前 2 个位置优先给该 agent 关注对象写的评论（即使全平台前三未选中），标记「（你关注的）」，其余按共享排名填并按作者去重 | 全平台共享的前三名让关注不产生任何后果，一个关注者可能整轮再也见不到被关注者，反馈边不存在 | 7 项单测通过；无关注或关闭态时返回与改前一致的列表 |
| **E8B** | 新增平台级「平台推荐关注（按粉丝数）」块，展示 t−1 粉丝最多的 3 个账号（句柄、粉丝数、昨日发言条数，**不含立场**以免诱导），仅当至少一人粉丝 ≥1 时出现 | 真实平台的推荐关注模块是富者愈富最直接的放大器 | 6 项单测通过；冷启动时块正确缺席（替身运行确认） |

设计与每个选择背后的测量：`docs/DESIGN_SOCIAL_GRAPH_E7_20260909.md`。预注册 **D31 已按四项修订**，含零结果措辞。

### 15.2 验收结果（全部不产生付费调用）

| 检查 | 结果 |
|---|---|
| 全套测试 | **502 passed**（新增 20 项：`test_social_visibility.py` 7、`test_followed_excerpt.py` 7、`test_suggest_follow.py` 6） |
| `loop` / `feed` 自检 | 均 exit=0，零 FAIL |
| 关闭态与 `runs/out/acc_e6` 逐字节相同 | **三次分别验证通过**（E7 后、E8A 后、E8B 后） |
| 替身 100 人 × 5 日开启态一致性检查 | PASS |
| 冷启动行为 | 「（你关注的）」标记与两个依赖关注的块正确缺席（替身从不关注） |

**关闭态逐字节不变**这条守住了，意味着主网格与预注册 D23 的十六份配置 sha 完全不受影响；只有 `runs/emerge_*.json`（开关开）的 `prompt_sha` 变动，而这三次运行尚未开始。

### 15.3 尚未验证的部分：需要一次真模型探针

替身模型从不关注任何人，所以"模型愿不愿意关注"只能用真模型验。**这是大 V 层唯一剩下的未知**。

```bash
# 100 人 × 5 日，社交图开启，约 600 次调用、约 2 小时
# 探针规模必须是 100 人：20 人规模下每人看到评论块的机会接近 0，
# 今天的 20 人探针就栽在这里，零边不能证明任何事
cd "D:/Desktop/ABM paper/flowmirror_v7"
python - <<'PY'
import json, io
c = json.load(io.open('runs/main_ref_s2027.json', encoding='utf-8'))
c['_what'] = 'Real-model probe of the influencer layer after cards E7/E8A/E8B.'
c['run_tag'] = 'probe_social_100x5'; c['out_dir'] = 'runs/out/probe_social_100x5'
c['n_agents'] = 100
c['window'] = dict(c['window'], max_trading_days=5)
c['social_graph'] = {'enabled': True}
c['llm'] = dict(c['llm'], cache='runs/out/probe_social_100x5/llm_cache.jsonl')
io.open('runs/probe_social_100x5.json', 'w', encoding='utf-8', newline='\n').write(
    json.dumps(c, ensure_ascii=False, indent=2) + '\n')
PY
python -m flowmirror.cli validate runs/probe_social_100x5.json
python -m flowmirror.engine.loop runs/probe_social_100x5.json --workers 6 --out runs/out/probe_social_100x5
```

判读（一条命令）：

```bash
python -c "
import json,io,collections
e=[]
for l in io.open('runs/out/probe_social_100x5/event_log.jsonl',encoding='utf-8'):
    l=l.strip()
    if l:
        try: e.append(json.loads(l))
        except ValueError: pass
fu=[r for r in e if r['ev']=='st' and r.get('what')=='follow_user']
dec=[r for r in e if r['ev']=='dec']
print('关注边 %d 条 | 关注过人的 agent %d | dec 带 p_follow_users %d/%d'%(
    len(fu), len({r['i'] for r in fu}), sum(1 for r in dec if r.get('p_follow_users')), len(dec)))
print('违规:', dict(collections.Counter(x for r in dec for x in (r.get('violations') or []))) or '无')
"
```

**判据**：

- **有边** → 大 V 层可行，直接进 `runs/emerge_*.json` 三次涌现运行，粉丝分布、关注图同质性、跟单率都能算。
- **零边且无 `unknown_handle` 违规** → 这才是真发现：**这一代 LLM 散户看得见也不自发建立社交连接**，与"12 日内从不卖出"同一个模式。此时按 D31 的零结果措辞写进论文边界，涌现章节改由「注意力级联」（种子热度）与「机构规范收敛」两条支撑。**不要再加机制去逼它关注**——那会把发现变成人工制品。
- **零边但有 `unknown_handle` 违规** → 说明模型想关注但句柄解析有问题，回头查 `parse_decision` 的 `visible_handles` 传参。

### 15.4 本会话结束时的确切状态

| 项 | 值 |
|---|---|
| HEAD | `69f6da2` |
| 工作树 | 干净 |
| 与 `origin/main` | 一致（`0 0`） |
| 全套测试 | 502 passed |
| **正在运行的进程** | **无**（引擎、发卡、探针全部已停） |

各运行目录的停机点（**一律不要删**）：

| 目录 | 缓存调用数 | 完成 | 说明 |
|---|---|---|---|
| `runs/out/main_ref_s2027` | 1,713 | 否 | 主网格第一个种子，第 3/12 日。**含 §13.2 的双进程污染**：174→247 次重复调用、事件日志 1 行损坏 2 行重复。**重启会备份并重写事件日志，污染自愈**；缓存可复用 |
| `runs/out/smoke_social_20x5` | 120 | 是 | 20 人社交探针，**已作废**：跑在 E7 之前的代码上，且 20 人规模看不到句柄，零边无意义 |
| `runs/out/smoke_inst_20x6` | 140 | 是 | 机构适应真模型探针，跑完 |
| `runs/out/smoke_heat_20x5` | 68 | 否 | 种子热度真模型探针，中途停 |
| `runs/out/flash_probe_20x3` | 60 | 是 | flash 模型测速，结论见 §7.4 |
| `runs/out/relay_probe_20x3` | 16 | 否 | autodl 中转测速，结论见 §7.4 |
| `runs/out/mock_sg_on_100x5`、`mock_sg_on_100x5b`、`mock_sg_off_100x5`、`mockcheck_social` | 0（替身） | 是 | 句柄曝光面的免费验证证据 |

### 15.5 Codex 接手后的执行顺序

### 多日无人值守驱动脚本（Codex 直接启动这一个）

`script/run_grid.py` 按预注册顺序逐个跑十六次运行，每次跑完自动做免费回放校验与八个分析模块，然后接下一个。合计约 94,400 次调用、按 300 次/小时约 13 天。

```bash
cd "D:/Desktop/ABM paper/flowmirror_v7"
python script/run_grid.py --dry-run          # 先看计划，不启动任何进程
python script/run_grid.py                    # 正式启动（会花钱，需业主授权）
```

它的设计要点，都是今天踩坑换来的：

- **并跑保护**：启动每个 tag 前查是否已有引擎进程（PowerShell 查 `CommandLine`，排除自身 PID），大于 0 就整脚本退出并报是哪个 tag 被拦下。今天两个实例同时写同一目录，浪费了约 247 次付费调用并弄坏了事件日志，这个保护就是为此而加。
- **跳过已完成**：`runs/out/<tag>/run_meta.json` 存在就跳过，所以**中断后直接重跑本脚本即可续跑**，已完成的不重跑、未完成的靠引擎缓存免费重放。
- **失败就停，不重试**：引擎退出码非 0、或回放校验没输出 `replay-check identical=True`，整脚本立刻停下并打印日志尾部。不自动重试——重试会在限流期把配额烧掉。
- **分析失败只告警**：分析层不产生付费调用，可事后重跑，不因此中断网格。
- **`modality` 自动带 `--out`**：不给这个参数它只打印不落盘。
- **每个 tag 之间默认间隔 60 秒**（`--gap`），给提供商的限流窗口留恢复时间。
- 参数：`--workers`（默认 6，**不要改**）、`--only TAG`（可重复）、`--stop-after N`、`--gap`、`--log`。
- 日志：驱动自身写 `runs/out/run_grid_driver.log`，每个 tag 另写 `runs/out/<tag>_driver.log`，都是追加，不覆盖任何文件。

已验证（免费）：语法通过；`--dry-run` 正确列出十六个 tag 且 `main_ref_s2027` 识别为"未完成，将运行"；`--only` 过滤正确；并跑保护在无引擎时返回 0（不会自匹配导致永不启动）；脚本内除日志追加外无任何删除或覆盖操作。

**第一步（必做，需向业主确认后执行）**：清理主网格并单实例重启。也可以直接用上面的驱动脚本，它会自己接着 `main_ref_s2027` 的 1,713 次缓存往下跑。

```powershell
# 先确认进程数；若非 0，全部停掉
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main_ref_s2027*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
```bash
cd "D:/Desktop/ABM paper/flowmirror_v7"
python -m flowmirror.engine.loop runs/main_ref_s2027.json --workers 6 --out runs/out/main_ref_s2027
```
**必须只启动一个实例**，`--workers` 保持 6（实测 6 并发 367 次/小时，10 并发反而掉到 91 并触发账户级限流）。

**其后按此顺序**：

1. 种子 1 跑完 → `--replay-check` 必须 `identical=True`（双进程污染在重启后应已自愈；若仍不通过，停下报告，不要继续）。
2. 跑八个分析模块（`modality` 必须带 `--out`），把数字填进 `paper/DRAFT_S3_S5_20260909.md` 的 §4.2 与 §5。
3. 依次跑 `main_ref_s3031/4049/5057/6071`，**每次只跑一个**。五个齐了跑跨运行 `modality`，得到 df=4 的模态主终点——这是论文的头条结果。
4. 插入 §15.3 的社交图真模型探针（约 600 次调用），按判据决定涌现章节的写法。
5. `main_nosuit_s*` 五次 → `heat_seed_s*` 三次 → `emerge_s*` 三次。
6. 请业主抽检两份样本：`data/creatives/cn/compliance_review_sample.md`、`caption_review_sample.md`。
7. 补做 v2 描述的颜色词稳健性检查（剔除 57 张含颜色词的图所属笔记后重算 TV−TC）。
8. 决定 guba `bull_ratio` 全缺（442 条记录 0 条有值）是补跑立场打标还是写进论文局限——**需业主定**。

**红线复述**：不删 `runs/out/` 下任何文件；不改数据库；不改十六份配置（sha 已冻结在 D23）；key 不入库；代码由 GLM 5.3 写。

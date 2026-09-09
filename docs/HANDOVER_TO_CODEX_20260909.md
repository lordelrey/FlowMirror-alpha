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
| GitHub | 私有仓库 `lordelrey/flowmirror`，分支 `main`，当前 HEAD `adb151c` |
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
for m in modality society_metrics sell_side disposition influence institutions social_proof compliance; do
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

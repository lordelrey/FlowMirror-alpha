# FlowMirror v7 开发交接文档（2026-09-06 13:20）

> 目标读者：接手本项目开发的下一个会话。读完本文件即可直接继续工作，无需回看历史对话。
> 定位：FlowMirror 是一个**多模态社会模拟沙盒**——LLM 投资者 agent 阅读真实基金营销笔记（真实图片 / 图片文字化 / 纯文本三种模态），互动、评论，并按真实净值申赎基金，动作空间里内置 CSRC 式适当性结账。
> 当前阶段目标：跑通端到端流程 + 做出可在浏览器体验的展示页 + 上架 GitHub。**不追求跑满实验网格**。

---

## 1. 仓库与关键路径

| 什么 | 路径 | 作用 |
|---|---|---|
| **开源沙盒（主开发对象）** | `D:\Desktop\ABM paper\flowmirror_v7\` | Python 包 `flowmirror`，GitHub 私有仓库 `lordelrey/flowmirror` |
| 研究仓库 | `D:\Desktop\ABM paper\fundmarket-sim\` | 预注册链、方法学账本、论文车道、原始数据管道。**不上 GitHub** |
| GLM 写码工具 | `D:\Desktop\ABM paper\tools\glm_batch.py` | 所有代码由 GLM 5.3 生成，此脚本批量发卡 |
| GLM 密钥 1 | `D:\Desktop\ABM paper\tools\.glm_key` | 原密钥，今晚已限流 |
| GLM 密钥 2 | `D:\Desktop\ABM paper\tools\.glm_key2` | 业主 2026-09-06 提供，支持 `glm-5.3-flash` 多模态。**tools 目录不在任何 git 仓库内，不会误提交** |
| 任务卡与响应 | `C:\Users\86137\AppData\Local\Temp\claude\D--Desktop-4-15--\be84aa7d-6c8a-469b-8b39-c31806db839e\scratchpad\glm\` | `p_*.md` 是卡，`p_*.md.response.md` 是 GLM 返回 |
| 落盘脚本 | 同上目录的上一级 `scratchpad\land_multi.py` | 把响应里的 `### FILE:` 块写进仓库，自动备份、编译校验。**处理 markdown 嵌套代码栏** |
| 图片真源 | `D:\Desktop\ABM paper\fundmarket-sim\sim\content_pool_v1\images\` | 801 张 768px 预缩放图，与内容池 `image_sha256` 逐一哈希一致。**永不进仓库** |

---

## 2. 沙盒代码结构（`flowmirror_v7/`）

| 路径 | 作用 | 状态 |
|---|---|---|
| `flowmirror/engine/world.py` | 世界层：加载人口/内容池/净值/股吧信号；机构发帖；**不变量注册表与检查**；报告写出 | 不变量有两条误报待修 |
| `flowmirror/engine/loop.py` | 十步交易日循环、决策应用、结账、CLI | 图片解析待加 |
| `flowmirror/agents/prompt.py` | 提示装配（A–G 块）、六通道渲染、决策解析。**三臂卡片渲染在这里** | 完成 |
| `flowmirror/agents/runtime.py` | LLM 调用、内容寻址缓存、预算闸、MockLLM、重试阶梯 | 完成 |
| `flowmirror/agents/null_policy.py` | 规则型空模拟器（Odean 处置 + Sirri–Tufano 追涨），零 API | 完成 |
| `flowmirror/channels/feed.py` | 三源推荐器、热度公式、评论气候、**格内分层区组臂分配** | 完成 |
| `flowmirror/regulator/cn_cxr.py` | C×R 适当性结账；`none.py` 提供反事实 | 完成 |
| `flowmirror/analysis/{common,modality}.py` | 模态分析：agent 级 bootstrap + 种子级 t 区间 + 三分判定 | 完成 |
| `flowmirror/population/sampler.py` | 400 人等比抽样，复现冻结队列哈希 | 完成 |
| `flowmirror/core/`、`platform/`、`society/`、`engine/gm.py`、`agents/perceive/` | 五层立面（门面再导出），见 `docs/LAYERS.md` | 完成 |
| `flowmirror/cli.py` | `run` / `demo` / `validate` / `schemas` / `tree` | 完成 |
| `data_pipeline/cn/caption_frozen.py` | TC 臂冻结描述生成器 | 刚修完，未跑批量 |
| `data_pipeline/cn/make_demo_nav.py` | 合成净值生成器 | 完成 |
| `config/schemas/*.json` | run/scenario/persona/event/fund_meta 五份 JSON Schema | 完成 |
| `runs/*.json` | 运行配置，见下表 | 完成 |
| `web/` | 浏览器展示页 | **尚未生成** |

### 运行配置

| 配置 | 用途 | 是否需要外部数据 |
|---|---|---|
| `runs/demo_two_arm.json` | 离线 demo，40 人 × 5 日，mock 模型 | 否，用自带合成净值 |
| `runs/demo_three_arm.json` | 三臂 + 费率，60 人 | 否 |
| `runs/demo_null.json` | 规则型空模拟器，零 LLM | 否 |
| `runs/demo_three_arm_images.json` | **真图片三臂**，指向本地图片库 | 是，需 `images_root` |
| `runs/mock_10x3*.json` | 研究级配置，用真实净值缓存 | 是，需 `nav_cache.json`（被 gitignore） |
| `runs/demo_live_20x5.json` | 真实模型冒烟（M1） | 是，需 `config/api.yaml` |

---

## 3. 现在能跑什么（已验证）

```bash
cd "D:\Desktop\ABM paper\flowmirror_v7"
pip install -e .
python -m pytest -q                    # 108 通过
flowmirror demo two-arm                # 离线，零 API，零外部数据
flowmirror demo three-arm
flowmirror demo null
python -m flowmirror.analysis.modality runs/out/demo_three-arm
```

全新克隆实测：14 MB、无密钥泄漏、pytest 全绿、README 快速开始逐条可执行。

三个研究级验收运行的基线哈希（重放逐字节一致）：
`mock_10x3` = `b6085086…`，`mock_10x3_3arm` = `b97a9376…`，`mock_10x3_null` = `aa9e476f…`。

---

## 4. 待办队列（按优先级，卡已写好在跑）

| 卡 | 文件 | 做什么 | 状态 |
|---|---|---|---|
| **IMG-A** | `loop.py` | **最高优先**：TV 臂真正附图。按 `image_ids` 在 `images_root` 下解析、校验 sha256、`image_pick: first\|random` 选图、`imp.img_idx` 记录、`run_meta.images` 统计 | 生成中 |
| INV-FIX | `world.py` | 修两条误报的不变量（见 §5） | 排队 |
| VIEW-A | `analysis/export_bundle.py` + `cli.py` | 导出 demo 规模的展示包（bundle/posts/agents/events 四个 JSON） | 排队 |
| VIEW-B | `web/` + README + Pages 工作流 | 零安装浏览器回放页 | 排队 |
| ENV | `world.py` | 按目标盈亏分布抽成本基准 + 环境退化不变量（见 §6） | 排队 |

配置侧（IMG-B）已落盘：`images_root`、`image_pick`、`imp.img_idx` 三个键已进 schema 与默认值，`runs/demo_three_arm_images.json` 已存在。

**队列脚本**：`scratchpad/glm/run_queue2.sh`，串行发卡、失败自动退避重试、已有响应则跳过。可直接重跑。

---

## 5. 已知缺陷与已修清单

### 今晚修好的（已提交）

| 缺陷 | 后果 |
|---|---|
| `_make_llm` 重复传 `model` | 任何非 mock 运行秒崩，活模型路径零测试覆盖 |
| `call_glm` 失败分支哈希未赋值变量 | 一次瞬时网络错误杀掉整场，五次重试阶梯是死代码 |
| 新克隆无净值 | 一个 demo 都跑不了；已加合成净值随仓库发布 |
| 图片臂独立抛硬币 | 35% 的 run_tag 违反预注册容差；已改格内分层区组随机化，五种子偏差归零 |
| 入口层停在脚手架 | run.sh 说引擎没接、CLI 无 run、tree 打印虚构模块表、密钥模板键名不符 |
| `post.ig` 写原始意图 | 与 schema 和 engine_v5 定义不符 |
| 赎回走申购路径 | 发无效 click 行、反事实给出拒签 |
| schema 要求 live 配置带 mock 字段 | live 配置被拒 |
| `--dump-prompt` 被 schema 挡住 | 论文附录拿不到提示原文 |
| **不变量返回元组被当字典接** | `bool(非空元组)` 恒真，**所有不变量结果一直被丢弃**，`invariants=PASS` 全失败也照打 |

### 待修（卡已在队列）

1. **TV 臂不附图**。遮蔽池剥掉了 `image_path` 字段，`_feed_card` 读的正是它，所以永远是 None。实时运行 80 次调用附图数为 0，**多模态主线空转**。
2. **两条不变量误报**（接线修好后才暴露，是检查本身写错，不是引擎有问题）：
   - `a_lagged_signals_only` 把股吧**周键**（2025-W41）和**日期**做相等比较，永远不等；`day_keys` 存的是列表却被当整数。
   - `b_nonholder_never_redeems` 只从 `act` 行累积持仓，而**开局持仓在 `init_investors` 里发放、不产生 `act` 行**，赎回初始持仓被误判 22 次。
3. **开局无人亏损**（真实净值下亏损持仓仅 4.9%，中位 +39.1%；合成净值下 48.9%、+0.5%）。修法：按目标盈亏分布抽成本基准。
4. **TC 描述从未生成**。生成器刚修好（此前默认模型是推理型而 96 token 全被思考吃掉），801 张图待跑一次批量。
5. **`srs_analysis.py` 右删失当零结局**。第 55 天被拦的事件只有 5 天随访却计为"没再买"，偏差与熟悉度相关，污染主指标。

---

## 6. M1 真实模型冒烟结果（重要事实）

配置 `runs/demo_live_20x5.json`，20 人 × 4 日完成后按预注册止损规则主动停机。

| 指标 | 实测 |
|---|---|
| 解析成功率 | 77/77 = 100%（门槛 ≥95%） |
| 三次失败 | 全是 HTTP 限流，非模型行为 |
| 吞吐 | **220 次调用/小时 @ 6 并发**（计划止损线是 2,400，低一个数量级） |
| 平均 token | 4,547 次/调用 |
| 行为面 | 51 笔申购、73 条评论、52 次结账（match 32 / 签署确认书 19 / 拒签 1） |

**三个同质性诊断**（是环境单边的指纹，不是模型或人口问题）：mood 恒为 4（零方差）；好感增量从不为负；73 条评论零条看空。行为计数（点赞 0–3、评论 0–2）是有分散的——只有带正负号的评价维度塌了。根因是开局无人亏损（见 §5 第 3 条）。

---

## 7. 相关文档

| 文档 | 位置 | 内容 |
|---|---|---|
| 方法学账本 | `fundmarket-sim/METHODS_LEDGER.md` R31、R32 | 逐轮改动与决策的权威时间线 |
| 入口层交接 | `fundmarket-sim/paper/HANDOVER_ENTRY_LAYER_2026-09-06.md` | 七个缺陷、体验实测、发布前清单 |
| 实施方案对照 | `fundmarket-sim/paper/SPEC_GAP_AND_BUILD_PLAN_2026-09-06.md` | 业主七板块方案 × 代码现状，分阶段落地计划，四个待拍板冲突 |
| 沙盒现状（给论文会话） | `flowmirror_v7/docs/SANDBOX_STATUS_2026-09-05.md` | 可写/不可写边界 |
| 架构与分层 | `flowmirror_v7/docs/ARCHITECTURE_v7.1.md`、`docs/LAYERS.md` | 五层递进结构 |
| 使用手册 | `flowmirror_v7/docs/RUNBOOK.md` | 171 行，含实跑、提示导出、输出解读、排错表 |
| 预注册草案 | `fundmarket-sim/specs/PREREG_v1.5_DRAFT.md` | **未冻结**；臂平衡容差那条须订正（见下） |

---

## 8. 交给业主拍板的事项

1. **内容池未遮蔽全文**。`content_pool_v1_masked.jsonl` 同时保留 200 条营销笔记的 `caption`/`ocr_text` 原文与 `note_id`，公开即逐字转载并可反查原帖。建议只发遮蔽版。
2. **窗口后素材**。`pre_window` 字段存在但无任何代码读取，窗口后素材照进主实验。建议只用窗口前素材，以保住样本外对齐这条贡献。
3. **TC 是否分 short/full 两档**。建议只在参考格加，主网格保持三臂。
4. **预注册容差订正**（非选择题）：格内"与整体差 ≤0.05"对 n=2 的格数学上不可满足，须改为 `|份额 − 1/k| ≤ 1/(2·n_cell)`。v1.5 未冻结，正好一并改。
5. **吞吐**。220 次/小时下，预注册的约 25 万次调用需约 1,100 小时。M2 前须重新评估并发上限、文本模型替换或设计缩减。

---

## 9. 工作约定（务必遵守）

- **所有代码由 GLM 5.3 写**。主模型只做设计、审查、验收、决策。发卡用 `glm_batch.py`，落盘用 `land_multi.py`。
- **单卡输出 ≤ 8 万字符**，超过必超时。大改动按文件拆卡。
- **并行卡必须写死接口契约**。本轮发生过三次互猜 API（`dump_prompt` 未进 schema、`assign_agent_arms` 签名猜错、captioner 图片路径猜错）。先用 `inspect` 导出真实签名再发卡。
- **同一文件永不并行**。且注意卡是从**发卡当时**的文件生成的：若 A 卡改了某文件，之前基于旧版生成的 B 卡落盘会覆盖 A 的改动，必须重新生成 B。
- **数据库红线**：`xhs_data.db` / `xhs_images.db` 只读，只能用 `mode=ro`。
- **图片永不进仓库**，密钥永不入库（`config/api.yaml` 已 gitignore，git 全历史零命中）。
- 后台调研任务用 Haiku 或 GLM，不用主模型。

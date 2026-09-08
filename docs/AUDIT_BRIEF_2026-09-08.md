# 审计交接单 · 2026-09-08 落地的机制与分析层

给独立审计上下文（Opus 5，不改代码，只找问题）。范围：今天 `main` 上从 `a7dffee` 到最新提交的全部改动。每一节列出：改了什么、为什么、验收证据、**建议攻击点**。

## 0. 不变量（全局）

- **关闭态逐字节**：`social_graph.enabled`、`institutions.adaptive`、`heat_seed.enabled`、`regulation.short_term_redemption.enabled` 四个开关默认关；关闭时事件日志与提示必须与开关不存在时逐字节相同。证据：每张卡的 `cmp` 验收（acc_e1 → acc_f1 → acc_f2_off → acc_a6 → acc_c1_off 链）。**攻击点**：找任何在开关关闭时仍写入的键、仍消耗的随机数、仍改变的排序。
- **一次性哈希变更只有两处**：X1（卡片显示真实点赞数、`dec.p_like/p_save`、`post.note`）与 F1（候选笔记抽样改派生流）。其余改动不得改变关闭态哈希。**攻击点**：`git log -p` 里任何触碰 `rng_platform` 消耗次数、提示字节、排序键的地方。
- **随机流隔离**：agent 流挂 `run_tag`，平台流挂 `seed`，新机制全部用 `rng_for(run_tag, <name>, ...)` 派生流（`note_pick`、`heat_seed`）。**攻击点**：任何新代码直接调用 `random.` 或复用 `inv.rng` / `rng_platform`。

## 1. X1 + A1 + A2（真模型运行基础）

- X1 `loop.py/world.py`：`likes_prev` 冻结、卡片 `likes` 为整数、`dec.p_like/p_save`、`post.note`、`run_meta.openings`。测试 `tests/unit/test_x1_log_fields.py`。
- A1 `runtime.py`：429 → `rate_limited`（传输类），`Retry-After` 优先、无则 30·k（拟改 10·k，尚未改）、`AdaptiveGate` 减半/恢复、`--retry-transport-holes`。测试 `test_rate_limit_gate.py`。**攻击点**：闸门在多线程下的竞态；`elapsed_s` 记录是否覆盖所有分支；传输洞重试是否会把"模型失败"误当传输洞。
- A2 `runtime.py`：env 覆盖 `FLOWMIRROR_API_KEY/ENDPOINT/VISION_MODEL/TEXT_MODEL`；`run_meta.provider` 只写主机名。**攻击点**：任何路径把 key 写进日志/报告。
- A5 `loop.py`：`llm.decision_failure_min_calls`（默认 100）。依据：10 人替身 t=3 时 1/40=2.5% 误停机，600 次真实率 0.33%。**攻击点**：100 是否会让小规模真模型运行（<100 人）失去保护；`t >= 3` 与样本下限的组合语义。
- A6 `prompt.py`：反诱导检查只扫 D/E/关注块；B/C 块（agent 自己的反思、记忆）不扫。`head` 字节不变。**攻击点**：是否还有别的路径把 agent 文本送进 `check_anti_priming`（反思提示 `build_reflection_messages`？）；D/E 块里是否有非我们撰写的文本混入。

## 2. B 卖出侧

- B1 批次 `inv.lots`、FIFO、`act.pnl/hold_days/st_units/st_fee`；B2 `regulator/cn_redeem.py`、`co.st_fee_cf` 始终记录、`disclose` 臂改提示。测试 `test_lots_and_short_term_fee.py`（11 项）。**攻击点**：FIFO 与 `inv.hold` 浮点残差；定投批次日期；财富守恒在费用开启时的等式；`disclose` 臂 `prompt_sha` 只在开启时动。
- B3a `analysis/sell_side.py`、B3b `analysis/disposition.py`（Odean PGR/PLR；阳性对照 acc_b2_off DE=+0.056）。**攻击点**：加权平均成本与引擎 FIFO 成本的口径差异（分析用加权平均、引擎用 FIFO——文档要说清）；净值缓存"最近更早日"规则在停牌/缺失日的行为。

## 3. E 大 V 层

- E1 `prompt.py`：句柄+粉丝数渲染、`render_following`、`follow_users` 解析与 `unknown_handle` 违规；E2a `loop.py`：`handle_of`、关注边、`followers_prev` 冻结、关注者动态（t−1）；E2b `feed.py` 排序 (−粉丝, −熟悉, id)；`dec.p_follow_users` 只在开启时写。测试 `test_social_graph.py`（8 项）。**攻击点**：句柄碰撞（5 hex ≈ 100 万空间，400 人碰撞概率约 8%！——请核算并给出建议：6 位？）；关注者动态里是否泄露 t 日信息；`following_recent` 最多 3 条的取舍是否确定性；替身模型永不关注 → 开启态从未在集成层被真正行使。
- E4a/E4b `analysis/influence.py`：粉丝 Gini、同质性零模型（保出度重连 200 次）、跟单 3 日窗、随机配对零模型。**攻击点**：零模型的重连是否允许自环/重边；`copy_rate` 分母定义。

## 4. F 机构适应

- F1 `world.py` 派生流抽笔记 + `intent_weights`；F2 `loop.py` `_inst_update`（纯函数）、响应计数（结账 +1、成交 +1，按原始意图）、`inst` 事件；F3 `analysis/institutions.py`；F4 测试（7 项）。**攻击点**：`floor` 后归一化使权重可低于 floor（已知、已记录，确认文档一致）；周期边界 `t % period == 0` 与"上一周期"的对齐；`inst_conv` 在周期内跨天累积但 `day` 对象每天重建——确认是同一 dict 引用。

## 5. C 种子热度

- C1 `world.py` 发布时分组（派生流 `rng_for(run_tag,"heat_seed",pid)`）、`post.heat_seed`；`loop.py` `seed_bonus` 只在首次展示日加到卡片 `likes`；C2 `analysis/social_proof.py`（首日差、级联乘数、路径判定、帖级自助）；C3 测试（5 项）。**攻击点**：`birth.get(pid) == t` 判"首日"——同一帖若在候选池里第二天才首次被某人看到，它看到的是真实值而非 +k（设计如此：种子按发布日不按个人首见日；请确认文档措辞一致）；`focus_fund` 与延后入场基金（`active_from`）的交互；乘数分母 k=0。

## 6. D 合规

- D1 `config/compliance_rules_cn_v1.yaml`（R01–R07）、`data_pipeline/cn/compliance_label.py`（两遍；dry-run 108/200 条进入候选）；D2 `analysis/compliance.py`。**攻击点**：法条条款号（业主抽检时核对）；R05 的风险短语表是否漏掉常见变体（"投资有风险，入市需谨慎"等）；R07 正则对活动金额（红包 50 元）的误报由 LLM 兜底——检查提示是否明确排除。

## 7. 数据与配置

- `data/creatives/cn/content_pool_v1_captioned.jsonl`（800/801 描述；550 条被 40 字硬截断；D22 待业主定：接受 / CAP2 重跑 / TC=OCR）。MANIFEST 已登记（LF 归一化后）。
- 16 份配置草稿（10 主网格 + 3 种子热度 + 3 涌现）在操作方暂存目录，全部过 schema；`emerge` 150×40 替身跑通；试跑结束后入库并在 PREREG D23 列 sha。

## 8. 今天的教训（供审计核对是否已在代码里体现）

1. 反诱导检查曾杀掉整次真模型运行（agent 自己的话）——A6。
2. 停机比率在 40 次样本上误判——A5。
3. 单模态组运行被 `h_arm_balance` 判死——A7。
4. `--self-test | tail` 吞退出码——验收链已改为单独取退出码；引擎自检两条陈旧期望已修。

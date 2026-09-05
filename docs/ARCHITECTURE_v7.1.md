# FlowMirror v7.1 — 递进式多模态基金营销数字孪生：架构重构方案（2026-09-05 晚，草案）

> 本文件取代 `PROJECT_ARCHITECTURE_v7.md` 的 §1/§4/§5 层次描述（其余章节——数据分层、市场模块、场景插件、工程规范、路线——继续有效）。依据两份调研：`research/twinmarket_repo_and_paper_anatomy.md`、`research/agent_cognitive_architectures_survey.md`。
> 业主 09-05 晚三条指令：① 框架优先，数据与机构/基金数量是配置变量（demo 基准 8 机构 × 各 50 条、400 投资者）；② 沙盒要按"小部件 → 子 agent → 环境 → 社会"递进组织，并检讨 TwinMarket 的 BDI 是否是最好的基线；③ **多模态（图片 vs 纯文本）对比是主打 insight**。

---

## 0. 三个结论先说

1. **BDI 不作为认知基线，只保留它可审计的那一部分。** TwinMarket 代码里并没有真正的 Desire/Intention/plan 层（调研 A.3：`_update_belief → _make_final_decision → _polish_decision`，BDI 是论文叙事）。它真正可取的是**显式、数值化、引擎维护的信念向量**（可审计、可消融）。我们采用 **CoALA 的记忆分层 + Concordia 的 Game Master 环境分离 + OASIS/PolicySim 的推荐器中介社会层**，把"BDI 信念向量"降为其中一个可审计状态组件。
2. **递进关系用五层表达**：Percept/Memory/Action 三种原子 → 投资者内部的六个组件（感知 × 6 通道、记忆、审议、执行）→ 三类 agent（投资者、机构、监管即环境规则）→ 环境 Game Master（时钟、市场、结账、推荐、气候聚合、事件账本）→ 社会（人口、机构群、中介互动图、宏观观测量、外生世界）。每层只通过上一层暴露的接口对话。
3. **多模态是"感知组件"的属性，不是 agent 的属性。** 每个 Percept 带 `modality ∈ {text, image, image_as_text}`；模态臂 T / TC / TV 决定感知组件的渲染方式；随机化可在 agent 级或整场级；效应在微观 / 中观 / 宏观三层观测。这使"多模态模拟社会"成为一个可控实验，而不是"给模型看图"。

---

## 1. 为什么不是 BDI，而是"CoALA 记忆 + GM 环境 + 中介社会"

| 候选 | 给我们的 | 不给我们的 | 取舍 |
|---|---|---|---|
| BDI（TwinMarket） | 显式信念向量，1–5 级，可审计、可回归 | 无真实 Desire/Intention 层；记忆无持久性（PIMMUR "Memory"）；社会层靠 prompt 注入 | **只取信念向量**作为可审计状态 |
| Generative Agents（Park 2023/2024） | 记忆流 + 检索 + 反思 + 计划 | 反思昂贵；无社会结构；25→1k 人 | **取反思机制**（每 5 日一次、缓存） |
| CoALA（Sumers 2023） | 工作 / 情景 / 语义 / 程序四类记忆 + 内部/外部动作的决策周期 | 元框架，无环境与社会规定 | **取记忆分层与决策周期命名** |
| Concordia（DeepMind 2023） | 组件化实体；**Game Master** 把意图翻译为环境效果并裁决合法性 | GM 依赖 LLM 判断合理性（我们用确定性规则替代） | **取 GM 结构**：环境是裁决者，agent 只表达意图 |
| OASIS / PolicySim | 推荐器中介的社会层、结构化立场、百万级异步 | 无市场、无监管 | **取社会层接口**：agent 之间不直接对话，经排序/聚合后可见 |
| AgentSociety | 档案/记忆/规划/行动 + 多环境模拟器 | 城市场景 | 佐证四组件分解 |
| Homo Silicus / EconAgent | 最小询问范式；宏观由微观聚合 | 无记忆、无社会 | **作为基线臂**（记忆关 = Homo Silicus 式一次性询问） |

**结论**：投资者 agent 的认知周期命名为 **Perceive → Retrieve → Deliberate → Act → Learn**（CoALA 术语），其中 Perceive 由六个模态感知组件产生 Percept，Retrieve 从四类记忆装配工作记忆，Deliberate 是唯一的 LLM 调用（结构化 JSON），Act 把意图交给 Game Master 裁决，Learn 由引擎写入情景记忆并周期性触发反思更新语义记忆（含 BDI 式信念向量）。**审计性**来自：信念向量数值化、每个 Percept 带来源哈希、每次 Deliberate 的提示哈希与原始响应哈希、GM 裁决结果全部入账本。

---

## 2. 五层递进（每层的对象、接口、所有权）

```
L0 原子      Percept{channel, modality, text, image_ref?, sha, t_source≤t−1}   MemoryItem{kind, t, text, sha}   Action{type, args}   Event{ev, t, d, …}
   │
L1 组件      感知组件×6（Feed / Experience / Trend / Social / News / Direct，每个 = f(WorldView_{t−1}, agent_state) → [Percept]）
（子 agent）  记忆组件（working / episodic / semantic / procedural）    审议组件（LLM 调用 + 解析 + 缓存）    执行组件（JSON → [Action]）
   │
L2 agent     InvestorAgent = Identity + 感知×6 + Memory + Deliberation + Actuator      InstitutionAgent = Profile + Policy(rule|llm) + Pool
   │          （监管不是 agent，是 GM 的规则插件）
L3 环境 GM   Clock · Market(NAV, 申赎结算, 定投, 财富守恒) · Checkout(regulator plugin: cn_cxr | us_regbi | none, 反事实 oc_cf)
             · Platform(三源排序 / 随机排序, 热度, 气候聚合, 图片臂分配) · Ledger(事件账本, 不变量) · Exogenous(股吧, 指数, 新闻)
   │
L4 社会      Population(400, 36 格, 权重) · Institutions(8) · 中介互动图(曝光—互动—评论—关注) · 宏观观测量(热度分布, 气候, 族级资金流, LSV, SRS)
   │
L5 实验      Scenario(国家/平台/监管/数据) × RunConfig(因子: suitability, strategy_mix, ranking, modality_arm, channels, memory, social) × Seeds
             → 缓存 / 零调用重放 / 不变量报告 / 分析脚本
```

### 2.1 接口契约（写进 `flowmirror/*/base.py`，每层一个 Protocol）
- `Perceiver.perceive(view: WorldViewT1, agent: AgentState, arm: Modality) -> list[Percept]` —— 只读 t−1 快照；输出带 `sha`、`modality`。
- `Memory.retrieve(query_ctx) -> WorkingMemory`；`Memory.write(item: MemoryItem)`；`Memory.reflect(llm) -> SemanticUpdate`（每 k 日）。
- `Deliberator.decide(working_memory, percepts, instr) -> Decision(JSON) + Provenance(prompt_sha, raw_sha, cache_hit, attempts)`。
- `Actuator.propose(decision) -> list[Action]`（只做类型归一，不做可行性判断）。
- `GameMaster.adjudicate(agent, actions) -> list[Event]`（可行性、结账、结算、互动登记；**唯一写账本的地方**）。
- `Platform.rank(agent_state, candidates, heat_t1, climate_t1, cfg, rng, mode) -> slots`；`Platform.aggregate(comments_t) -> climate_{t+1}`。
- `InstitutionPolicy.publish(day, pool, mix, rng) -> list[Post]`；`Tactics.apply(post|agent) -> modifiers + Event(tac)`。
- `Society.observe(ledger_t) -> Aggregates`（热度分布、气候、族级流、LSV、SRS 事件集）。
- `Experiment.run(scenario, run_cfg) -> RunArtifacts`（event_log, run_meta, cache, invariants, snapshots）。

### 2.2 与现有代码的映射（重构路径：先立面、后搬家）
| 现文件 | 属于哪层 | 处理 |
|---|---|---|
| `channels/feed.py`（已落盘，36/36） | L3 Platform.rank / aggregate | 加 `flowmirror/platform/__init__.py` 立面导出；文件不动 |
| `agents/prompt.py`（已落盘，56/56） | L1 六个感知组件的渲染器 + 审议指令 + 解析 | 拆出 `agents/perceive/{feed,experience,trend,social,news,direct}.py` 各自实现 `Perceiver`，`prompt.py` 只做装配；**模态臂在此生效** |
| `agents/runtime.py`（已落盘，Mock 修订中） | L1 审议组件（LLM 客户端、缓存、预算闸、并行） | 重命名立面 `agents/deliberate.py` 指向它 |
| `engine/world.py`（已落盘，12/12） | L3 GM（Clock/Market/Ledger/Invariants）+ L4 Population/Institutions 加载 | 拆 `engine/gm.py`（裁决）与 `society/population.py`、`institutions/policy.py` 立面 |
| `engine/loop.py`（生成中） | L5 实验编排 + 日循环 | 保留为编排器；`apply_decision` 迁入 GM |
| `regulator/cn_cxr.py`、`none.py` | L3 Checkout 插件 | 不动 |
| `population/sampler.py` | L4 | 不动 |

搬家分两步：**P3a** 先跑通现有四模块的 M0（功能优先）；**P3b** 按上表加立面与 Protocol，再逐个抽出感知组件（首先抽 Feed/Social，因为模态臂在这里）。

---

## 3. 投资者 agent 的内部（L1/L2 细节）

### 3.1 Identity（人设，三层：格 → 个体 → 卡）
不变：36 格（年龄 × 资产 × 潜在风险）、个体字段、第一人称访谈式中文卡；v7.1 加**分析风格段**（行为核映射）与引擎生成的**经历段**。

### 3.2 记忆（CoALA 四类，全部引擎持有、agent 只读）
| 类 | 内容 | 谁写 | 进提示的位置 |
|---|---|---|---|
| working | 今日装配的 Percept 集合 + 当日账户 | 引擎每日 | D/E/F 块 |
| episodic | 5 日滚动日志（每日一行 ≤80 字：看了几条 / 交易 / 评论 / 好感 / 心情 / 理由；被拦记为"申购被拦下：需签确认书，你放弃了"） | 引擎 | C 块 |
| semantic | **信念向量**（BDI 遗产，可审计）：`market_view`、`risk_mood`（agent 反思改写）；`trust[org]`、`attention[fund]`、`ref_point[fund]`、`gain_loss[fund]`、`experience[fund]`、`trend_read[fund]`（引擎数值维护）+ 反思产出的 1–3 条信念句 | 引擎 / 反思 | B 块 |
| procedural | 决策 JSON schema、可行性规则（由 GM 强制，不让模型自查） | 固定 | G 块 |

### 3.3 感知组件（六通道，每个组件 = 一个 `Perceiver`，配置可开关）
Feed（6 张卡：文案 / 图或图文 / 基金档案 / 昨日热评 / 气候）· Experience（持仓浮盈亏、上次交易结果、实现盈亏、定投扣款）· Trend（1 周/1 月/3 月收益、最大回撤、同类分位——引擎算数、渲染成一句话）· Social（t−1 前 3 热评 + 气候标签，按 strat_weight 加权）· News（指数行、股吧周讨论量与多空比）· Direct（关注机构的推送 / 费率优惠 / 新发提醒；战术库驱动）。
每个 Percept 的文本进 `dec.prompt_sha`，并单独记 `channel_sha`，供归因分析（哪条通道的存在改变了决策）。

### 3.4 审议与执行
一次 LLM 调用输出 `{reads, engage, comments[{post_id,stance,text≤40}], trade{action,fund,amount_pct,sign_mismatch_confirm}, org_affinity_delta, mood, reason, info_request, attention_fund}`；四步独立指令（先交易、再互动、再留言、再感受）；反诱导词表断言；解析失败一次带 schema 重试；可行性由 GM 裁决。

---

## 4. 多模态：从"附录消融"升为主线实验（L1 感知 × L5 因子 × L4 观测）

### 4.1 模态臂（三档，随机化单位两种）
| 臂 | Feed 感知组件如何渲染图片 | 分离的效应 |
|---|---|---|
| **T** | 写"配图不展示"，无任何图片信息 | 基线 |
| **TC**（新增） | 不给像素；给图片的 **OCR 文字 + 一句中性机器描述**（"图为收益曲线截图，标注近一年 +12.3%"），描述由冻结的 VLM 提示离线生成并存入内容池，与 TV 看到的图一一对应 | TC − T = **图片携带的信息**效应 |
| **TV** | 真实图片像素（768px data-URI） | TV − TC = **视觉呈现本身**的效应（显著性、情绪、可信感） |

随机化单位：**agent 级**（整场固定臂；个体效应，SUTVA 干净；默认）与**整场级**（全社会同一臂；社会效应——气候、热度集中度、族级资金流是否随模态改变；预注册为三组 × 种子的因子）。混合臂（人群一半 TV 一半 T）作为探索性溢出分析。

### 4.2 观测层次（每层至少一个预注册量）
| 层 | 量 | 检验 |
|---|---|---|
| 微观（agent） | like / save / follow / 点击 / 申购转化率、评论立场分布、`org_affinity_delta`、`attention_fund` 自报 | agent 级两样本差（TV−TC、TC−T），种子级 t 区间 |
| 中观（帖 / 机构） | 帖子热度分布（基尼）、气候标签分布、机构熟悉度 level 1/2 到达天数、评论内容与图片内容的语义重合度 | 整场臂间对比 |
| 宏观（社会） | 族级资金流集中度（Herfindahl）、LSV 羊群、SRS 与 Δ1 是否随模态变化（模态 × 适当性交互，探索性） | 整场臂间对比 |

### 4.3 机制与异质性（让 insight 有解释力）
- **素材视觉特征**（来自现有五维标签 + OCR + 新增视觉标签：有无收益数字 / 图表 / 人物 / 品牌色 / 封面 vs 内图）× 模态臂：图片效应集中在哪类素材？
- **投资者格**（年龄、资产、风险）× 模态臂：谁更受图影响？
- **信息 vs 呈现分解**：TC−T 与 TV−TC 的比例，是多模态模拟社会领域可直接引用的数字。
- **感知保真审计**：TV 臂下让模型（独立调用）描述所见图片，与 OCR/人工标签比对 → "模型确实看见了图"的证据；TC 臂下审计描述文本不含视觉线索以外的信息（模态泄露）。

### 4.4 真实数据标尺
小红书真实笔记的互动量（点赞 / 收藏）与图片数量、图片类型的关系（948 条 / 2,714 图；扩容后更多）作为图片效应方向的外部参照；同一素材在 TV 与 T 下的模拟互动差 vs 真实图文 vs 纯文/封面帖的互动差。

### 4.5 与预注册的关系（必须诚实）
- 目前预注册把图片 ATE 放在附录 C 并预期"有界零"（引导测试 TV ≈ T）。升为主线需 **PREREG v1.5 在任何生产运行前**声明：三档臂、两种随机化单位、三层观测量、SESOI、失败措辞（"在本模型下图片呈现不改变交易决策，但改变互动/气候"本身也是发现）。
- 不得为了正结果调设计；TC 臂的机器描述提示先冻结。

---

## 5. 机构与监管在递进中的位置

- **InstitutionAgent（L2）**：Profile（内容池、实测意图组合、风格）+ Policy（rule：意图组合 / 节奏 / 时机 / 战术；llm：读上周互动汇总选战术）+ Pool。机构数是配置。8 家 × 50 条是 demo 基准配置。
- **监管是 GM 的规则插件（L3）**，不是 agent：`cn_cxr`（C×R 匹配 / 确认书 / 硬拦 / QDII）、`us_regbi`（披露型）、`none`。suitability on/off 因子 = 换插件，反事实 `oc_cf` 恒记录。
- **平台是环境（L3）**：推荐、热度、气候聚合、图片臂分配；ranking 因子 = 换排序模式。

---

## 6. 社会层的观测与验证（L4）

风格化事实（只比符号）：流量-业绩凸性、处置效应、注意力买入、追涨、LSV 羊群、排名效应；留出：族级资金流 vs 真实季度面板 + 领先安慰剂；异质性：η²、跨种子 ρ、坍缩警报；PIMMUR 六项逐条对应（档案异质：36 格 + 权重；互动：中介互动图；记忆：四类记忆持久；最小控制：反诱导词表；不知情：探针；经验接地：留出与真实互动量）。

---

## 7. 实施顺序（不碰数据；配置以 8 × 50、400 为 demo 基准）

| 步 | 内容 | 验收 |
|---|---|---|
| P3a（进行中） | 四模块落盘：world 12/12 ✅、prompt 56/56 ✅、runtime（Mock 重写中）、loop（生成中）→ M0 干跑 + 重放 | `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 3 --agents 10 --replay-check` 退出 0 |
| P3b | 五层立面与 Protocol（`agents/perceive/*`、`engine/gm.py`、`society/`、`platform/`）；`prompt.py` 拆为六个 Perceiver；模态臂 T/TC/TV 进感知组件与配置 schema（`modality_arm`, `modality_level ∈ {agent, run}`） | 现有测试全绿 + 新增 Perceiver 单测；M0 输出逐字节不变（纯重构） |
| P3c | TC 臂的离线图片描述生成器（冻结提示，写入内容池 `image_caption_frozen`）；感知保真审计脚本 | 有内容池即可跑；不依赖新数据 |
| P4 | 分析层：`analysis/modality.py`（微/中/宏三层）、`attribution.py`（通道归因）、`srs.py` 不改 | 合成事件日志上植入效应能被恢复 |
| P5 | 8 × 50、400 的 demo 配置与场景文件（数据由其他会话填入 `data/`） | `flowmirror validate` 通过；M0 在新数据上重跑 |
| PREREG v1.5 | 三档模态臂为主终点之一、两种随机化单位、三层观测、8 机构因子二规则 | 运行前冻结 |

---

## 8. 一句话给论文会话
FlowMirror 是一个**递进式多模态基金营销数字孪生**：CoALA 式四类记忆 + Concordia 式 Game Master 环境 + 推荐器中介的社会层，把 TwinMarket 的 BDI 信念向量保留为可审计状态；**模态是感知组件的属性**，三档臂（T / TC / TV）在 agent 级与整场级随机化，在微观、中观、宏观三层观测图片对模拟社会的影响；监管是环境规则插件，机构是策略 agent；机构数、基金数、人数全部是配置。

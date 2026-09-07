# FlowMirror 沙盒内部规范 v1（草案，2026-09-05 晚）——每个因素一张卡：定义 · 量化 · 更新 · 出处 · 配置 · 日志

> 用途：与业主逐卡对细节；对齐后 → PREREG v1.5 冻结、论文 §3/§4 措辞、引擎实现的验收标准。
> 标记：`[已实现]` 代码已有；`[设计]` 已定未实现；`[待对齐]` 需业主拍板；`[REF ?]` 出处待三份调研（`research/state_quantification_theories.md`、`sandbox_projects_survey.md`、`multimodal_persuasion_evidence.md`）回填。
> 原则：**引擎算数、agent 读文**。任何写进提示的量都先由引擎按本规范算成数值，再渲染成一句中文，不给权重、不给"该怎么做"。

---

## 0. 卡片模板

| 字段 | 含义 |
|---|---|
| 定义 | 这个量表示什么、属于哪一层（L0 原子 / L1 组件 / L2 agent / L3 环境 / L4 社会） |
| 量化 | 取值域与计算式 |
| 更新 | 何时、由谁更新（引擎 / agent 反思 / 外生数据） |
| 出处 | 定义与阈值来自哪篇文献或哪个已发表模拟器 |
| 配置 | `runs/*.json` / `scenarios/*.yaml` 里可调的旋钮与默认值 |
| 日志 | 进哪个事件、哪个字段 |
| 验证 | 用什么真实数据或风格化事实校验 |

---

## A. 投资者层（L1/L2）

### A1. 人设格与个体 `[已实现]`
- 定义：36 格 = 年龄{<30, 30–45, 45–60, >60} × 资产{低, 中, 高} × 潜在风险{fragile, typical, tolerant}；个体从格内合成人口抽出。
- 量化：格权重 = AMAC 84,807 人问卷的边际乘积（联合为合成，标注 T1）；`reported_C ∈ {C2,C3,C4}` 由 `risk_latent` 决定性映射（fragile→C2, typical→C3, tolerant→C4）；`wealth_wan`、`entry_day`、`traits{holding_duration, dca, n_funds, invest_share}`。
- 更新：不变（静态身份）。
- 出处：AMAC《全国公募基金投资者状况调查报告》边际；CSRC 2017 适当性办法 C1–C5 `[REF ?]`。
- 配置：`agents_file`、`n_agents`；抽样 `select_agents --n --seed`。
- 日志：`run_meta.agents`。验证：加权后 C2 份额 = 0.0807。
- `[待对齐]` 8 机构 demo 是否仍用 400 人等比（当前冻结）；是否引入 C1/C5（人口中不存在，若引入需合成 → 破坏问卷锚定，建议不引入）。

### A2. 风险态度与适当性等级 `[已实现]`
- 定义：`reported_C` 是平台可见的测评等级；`risk_latent` 是行为倾向；CPT 参数（λ 损失厌恶、α 曲率、w± 概率权重）挂在格上。
- 量化：C×R 匹配表：`cr ≥ rr → match`；`cr < rr → 需签确认书（signed/declined）`；`cr==1 且 rr>1 → hard_block`；QDII 限购 → `purchase_blocked`。CPT：λ∈[1.5,3.0]（格内按 KT1992 λ=2.25 为中心）。
- 更新：不变。出处：CSRC 2017；Tversky–Kahneman 1992 `[REF ?]`。
- 配置：`suitability` 因子（on/off）；`regulator.plugin`。日志：`co.oc`, `co.oc_cf`。
- 验证：无 C1 → `hard_block` 永不出现（已知事实，论文披露）。

### A3. 信念状态（8 维语义记忆）`[设计，部分已实现]`
| 维 | 取值 | 谁更新 | 更新式 | 出处 |
|---|---|---|---|---|
| `market_view` 后市观 | 1–5 整数 | agent 每 5 日反思 | 直接改写（LLM 输出）| TwinMarket 5 维信念向量 1–5 `[REF ?]`；Greenwood–Shleifer 预期调查五档 `[REF ?]` |
| `risk_mood` 亏损承受心情 | 1–5 | agent 反思 | 同上 | Dohmen 等风险态度量表映射 `[REF ?]` |
| `trust[org]` 机构信任/好感 | 连续 ≥0 | 引擎 | adstock：`trust_t = λ_a·trust_{t−1} + Σ org_affinity_delta_t`，λ_a=0.9 | Nerlove–Arrow 商誉 / Broadbent adstock `[REF ?]` |
| `attention[fund]` 基金注意力 | 连续 | 引擎 | `att_t = λ·att_{t−1} + 曝光数_t + β·股吧异常讨论 z_t` | Barber–Odean 2008；adstock `[REF ?]` |
| `ref_point[fund]` 参考点 | 成本净值 | 引擎 | 加权平均成本 | Shefrin–Statman 1985；Odean 1998 `[REF ?]` |
| `gain_loss[fund]` 浮盈亏 | % | 引擎每日 | `nav_t/ref_point − 1` | 同上 |
| `experience[fund]` 经历 | {实现盈亏, 交易次数, 后悔计数} | 引擎 | 事件驱动累加 | Malmendier–Nagel 经验效应 `[REF ?]` |
| `trend_read[fund]` 走势印象 | {up, flat, down, drawdown} | 引擎 | 63 交易日收益 ±5% 分箱 + 最大回撤阈值 | v5 `market_state`；`[待对齐]` 阈值 |
- 渲染：一行中文（"你对后市偏乐观(4)；持有的 XX 浮亏 −8%；……"），不给权重。
- 日志：`st` 事件（level/aff）、`refl`（summary_sha, market_view, risk_mood）。
- `[待对齐]`：`market_view` 是否改为 TwinMarket 式多维（经济基本面 / 估值 / 短期趋势 / 情绪 / 自评）——建议**不扩**，保持 2 维 agent 可写 + 6 维引擎维护，理由：每多一维反思提示就多一处可被诱导的地方。

### A4. 熟悉度等级（familiarity level）`[已实现 v5 语义]`
- 定义：agent 对某机构的接触深度，三档 0/1/2，是旗舰对比的分层变量。
- 量化：`fam_t = (1−δ)·fam_{t−1} + 1[今日看到该机构帖]`（EMA）；`level = 2 若关注；1 若 fam ≥ 1.0 或 trust ≥ 1.0；否则 0`。**滞后**：t 日用的是 t−1 收盘状态。
- 出处：mere-exposure / 熟悉度偏好（Huberman 2001）`[REF ?]`；阈值 1.0 为设计常数（`[待对齐]` 是否敏感性分析 0.5/2.0）。
- 配置：`delta`（EMA 衰减，默认 `[待对齐]` 0.2）。日志：`st{what:level, lv, fam, aff}`。

### A5. 记忆（CoALA 四类）`[设计]`
- working：当日 Percept 集合 + 账户；episodic：5 日滚动，每日一行 ≤80 字（引擎写，含"申购被拦下：需签确认书，你放弃了"）；semantic：A3；procedural：schema 与规则。
- 反思：每 5 交易日，≤120 字总结 + 1–3 条信念 + market_view + risk_mood。
- 出处：Sumers et al. 2023 CoALA；Park et al. 2023 反思 `[REF ?]`。配置：`memory_days=5`, `reflection_every_days=5`, `memory` 开关。日志：`refl`。
- `[待对齐]`：反思频率 5 日（成本 vs 记忆保真）；记忆是否含图片（否：记忆只存文字摘要，图片效应只通过当日感知进入）。

### A6. 决策输出与可行性 `[已实现]`
- schema：`reads, engage{pid:[like|save|follow]}, comments[{post_id, stance, text≤40}], trade{action∈{buy,redeem,none}, fund, amount_pct 0–100, sign_mismatch_confirm}, org_affinity_delta{org:−2..2}, mood 1–5, reason≤60`；v7.1 加 `info_request∈{none,trend,holdings}`、`attention_fund`。
- 可行性由 GM 强制：赎回须持有；申购须为今日所示 I2 帖的落地基金；金额 = 百分比 × 可投现金（申购）/ 持仓（赎回）；最低 100 元。
- 出处：PolicySim JSON 结构化动作；TwinMarket `_polish_decision` 约束修正 `[REF ?]`。日志：`dec`、`act`、`co`、`click`。
- `[待对齐]`：是否允许一日多笔（当前一笔）；`amount_pct` 是否改为金额档位（当前百分比，便于跨财富比较）。

### A7. 评论立场（stance）`[已实现 4 类]`
- 定义：agent 对某帖的态度标签 + ≤40 字文本。
- 量化：`stance ∈ {bullish 看多/想买/加仓, bearish 看空/想卖/怕跌, watching 观望/提问/中立, no_comment}`；分类器侧用冻结零样本提示做同一四分类，κ 作代理一致性。
- 出处：StockTwits/雪球自标"看多/看空"；Antweiler–Frank 2004 buy/hold/sell 三类；Renault 2017；PolicySim stance∈{−1,0,1} `[REF ?]`。
- `[待对齐]`：是否加 −2..+2 强度（不建议：40 字文本承载不了，且立场比只用于气候聚合）。

### A8. 心情 `mood` `[已实现]`：1–5 单项自评；只作诊断，不进任何主张（DualMind 情感态为参照 `[REF ?]`）。

---

## B. 感知层：六条通道（L1）

| 通道 | 引擎算什么 | 渲染示例 | 量化细节 | 出处 | 开关 |
|---|---|---|---|---|---|
| B1 feed | 三源排序 K=6；每卡：文案(≤200 字)/图或描述/基金档案/昨日前 3 热评/气候标签 | 【p1】机构：… | 见 C1–C3 | PolicySim 推荐器中介 | `channels.feed` |
| B2 experience | 持仓浮盈亏、上一笔交易、实现盈亏、定投扣款、被拦记忆 | "你上个月买的 XX 目前浮亏 −6%" | `gain_loss`、`experience` | Odean；Malmendier–Nagel | `channels.experience` |
| B3 trend | 1 周/1 月/3 月收益、最大回撤、同类分位（若有 fund_meta） | "XX 近一月 +4.2%，处于近半年高位" | 收益 = nav_t/nav_{t−k} − 1；回撤 = 峰谷；"高位/低位" = 63 日区间分位 >0.8 / <0.2 `[待对齐]` | TwinMarket 技术指标检索；Barber–Odean 极端收益注意力 | `channels.trend`（论文主网格关） |
| B4 social | t−1 评论 → 气候标签 + 前 3 热评（strat_weight 加权） | "这条帖子下多数人看多；有人说'…'" | 见 C3 | PolicySim；Salganik–Dodds–Watts 社会证明 `[REF ?]` | `channels.social` / `social` |
| B5 news | 指数 5 日涨跌、持仓 1 日涨跌、股吧周讨论量 z 与多空比 | "近一周股吧里 XX 讨论量比上月多 2 倍，多空约 6:4" | z = (n_w − mean_8w)/sd_8w；多空比 = bullish/(bullish+bearish) | Barber–Odean；Da–Engelberg–Gao SVI `[REF ?]` | `channels.news` |
| B6 direct | 关注机构推送、费率优惠、新发提醒（战术库） | "你关注的鹏华推送：…" | 事件 `tac` | 促销/稀缺 `[REF ?]` | `channels.direct`（默认关） |

- 每个 Percept 记 `channel_sha`，进 `dec.notes`；归因分析按通道消融。
- **模态属性**（见 E）：只有 B1 的图片部分随模态臂变化。

---

## C. 环境层：平台（L3）

### C1. 推荐器（三源）`[已实现 feed.py]`
- 槽位：follow 2 / fit 2 / trending 2 = K=6；`match_score = w_trust·trust[org] + w_fit·fit(agent,post) + w_heat·heat + w_soc·climate_bonus + eps·U(0,1)`；fit：I2 帖对 tolerant 1.0 / typical 0.6 / fragile 0.3，非 I2 0.5；`ranking=random` 为对照臂。
- 出处：PolicySim 三源（关系 / 个性化 / 头条）`[REF ?]`；权重为设计常数（`[待对齐]` 默认 w_trust 1.0, w_fit 1.0, w_heat 1.0, w_soc 0.5, eps 0.05）。
- 日志：`imp{arm, slot, source}`。

### C2. 热度 `[已实现]`：`h(p) = log10(likes + 2·saves + 3·comments + 1) / (age_days + 1)^γ`，γ=1.8；计数按 `strat_weight` 加权。出处：TwinMarket / Reddit hot 公式 `[REF ?]`。`[待对齐]` 权重 1/2/3 的依据（努力成本递增；有无平台数据支持——可用小红书真实点赞/收藏/评论比校验）。

### C3. 评论气候 `[已实现]`
- 量化：对帖 p 取 t−1 的评论集（加权计数）；`n_signal = bull + bear + watch`；`n_signal < 4 → no_signal`；`(bull − bear)/n_signal > 1/6 → bullish_majority`；`< −1/6 → bearish_majority`；否则 `mixed`。前 3 热评按（评论者对该机构熟悉度 desc, 点赞 desc, id asc）。首日冷启动：真实股吧多空比 >0.6 / <0.4。
- 出处：多数阈值为设计常数；社会证明实证 Salganik 2006、Muchnik 2013 `[REF ?]`。
- `[待对齐]` `min_n=4` 与 `margin=1/6` 的敏感性；是否展示评论数（是，"共 n 条"）。

### C4. 图片臂分配 `[已实现 agent 级]`：见 E。

---

## D. 环境层：市场与监管（L3）

### D1. 基金与净值 `[已实现]`：真实日净值；基础宇宙 = 窗口前 ≥63 个净值；素材落地基金可延迟进入（首日 +21 天）；`market_state` = 63 日收益 ±5% 三分箱。`[待对齐]` 63/±5% 阈值。
### D2. 申赎结算 `[已实现 v5 语义]`：申购按当日净值、最低 100 元；赎回按持仓比例；实现盈亏入 `experience`；定投固定日扣款、过 QDII 不过 C×R。`[待对齐]` T+1 确认（当前当日成交，简化）；申购费/赎回费（scenario.market.fees 已留，引擎未用）。
### D3. 财富守恒 `[已实现]`：现金 + 持仓市值 + 实现 = 初始 ± 费用，容差 1e-6·max(w0)。
### D4. 监管插件 `[已实现]`：`cn_cxr` / `none`（反事实 `oc_cf`）；`us_regbi` 骨架。
### D5. 外生世界 `[已实现]`：股吧周信号（讨论量 z、多空比）、指数行；`[待对齐]` 是否加新闻标题（TwinMarket 做法）——数据由其他会话决定。

---

## E. 多模态（主线）`[设计]`

### E1. 模态臂
| 臂 | Feed 卡片图片部分 | 量化的"看见" |
|---|---|---|
| T | "配图不展示" | 无 |
| TC | OCR 文本 + 冻结提示生成的一句中性描述（离线生成，存内容池 `image_caption_frozen`） | 信息内容 |
| TV | 真实图片像素（≤768px，data-URI，每卡 ≤1 张，每日 ≤3 张） | 信息 + 呈现 |
- 随机化：`modality_level=agent`（每人整场固定，p 各 1/3）或 `run`（整场同臂）；平衡不变量（决策 16，对齐实现）：整体 |份额 − 1/k| ≤ 0.01；格内容差
  max(1/(2·n_cell), max(m, k−m)/(k·n_cell))，m = n_cell mod k。
- 出处：picture superiority / vividness / 广告视觉注意 `[REF ?]`；VLM 作消费者 `[REF ?]`。
- `[待对齐]` 三臂 vs 两臂（预算 ×1.5）；TC 描述提示的措辞（必须中性、不含评价词）。

### E2. 观测量（微 / 中 / 宏）
- 微：`P(read)`, `P(like)`, `P(save)`, `P(follow)`, `P(click→checkout)`, `P(subscribe | click)`, 立场分布, `org_affinity_delta` 均值, `attention_fund` 命中率（自报最在意基金是否为有图卡）。
- 中：帖子热度基尼系数、气候标签分布、机构到 level 1/2 的中位天数、评论文本与图片 OCR 的词重合率（图片信息是否被"说出来"）。
- 宏：族级资金流 Herfindahl、LSV、Δ1 与 r_off−r_on 在各臂下的差（探索性交互）。
- 效应量：比例差用 Cohen's h；SESOI `[待对齐]`（建议 h = 0.1 ≈ 5 个百分点）。

### E3. 审计
- 感知保真：TV 臂独立调用让模型描述所见图 → 与 OCR/人工标签比对（命中率）；
- 模态泄露：TC 描述文本中不得出现像素才有的信息（颜色、人物表情），用词表 + 抽检；
- 反诱导：提示中不出现"配图/图片效应/实验"。

---

## F. 机构层（L2）`[设计，rule 模式已在 world.py]`
- Profile：内容池、实测意图组合 I1/I2/I3；Policy(rule)：`intent_mix ∈ {measured, push_heavy, edu_heavy | 显式分布}`、`cadence`、`timing`、`tactics[]`、`targeting`；Policy(llm) 路线图。
- 战术库量化：`fee_discount`（申购费 ×0.1，持续 d 天，direct 推送）、`new_issue_push`（认购期标记）、`series_education`（连续 N 天同系列 I3）、`live_stream/kol`（载体系数 κ_carrier 进热度，默认 1）、`retarget_after_click`（次日 direct）。全部默认关。
- 出处：五维标签 D1–D5 的真实语料；促销/稀缺文献 `[REF ?]`。
- `[待对齐]` 8 机构下因子二分组规则（按实测 I2 占比前 4/后 4）；机构是否允许 LLM 策略（v7.1 路线图）。

---

## G. 社会层观测与验证（L4）
| 量 | 量化 | 真实参照 | 出处 |
|---|---|---|---|
| 流量-业绩凸性 | 分段回归 b⁺/b⁻（族 × 季） | 面板符号（当前 0.58/0.37，幅度待复权重估） | Sirri–Tufano 1998 `[REF ?]` |
| 处置效应 | PGR/PLR | >1 | Odean 1998 |
| 追涨 | 申购进前五分位份额 | 方向 | Chevalier–Ellison |
| 注意力买入 | 净买入 ~ 异常注意力 z | 真实季度弹性 ≈0（已估，不定） | Barber–Odean |
| 羊群 | LSV 同日同基金同向比例超出随机 | 文献 0.05–0.10（小样本偏差校正 `[REF ?]`） | LSV 1992 |
| 排名效应 | 卖出持仓中最高/最低者概率 | U 形 | Hartzmark 2015 |
| 留出 | 族级净流 vs 真实 2025Q4，ρ、符号、τ；领先安慰剂 Q3 | 24 只可落地基金 | — |
| 异质性 | η²、ICC、跨种子 ρ、坍缩警报 | η²>0.15, ρ>0.6 | PIMMUR |
| SRS | \|(a)\|/\|E\|，H=30；Δ1、Δ2；r_off−r_on | 预注册 | PREREG v1 |

---

## H. 实验层（L5）
- 因子：`suitability {on,off}` × `strategy_mix {push_heavy, edu_heavy}`（主）；`modality_arm {T,TC,TV}` × `modality_level {agent,run}`（主线第二实验）；对照：`ranking=random`、`memory=false`、`social=false`、`climate_weighting=false`、底座替换。
- 推断：种子级批均值 t 区间（df = S−1）；投资者聚类自助法为第二方差分量；SESOI 预注册。
- 确定性：所有随机流 `rng_for(run_tag, …)`；缓存键 `sha256(model|temp|schema|prompt_sha|image_shas)`；零调用重放；不变量 (a)–(l)。

---

## I. 待业主拍板清单（对齐会用）
1. A3 信念维度是否扩为 TwinMarket 式多维（建议不扩）。
2. A4 熟悉度阈值 1.0 与 EMA δ 的默认值及敏感性范围。
3. A6 一日一笔 vs 多笔；金额百分比 vs 档位。
4. C2/C3 热度权重 1/2/3、气候 `min_n=4`、`margin=1/6` 是否用真实小红书互动比校验。
5. D2 是否实现 T+1 与费率（会改财富守恒式）。
6. E1 三臂还是两臂；TC 描述提示的措辞与生成模型；E2 SESOI。
7. F 8 机构分组规则；战术库是否进 demo。
8. 反思频率 5 日；记忆不含图片。
9. B3 trend 通道是否进论文主网格（建议不进，作 v7 展示）。

---

## J. 调研回填（2026-09-05 晚三份报告）与一条设计立场

### J0. 设计立场：文献里的行为公式是"验证靶"或"空模拟器规则"，不是活 agent 的决策规则
调研 `state_quantification_theories.md` 给出了大量可直接编码的行为方程（处置效应"赎回概率 = base + α_gain·gain − β_loss·|loss|"、Friedkin–Johnsen 信念平均、Flow = β₀+β₁Ret+β₂Ret²、外推期望 ρ 等）。**我们不把这些方程写进投资者 agent**：那会把"LLM 是否自发表现出这些规律"这一研究问题变成"我们是否把规律硬编码进去"（PIMMUR 的 Minimal-control 原则；TwinMarket 的立体感来自数值状态 + LLM 决策，而非规则决策）。这些方程有两个正当去处：
1. **验证靶**（G 节）：模拟事件日志上估出的 PGR/PLR、LSV、b⁺/b⁻、注意力弹性与文献比较符号与量级；
2. **规则型空模拟器**（反 A1 基线）：用这些方程替代 LLM 决策，同排序同结账零调用，用来分离"机械效应"。
引擎只负责把状态算成**数值并渲染为一句话**（A3/B 节），决策权在 LLM。

### J1. 逐卡出处回填（详见 `research/state_quantification_theories.md`、`multimodal_persuasion_evidence.md`、`sandbox_projects_survey.md`、`agent_cognitive_architectures_survey.md`）
| 卡 | 文献定义与量表 | 我们的量化（与文献的差异，需对齐） |
|---|---|---|
| A7 立场 | Antweiler–Frank 2004 三类 buy/hold/sell（Naive Bayes/SVM）；Renault 2017 StockTwits 词典；Cookson–Niessner 2020 StockTwits 自标 bullish/bearish；FinBERT {pos, neu, neg}；PolicySim stance∈{−1,0,1} EMA α=0.8 | 四类 {bullish, bearish, watching, no_comment}：比文献多一个 `no_comment`（"大多数人不留言"是真实基线，必须可表达）；不做强度分级；不对个人立场做 EMA（立场是每条评论的属性，聚合在帖级气候里） |
| A3 信念 | Greenwood–Shleifer 2014 预期调查；Barberis 等 2015 外推期望；TwinMarket 5 维 1–5 | 2 维 agent 可写（market_view, risk_mood）+ 6 维引擎数值；**不引入外推系数 ρ 作为更新规则**（J0），但用"期望 vs 近期收益相关性 ρ>0.3"作**验证靶** |
| A3 attention | Barber–Odean 2008（异常成交量/极端收益/新闻）；Da–Engelberg–Gao 2011 SVI；Broadbent 1979 adstock；Nerlove–Arrow 1962 | `att_t = λ·att_{t−1} + 曝光 + β·z_guba`；λ 建议 0.7–0.9（日）；调研建议的 λ∈[0.1,0.3] 是"新信息权重"参数化（1−λ 记法），两者等价，**统一记法待对齐** |
| A3 trust / A4 熟悉度 | Guiso–Sapienza–Zingales 2008；Chaudhuri–Holbrook 2001（单维可靠性 1–5）；Delgado-Ballester 2003；Huberman 2001；Zajonc 1968 | 熟悉度 0/1/2 由曝光 EMA 与关注决定；trust 为 agent 自评好感增量的 adstock（λ_a=0.9）。**不采用**调研建议的"trust = 0.4·超额收益 + 0.3·正面评论% + 0.3·公司规模"（那是把结论写进输入） |
| A3 ref_point / F 处置 | Odean 1998 PGR≈0.148 / PLR≈0.098；Shefrin–Statman 1985；Barberis–Xiong 2009；Baucells–Weber–Welfens 2011（参考点适应） | 参考点 = 加权平均成本（文献主流）；PGR/PLR 只作验证靶（>1）；**不写赎回概率公式** |
| E 羊群 | LSV 1992；Frey–Herbst–Walter 2014 小样本校正；Sias 2004；Wermers 1999 | 日级基金层 LSV，需 Frey 等校正（模拟里每日每基金交易者少）；调研给的"中国零售热点基金 LSV 0.3–0.5"**未核实**，不作为靶，只比符号与相对高低 |
| G 流-业绩 | Sirri–Tufano 1998；Chevalier–Ellison 1997；中国"赎回悖论"文献（`[REF ?]` 待核） | 分段回归 b⁺/b⁻ 比符号；不把 Flow 方程写进 agent |
| H 社会影响 / C3 气候 | DeGroot 1974；Friedkin–Johnsen 1990；Hegselmann–Krause；Granovetter 1978；Bikhchandani 等 1992；Salganik–Dodds–Watts 2006；Muchnik–Aral–Taylor 2013（显示点赞 → +32% 正向投票） | 气候 = 帖级 t−1 立场多数标签（阈值 1/6、min_n 4），只作**展示**；不做信念平均更新；Muchnik 2013 是"展示气候改变行为"的实证锚，用作 B5 气候关联分析的预期方向 |
| I 风险 | CSRC 2017 C1–C5/R1–R5；Dohmen 等 2011；Tversky–Kahneman 1992 λ≈2.25 α≈0.88 | 已实现；CPT 参数只进人设卡渲染，不进效用计算 |
| J 互动权重 | Reddit/TwinMarket 热度 1/2/3；营销漏斗 CTR/转化 | 保留 1/2/3（like/save/comment）；`[待对齐]` 用小红书真实互动比校验 |
| K 心情 | PANAS 单项；Edmans–García–Norli 2007；Kaplanski–Levy 2010；DualMind 情感态 | 自报 1–5，诊断用，不进主张 |
| E 多模态 | picture superiority（Paivio 双编码）；Li–Xie 2020 JMR（图像存在→参与度↑，d≈0.35–0.6）；Pieters–Wedel 2004 眼动；Mitchell–Olson 1981；Glaser–Iliewa–Weber 2022 RFS（图表视觉显著性影响决策）；Tal–Wansink 2016（复制存疑）；MacKenzie–Lutz–Belch 1986 Aad→Ab→PI；VLM 语言先验压过视觉、金融 VLM 过度自信（arXiv 2505.23941, 2608.06532） | 三臂 T/TC/TV；输出映射：reads→注意捕捉，engage→Aad，org_affinity_delta→Ab，trade→PI；**预期**：互动 TV>TC>T（TV−TC 中等、TC−T 小），交易可能无差异——预注册两种结果都是发现；~200 人/臂可检 d≈0.35–0.45；TC 描述保真度人工抽检 ≥8/10 才可用 |
| 架构 | CoALA 四类记忆；Concordia GM；OASIS/PolicySim 中介社会层；TwinMarket 信念向量；Mesa DataCollector；AgentSociety DuckDB+JSONL 账本 | 见 `PROJECT_ARCHITECTURE_v7.1.md`；账本 = 不可变事件 JSONL + 快照，Mesa 式 DataCollector 作分析层立面 |

### J2. 三份调研里我不采纳的建议（说明理由，供对齐时反驳）
- 把处置效应、流-业绩、外推期望写成 agent 的概率规则（J0）。
- trust 由业绩/正面评论/公司规模加权合成（把机构效应的结论当输入）。
- 立场 EMA 到个人层（我们的立场是评论属性；个人层的持久量是信念向量）。
- "热点基金 LSV > 0.4" 之类未核实的中国基准作为通过判据。
- 多模态调研建议"每人首次遇到某基金时随机臂"（会造成同一 agent 跨基金混臂，破坏 agent 级 SUTVA）；我们坚持 agent 级整场固定或整场级。

### J3. 对齐会新增问题（在 §I 之上）
10. attention adstock 的记法与默认 λ（0.8 日衰减？）。
11. 气候是否同时展示"共 n 条评论"与多数标签（社会证明强度 → Muchnik 效应）。
12. 多模态主终点选哪三个（建议：reads 比例 h、comment 率、org_affinity_delta 均值；trade 转化为次终点并预注册"可能无差异"）。
13. TC 描述由哪个模型生成、提示措辞（中性、禁评价词、禁颜色/表情词）。
14. 空模拟器采用哪些文献方程（建议：处置效应赎回规则 + 追涨申购规则 + 均匀选基）。

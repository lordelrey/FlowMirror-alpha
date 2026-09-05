# 沙盒内部细节决策记录（业主逐条确认，2026-09-05 晚）

> 来源：`SANDBOX_INTERNAL_SPEC_v1.md` §I/§J3 的 14 条待拍板项，经三轮逐条确认（每条附顶会/经典文献做法与建议）。全部取"建议项"。第 13、14 条为默认值，业主未反对即生效。
> 去向：PREREG v1.5 草案（`specs/PREREG_v1.5_DRAFT.md`）、引擎验收标准、论文 §3/§4。

| # | 决策 | 顶会/文献参照 | 我们的选择 | 引擎/配置落点 |
|---|---|---|---|---|
| 1 | 信念维度 | TwinMarket 5 维 1–5（GPT 每日改写）；PolicySim 1 维 EMA；EconAgent 季度自然语言反思 | **2 维 agent 可写**（market_view、risk_mood，反思时改写）+ **6 维引擎数值**（trust、attention、ref_point、gain_loss、experience、trend_read） | `agents/prompt.py` B 块；`engine/world.py` Inv 状态 |
| 2 | 评论立场 | Antweiler–Frank 三类；Cookson–Niessner 两类自标；PolicySim {−1,0,1} | **四类** bullish / bearish / watching / **no_comment**；不做强度；不做个人层 EMA | `parse_decision`；气候聚合只用前三类 |
| 3 | 记忆与反思 | GA 重要性阈值反思（贵）；EconAgent 季度；PolicySim 双记忆 | **5 日滚动日志（引擎写，≤80 字/日）+ 每 5 交易日反思**（≤120 字 + 1–3 条信念）；记忆只存文字 | `memory_days=5`, `reflection_every_days=5` |
| 4 | 交易粒度 | TwinMarket 一日一次决策可多单、整手约束 | **一日一笔**（buy/redeem/none），**百分比金额**（申购=可投现金%，赎回=持仓%），最低 100 元，可行性由 GM 强制 | `apply_decision` |
| 5 | 熟悉度/注意力衰减 | adstock（Broadbent；Nerlove–Arrow）；Barber–Odean 异常注意力 z；Huberman/Zajonc 曝光→熟悉 | **δ=0.2**（fam_t=(1−δ)fam_{t−1}+1[曝光]），**level 阈值 1.0**（fam≥1 或 trust≥1 → 1；关注 → 2）；trust adstock λ_a=0.9；attention 记法统一为 `att_t=λ·att_{t−1}+曝光+β·z_guba`，λ=0.8；**敏感性臂**：阈值 0.5 / 2.0 各 1 种子 | `engine/world.py` 滞后更新；配置 `delta`, `fam_threshold`, `lambda_trust`, `lambda_attention` |
| 6 | 热度与气候 | Reddit/TwinMarket hot 公式；OASIS 复现平台算法；Muchnik 2013 展示效应 | **保留** h=log10(赞+2收藏+3评论+1)/(age+1)^1.8 与气候阈值（min_n 4，margin 1/6）；**用小红书真实笔记的 赞:收藏:评论 中位比校验权重**，不合再改；阈值做敏感性 | `channels/feed.py`；校验脚本读 `content_pool` 的 likes/collects/comments |
| 7 | 多模态臂 | Li–Xie 2020；Glaser–Iliewa–Weber 2022；Taylor–Thompson 综述；顶会模拟器无先例 | **三臂 T / TC / TV**；**agent 级**（主网格内每人整场固定，各 1/3）**+ 整场级**（参考格上全 T / 全 TC / 全 TV，各 2 种子） | `modality_arm`, `modality_level`；`feed.assign_arms` 扩为三值 |
| 8 | 多模态主终点 | MacKenzie–Lutz–Belch 效应链 Aad→Ab→PI | **主**：阅读/互动率（like+save）、评论率、机构好感增量均值；**次**：点击率、申购转化；预注册"交易可能无差异亦为发现" | `analysis/modality.py` |
| 9 | 走势通道 | TwinMarket 给技术指标并允许检索 | **不进主网格**；作 v7 展示 + 1 次消融（2 种子）；`info_request` 主动索取列 v7.1 路线图 | `channels.trend=false`（主网格） |
| 10 | TC 描述生成 | VLM 描述会漏/添信息；金融 VLM 过度自信 | **同一 VLM（glm-4.6v）离线生成**，冻结提示，OCR 原文 + ≤40 字中性内容描述（写"有什么"不写"好不好"，禁评价词与颜色/表情词）；**业主人工抽检 30 张 ≥8/10** 才可用 | `data_pipeline/cn/caption_frozen.py`（待 GLM 写）；内容池字段 `image_caption_frozen` |
| 11 | 规则型空模拟器 | Odean 处置效应；Sirri–Tufano 追涨 | **两条规则**：赎回概率按盈亏符号两档（参数=人口平均响应率）；申购概率∝基金近 3 月收益分位；互动/评论按人口均值率随机；其余均匀。**文献方程不进活 agent** | `agents/null_policy.py`（待写）；配置 `agent_policy=llm|null` |
| 12 | 结算与费率 | 真实 T+1、申购费 1.2%（常 1 折）、赎回费递减；TwinMarket 无佣金 | **加费率**（可配置，默认申购 0.12%、赎回 0.5%），**成交仍当日**（不做 T+1）；财富守恒式加"累计费用"项 | `market/orders.py`；`scenario.market.fees` |
| 13 | 气候展示形式 | Muchnik 2013：社会证明强度 | 默认：同时展示"共 n 条评论"与多数标签 + 前 3 热评 | `render_social` |
| 14 | 8 机构因子二分组 | v1.3 §B9（4 家显式分组） | 规则：按打标后实测 I2 占比排序，**前一半 = 推品重、后一半 = 投教重**；规则先冻结，分组在打标后、运行前落定 | `strategy_groups` 由脚本按规则生成 |

**设计立场（业主已认可）**：文献行为方程只作验证靶与空模拟器规则，不进活 agent；引擎算数、agent 读文；每条通道的文本进 `dec.prompt_sha` 并单独记 `channel_sha`。

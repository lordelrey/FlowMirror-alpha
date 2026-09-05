# 开源 LLM 智能体社经模拟沙箱对标研究

**研究目标**：为基金营销数字孪生框架（投资者浏览推荐信息流、评论、在监管结算下订阅/赎回基金）的内部架构做工程级对标。

---

## 1. MiroFish

**仓库**：https://github.com/666ghj/MiroFish | 18,000+ stars

**世界模型**：根据新闻、政策文稿、金融信号等种子数据自动构建高保真平行数字世界。数千个具有独立人格、长期记忆、行为逻辑的 LLM 代理自由互动并经历社会演化。

**代理分解**（5 子模块）：
- Graph Building：从种子数据提取实体关系，用 GraphRAG 构建结构化知识图（10 实体类型 × 10 关系类型）
- Persona Generation：基于知识图生成代理初始特征
- 状态变量：代理在知识图中的节点身份、属性（如股东身份、政治立场——未明确量化）
- 记忆：与外部知识图绑定，避免幻觉漂移
- 决策循环：未明确公开；模拟约 40 轮以内

**环境分离**：FastAPI 事件驱动堆栈；无显式时钟所有权描述；支持"上帝视角"动态注入变量

**社会涌现**：双平台并行模拟（社交媒体风格）；ReportAgent 进行深交互分析

**状态量化**：仅知识图结构化，行为倾向未量化；未引用理论基础

**配置**：`.env` 环境变量（LLM API、Zep Cloud）；`.json` 模型参数

**数据**：输入为种子文本；输出为预测报告和事件序列；无标准日志格式公开

**验证**：未找到公开验证套件

**规模/成本**：支持千级代理；API 成本主要来自 LLM 调用

**测试/CI**：仓库无 CI 配置公开

---

## 2. OASIS (camel-ai)

**仓库**：https://github.com/camel-ai/oasis | ~5,000 stars

**世界模型**：Twitter/Reddit 风格的社交媒体平台；1M 代理规模的开放式社交互动模拟。

**代理分解**（4 子模块）：
- LLMAction：基于 LLM 的自主决策；ManualAction：显式指令控制
- 状态变量：从 `user_data_*.json` 加载的档案（兴趣、行为模式）；未明确类型/范围
- 记忆：SQLite 数据库持久化档案 (`database_path`)
- 决策循环：异步事件循环；每步允许 LLM 生成或手动指令

**环境分离**：`oasis.make()` 创建环境；异步调度器按时步处理动作

**社会涌现**：23 动作空间（点赞、评论、发帖、搜索、关注、屏蔽、趋势查询、刷新）；基于兴趣和热分数的推荐算法

**状态量化**：未显式量化；无学术引用

**配置**：模型选择（GPT-4O-Mini、Qwen）、激活概率、代理数、平台类型

**数据**：SQLite 持久化；支持 Pandas 导出；可视化和分析工具集成

**验证**：每 100 代理基线的 token 成本基准

**规模/成本**：1M 代理规模；SQLite 和异步架构支撑；API 成本显著

**测试/CI**：GitHub Actions 集成；发布到 PyPI

---

## 3. AgentSociety (清华 FIB Lab)

**仓库**：https://github.com/tsinghua-fib-lab/AgentSociety | ~2,500 stars

**世界模型**：LLM 原生代理模拟平台；社会科学研究和实验设计的灵活框架（城市规模模拟）。

**代理分解**（3 子模块）：
- 无状态记录驱动 Ray Tasks；环境、LLM 客户端、追踪、回放句柄统一 ServiceProxy
- CodeGen/ReAct/Plan-Execute/Two-Tier/Search 多种推理模式
- 状态变量：代理元数据规范定义，未明确类型/范围

**环境分离**：模块化热插拔工具架构；Ray 分布式执行

**社会涌现**：实验配置驱动的工具组合；研究能力集成（文献搜索、假设生成）

**状态量化**：无显式量化；无理论引用

**配置**：JSONL 目录驱动的参数化；DuckDB 数据库配置

**数据**：DuckDB 数据采集；Catalog 驱动 JSONL 回放；分布式追踪

**验证**：JSONL 回放机制支持确定性重现

**规模/成本**：城市规模模拟；Ray 支持水平扩展

**测试/CI**：GitHub Actions；PyPI 发布

---

## 4. Concordia (Google DeepMind)

**仓库**：https://github.com/google-deepmind/concordia | ~2,000 stars

**世界模型**：桌面 RPG 风格的游戏引擎；代理在物理、社交或数字空间中 grounded。

**代理分解**（4 子模块）：
- 实体 Entity：Player Agents 或 Game Master (GM)
- 组件 Component：内存系统、推理链、感知模块（模块化重用）
- 记忆：自然语言记忆流（长期外存）；QuestionOfRecentMemories 自省；Plan 组件
- 决策循环："我是什么样的人？""这是什么样的情境？""这样的人在这样的情境中做什么？"（logic of appropriateness）

**环境分离**：GM 模块仲裁所有行动；检查物理/社交合理性

**社会涌现**：自然语言行动描述 → GM 实现 → 涌现交互

**状态量化**：无显式向量化；无量化范围

**配置**：组件配方库（prefabs）；文档模块管理 LLM 提示

**数据**：Document 模块管理 LLM 上下文；无标准日志格式

**验证**：无公开测试套件

**规模/成本**：未指定最大规模；GPU 不必需

**测试/CI**：GitHub 主分支活跃；PyPI 发布

---

## 5. TwinMarket (FreedomIntelligence)

**仓库**：https://github.com/FreedomIntelligence/TwinMarket | ~800 stars | **NeurIPS 2025 & ICLR 2025 金融 AI 最佳论文**

**世界模型**：股票市场模拟；多代理框架通过 LLM 模拟社经系统，复现市场风格化事实（肥尾、杠杆效应、波动率聚类）。

**代理分解**（BDI 框架 + 4 子模块）：
- Belief（信念）：经济基本面、市场估值、短期趋势、同侪情绪、自我评估 5 维情绪评分
- Desire（欲望）：投资收益、风险偏好
- Intention（意图）：交易订单执行
- 状态变量：信念向量 (5 维)、过度自信、损失厌恶、锚定偏差、羊群行为、理性衰退等认知偏差（未给范围）；记忆：历史价格、同侪讨论
- 决策循环：GPT-4 信念更新 → 欲望评估 → 订单生成

**环境分离**：完整订单匹配和执行引擎；论坛风格交互（社交网络）

**社会涌现**：论坛互动生成同侪影响；自我实现预言

**状态量化**：情绪得分 ∈ [−1, 1] 区间；认知偏差编码为行为规则；无论文具体量化方法

**配置**：`config/` 目录参数化

**数据**：`data/` 目录存储；完整订单日志；市场数据分析

**验证**：再现 4 个已知市场风格化事实

**规模/成本**：1,000 代理规模实证；高性能并发架构

**测试/CI**：GitHub Actions；论文验证

---

## 6. YuLan-OneSim (人大 GSAI)

**仓库**：https://github.com/RUC-GSAI/YuLan-OneSim | ~1,200 stars | **NeurIPS 2025**

**世界模型**：通用社会模拟器；8 领域 × 50+ 默认场景（经济、社会学、政治、心理学、组织、人口、法律、传播）；支持代码生成的无编码场景构建。

**代理分解**（3 子模块）：
- 短长期策略 ShortLongStrategy 内存管理
- COTPlanning 链式思考规划
- 状态变量：记忆表示、文化/行为属性（未明确类型）
- 行为图和代码自动生成

**环境分离**：可配置轮次处理；模块化 8 领域环境

**社会涌现**：代理行为图自动生成；文化和社会动态涌现

**状态量化**：无显式量化

**配置**：`config/config.json`（模拟参数）+ `config/model_config.json`（LLM/embedding 模型、API 凭证）

**数据**：可选 `export_training_data` 和 `export_event_data` 标志；结构化结果采集

**验证**：无公开验证套件

**规模/成本**：分布式模式支持 100k 代理；vLLM 本地部署减少 API 瓶颈

**测试/CI**：GitHub Actions；PyPI 发布

---

## 7. Generative Agents (joonspk-research)

**仓库**：https://github.com/joonspk-research/generative_agents | ~20,000 stars

**世界模型**：Smallville 虚拟城镇；LLM 驱动的人类可信行为模拟（2023 年 Park et al. 论文实现）。

**代理分解**（5 子模块）：
- 记忆流：长期外存记忆，记录观察、事件（自然语言）
- 感知 Perception：动态环境感知（地图导航）
- 反思 Reflection：新旧记忆触发递归汇总
- 规划 Planning：每日计划从昨日事件 + 广播事件合成；分解为块/小时/动作级
- 行动执行：执行计划或实时调整（响应式）

**环境分离**：Django 环境服务器 + Reverie 后端并发；浏览器可视化

**社会涌现**：基于内存驱动的对话和社交互动

**状态量化**：无显式向量化；记忆为自然语言

**配置**：命令行参数（`run <step-count>`，每步 10 秒仿真）

**数据**：保存模拟可回放；压缩演示后处理；URL 基础访问回放

**验证**：演示可视化

**规模/成本**：人工场景规模（~25 代理）；API 成本主要

**测试/CI**：无 CI 配置公开

---

## 8. SocioVerse (复旦 DISC)

**仓库**：https://github.com/FudanDISC/SocioVerse | ~500 stars | **代码"Coming Soon"**

**世界模型**：世界模型社会模拟器；由 LLM 代理驱动，接入 1000 万真实用户池。三个演示场景：美国总统大选预测、突发新闻反应、中国经济调查。

**代理分解**（4 对齐模块）：
- Social Environment：社会结构（人口分布）、动态（时事政策）、个性化上下文
- User Engine：15 维人口特征（年龄、性别、职业、种族、收入、意识形态、性格）
- Behavior Engine：用户历史、交互机制、社会背景对齐
- 状态变量：15 维人口向量；无显式行为向量

**环境分离**：未公开

**社会涌现**：从 1000 万用户历史学习；涌现集体行为

**状态量化**：意识形态、性格无量化细节；无理论引用

**配置**：未公开（代码未释放）

**数据**：Hugging Face 提供部分用户池；问卷、指标脚本、评估指标

**验证**：三域大规模实验（政治、新闻、经济）

**规模/成本**：1000 万用户背景；实验规模未明确

**测试/CI**：无公开测试

---

## 9. AgentTorch (MIT)

**仓库**：https://github.com/AgentTorch/AgentTorch | ~2,000 stars

**世界模型**：大规模种群可微模型；GPU 加速模拟支持百万规模代理。

**代理分解**（3 子模块）：
- 持久状态演化的自主实体
- 可微模拟：梯度穿过随机动态，支持基于梯度优化
- 平滑和重参数化技术实现离散随机程序可微性
- 状态变量：标准配置驱动定义（未明确类型）

**环境分离**：标准配置格式；与深度神经网络（LLM）、机械模拟器组合

**社会涌现**：种群级涌现行为

**状态量化**：无显式量化

**配置**：标准配置格式（未公开具体格式）

**数据**：支持数据采集和分析

**验证**：587 commits 活跃开发；核心模块、文档、测试覆盖

**规模/成本**：消费级 GPU 到计算集群；商用硬件秒级百万代理仿真

**测试/CI**：GitHub Actions；PyPI 发布；Python ≥3.9

---

## 10. Mesa (Python ABM 框架)

**仓库**：https://github.com/projectmesa/mesa | ~3,800 stars

**世界模型**：通用 ABM 框架；Python 替代 NetLogo/Repast/MASON。

**代理分解**（4 子模块）：
- Agent/Model/Environment 抽象
- Scheduler 模式（同步/异步执行顺序控制）
- DataCollector：4 类数据（模型级、代理级、代理类型级、表）
- 空间网格和可视化界面

**环境分离**：Scheduler 控制时钟；Agent.step(model) 标准签名

**社会涌现**：自定义环境逻辑；模块化组件

**状态量化**：DataCollector 接受任意 lambda 函数和属性名；报告者可返回任意向量

**配置**：参数化模型初始化；字典配置

**数据**：Pandas DataFrame 导出；内存 dict 存储 {时步: [agent_id, value_dict]}

**验证**：comprehensive tests；月度开发者会议社区

**规模/成本**：依赖硬件；未指定上限；v3 稳定、v4 预发布

**测试/CI**：GitHub Actions；PyPI；Discord 社区

---

## 11. AgentA/B 与营销类模拟器

**仓库**（AgentA/B）：未公开 | **CHI 2026 论文**；示例：https://github.com/carolchu1208/LLM-Based-Generative-Agents-Simulating-Consumer-Decisions

**世界模型**：A/B 测试框架用 LLM 代理替换真实用户流量；1,000 LLM 代理在真实网页上模拟交互。

**代理分解**（3 子模块）：
- 角色 Persona：心理图形 ×7（AgentA/B 示例）或人口统计（Carol Chu 实现）
- 动作空间：搜索、点击、筛选、购买等多步交互
- 状态变量：偏好、品牌意识、购买意图；人口因素（收入、年龄、生活风格）（无量化范围）
- 记忆：购买历史、交互模式

**环境分离**：真实网页 DOM 作为环境；LLM 决策模块 → 页面交互

**社会涌现**：推荐反馈循环影响后续决策

**状态量化**：AIDA/AISAS 框架；心理状态无显式向量化

**配置**：角色参数化；意图设置

**数据**：代理行为日志；支出、转化、交互序列

**验证**：Amazon 对照实验（处理组 $60.99 vs. 对照组 $55.14）；消费者行为研究基准对标

**规模/成本**：1,000 代理演示；可扩展至万级

**测试/CI**：学术论文验证

---

## 综合分析（≤60 行）

### (a) 推荐的最小接口集合

```python
# Agent: state, memory, step(env) → Action
class Agent:
    def __init__(self, id, state: Dict[str, Any]): ...
    def step(self, perception: Dict) -> Action: ...

# Environment/GameMaster: 时钟所有权、行动仲裁
class GameMaster:
    def execute_action(self, agent_id: str, action: Action) -> Outcome: ...
    def tick() -> None: ...

# Channel/Perception: 推荐的信息流
class RecommendationChannel:
    def recommend(self, agent: Agent) -> List[Content]: ...

# Memory: 可持久化、可回放
class MemoryStore:
    def record(self, event: Event) -> None: ...
    def retrieve(self, query: str, k=5) -> List[Memory]: ...
    def export_deterministic_snapshot() -> bytes: ...

# Policy: 决策分解
class Policy:
    def forward(self, observation: Dict) -> Action: ...

# Ledger: 不可变交易/行动日志
class Ledger:
    def append(self, transaction: Dict) -> None: ...
    def replay(self, n_steps: int) -> Iterator[State]: ...

# Metrics: 状态/行为采集器
class MetricsCollector:
    def collect(self, step: int, agent_id: str, metric_name: str, value: Any): ...
    def export_pandas() -> pd.DataFrame: ...
```

### (b) 量化情绪/立场的项目及方法

- **TwinMarket**：信念向量 5 维（经济基本面、估值、趋势、同侪、自我评估），范围 [−1, 1]；情绪分数驱动 BDI 决策（无论文引用）
- **消费者行为模拟**（Carol Chu）：AIDA/AISAS 框架；心理状态无量化，决策通过 LLM prompt 隐式编码
- **OASIS**：23 动作空间（离散）；代理档案偏好无向量化（对应动作选择的隐式分布）
- **SocioVerse**：15 维人口特征；意识形态/性格未量化（从用户历史学习但不暴露向量）
- **Concordia**：无显式量化；通过自然语言内存和 LLM 推理隐式表示

**缺口**：大多数项目缺乏学术理论引用（如社会学的立场空间、心理学的 Big Five）

### (c) 使用推荐者中介反馈的项目及参数化

- **OASIS**：兴趣基础推荐 + 热分数排名；23 动作空间包含"刷新"感知推荐流
- **TwinMarket**：论坛风格交互生成同侪 signal，影响信念更新；无显式推荐算法
- **AgentA/B**：网页 DOM 结构 + 视觉设计变体 → 推荐重排；A/B 对照测试
- **消费者行为**：个性化内容投放基于代理状态；反馈循环驱动后续购买决策
- **SocioVerse**：个性化上下文模块（社交网络过滤内容）；1000 万用户历史驱动推荐

### (d) 支持确定性回放的日志/指标抽象

- **AgentSociety**：DuckDB + JSONL Catalog；行为可从 DuckDB 读取重现
- **Mesa**：DataCollector（模型级/代理级/表格）→ Pandas DataFrame；配合种子固定可确定性重现
- **TwinMarket**：完整订单日志 + 市场数据快照；基于日志回放重构市场状态
- **Generative Agents**：内存流自然语言存储 + 时间戳；回放需重新运行 LLM 推理（非严格确定性）
- **推荐接口**：Ledger（append-only 事务日志）+ MemoryStore.export_deterministic_snapshot()；支持快照回放 + delta 增量应用

**最佳实践**：将行动、感知、订单、推荐作为 immutable 事件链；配合 seed 和 LLM 温度=0 实现可重现回放。

---

**数据来源**：
- https://github.com/666ghj/MiroFish
- https://github.com/camel-ai/oasis
- https://github.com/tsinghua-fib-lab/AgentSociety
- https://github.com/google-deepmind/concordia
- https://github.com/FreedomIntelligence/TwinMarket
- https://github.com/RUC-GSAI/YuLan-OneSim
- https://github.com/joonspk-research/generative_agents
- https://github.com/FudanDISC/SocioVerse
- https://github.com/AgentTorch/AgentTorch
- https://github.com/projectmesa/mesa
- https://arxiv.org/abs/2502.01506 (TwinMarket)
- https://arxiv.org/abs/2505.07581 (YuLan-OneSim)
- https://arxiv.org/abs/2312.03664 (Concordia)
- https://arxiv.org/abs/2411.11581 (OASIS)
- https://arxiv.org/abs/2504.10157 (SocioVerse)
- https://arxiv.org/abs/2510.18155 (LLM 消费者行为)
- https://arxiv.org/abs/2504.09723 (AgentA/B)

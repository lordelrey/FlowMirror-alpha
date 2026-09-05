# FlowMirror v7 沙盒现状与方案 —— 移交论文撰写会话（2026-09-05 14:30）

> 读者：负责论文写作的会话。本文件回答三件事：沙盒**现在**长什么样（已建成 / 在建 / 未建）、代码与数据在 GitHub 上的位置、论文里可以据此写什么、不能写什么。数字均来自可核对的文件；未落地的部分明确标 ⏳。
> 权威文件顺序：`specs/PREREG_v1.md → v1.1 → v1.2 → v1.3 → v1.4`（后者覆盖前者）> `paper/CLAIM_BOUNDARIES.md` > `paper/STYLE_RULES.md`。方法学时间线：`METHODS_LEDGER.md` R17–R28。

> **2026-09-05 22:2x 更新（P3b 落地，METHODS_LEDGER R31）**：引擎已接入 **三臂模态 T/TC/TV**（`modality_level` agent/run/exposure、`modality_arms`、`modality_run_arm`）、**费率**（`fees`，默认 0，进不变量 (d)）、`dec` 六个互动计数、`run_meta.arms`；**规则型空模拟器**（`agent_policy: null`）、**模态分析层**、**五层立面**、**TC 冻结描述器**全部落盘。验收：7 模块自检、pytest 72、三份 mock 事件日志逐行过 event.schema、M0/三臂/空模拟器各自重放一致（sha 4df9263c… / e016807e… / b0cdeacd…）。两处既有 bug 已修：`post.ig` 现为 {I2,nonI2}；赎回结账不再发 click 行且 `oc_cf` 恒为 match。**论文可写**：三臂设计与随机化单位、TC 的"文字化图片"定义、空模拟器规则与参数（在结果前固定）、费率机制；**仍不可写**：任何运行结果、TC 描述已生成、M1 已跑。M0 基线哈希因遮蔽文案/评论总数/新字段而变化，属有意变更（R31）。

---

## 1. 仓库与文件位置

| 什么 | 在哪 |
|---|---|
| **v7 开源沙盒（Python 包 `flowmirror`）** | GitHub **私有**仓库 `github.com/lordelrey/flowmirror`（首次提交 2026-09-05；业主可改公开）。本地 `D:\Desktop\ABM paper\flowmirror_v7\` |
| 研究仓库（实验方案、预注册、论文车道、原始数据管道） | `D:\Desktop\ABM paper\fundmarket-sim\`（未上 GitHub） |
| 论文车道交接 | `paper/HANDOVER_PAPER_2026-09-05.md`（阅读顺序、设计事实表、可 `\input` 产物、阶段门） |
| 项目架构方案 | `PROJECT_ARCHITECTURE_v7.md`（仓库布局、数据分层、人设、六通道、战术库、市场模块、CN/US 场景、工程规范、P1–P7 路线） |
| 实验运行手册 | `EXPERIMENT_RUNBOOK.md`（四层结构、十步日循环、数据流 A1–A8、配置契约、里程碑门 M0–M5、逐步命令、8 家机构决策点） |
| 原始数据（不上传） | 小红书 `xhs_data.db` / `xhs_images.db`（22 家机构 9,677 条）、股吧 `guba_posts/`（922 文件 / 742,054 行）、美国 EDGAR 497AD 3,499 张 / 官网 448 张 / Google Ads 400 张 |

### GitHub 仓库里有什么（`flowmirror_v7/`，约 17 MB，MIT，pytest 24/24 绿）
```
config/schemas/{run,scenario,persona,event,fund_meta}.schema.json   运行/场景/人设/事件/基金元数据的 JSON Schema（事件形状与 srs_analysis.py 一致）
config/engine_defaults.yaml · config/api_example.yaml               引擎默认参数；API 密钥模板（真密钥 .gitignore）
scenarios/cn_xhs_2025q4/scenario.yaml                              = WWW 论文的预注册场景（4 家机构、CSRC C×R、2025Q4、股吧外生信号）
scenarios/us_2025/scenario.yaml                                    美国场景骨架（Reg BI 披露型监管；数据管道 P6 路线图）
runs/mock_10x3.json · runs/smoke_20x10.json                        M0 干跑 / M1 冒烟配置（路径指向 data/）
flowmirror/channels/feed.py            三源推荐器、热度公式、评论气候(加权)、前 3 热评、agent 级图片臂、随机排序臂   ✅ 自检 36/36
flowmirror/regulator/cn_cxr.py         C×R 结账（与 v5 逐项一致的穷举测试）；none.py 提供 suitability-off 下的反事实 oc_cf ✅
flowmirror/population/sampler.py       400 人等比抽样（复现冻结队列 sha ddae7d79…）✅
flowmirror/config/{loader,validate}.py · flowmirror/cli.py         `flowmirror validate <cfg>`、`flowmirror tree`
flowmirror/io/{jsonl,hashing,backups}.py
flowmirror/agents/{prompt,runtime}.py · flowmirror/engine/{world,loop}.py   ✅ 落盘、自检、M0 通过；P3b 已接入三臂模态 / 费率 / dec 计数
flowmirror/agents/null_policy.py       规则型空模拟器（Odean 处置 + Sirri–Tufano 追涨 + 人口均值率；agent_policy: null，零 API）✅
flowmirror/analysis/{common,modality}.py   模态实验分析：agent 级 bootstrap + 种子级 t 区间 + 三分判定（单种子只报描述）✅
flowmirror/core/{types,protocols}.py · platform/ · society/ · engine/gm.py · agents/perceive/   五层立面（docs/LAYERS.md）✅
data_pipeline/cn/caption_frozen.py     TC 臂冻结描述器（提示哈希键、断点续跑、泄漏审计）✅ 脚本就位，描述尚未批量生成
runs/mock_10x3_3arm.json · runs/mock_10x3_null.json                三臂（费率开）与空模拟器的 M0 配置 ✅
data/population/  persona_grid_v3.json（36 格）· population_10k_v3.json · agents_seed2027.json（400 人）
data/creatives/cn/content_pool_v1_masked.jsonl（200 条，**去图片路径**，含遮蔽后文案/OCR）· tables_frozen_meta.json
data/attention/   guba_signal_v1.json（周度聚合，**去帖子 id**）· elasticity_real_v1.json
data/flows/       flow_panel_v2.json（1,557 只季度申赎）· stylized_facts_real.json
data/funds/       nav_codes.txt（399 码）+ README（净值缓存本身不上传，用户自行拉取）
data/MANIFEST.sha256 · data/MANIFEST.json                           11 个文件的哈希与变换说明
data_pipeline/cn/import_from_research.py                           从研究仓库导入 L1 层并脱敏
tests/unit/  test_schemas · test_io · test_feed_migration · test_cn_cxr · test_sampler
docs/  PERSONA.md · SCENARIOS.md · RUNBOOK.md · ARCHITECTURE.md · DATA.md
script/run.sh（校验配置；引擎接线后即可跑）· .github/workflows/ci.yml
```
**数据分层规则**：L0 原始爬取与图片永不进仓库；L1 派生小文件（上表）进仓库并带哈希；L2 大件（预缩放图片、LLM 缓存、20 次主网格事件日志）计划放 Hugging Face / Zenodo；密钥忽略。

---

## 2. 沙盒现在是什么样子（可以写进论文 §3 的部分）

**一句话**：一个基金营销信息流的模拟社会。400 个由视觉大模型扮演的散户（按 84,807 人问卷的 36 格人口比例抽出，报告等级只有 C2/C3/C4）每个交易日刷 6 条真实基金公司的真实小红书笔记（含图），可以点赞/收藏/关注/留一句评论/申购或赎回；申购要过 CSRC 式 C×R 适当性结账；基金按 2025Q4 真实日净值涨跌；股吧真实讨论量作为外生注意力信号。

### 2.1 四层
| 层 | 内容 | 状态 |
|---|---|---|
| 机构 | 4 家真实公司 × 50 条真实笔记（正文 + OCR + 五维标签，时点表述已遮蔽），每交易日各发 2 条，按**实测意图组合**抽（广发 I2 20% / 鹏华 34% / 国联 38% / 汇添富 48%） | ✅ 数据冻结（pool_sha 64a683ae…）；**扩到 8 家进行中**：华夏 277 / 国泰 121 / 富国 87 / 平安 67 / 天弘 50 条合格，其中华夏、平安只有封面图；新四家提到的基金多无净值 |
| 平台 | 三源推荐器（关注 2 / 匹配 2 / 热门 2 = K=6）；热度 h = log10(L+2S+3C+1)/(age+1)^1.8；评论只显示 t−1 的前 3 条 + 气候标签（按 strat_weight 加权）；图片臂 **agent 级**（每人整场固定看图/不看图）；随机排序臂作反 A1 对照 | ✅ `feed.py` |
| 投资者 | 400 个活 LLM agent（glm-4.6v）：人设卡 + 2 维 agent 可写信念（market_view/risk_mood）+ 6 维引擎数值（trust/attention/ref_point/gain_loss/experience/trend_read）+ 5 日记忆 + 每 5 日反思；输出结构化 JSON（reads/engage/comments{四类立场}/trade{一日一笔，百分比}/org_affinity_delta/mood/reason）；六条信息通道 feed / experience / trend / social / news / direct，论文场景只开 feed+experience+social+news | ✅ `agents/prompt.py`（自检 57/57）、`agents/runtime.py`（5/5） |
| 监管与市场 | C×R 结账只管申购、赎回不拦；**适当性开/关是运行级随机化因子**（关时仍记录反事实 `oc_cf`）；真实日净值（R 级由申赎面板的类型字段分类）；定投；财富守恒；费率（申购 0.12% / 赎回 0.5%，决策 #12，待实现） | ✅ `regulator/`、`engine/world.py`（12/12）、`engine/loop.py`（**M0 通过**：10×3 与 40×5 mock，不变量 PASS，重放逐字节相同，`srs_analysis.py` 可算） |

### 2.2 实验设计（不变，= 预注册）
2×2 主设计：适当性 {on, off} × 策略组合 {推品重 = 国联+汇添富实测组合, 投教重 = 广发+鹏华}，每格 5 种子（2027–2031），共 20 次运行；参考格 {on, measured} 上加随机排序臂 3 种子、记忆关/社交关 ≤3 种子、底座替换 2 种子、气候加权关 1 种子。配置由 `make_grid_configs.py` 生成（37 个）。主终点 Δ1 = SRS(熟悉≥1) − SRS(0)，SESOI +0.05，**种子级 t 区间（df 4）**；第二终点 r_off − r_on；C4 = 策略因子主效应（v1.4）。

### 2.3 里程碑与门
M0 干跑（10 人 × 3 日 mock；不变量全过、两次日志逐字节相同）→ M1 冒烟（20 × 10 真模型；解析 ≥95%、失败 ≤2%）→ M2 试点（参考格 400 × 60）→ M3 主网格 20 次 → M4 对照 → M5 分析。**目前停在 M0 之前**：引擎四模块落盘后 `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 3 --agents 10 --replay-check`。

---

## 3. 进行中 / 未完成（论文里写成路线图或 DATA_NEEDED，不能写成已完成）

| 项 | 状态 | 影响论文 |
|---|---|---|
| 引擎四模块 + P3b（三臂/费率/空模拟器/分析层/立面） | ✅ 全部落盘并验收（R31：pytest 72、三份日志 schema 干净、三配置重放一致）；M1 真模型冒烟待 `config/api.yaml`；成本数字仍为 `GAP_S4_COST` | §3.5 成本表、全部 §5–§6 数字 |
| **沙盒细节 14 条已由业主逐条拍板** | ✅ `specs/DECISIONS_2026-09-05_SANDBOX.md`（每条附顶会做法与选择）；**PREREG v1.5 草案** `specs/PREREG_v1.5_DRAFT.md`（多模态升为第二主线：三臂 T/TC/TV，agent 级 + 整场级；主终点 = 互动率/评论率/好感增量，交易为次；SESOI h=0.10；费率；空模拟器规则；8 机构分组规则；预算 ≈45 次运行）——**未冻结**，业主复核后改名 v1.5 | §3 全节措辞；§4 设计；附录 C 升为 §6 的第二结果节 |
| 内部规范 | ✅ `SANDBOX_INTERNAL_SPEC_v1.md`（每因素一卡：定义/量化/更新/出处/配置/日志；§J 调研回填与不采纳建议）；`PROJECT_ARCHITECTURE_v7.1.md`（五层递进：原子→组件→agent→GM 环境→社会；CoALA 记忆 + Concordia GM + 中介社会层；BDI 只留信念向量） | §3 系统描述的权威来源 |
| 8 家机构内容池 v2 | ⏳ 新四家已导出 602 条候选（`sim/manifest_ext_20260905.jsonl`），待 GLM 打标/OCR → `content_pool_build.py --orgs … --out sim/content_pool_v2` → 时点清洗 → PREREG v1.5（因子二分组规则：按实测 I2 占比前 4 / 后 4） | Table 2 → 8 行；§3.2 文字；Fig 2 机构带；**所有语料数字用 LaTeX 宏** |
| 新机构基金净值 | ⏳ 61 只待且慢 MCP（业主会话） | 落地率、留出覆盖 |
| Table 3 真实幅度列 | ⛔ 复权净值待重估；只取符号。**新增真实结果**：股吧讨论量→季度净申购弹性 β = −0.015 (SE 0.015)，申购 +0.009、赎回 +0.013，均不显著，领先安慰剂 −0.035 (t −2.2)（2023Q1–2025Q4、752 只、8,231 基金季度、双向 FE，`sim/elasticity_real_v1.json`）→ v1.4 §C2 预期 β>0 **未获支持**，按预注册如实报告 | §5.1 注意力买入行、`GAP_S5_ELAST` |
| 股吧立场标注（v1.4 §C1） | ⏳ 脚本就位（标题级分类，`_meta.text_field="title"`），未发 GLM 调用 | `GAP_S5_STANCE_KAPPA` 分类器侧 |
| Fig 1 / Fig 2 | 首版 SVG 有缺陷，Codex 17:17 修（清单 `paper/figs/FIG_REVIEW_2026-09-05.md`） | Fig 1、Fig 2 |
| 美国场景 | 路线图 P6；仅 scenario 骨架与已爬素材 | §8 结论"架构还能做什么"一句 |
| GitHub 仓库 | 私有；无 CI 运行记录；未打 tag | 双盲：论文只写 anonymous.4open.science / supplementary，不写仓库名 |

---

## 4. 论文可以怎么写这个沙盒（措辞边界）

- **可写**：四层结构、十步日循环、所有展示信号 ≤ t−1、三源推荐器与热度公式、agent 级图片臂、C×R 结账语义与 on/off 因子、`oc_cf` 反事实记录、sha256 内容寻址缓存与零调用重放、不变量 (a)–(l)、JSON Schema 校验的配置与事件、L0–L3 数据分层与脱敏发布、`MANIFEST.sha256`。
- **可写但须标 DATA_NEEDED**：调用数 / token / 时长 / 缓存命中率 / 决策失败率（`GAP_S4_COST`）、任何 SRS / Δ1 / r_off−r_on / 验证层数字。
- **不可写**：引擎"已运行"、任何结果数字、"validated"、"predicts investors"、"first"、机构层推断（n=4）、8 家机构已入池、美国场景已实现、trend/direct 通道进入主网格。
- **贡献四**标题按 v1.2 §H："An out-of-sample, externally disciplined comparison against real 2025Q4 fund flows"。
- **新增可披露事实**：strat_weight 范围 0.26–1.64（v1.3 §F1）；留出覆盖 24 只可落地基金（§F2）；弹性结果为零/不定（§3 表）。

---

## 5. 论文会话接下来该做什么（与本沙盒相关的部分）

1. 用 `paper/HANDOVER_PAPER_2026-09-05.md` §1 的阅读顺序开工；§3 系统节直接按本文件 §2 写，所有引擎数字留槽。
2. `paper/v6/macros.tex`：语料数字（机构数、笔记数、图数、I2 份额、落地数、基金数、股吧数字）全部做成宏，4→8 家只改宏。
3. Codex R7（配额恢复后）审 `PAPER_PLAN.md` + 预注册链 + 本文件；结论若改终点 → PREREG v1.5（写前 `ls specs/PREREG_v1.*.md` 取最大号 +1）。
4. 引擎落盘 / M0 通过 / M1 吞吐实测 由实验会话在 `METHODS_LEDGER.md` 记 R29+，论文会话据此更新 §3.5 与 `GAP_S4_COST`。

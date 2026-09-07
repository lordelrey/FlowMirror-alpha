# FlowMirror v7 沙盒全面审计与修复方案（2026-09-07）

> 交付对象：负责实现的其他模型/会话。本文件自足，不依赖任何对话上下文。
> 执行前请先读 §三（发卡约束）与 §4.0（契约），再按 §九 的顺序推进。
> 只出方案、不动代码是本次审查的要求；所有断言均已在源码逐条复核。

## Context

业主 2026-09-07 要求：全面审查 `D:\Desktop\ABM paper\flowmirror_v7`，重点三问——**参数与数学模型是否真的可用**、**能否支持真实数据**、**图片为何仍未进入模拟演变**；其次是可视化难看难懂，参考 MiroFish 重做。优先级：功能模拟 > 可视化。本方案由三路只读审计（引擎数学、真实数据路径、界面）加两路设计得出，所有关键断言已在源码逐条复核。

**一句话诊断**：引擎的骨架（事件日志、重放、C×R 结账、臂随机化、财富恒等式、空模拟器、决策解析）是扎实的；但**大量机制在文档与 schema 里"存在"、在代码里断线**——参数读不到、字段名对不上、验收配置测的是假场景、分析层验证的是虚构日志格式。多模态主线（TV 臂附图）从未运转过。

**根因模式**（贯穿全部缺陷，执行时要针对性防复发）：
1. 配置键与代码读取的键名不一致（`mock_options` vs `mock_force_c2_r4`、`fam_decay` 不在 schema、`qdii_blocked` 不在 schema）。
2. 数据文件字段与代码期望不一致（股吧 `z_abnormal` vs `bull_ratio`；评论 `p/i/fam` vs `post_id/agent_id/fam_level`——后者已修）。
3. 自检与单元测试只喂**自己理想化**的数据形状，从不喂引擎/数据管道**真实产出**的形状（模态分析的 `_is_i2`、气候函数的旧键名）。
4. 文档先于实现（RUNBOOK 详述的 `images_root` 解析、`img_idx`、`m_tv_arm_carries_images` 不变量均不存在）。

---

## 一、审计结论（已在源码复核，可直接作为修复依据）

### 1A. 引擎机制缺陷（按对实验有效性的影响排序）

| # | 缺陷 | 位置 | 与拍板决策的关系 |
|---|---|---|---|
| E1 | **股吧信号完全断线**：`guba_seed_label` 读 `bull_ratio/bull/bear`，数据文件只有 `n_posts/reply_n/read_n/z_abnormal/ratio_vs_baseline`（`_meta` 自述 "no stance is computed here"），永远返回 None；`z_abnormal` 等**无任何代码读取**；新闻通道 `render_news` 在真实数据下从未输出。潜伏崩溃：`_agent_view` 存字符串而 `render_news` 期望 dict | `world.py:596-619`、`loop.py:~900-922`、`prompt.py:221-231` | DECISIONS #5 要求 `att_t = λ·att_{t−1} + exposure + β·z_guba`，λ=0.8；代码无 β 项、λ 复用 fam_decay |
| E2 | **模态分析 I2 判定结构性失效**：`_is_i2` 检查 `imp.source=="I2"` 或 id 前缀 "I2"，引擎从不产生这两种（source∈{follow,fit,trending,spill,random}，id 为数字）；I2 标签在 `post.intent_group` 而 `_aggregate` 不读 post 行 → **点击率与申购转化（论文次要终点）在真实日志上永远算不出**；自检用手造的错误格式，故测试全绿 | `analysis/modality.py:99-104, 629-691` | DECISIONS #8 的次要终点无法测量 |
| E3 | 气候多数阈值：代码 1/3，决策 1/6 | `feed.py:195-197` | 与 DECISIONS #6 相悖，严一倍 |
| E4 | 熟悉度衰减默认 0.1，决策 0.2；`fam_decay` 不在 schema，配置即被拒；注意力无独立 λ、无 β·z_guba；阈值 1.0 与 adstock 0.9 为裸字面量 | `loop.py:763, 1044-1058`、schema | 与 DECISIONS #5 相悖 |
| E5 | **`mock_options` 从未被读**：`_make_llm` 读顶层 `mock_force_c2_r4/mock_malformed_rate`，schema/默认值/全部 demo 配置写的是嵌套 `mock_options.{force_c2_r4_click,malformed_rate}` → 所有验收配置里 `force_c2_r4_click: true` 静默无效，**验收运行并未触发它声称要测的适当性确认场景** | `loop.py:310-315` | 验收在测假的东西 |
| E6 | `qdii_blocked/qdii` 不在 schema，QDII 停购永久死代码 | `loop.py:_qdii_blocked_set` | — |
| E7 | 定投只能加仓 `min(inv.hold)`，零持仓者永远无法开始定投（最小档 N_FUNDS_K=1 时 50% 的人）；2% 与最低 100 元为字面量 | `loop.py:1021-1039, 651` | 文档未披露 |
| E8 | 费率默认 0/0，决策 0.12%/0.5% | `engine_defaults.yaml` | 与 DECISIONS #12 相悖 |
| E9 | `TEMP=0.3` 硬编码、不可配、**不入 run_meta**；`llm.max_attempts`（宏重试 1 次开关）与内部 `MAX_ATTEMPTS=5`（物理 HTTP 重试）同名异义 | `runtime.py:43-47` | 未记录的实验参数 |
| E10 | 反思产出的 `beliefs` 存入 `inv.beliefs` 后**从不渲染进任何后续提示**（只写状态） | `prompt.py:590-603`、`loop.py` | 声明的信念维度无行为效应 |
| E11 | 开局持仓成本按回看天数 `randint(60,250)` 抽样，真实净值下**亏损持仓仅 4.9%、中位 +39%** → 情绪表达单边（mood 恒 4、零看空）；合成净值下 48.9% | `world.py:init_investors:451-455` | 需 `initial_pnl` 目标分布模式 + 环境效价检查 |
| E12 | `decision_failure_halt` 把 HTTP 错误/异常与模型坏 JSON **计成同一种失败**，坏密钥伪装成"模型不稳定"；缓存过的失败永久重放 | `loop.py:979-1019`、`runtime.py` | 活跑诊断失真 |
| E13 | 裸常量：初始配置比例 0.3–0.8、回看 60–250、`fit()` 的 ±0.15/±0.25、DCA 2%、最低 100 元、`N_FUNDS_K` 表 | 各处 | 审稿人无法从配置知道运行用了什么值 |

**已验证为健全、不要重做的部分**：`rank_feed`（三源、确定性、配置驱动）、`hot_score`（t−1 滞后正确）、`assign_agent_arms` 与 `check_arm_balance`（分层区组随机化，自检覆盖 24 tag × 2/3 臂）、C×R 结账语义与反事实、财富恒等式、`parse_decision`/`apply_decision` 的可行性分工、空模拟器（正则直接对着 `prompt.py` 真实渲染串写，自检回灌 `extract_decision`）、`seed_t_interval`/bootstrap/三分判定。

### 1B. 真实数据路径逐条定性

| 路径 | 真实数据下 | 结论 |
|---|---|---|
| A 图片 | **坏** | `_feed_card` 读 `note.image_path/image/cover`，池里只有 `image_ids`（0-based，部分为 JSON 字符串）与 `image_sha256`；`images_root/image_pick` 在 schema 与默认值里，**无任何代码读取**；RUNBOOK 描述的 `img_idx`、`run_meta.images`、`m_tv_arm_carries_images` 均不存在。TV 臂在任何数据上与 T 逐字节相同 |
| B 真实净值 | 好 | 加载、跳过 `_meta`、合成警告、递延基金（<63 个净值且被素材引用 → active_from=首日+21）、无前视。注意：无引用且 <63 个净值的基金被**静默丢弃** |
| C 真实 LLM | 部分 | 凭据链（api.yaml → env → legacy 文件）与 RUNBOOK/模板一致；缺陷见 E9、E12 |
| D 股吧 | 部分 | 见 E1；`_meta.stance_jobs_stripped: true` |
| E TC 描述 | 部分 | `caption_frozen.py` 已修好但**从未跑过**，`data/` 无 captioned 池；TC 臂只带 OCR，缺一半刺激且不记降级标记 |
| F 导出器/界面 | 坏 | `flowmirror export-bundle` 被 `web/app.js` 引用但**不存在**；"未附带图片"警示条读 `run_meta.images`（引擎从不写）恒显示 |
| G 配置漂移 | 坏 | E5、E6 |
| H 测试 | — | CI 只跑 mock + 合成净值 + 无图；四条真实路径零自动覆盖 |

### 1C. 界面问题（现有 `web/`）
无运行选择器（写死 `demo_three-arm`）；无法从界面发起运行；臂与适当性结账无任何页内解释；启用的市场 tab 无点击处理；头部警示条堆叠无层级；canvas 固定 1180×700、点半径固定 4px，400 人会重叠、手机上文字缩成糊；检视栏只显示"今天"且机器结果与 LLM 理由同一样式；模态面板显示不了帖子正文和图片（事件日志没有）；`agent_policy`（LLM vs 规则）未显示；两臂运行留空第三列；60 日运行一次拉全量日志。

---

## 二、业主已拍板（2026-09-07）

1. **股吧通道**：同时做——先用已有 `z_abnormal/ratio_vs_baseline` 接注意力项与新闻行，**并**跑 GLM 立场标注补出 `bull_ratio` 让多空冷启动按原设计生效。
2. **界面范围**：静态回放（GitHub Pages 零安装读样本包）+ 本地小服务（stdlib，列运行、发起运行、流式日志、本机显示真图）。密钥永不经浏览器。
3. **"采访 agent"**：这轮不做，列入路线图；先把结构化档案（决策、理由、评论、结账、持仓、提示原文）做扎实。检视页可做**仅检索**式查询（只引用已记录内容，无匹配即"未找到"）。

---

## 三、执行模型必须遵守的发卡约束（否则会重蹈本轮覆辙）

- **所有代码由 GLM 5.3 写**，主模型只出卡、审、验收。运行器：`scratchpad/glm_run.py`（已加校验：响应无 `### FILE:`/`### PATCH:` 头即判失败重试）；落盘：`scratchpad/apply_patch.py`（替换块）与 `land_multi.py`（整文件）。
- **服务端 902 秒硬关连接**。大文件（`loop.py`~1500 行、`world.py`~1500、`feed.py`~1200、`prompt.py`~850、`runtime.py`~800）**只能用替换块**（`### PATCH: 路径` + `<<<<<<< OLD / ======= / >>>>>>> NEW`，OLD 必须在文件中**恰好出现一次**）。新文件用 `### FILE:` 整文件，**单文件 ≤400 行**。单卡输出 ≤60 KB。
- **卡头必须带输出纪律**："第一行必须是 `### FILE:` 或 `### PATCH:`，不要复述任务、不要在回复里推理"——推理型模型曾两次返回 220 KB 纯推理零代码。
- **同一文件永不并行**；并行卡之间的接口（函数签名、配置键名、事件字段名、DOM id）必须**先写成契约文本贴进每张卡**，禁止互猜（本轮三次互猜 API 失败）。发卡前用 `inspect.signature` 导出真实签名。
- **每张卡以验收命令结尾**。标准验收集：`python -m pytest -q`；`python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 5 --agents 40 --out runs/out/mock_40x5 --replay-check`；同样跑 `runs/mock_10x3_3arm.json`（`--agents 60`）与 `runs/mock_10x3_null.json`（无 `--mock`）。三者必须 `invariants=PASS`、`replay-check identical=True`。机制有意改变时哈希会变，**必须在卡的 bullets 里声明**。
- **CI 是干净克隆**（无真实净值缓存、无密钥、无图）：测试夹具一律从 `tests/conftest.py` 的 `build_demo_cfg`（基底 `runs/demo_two_arm.json`）构造，**禁止**从 `runs/mock_10x3*.json` 构造；依赖不发布数据的配置须列入 `tests/unit/test_configs_are_self_contained.py` 白名单并注明原因。
- **决策记录是契约**：`docs/DECISIONS_2026-09-05_SANDBOX.md` 14 条。代码与之相悖时以决策为准；决策在数学上不可满足时，方案须写明改决策文本（本轮已有一例：格内臂平衡容差 ≤0.05 对 n=2 不可满足，改为 `|份额−1/k| ≤ 1/(2·n_cell)`）。
- **图片永不入库**；`images_root` 只在本地配置；数据库 `xhs_data.db`/`xhs_images.db` 只读（`mode=ro`）。

---

## 四、功能修复计划（优先级最高；四个阶段，逐卡列文件、改动、契约、测试、验收）

### 4.0 两条串行链与并行规则（发卡前必读）
- **`loop.py` 链**（同一文件，严格串行，每张卡的 OLD 锚点须对着**上一张落盘后**的文件重新引用）：`L1 → L2 → L3 → L4 → L5 → L6`。
- **`world.py` 链**（同上）：`W1 → W2 → W3(=W4 定投槽位) → W5 → W6(环境效价)`。
- 其余文件（`runtime.py`、`feed.py`、`prompt.py`、`modality.py`、两份 schema、`engine_defaults.yaml`）每阶段最多一张卡，可与两条链并行。
- **契约先行**（并行卡两边都要原文贴入）：
  - `view["guba"]`：`{code: {"name": str, "mult": float, "bull_ratio": float|None}}`，`mult` = 信号行 `ratio_vs_baseline`（缺则 1.0）；`bull_ratio` 立场标注前为 None（`render_news` 已把 None 当 0.5，prompt.py 不用改）。
  - `state["images"]`：`{"root": cfg.get("images_root"), "policy": cfg.get("image_pick","first"), "attached": int, "missing": int, "sha_mismatch": int}`；`write_reports` 原样写入 `run_meta["images"]`。
  - `rec["failure_kind"]`：`"transport"`（call_glm 的 cls ∈ {exception, http_error, empty_response, reasoning_salvage_rejected}）| `"model"`（有响应但 schema/JSON 失败）| `None`；写入 `dec.failure_kind`。
  - 新配置键（schema 卡 + defaults 卡先落）：`dynamics{fam_decay:0.2, fam_threshold:1.0, lambda_trust:0.9, lambda_attention:0.8, beta_guba:0.0}`（β=0 是业主裁定，见 §五 #3——管道接通、系数为零）；`dca{pct:0.02, min_ticket:100.0}`；`initial_pnl{mode:"lookback", lookback_days_min:60, lookback_days_max:250, share_at_loss:0.05, quantiles:null, tolerance:0.02}`（默认 `lookback` 必须逐字节复现今天行为；`target` 只在研究级配置里启用，见 §五 #13）；`market{benchmark_path:null, benchmark_label:null}`；`llm.temperature:0.3`；`llm.max_provider_attempts:5`；`feed.climate_margin:0.1667`；`qdii_blocked{date_iso:{codes:[...]}}`。**除有意改变的（fam_decay、climate_margin、fees）外，默认值必须等于今天的硬编码值**，保证省略键的配置逐字节不变。

### 4.1 阶段一 · 解封"真正多模态、真实数据"的活跑

| 卡 | 文件（方式） | 改动 | 测试（CI 可跑，基于 `demo_two_arm.json` 夹具） |
|---|---|---|---|
| **L1** | `loop.py`（PATCH） | ① `_make_llm` 改读 `(cfg.get("mock_options") or {}).get("force_c2_r4_click")/.get("malformed_rate")`；② `_agent_view` 的 `guba` 改为按契约的 dict-of-dicts（新 helper `_guba_line(W, code, wk_prev)`），日循环里 `guba_view[code]` 改存**原始信号行**而非标签；③ 补 `holdings_1d`（需在日循环保留 `prev_navday`）；`index_5d` 在 `market.benchmark_path` 为 null 时留空，配置了则按 §五"基准指数数据"第 2 条算 `(v[t−1]/v[t−6]−1)`，缺日留空不插值；④ `_agent_view` 加 `"beliefs": inv.beliefs`；⑤ live `_call` 包装加 `kw.setdefault("temperature"/"max_provider_attempts", ...)` | `test_mock_options_wiring.py`：`force_c2_r4_click=True` 跑 3 日 10 人，断言出现 confirm_signed/declined；`test_agent_view_guba_shape.py`：假 World 带股吧行，断言 `view["guba"][code]` 为 dict 且 `render_news(view)` 不抛并非空 |
| **W1** | `world.py`（PATCH） | `guba_seed_label` 契约不变；`load_world` 加一次性打印"N 个基金周无 bull_ratio（立场标注未跑），气候种子回退到评论气候" | `test_world_guba.py`：真实字段形状（无 bull_ratio）→ 返回 None 且不抛，打印触发 |
| **L2** | `loop.py`（PATCH，L1 后） | 新 helper `_resolve_tv_image(note, cfg, inv_rng) → (path, idx, status)`：解析 `image_ids/image_sha256`（list 或 JSON 字符串，含单引号 repr），`first`→0、`random`→`inv_rng.randrange`，`os.path.join(images_root, id)` 后**校验 sha256_file**，匹配才附；未匹配记 `missing/sha_mismatch`。只对 arm=="TV" 执行。在 `logd("imp")` 处加 `img_idx`。`_feed_card` 的 `image_sha` 改为真实文件摘要。计数 `S["images_attached/missing/sha_mismatch"]`；`state["images"]` 按契约。运行开始打印 `[world] images: <n> resolvable under <root>` 或 RUNBOOK 已写的 WARNING 行 | `test_images_wiring.py`：临时 `images_root` 放两个假 JPEG 字节，池行植入匹配 sha；`modality_level:"run"`, `modality_run_arm:"TV"`；断言 `run_meta.images.attached>0`、有非空 `img_idx`；篡改字节→`sha_mismatch>0` 且 `--dump-prompt first` 出现 `image_sha_mismatch` 注记。`_self_test` 加 images_root 为 null 时 `images` 全零且 TV 与 T 逐字节相同 |
| **W2** | `world.py`（PATCH，W1 后） | `image_pool_summary(pool, images_root)`；`INVARIANTS["m_tv_arm_carries_images"]`——**告警形态，永远 pass=True，绝不触发退出码 4**（全局规则：门禁只放在不可逆/跨系统/安全/发布边界，模拟运行不在其中），三个分支都在 `reason` 里说清楚并携带 attached/missing/sha_mismatch；`write_reports` 写 `run_meta["images"]` | `test_images_invariant.py`：手造 state，三个分支都 pass 且 reason 正确，attached 计数如实出现；「图片有没有进来」由 `run_meta.images.attached` 这个数回答，不由运行失败回答 |
| **RT1** | `runtime.py`（PATCH，并行） | `decide()/reflect()`：传输类 cls 直接作 status（不再把空串重新喂 `extract_decision` 制造假的 schema_invalid）；`rec["failure_kind"]` 按契约 | `test_call_glm_retry.py` 加两例：stub 恒 http_error → `"transport"`；200 但不可解析 → `"model"` |
| **L3** | `loop.py`（PATCH，L2 与 RT1 后） | `S["decision_failures_"+kind]` 并行计数；`logd("dec", failure_kind=...)`；halt **阈值与判定不变**，只在 `_finish` 明细里加两类计数 | `test_decision_failure_kind.py`：`malformed_rate:1.0` → 全 `"model"`；monkeypatch mock 抛异常 → `"transport"` |
| **SCHEMA-1b** | `event.schema.json` | `dec.failure_kind` 可选 `["string","null"]` | 现有 schema 校验测试 |

并行图：{L1, W1} 并行 → {L2 ∥ W2}（契约已钉）→ RT1 全程并行 → L3 在 L2、RT1 之后。

**阶段一验收**：标准验收集 + 各卡新测试；本地有图片库的机器上 `python -m flowmirror.engine.loop runs/demo_three_arm_images.json --mock --days 5 --agents 60 --out runs/out/imgs_check --replay-check` 报 `images.attached>0`。

### 4.2 阶段二 · 让预注册实验可测量

| 卡 | 文件 | 改动 | 测试 |
|---|---|---|---|
| **MOD1** | `analysis/modality.py`（PATCH，独立） | `_aggregate` 从 `ev["post"]` 按 `ig=="I2"` 建 `i2_posts`；`_is_i2` 降为无 post 行时的 legacy 回退；**修 `_synthetic_run` 夹具**同时产出 `post` 行（ig I2/nonI2），让自检走真实路径 | 新增 `test_i2_detection_uses_post_ig_not_source_or_prefix`：数字 id + `source="random"` + post.ig="I2" → click_rate/subscribe_conversion 非 None；端到端跑 demo_three_arm 后 `analyze` 至少一臂 click_rate 非空 |
| **FEED1** | `feed.py`（PATCH，独立） | `climate_for(..., margin=1/6)`；两处调用传 `cfg["feed"]["climate_margin"]` | 新增跨阈值用例：3 多/2 空/1 观望 → d=1/6，1/3 下 mixed、1/6 下 bullish_majority；参数化两档 |
| **L4** | `loop.py`（PATCH，L3 后） | 读 `dynamics`：注意力 `att = λ_att·att_prev`（每日）+ 曝光处 +1（已有）+ `β·z_guba`（`world.guba[code][wk_prev]["z_abnormal"]`，None→0）；熟悉度衰减用 `fam_decay`；`aff *= lambda_trust`；等级阈值用 `fam_threshold` | `test_dynamics_formula.py`：两日夹具验证注意力与熟悉度按不同常数衰减（证明耦合已解），`β·z` 项按已知 z 生效 |
| **W3** | `world.py`（PATCH，W2 后） | `Inv.__slots__` 加 `dca_target`；`init_investors` 中 `dca=True 且 hold 为空` 时 `dca_target = inv.rng.choice(base_codes)` | — |
| **L5** | `loop.py`（PATCH，L4 后） | 定投块去掉 `and inv.hold`；`code = min(inv.hold) if inv.hold else inv.dca_target`；`0.02`/`100.0` 改读 `cfg["dca"]` | `test_dca_opens_position.py`：强制某 agent dca=True、hold={}，到月初断言 `act.kind=="dca"` 且 fund==dca_target |
| **RT2** | `runtime.py`（PATCH，独立） | `_llm_cfg` 返回 `temperature`；cache key 用它；`call_glm(..., temperature=None, max_provider_attempts=None)` | 加两例：自定义 `max_provider_attempts=2` 恰停 2 次；payload temperature 反映配置 |
| **PROMPT1** | `prompt.py`（PATCH，独立） | `render_belief` 加一行"你最近的几条判断：…"（≤3 条），依赖 L1 的 `view["beliefs"]` | 新增用例断言两条 belief 出现在 system+user 文本 |
| **E8** | `engine_defaults.yaml` | `fees: {subscribe_rate: 0.0012, redeem_rate: 0.005}` | `test_defaults_match_decisions.py`：断言 fees 与 `dynamics.fam_decay==0.2` |
| **SCHEMA-2..5, DEFAULTS-1** | 两份配置文件 | 加 `feed.climate_margin`、`dynamics`、`dca`、`llm.temperature`、`llm.max_provider_attempts` | schema 校验测试 |
| **EXP1** | 新 `flowmirror/analysis/export_bundle.py`（FILE，≤400 行）+ `cli.py`（PATCH 加 `export-bundle` 子命令） | 展示包导出器，**demo 规模**（只导出该运行出现过的帖子，单臂文本 ≤1200 字，总包 ≤4 MB，超则先丢 `imp/st` 行并记录 `truncation`）。四个文件：`bundle.json`（run_meta 全部键 + `images` + `invariants{summary,entries}` + `per_day` 聚合 + `truncation`）、`posts.json`（每帖 `text.T/TC/TV` 由 `prompt.render_card` 渲染，`image.sha` 但**永不含像素**，`seen_by` 各臂触达/互动率）、`agents.json`（`cell/risk_class/arm/cash0/hold0/by_day{mood,n_read,n_like,n_save,n_follow,n_comment,aff_sum,reason,status,comments,trades,checkouts}/familiarity`，人设摘要 ≤200 字）、`events.json`。`--anonymise-orgs` 可选。禁止绝对路径、密钥、图片字节 | `test_export_bundle.py`：四文件生成；无绝对路径与密钥样式字串；每帖三臂文本互异；预算生效时 `truncation.applied` 为真。**前置**：L2/W2（`images`、`hold0`）落地后再发，否则字段为空 |

并行图：{MOD1, FEED1, RT2, PROMPT1, E8, SCHEMA-*} 全并行；`L4 → L5` 与 `W3` 串行在各自链上；**EXP1 在 L2/W2 之后、与 L4/L5 并行**（不碰 loop/world）。上一轮 GLM 两次对该卡返回纯推理零代码：**卡头必须带输出纪律，且拆成"导出器模块"与"CLI 补丁"两张卡**。

### 4.3 阶段三 · 参数与配置卫生

| 卡 | 文件 | 改动 | 测试 |
|---|---|---|---|
| **L6 + SCHEMA-6** | `loop.py`、schema | `qdii_blocked` 进 schema（`{date_iso: {codes: [...]}}`）；配置了却无 QDII 基金时打印提示 | `test_qdii_blocked.py`：直接单测 `apply_decision` 对 QDII 基金在停购日 → `co.oc=="purchase_blocked"` |
| **W5** | `world.py`（PATCH，W3 后） | `initial_pnl`：`lookback` 模式逐字节保持今天行为（只是把 60/250 改读配置）；`target` 模式按 `share_at_loss` 两点混合抽目标盈亏，在回看窗内线性扫描最接近的成本日，超容差计 `initial_pnl_misses` 入 `run_meta.investors` | `test_initial_pnl.py`：lookback 同种子逐位复现；target 且 `share_at_loss:1.0` 在单调上涨序列上全部落入亏损区间 |
| **W6** | `world.py`（PATCH，可与 W5 合卡） | `INVARIANTS["m_env_valence_warning"]`：报告开局亏损持仓人数、bearish_majority 气候天数；**永远 pass=True 只作警示**，不触发退出码 4 | 并入 `test_invariants_correct.py` |
| **FEED2** | `feed.py`（PATCH，独立） | `fit()` 的 ±0.15/±0.25 改读 `feed.fit_band_narrow/fit_band_wide`（**发卡前先 `grep -n "0.15\|0.25" feed.py` 确认锚点**） | `test_feed_migration.py` 加默认值回归 + 自定义带位移 |
| — | `N_FUNDS_K`/`INVEST_SHARE_CASH` | **建议不动**：它们是从 engine_v5 逐字复用的冻结人口生成常量，与人口文件自身的档位标签绑定；可配置反而会让配置与人口文件脱钩 | — |

### 4.4 阶段四 · 加固

| 卡 | 内容 |
|---|---|
| **CI1** | 新 `tests/integration/test_real_data_smoke.py`（`pytest.mark.integration`，注册进 pyproject）：缺真实净值/密钥/图片库任一即**干净跳过**；齐全时以 `runs/mock_10x3.json --agents 20 --days 3` 无 `--mock` 跑一小段，断言 `decision_failures_transport==0` 且 `images.attached>0`。**明确不进 CI**，只本地/夜间跑 |
| **DOC1** | `docs/RUNBOOK.md`：新键说明、排错表、新增 §"已知会改哈希的修复"指向 §五 |

---

## 五、业主已裁定事项（2026-09-07 全部结清；执行时按"裁定"列照做，不要再按推荐列判断）

19 项全部有业主签字（1–14 于 09-07 上午，15–19 于当日审查后）。**执行模型不得改动本表任何一项**；发现数学上不可满足时按 §三 的规矩上报，不得自行改默认值。

| # | 事项 | 裁定（业主 2026-09-07） | 后果 |
|---|---|---|---|
| 1 | 气候阈值 1/3→1/6 | **1/6**，做成 `feed.climate_margin` 便于敏感性 | 有评论流量的运行哈希变 |
| 2 | `fam_decay` 0.1→0.2 | **0.2** 为默认 | 熟悉度等级时序变，哈希变 |
| 3 | 注意力 `β_guba` | **β=0**。管道照接（读 `z_abnormal`、乘 β、进 adstock），系数取 0，所以本轮注意力时序与今天逐字节相同；`dynamics.beta_guba` 默认写 0.0 并在 RUNBOOK 注明"管道已通、系数待主网格前由业主定值"。**不得写 0.1** | 无哈希变化；主网格前改一个配置值即可启用 |
| 4 | 股吧多空种子 | **双轨**：代码先修字段与形状，让未来 `bull_ratio` 零改动可用；同时跑立场标注（§六 第 2 步）。标注落地前 `guba_seed_label` 返回 None 是**正确行为**，论文/README 不得宣称"股吧情绪种子" | 代码修复本身不改哈希；数据落地后才改 |
| 5 | 新闻行 `mult` | 立即用 `ratio_vs_baseline`，`bull_ratio` 留 None | 新闻通道首次输出，哈希变 |
| 6 | `index_5d` | **接入真实基准序列**，见下方"基准指数数据"小节。业主指令为"用上证指数 000001，走且慢 MCP"；000001 在该 MCP 不可得，已按最近替代落盘 `510760 上证综指ETF` 单位净值，**必须以"代理"披露，不得称作指数** | 市场行首次出现大盘 5 日涨跌，哈希变 |
| 7 | 定投开仓 | 按设计修（初始化时确定性选 `dca_target`） | 有零持仓定投者的运行哈希变 |
| 8 | 费率默认 | **0.12% / 0.5%** | `act.fee` 非零，恒等式已含费项无需改 |
| 9 | 止损阈值语义 | **只拆诊断计数，阈值与判定一律不动**。`decision_failure_halt` 仍按总失败率 0.02 触发；只新增 `decision_failures_transport/_model` 两个计数。拆阈值属预注册级改动，待活跑数据后由业主签字 | 无行为变化 |
| 10 | `qdii_blocked` | **接通 schema，但不设值**。任何发布配置、任何测试夹具之外的配置都不得出现该键，直到取得真实停购日历（数据任务） | 无变化 |
| 11 | `beliefs` 渲染 | **只前馈进下一次决策提示**；不喂进下一次反思输入（后者是独立增强，本轮不做） | 首次反思后哈希变 |
| 12 | 预注册容差 | 格内臂平衡 `≤0.05` 对 n=2 不可满足，v1.5 冻结前改为 `\|份额−1/k\| ≤ 1/(2·n_cell)` | 文本订正 |
| 13 | `initial_pnl`（E11 环境效价） | **研究级配置用 `mode:"target"`（目标盈亏分布），三个 demo 配置保持 `mode:"lookback"` 不动**。即 W5 卡的 lookback 分支必须逐字节复现今天行为，target 分支只在新研究配置里启用 | demo 哈希不变；研究运行首次出现可控的亏损持仓比例 |
| 14 | `N_FUNDS_K` / `INVEST_SHARE_CASH` | **保持冻结、不入配置**。它们与人口文件自身的档位标签绑定，可配置会让配置与人口文件脱钩 | 无变化 |
| 15 | 定投每期的申购费 | **照收 `fees.subscribe_rate`**。定投每期本质就是一次申购，算法与 `apply_decision` 的 subscribe 分支完全一致：费用从票面里扣，units 与成本基按 amt−fee，现金按全额 amt 减少，`_bump_fees` 累计、进 `act.fee`、进 `S["fees_cny"]`、进财富恒等式 | 有定投成交的运行哈希变；四条 5 日验收运行跨不过月初，故未变 |
| 16 | 臂平衡容差以谁为准 | **预注册文本改成与代码一致**：整体 `\|份额−1/k\| ≤ 0.01`，格内 `max(1/(2·n_cell), max(m,k−m)/(k·n_cell))`。旧文本的格内 0.05 对 n_cell=2 不可满足（裁定 12），整体 0.03 比代码实际执行的 0.01 宽三倍。五个预注册种子实测偏差 0.0000，0.01 完全可满足 | 仅文本；代码不动 |
| 17 | `inv.attention` 没有读取者 | **给 `rank_feed` 加权重 `feed.w_att`，默认 0.0**。此前 `beta_guba`/`lambda_attention` 改动的是一个无人消费的序列，裁定 3 的"管道接通、改一个值即可启用"对排序器并不成立。默认 0 时所有运行逐字节不变，`tanh` 有界（同 `w_trust`） | 无（默认 0）；与 `beta_guba` 一同调高即端到端启用 |
| 18 | `initial_pnl.share_at_loss` 默认值 | **取消默认值**。旧的 0.05 恰是 target 模式要逃离的单边环境数值（真实净值下约 4.9% 开局亏损），设了 `mode:"target"` 却忘了写比例就会静默复现病灶。target 模式缺该键即报错并说明原因；lookback 从不读它 | 无；demo 全在 lookback 分支 |
| 19 | `modality_run_arm` 无条件默认 "TV" | **从 `engine_defaults.yaml` 抽掉**，仅当 `modality_level == "run"` 时在代码里默认为 `modality_arms[0]`，也仅在该层要求它属于 `modality_arms`。此前它进入每份合并配置，校验又不分层地要求它在 arms 里，于是只跑 T/TC 的参考格被直接拒绝，报错还指向用户写的 arms。非 run 层若显式设了值仍校验拼写，避免错别字长驻 run_meta | 仅元数据；事件日志不含它 |

### 基准指数数据（裁定 6 的落地）

业主指令"就用上证指数 000001，用我给的且慢 MCP 调一下就行"。实际调用结果：

- 且慢/盈米 MCP（`stargate.yingmi.com/mcp/v2`，`x-api-key`，JSON-RPC，服务器 `YM-Stargate 0.1.0`，69 个工具）的 `GetLatestQuotations` 只提供 **000300 沪深300、399006 创业板指、DJIAF 道琼斯、IXIC 纳斯达克、LBMAGOLDP 伦敦金** 五条日收盘，**上证指数 000001 不在其中**。
- 基金代码空间里 `000001` 是华夏成长混合，上证综指是 `sh000001` / `000001.SH`，二者不可混用。`nav_cache.json` 的 399 只基金里不含 `000001/000300/510300/510050` 任何一只。
- `SearchFunds("上证综指")` 只返回两只：`025883 汇添富上证综指Y`（2025-10-31 成立，晚于窗口起点，不可用）与 `510760 上证综指ETF`（2020-08-21 成立，全覆盖窗口）。

**落地**：`BatchGetFundNavHistory(["510760"], isDesc=False, dimensionType="oneYear")` 取回 **243 个交易日（2025-09-04 → 2026-09-04）**，其中 **65 日覆盖 2025-09-24 … 2025-12-31**（模拟窗口加 `index_5d` 所需的 5 日前置）。已写入 `data/market/benchmark_sse_composite_etf_510760.json`，格式：

```
{"_meta": {series, why_a_proxy, source, fetched_at, trading_days, range, units, third_party, redistribute}, "series": {"YYYY-MM-DD": nav}}
```

`data/market/` 已加入 `.gitignore`——第三方序列不随仓库分发，与 `nav_cache.json` 同等对待，用脚本重取。

**执行约束**（写进相关卡）：
1. 新配置键 `market{benchmark_path: null, benchmark_label: "上证综指ETF(510760)代理"}`；**默认 null**，三个 demo 配置不设，故 demo 哈希不变、`index_5d` 仍留空。
2. `index_5d` 只算**收益率**：`(v[t−1] / v[t−6] − 1)`，全部取 ≤t−1 的值（前视是不变量 (a) 的红线）。单位净值不是指数点位，绝对数值无意义，**只有收益可用**。
3. 提示与论文里一律写"上证综指ETF（510760）单位净值，作为上证综指的代理"，**禁止**写"上证指数"。若业主后续要真指数，`000300 沪深300` 可从同一 MCP 逐日取（约 68 次 `GetLatestQuotations` 调用），是真实指数收盘而非代理——换源只改 `benchmark_path` 与 label。
4. 缺日（停牌、数据缺口）时 `index_5d` 留空，不做插值、不向前填充。

**哈希总说明**：以上所有"机制符合决策"的修复都会改变 `event_log_sha`/`prompt_sha`，**属预期且必须在卡里声明**；`--replay-check identical=True`（同配置两次运行一致）必须始终成立——新增随机性（`dca_target`、`image_pick:"random"`）全部挂在 `inv.rng`/`rng_seed_from` 上，重放确定性按构造保持。**裁定 3/9/10/13/14 与三个 demo 配置的哈希无关**（β=0、阈值不动、qdii 不设值、demo 仍走 lookback、常量冻结），所以 `mock_40x5`/`mock_3arm`/`mock_null` 的哈希变化只应来自裁定 1/2/5/6/7/8/11。

**2026-09-07 第二轮**：对 `2c95d1a` 做了四视角对抗审查（0 critical、12 major、11 minor），
其中两条最要紧的并不在原修复范围内：`feed.fit()` 因键名不一致恒返回 0.5（匹配源从未人设化），
以及 `initial_pnl` 切模式会移动 `inv.rng`（让 E11 不再是单一操纵）。修复见 `4c9713e`。
审查同时推翻了本会话自己写下的两处说法：无图运行下 TV 与 T **并非**逐字节相同（T 卡多印一行
`配图不展示。`），以及 `beta_guba` 当时只是管线而非开关。裁定 15–19 由此产生。

**待裁定（第三轮新发现）**：定投的触发条件是 `dt_cur.day == 1`，即**日历 1 号恰好是交易日**才执行。
demo 窗口里 2025-11-01 是周六，所以整个 11 月一期都没有；真实日历下 10 月 1–8 是国庆假，10 月也整月跳过。
真实定投是顺延到下一个交易日的。改成"每月第一个交易日"会改变期数、资金流与哈希，属机制变更，未自行决定。


---

## 六、代码之外的数据步骤（业主的其他会话执行；需本机图片库与密钥）

1. **TC 冻结描述**（`caption_frozen.py` 已修好，从未跑过）
   ```
   python data_pipeline/cn/caption_frozen.py --pool data/creatives/cn/content_pool_v1_masked.jsonl --images-root "D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images" --out data/creatives/cn/content_pool_v1_captioned.jsonl --model glm-5.3-flash
   ```
   801 张；输出首行 `_meta`（prompt_sha256/model/counts），行内加 `image_caption_frozen` 与 `caption_meta`。**业主抽 30 条人工评分 ≥8/10**（DECISIONS #10）后方可用于研究运行。新增 `runs/demo_three_arm_captioned.json`（复制 images 配置并把 `content_pool` 指向 captioned 池），加入 `test_configs_are_self_contained.py` 白名单。
2. **股吧立场标注**（业主已拍板）：研究仓库已有 `sim/guba_stance_label.py`（预注册 v1.4 §C1 实现，未跑；密钥约定为 `../tools/.glm_key`，与 flowmirror 的凭据链不同，须注意）。先只标沙盒交集基金（约 8–12k 条短调用）。产出 `sim/guba_stance_labels_v1_meta.json` 的 `bull_ratio_by_fund_week`。新增小脚本 `data_pipeline/cn/merge_guba_stance.py` 把 `bull_ratio` 合并进信号文件并**写为版本化的 `guba_signal_v2.json`**（配置 `guba_signal` 路径升版，旧版仍可钉住复现）。业主 200 条人工核对后才可引用 κ。
3. **真实净值缓存**：已有文档流程，本方案不改其获取。
4. **基准序列**（已由本会话取回一次，2026-09-07；文件在 `.gitignore` 内，须可重取）。需要 GLM 写一张卡：新 `data_pipeline/cn/fetch_benchmark.py`（≤120 行），密钥读 `../tools/.qieman_key`（22 字符，不在任何 git 仓库内），走 JSON-RPC over HTTP：
   - `POST https://stargate.yingmi.com/mcp/v2`，头 `x-api-key: <key>`、`Content-Type: application/json`、`Accept: application/json, text/event-stream`；
   - 先 `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"flowmirror","version":"1"}}}`（无需 session id），再 `notifications/initialized`；
   - 然后 `tools/call`，`name="BatchGetFundNavHistory"`，`arguments={"fundCodes":["510760"],"isDesc":false,"dimensionType":"oneYear"}`。
   **响应不是 JSON，是紧凑的类 YAML 文本**，行形如 `data[N]{navDate,nav,dailyReturn}`，日期为 `YYYY年MM月DD日`；用 `re.findall(r"(\d{4})年(\d{2})月(\d{2})日,([\d.]+),")` 解析。输出 `data/market/benchmark_sse_composite_etf_510760.json`，`_meta` 必须带 `why_a_proxy`、`source`、`units`、`third_party:true`、`redistribute:false`。验收：交易日数 ≥240，窗口 2025-09-24…2025-12-31 内 ≥60 日，无重复日期，`nav` 全为正浮点。
   **上证指数 000001 在该 MCP 不可得**（`GetLatestQuotations` 只有 000300/399006/DJIAF/IXIC/LBMAGOLDP），换真指数请改取 `000300` 逐日收盘（约 68 次 `GetLatestQuotations`，参数 `calDate="YYYY-MM-DD"`），只改 `market.benchmark_path` 与 `benchmark_label`。

---

## 七、"真正多模态活跑"的完成定义（同一次运行须同时满足）

配置：`runs/demo_three_arm_images.json` 或同等研究级配置，指向真实图片库、真实净值、真实密钥，**不带 `--mock`**。

1. `run_meta.json["images"]["attached"] > 0`
2. 至少一条 `arm=="TV"` 的 `imp` 行 `img_idx` 非空
3. 用 captioned 池时，`--dump-prompt first` 中至少一张 TC 卡带 `image_caption_frozen`
4. 窗口与股吧覆盖重叠时，新闻通道至少输出一条股吧行（`ratio_vs_baseline` 驱动；`bull_ratio` 在立场标注后才非空）
5. `run_meta.counters` 中 `decision_failures_transport + decision_failures_model == decision_failures`；撤销密钥的对照运行应得 transport==全部、model==0
6. 三个标准验收运行 `invariants=PASS`、`identical=True`（相对修复前的哈希差异是预期的；同配置两次运行之间的差异不可接受）
7. `run_meta.images.attached` 这个数在配图运行上 > 0、在无图运行上 == 0；`m_tv_arm_carries_images` 三种情形都 pass 且 `reason` 说清是哪种（它是告警不是门禁）
8. 配置了 `market.benchmark_path` 时，`--dump-prompt first` 的市场行出现大盘 5 日收益，且该值等于手算的 `(v[t−1]/v[t−6]−1)`；未配置时该行不出现（不是出现空值）

## 八、可视化重构方案（优先级在功能之后；依赖 §四 的导出器契约）

### 8.1 设计立场
保留三样已验证的主观决定：**人口格场**（年龄×资产×风险容忍度，位置编码身份而非空间——比 MiroFish 的力导向图更能回答"触达了哪类投资者"）；**唯一高饱和重音只给适当性事件**（`--signal` 橙=签署确认书，`--stop` 红=拒签）；**三臂始终可见且要解释**。从 MiroFish 借三样：命名的**步骤梯与逐步状态芯片**（"Step 3/5 逐日循环 · 运行中"）、**产物旁的实时日志流**、以及（列入路线图的）自然语言采访。不学它的：泛用图节点检视器、看不见的运行配置、亮色营销页与暗色应用壳两套割裂的视觉。

### 8.2 页面（单一静态壳 + hash 路由，无构建步骤）

| 页面 | 路由 | 显示 | 读取 | 静态/本地 |
|---|---|---|---|---|
| 首页 | `#/` | 一段介绍；三臂色块与解释；结账结果词汇表（`OC_ZH` 四项 + 反事实 `oc_cf`）；样本包摘要卡；入口链接 | `bundle.json` | 静态 |
| 配置与运行 | `#/run` | 表单（场景 cn 可选、us 禁用标路线图；天数/人数/种子；mock/live 仅在服务端检测到 `config/api.yaml` 时显示 live 为只读指示）；**五步梯**：校验配置→装载人口与内容池→逐日循环→结账与不变量→导出展示包；右上步骤芯片；等宽实时日志面板。静态模式下全部控件禁用并给出 CLI 命令 | `POST /api/run`、`GET /api/run/<id>/log` | **需本地服务** |
| 回放 | `#/replay?run=` | 现有三栏（帖子流 / 人口场 / 检视栏）+ 传送控件 + 计数 + 热力图，逻辑保留；改为读 bundle，**无 bundle 时回退到今天的直读路径**（`event_log.jsonl`+`run_meta.json`+队列文件），这样本地服务里跑到一半的运行也能看 | bundle 或原始文件 | 静态；运行选择器需 `GET /api/runs` |
| 投资者检视 | `#/agent/<id>?run=` | **整段运行的逐日时间线**（决策/评论/交易/结账），开局现金与持仓（`cash0/hold0`），各机构熟悉度/好感随天变化（来自 `st` 行）；**仅检索**的查询框，只引用已记录内容 | `agents.json` | 静态 |
| 模态对照 | `#/modality?run=&post=` | 同一帖子在**实际存在的臂数**下并排（两臂就两列，修掉空第三列）：T 文案、TC 文案+OCR+冻结描述、TV 文案+占位卡"配图：sha `xxxx…`（不随仓库分发）"；本地且配了 `images_root` 才显示真图并标"本地专用"；下方各臂触达/互动率 | `posts.json` + 事件 | 文本静态；真图需本地服务 |
| 审计 | `#/audit?run=` | 不变量表（现有 `renderInvariants`）+ **诚实面板**：demo 规模、合成净值、mock/live、**`agent_policy`**（现在完全没显示）、`images{root,policy,attached,missing,sha_mismatch}`、`inputs_sha256` | bundle 或 `invariants_report.json`+`run_meta.json` | 静态 |
| 数据与场景 | `#/data` | `docs/SCENARIOS.md` 的四件套表（人口/平台素材/监管/市场数据，cn 与 us 各自状态）；运行选择器（静态列 `web/samples/index.json`，本地列 `GET /api/runs` 并加"本地"徽章） | 样本索引 / API | 静态；本地扩展 |

### 8.3 视觉系统
- **保留** `web/style.css` 现有 tokens：`--ground #0e1420`、`--surface`、`--surface-2`、`--line`、`--ink`、`--ink-dim`、`--arm-T/TC/TV`、`--ok/--signal/--stop`、`--mono`（JetBrains Mono）、`--ui`（系统中文字族）、`--r 4px`。
- **修**：`--arm-TV`（金）目前被挪用作激活 tab 下划线和机构脉冲色，稀释了"金=真图臂"的语义；改为中性 `--ink` 或新增冷色 `--accent-ui`。焦点环 `focus-visible` 同理，不再借用 `--arm-TV`。
- **新增**：`--step-active/--step-done/--step-pending`（映射到 `--arm-T/--ok/--ink-dim`，不加新色相）、`--r-lg 8px`（页级卡片）、`--gap 14px`、`--local-badge`（用 `--arm-TC` 紫，只给"本地专用"徽章）。
- **头部**：七个页面 tab（复用 `.tab` 样式、`role=tablist`，驱动路由）；右侧步骤芯片仅在 `#/run` 显示；诚实芯片（`#chip-nav/#chip-mock/#chip-img/#chip-inv` + 新 `#chip-policy`）在**每页**显示，`#chip-img` 改读真实 `run_meta.images`。
- **人口场在 60 vs 400 人**：① 用 `ResizeObserver` 按容器宽与 `devicePixelRatio` 重设 canvas 后备存储并重排（修手机糊字）；② 点半径改为密度函数 `r = clamp(1.4, 3.2·sqrt(60/max(60, 该带人数)), 4.0)`；③ 某带按 13px 步长排不下时退化为按互动比例着色的小方块 + 居中人数标签（复用现有"N 人"标注机制）；④ ≤480px 时 canvas 给 `min-width`，在面板内横向滚动而非缩成糊；传送栏 `flex-wrap` 已在，核对 375px 下不重叠；⑤ 机构标签间距钳制、超宽省略号 + `title`。

### 8.4 模块拆分与契约（每文件 ≤400 行 = 一张卡）

| 文件 | 职责 | 导出 |
|---|---|---|
| `web/data.js` | 两个加载器同一返回形状：`loadBundle` 与 `loadRaw`（今天的 `load()` 原样保留作回退）；`loadRun(base, tag) → {meta, days, byDay, agents:Map, arms, armColor, orgs, postById, invariants, source:'bundle'\|'raw', truncation}`；移植 `dayState/tallyUpTo/shownToday` | 如左 |
| `web/field.js` | 人口场 canvas：布局、密度自适应、`ResizeObserver`、命中测试、曝光波动画 | `mountField(container, model) → {setDay, setSelection, render, destroy}` |
| `web/panels.js` | 帖子流、图例/检视默认态、计数、传送控件、热力图（移植现有 `renderFeed/renderTallies/drawHeat/wire`） | `mountReplayPanels(container, model) → {setDay, onSelectPost, onSelectAgent}` |
| `web/agent.js` | 投资者检视页：逐日时间线、熟悉度趋势、检索式查询框 | `mountAgentPage(container, model, agentId)` |
| `web/modality.js` | 模态对照页；无 bundle 时**显式降级**为只显触达/互动并给出提示，不静默留白 | `mountModalityPage(container, model, postId)` |
| `web/audit.js` | 不变量表 + 诚实面板 + 数据与场景页 | `mountAuditPage`、`mountDataPage` |
| `web/configure.js` | 配置与运行页；无 `localApi` 时全部禁用并解释 | `mountRunPage(container, {localApi})` |
| `web/app.js` | 仅引导：hash 路由、`?run=/?base=`、共享头部、键盘绑定（空格/方向键保留）、按路由挂载**一个**页面 | 入口 |
| `web/style.css` | tokens 增补与修正、页面壳、导航/步骤芯片、移动规则（扩展现有文件） | — |
| `web/server.py` | 本地服务，见 8.5 | 脚本 |

**DOM id**：每页根 `<section id="page-<name>" hidden>`（home/run/replay/agent/modality/audit/data），路由切 `hidden`。页内沿用现有 id（`#feed #field #inspector #tallies #checkout #modality #heat #invariants #btn-first/prev/play/next/last #scrub #speed #daylabel #legend`）；头部沿用 `#runtag #chip-scale #chip-nav #chip-mock #chip-img #chip-inv`，新增 `#chip-policy #stepchip #topnav`。

**Bundle 契约**（与 §四 导出器一致，不得另造字段）：`bundle.json` 含 run_meta 全部现有键 + `images{root,policy,attached,missing,sha_mismatch}` + `invariants{summary,entries}` + `truncation{applied,rule,dropped}`；`posts.json` 每帖各臂 `render_card` 渲染文本 + `image.sha`，**永不含像素**；`agents.json` 每人 `cell/risk_class/arm/cash0/hold0/by_day{...}/familiarity`；`events.json` 行流 + 截断规则；`imp.img_idx` 与 `hold0` 缺失时必须优雅降级。截断必须在界面**可见**（"事件日志已截断至前 N 天"）。

**落地顺序**：① 契约冻结（文本，非代码卡）→ ② `style.css` ∥ ③ `data.js` → ④ 并行：`field.js`、`panels.js`、`agent.js`、`modality.js`、`audit.js`、`configure.js`（只依赖 `data.js` 的导出形状）→ ⑤ `app.js` 最后 → ⑥ `server.py` 任意时间（只依赖 RUNBOOK 的 CLI 契约）。

### 8.5 本地服务 `web/server.py`（stdlib，~250 行，只绑 127.0.0.1）
- `GET /api/runs`：列 `runs/out/*`（tag、mtime、是否有 run_meta/invariants/bundle）。
- `GET /api/runs/<tag>/<file>`：透传 `event_log.jsonl/run_meta.json/invariants_report.json/bundle/*`，**显式 `charset=utf-8`**（默认映射会把中文按 latin-1 解成乱码，本轮已踩）；路径严格限定在 `runs/out/` 下，拒绝 `..` 与绝对路径。
- `POST /api/run`：按 `run.schema.json` 校验后子进程调用 `python -m flowmirror.engine.loop <run.json> --mock --days N --agents N --out runs/out/<tag>`，成功后 `flowmirror export-bundle <run_dir> --out <run_dir>/bundle`；立即返回 run id。
- `GET /api/run/<id>/log`：流式（SSE 或 `?since=` 长轮询）转发 stdout；前端用引擎已有的 `[world]/[engine]/[flowmirror]` 前缀行映射到五步梯，**不新造引擎侧标记**。
- `GET /api/images/<image_id>`：**仅当服务以 `--images-root` 启动且与运行配置一致**才提供；每次请求重新校验 sha256；永不静态托管、永不进公开包。
- **安全**：拒绝非 127.0.0.1 绑定；密钥只走 `config/api.yaml → FLOWMIRROR_GLM_KEY → FLOWMIRROR_LEGACY_KEY_FILE`，浏览器永不输入、服务永不回显；mock/live 与 `agent_policy` 徽章一律从 `cfg` 读，前端不可伪造。

### 8.6 验收（另一模型可冷启动执行）
1. 无 `?run=` 打开 `web/index.html`：七页均非空、零控制台错误。
2. `?run=demo_two-arm`：任何地方无空第三格，臂表恰两行。
3. `?run=demo_null`：头部显示"规则型模拟（rule-based），无 LLM 调用"芯片；检视页不出现空的 LLM 理由块。
4. 无 `images_root` 的运行：TV 列显示占位说明而非破图；图片芯片读真实 `run_meta.images`。
5. 375×812：导航折叠、场可读、传送栏不重叠、页面无横向滚动。
6. 空格播放/暂停，左右键步进；焦点环可见且不用金色。
7. 本地服务：数据页出现带"本地"徽章的运行；配置页步骤芯片按序走完 5 步；新运行无需刷新即可在回放页选中。
8. 回放页对同一样本的行为与今天逐项一致（帖子点击高亮、场点击选人、结账表配色）。

**路线图（本轮不做）**：检索接地的自然语言采访 agent；多运行并排对比；60 日以上运行的事件流分页/流式解析。

---

## 九、总执行顺序与最终验证

### 9.1 顺序（功能优先，界面其后）
1. **契约冻结**（文本，不是代码卡）：把 §4.0 的四条契约与 §8.4 的 bundle/DOM 契约整理成一页 `docs/CONTRACTS_2026-09-07.md`，之后每张卡原文贴入相关段落。
2. **阶段一**（§4.1）：L1 ∥ W1 → L2 ∥ W2 → RT1（全程并行）→ L3；SCHEMA-1b 并行。验收：标准集 + 本地图片库上 `demo_three_arm_images.json` 报 `images.attached>0`。**此步完成即可宣称"图片已进入模拟"**。
3. **数据步骤 1**（§六）：跑 `caption_frozen.py`，业主抽检；新增 captioned 配置。
4. **阶段二**（§4.2）：并行组 {MOD1, FEED1, RT2, PROMPT1, E8, SCHEMA-2..5, DEFAULTS-1}；链 L4→L5、W3；EXP1 在 L2/W2 后。验收：标准集（哈希按 §五 声明变更）+ `analyze()` 出 click_rate + `test_defaults_match_decisions`。**此步完成即可宣称"参数与机制符合决策记录且可测量"**。
5. **数据步骤 2**（§六）：股吧立场标注 + `merge_guba_stance.py` → `guba_signal_v2.json`。
6. **阶段三**（§4.3）：L6、W5+W6、FEED2、SCHEMA-6/7。
7. **界面**（§八）：契约冻结 → `style.css` ∥ `data.js` → 六个页面模块并行 → `app.js` → `server.py`；GitHub Pages 工作流；README 顶部加"在浏览器里试一试"。
8. **阶段四**（§4.4）：CI1 集成标记、DOC1 文档；更新 `docs/DEV_HANDOVER.md` 与 `METHODS_LEDGER` R33。
9. 全部落地后：全新克隆复测（无净值缓存、无密钥）pytest 全绿；本地带数据机器跑 §七 七项完成定义；再推送 GitHub。

### 9.2 每阶段的固定验证
```
python -m pytest -q
python -m flowmirror.engine.loop runs/mock_10x3.json      --mock --days 5 --agents 40 --out runs/out/mock_40x5 --replay-check
python -m flowmirror.engine.loop runs/mock_10x3_3arm.json --mock --days 5 --agents 60 --out runs/out/mock_3arm  --replay-check
python -m flowmirror.engine.loop runs/mock_10x3_null.json       --days 5 --agents 40 --out runs/out/mock_null  --replay-check
python -m flowmirror.channels.feed && python -m flowmirror.engine.world --self-test && python -m flowmirror.engine.loop --self-test
```
外加：三份事件日志逐行过 `event.schema.json`；干净克隆 pytest 全绿。

### 9.3 关键文件（本部分）
- 引擎：`flowmirror/engine/loop.py`、`flowmirror/engine/world.py`、`flowmirror/channels/feed.py`、`flowmirror/agents/prompt.py`、`flowmirror/agents/runtime.py`、`flowmirror/analysis/modality.py`、新 `flowmirror/analysis/export_bundle.py`、`flowmirror/cli.py`
- 配置：`config/schemas/run.schema.json`、`config/schemas/event.schema.json`、`config/engine_defaults.yaml`
- 测试：`tests/conftest.py`（夹具基底）、`tests/unit/test_configs_are_self_contained.py`（白名单）、各新增测试文件
- 数据脚本：`data_pipeline/cn/caption_frozen.py`、研究仓库 `sim/guba_stance_label.py`、新 `data_pipeline/cn/merge_guba_stance.py`
- 界面：`web/{index.html,style.css,app.js,data.js,field.js,panels.js,agent.js,modality.js,audit.js,configure.js,server.py}`、`.github/workflows/pages.yml`
- 文档：`docs/DECISIONS_2026-09-05_SANDBOX.md`（契约）、`docs/RUNBOOK.md`、`docs/DEV_HANDOVER.md`、新 `docs/CONTRACTS_2026-09-07.md`
- 发卡工具：`scratchpad/glm_run.py`（带输出校验）、`scratchpad/apply_patch.py`、`scratchpad/land_multi.py`、`scratchpad/glm/_patch_format.md`（替换块格式说明）

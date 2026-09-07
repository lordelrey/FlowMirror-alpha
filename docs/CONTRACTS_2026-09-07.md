# 接口契约 · 2026-09-07 引擎修复轮

本文件是**唯一权威**。实施者不得推测任何接口——签名、配置键名、事件字段名、数据形状全部在此。
与本文件相悖的代码按本文件改；本文件与 `docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07.md` §五
的业主裁定相悖时，以 §五 为准并上报。

基线（修改前）：commit `b4e1926`。四条验收运行的事件日志 sha 与 `identical` 标志：

| 运行 | 命令 | sha | identical |
|---|---|---|---|
| mock_40x5 | `runs/mock_10x3.json --mock --days 5 --agents 40` | `ec0a8728…` | True |
| mock_3arm | `runs/mock_10x3_3arm.json --mock --days 5 --agents 60` | `0faa60d9…` | True |
| mock_null | `runs/mock_10x3_null.json --days 5 --agents 40` | `0c113c4b…` | True |
| demo_two_arm | `runs/demo_two_arm.json --mock --days 5 --agents 40` | `bb3ee0e3…` | True |

`identical=True` 必须**始终**成立（同配置两次运行逐字节一致）。sha **会变**，那是预期的，
因为多项机制要按决策记录改正；每次改动必须说明哪条裁定导致 sha 变化。

---

## 1. 真实签名（inspect.signature 导出，勿凭记忆）

```
flowmirror.engine.loop
  _make_llm(cfg)
  _agent_view(inv, persona_rec, shown, W, cfg, navday, hist, trend_cache, guba_view,
              last_trade, declined)
  _feed_card(W, post, notes_by_id, arm, heat_prev, clim_prev, top_prev, dt_cur, n_prev)
  _week_key(d)
  apply_decision(inv, rec, shown, day, fees=None)
  _qdii_blocked_set(cfg, dstr)
  main(argv=None)

flowmirror.engine.world
  guba_seed_label(world, code, week)
  load_world(cfg) -> World
  check_invariants(state, events_path, cfg)      # -> (checks: dict, core_ok: bool)
  write_reports(out_dir, state, cfg, world, checks, counters, elapsed) -> None
  init_investors(world, cfg) -> list
  INVARIANTS keys: a_lagged_signals_only, b_nonholder_never_redeems,
    c_hard_block_never_subscribes, d_wealth_conservation, e_all_cells_exposed,
    f_post_ig_is_intent_group, g_comments_lagged_only, h_arm_balance,
    i_redeem_checkout_never_blocked, j_displayed_comment_matches_prev_day,
    k_dec_matches_active, l_replay_byte_identical   (第 13 个 m_... 由本轮新增)
  N_FUNDS_K / INVEST_SHARE_CASH: 冻结常量，不得改、不得入配置（裁定 14）

flowmirror.channels.feed
  climate_for(post_id, comments_prev, min_n=4, weights=None)    # -> (label, counts)
  top_comments(post_id, comments_prev, k=3, weights=None)
  fit(agent_state, post)
  rank_feed(agent_state, candidates, heat_prev, clim_prev, cfg, rng, mode="three_source")
  assign_agent_arms(run_tag, agents, arms=("T", "TV"))          # agents = [(agent_id, cell), ...]
  check_arm_balance(agent_arms, agent_cells, arms=None)
  hot_score(likes, saves, comments, age_days, gamma=1.8)

flowmirror.agents.runtime
  decide(agent_view, feed_cards, cfg, cache, governor, llm, shown)
  reflect(agent_view, cfg, cache, governor, llm)
  call_glm(messages, max_tokens, model=None, parser=None, governor=None, first_open=False)
  _llm_cfg(cfg)
  sha256_text(text) -> str
  TEMP = 0.3         # 硬编码，本轮改为可配
  MAX_ATTEMPTS = 5   # 物理 HTTP 重试，与 cfg["llm"]["max_attempts"] 同名异义

flowmirror.agents.prompt
  render_news(view) / render_belief(view) / render_card(card, social_on=True)
  build_decision_messages(agent_view, feed_cards, cfg)

flowmirror.analysis.modality
  _is_i2(pid, source=None) / _aggregate(ev) / analyze(run_dirs, level="agent")
  _synthetic_run(run_dir, seed, like_tv=None, run_arm=None, legacy=False, with_meta_arms=True)
```

跨文件调用点（**只有拥有该文件的车道可以编辑它**）：

- `loop.py:907-908` 调 `feed.climate_for(pid, prev_cmts, weights=wmap)` 与 `feed.top_comments(...)`
- `world.py:427, 977, 1249, 1327` 调 `feed.assign_agent_arms` / `feed.check_arm_balance`
- `feed.py:681` 内部调 `fit(...)`（无外部调用者）

---

## 2. 数据契约（并行卡两边原文照抄）

### 2.1 view["guba"]（`_agent_view` 产出 → `prompt.render_news` 消费）

`prompt.render_news` 已经按 dict-of-dicts 写好，**不要改 prompt.py 的 render_news**：

```python
view["guba"] = {code: {"name": str, "mult": float, "bull_ratio": float | None}}
```

- `mult` = 该基金该周信号行的 `ratio_vs_baseline`，缺失则 `1.0`
- `bull_ratio` 在立场标注数据落地前一律 `None`（`render_news` 已把 None 当 0.5 处理）
- 现状是 `{code: label_string}`，`render_news` 对字符串调 `.get` 会抛 AttributeError；
  只因 `guba_seed_label` 永远返回 None 才没炸过。这是必须修的潜伏崩溃。
- 只保留 `code in inv.hold or code in codes` 的条目（现有过滤逻辑保持）

### 2.2 股吧信号文件的真实字段

`guba_signal` 指向的 JSON 里，每个 `{基金代码: {ISO周: 行}}` 的行**只有**：

```
n_posts, reply_n, read_n, z_abnormal, ratio_vs_baseline
```

**没有** `bull_ratio` / `bull` / `bullish` / `bear` / `bearish`。文件 `_meta` 自述
`no stance is computed here` 且 `stance_jobs_stripped: true`。所以 `guba_seed_label`
返回 None 是**当前正确行为**（裁定 4 双轨：先接形状，立场标注是独立数据任务）。

### 2.3 state["images"]（`loop.py` 产出 → `world.write_reports` 写入 run_meta）

```python
state["images"] = {
    "root": cfg.get("images_root"),            # str | None
    "policy": cfg.get("image_pick", "first"),  # "first" | "random"
    "attached": int, "missing": int, "sha_mismatch": int,
}
```

`write_reports` 原样写成 `run_meta["images"]`；`state` 里没有该键时写
`{"root": None, "policy": ..., "attached": 0, "missing": 0, "sha_mismatch": 0}`。

### 2.4 rec["failure_kind"] → 事件行 dec.failure_kind

```
"transport" : call_glm 的 cls 属于 {exception, http_error, empty_response,
                                   reasoning_salvage_rejected}
"model"     : 拿到了响应但 JSON / schema 解析失败
None        : 未失败
```

**裁定 9**：只加 `S["decision_failures_transport"]` 与 `S["decision_failures_model"]`
两个计数并写进 `run_meta.counters`；`decision_failure_halt` 的阈值 0.02 与判定条件
（`t >= 3 and halt and S["decisions"] and S["decision_failures"]/S["decisions"] > thr`）
**一律不动**。恒等式必须成立：`transport + model == decision_failures`。

### 2.5 TV 臂图片解析

池文件 `data/creatives/cn/content_pool_v1_masked.jsonl` 每行携带：

```
image_ids    : list[str] | str   # 如 ["68f0..._0.jpg", ...]，0-based；可能是 JSON 字符串，
                                 # 也出现过 Python repr（单引号）形式
image_sha256 : list[str] | str   # 与 image_ids 平行的 64 位十六进制摘要
```

池里**没有** `image_path` / `image` / `cover`——现状 `_feed_card` 读的正是这三个键，
所以 `image_path` 恒为 None，TV 臂与 T 臂逐字节相同。

解析规则：

1. 只在 `arm == "TV"` 时执行，T / TC 一行代码都不许走（文本臂不得被图片库拖慢或改变）
2. `image_pick == "first"` → 索引 0；`"random"` → 从该 agent 自己的确定性流取
   （用现有 `inv.rng` / `rng_seed_from` 惯例，**禁止** `hash()`，重放必须复现同一 `img_idx`）
3. `os.path.join(images_root, image_ids[idx])`，读文件算 **sha256 并与
   `image_sha256[idx]` 精确比对**，只有相符才附图
4. root 为空 / 文件不存在 / 摘要不符 → 不附图，计入 `missing` 或 `sha_mismatch`，
   并在卡上留标记（`image_missing` / `image_sha_mismatch`）
5. `card["image_sha"]` 必须是**文件的真实摘要**，不是现状的 `sha256_text(str(image_path))`
   （那只是把路径字符串哈希了一遍）
6. `imp` 事件行加 `img_idx`（整数，未附图时 None）——`config/schemas/event.schema.json`
   里 `imp.img_idx` 已是可选字段，**不需要改 schema**
7. 运行开始打印恰好一行：
   - `[world] images: <n> resolvable under <images_root>`，或
   - `[world] WARNING: no images_root configured -- the TV arm degrades to text-only; the modality comparison measures nothing.`

### 2.6 新不变量 m_tv_arm_carries_images —— 告警，不是门禁

按全局规则「门禁只放在不可逆、跨系统、安全或正式发布边界」，一次模拟运行不属于这四类。
所以本条**永远 pass=True**，只把事实摆出来，**绝不触发退出码 4**：

- `images_root` 为空 → `pass=True`，`reason` 写明未配置图片库、TV 臂退化为纯文本
- `"TV"` 不在本次运行的臂集合里 → `pass=True`，`reason` 写明本次运行无 TV 臂
- 否则 `pass=True`，并携带 `attached` / `missing` / `sha_mismatch` 三个数

判断"图片有没有真的进来"靠 `run_meta.images.attached`——它是个数，看一眼就知道，
不需要用运行失败来表达。与 `m_env_valence_warning` 同一形态。

### 2.7 基准序列（裁定 6）

文件 `data/market/benchmark_sse_composite_etf_510760.json`（**已 gitignore**，不随仓库分发）：

```
{"_meta": {...}, "series": {"YYYY-MM-DD": float, ...}}
```

- 243 个交易日，2025-09-04 → 2026-09-04
- 数值是 **510760 上证综指ETF 的单位净值**，不是指数点位。绝对值无意义，**只有收益可用**
- `index_5d = v[t-1] / v[t-6] - 1`，两个取值都必须 ≤ t−1（前视是不变量 (a) 的红线）
- 缺日 → `index_5d` 留空（`view` 里不出现该键），**不插值、不向前填充**
- 提示与论文一律写「上证综指ETF（510760）单位净值，作为上证综指的代理」，
  **禁止**写「上证指数」

---

## 3. 新增配置键

`config/schemas/run.schema.json` 的 `additionalProperties` 是 **false**，
所以任何新键不进 schema 就会让配置被拒。

| 键 | 默认值 | 依据 | 是否改哈希 |
|---|---|---|---|
| `dynamics.fam_decay` | `0.2` | 裁定 2（今天硬编码 0.1） | **是** |
| `dynamics.fam_threshold` | `1.0` | 今天的裸字面量 | 否 |
| `dynamics.lambda_trust` | `0.9` | 今天的 adstock 常数 | 否 |
| `dynamics.lambda_attention` | `0.8` | DECISIONS #5 | **是**（今天注意力复用 fam_decay） |
| `dynamics.beta_guba` | `0.0` | **裁定 3：β=0，管道接通系数为零。不得写 0.1** | 否 |
| `dca.pct` | `0.02` | 今天的裸字面量 | 否 |
| `dca.min_ticket` | `100.0` | 今天的裸字面量 | 否 |
| `feed.climate_margin` | `0.16666666666666666` | 裁定 1（今天 1/3） | **是** |
| `llm.temperature` | `0.3` | 等于今天硬编码的 `TEMP` | 否 |
| `llm.max_provider_attempts` | `5` | 等于今天的 `MAX_ATTEMPTS` | 否 |
| `fees.subscribe_rate` | `0.0012` | 裁定 8（今天 0.0） | **是** |
| `fees.redeem_rate` | `0.005` | 裁定 8（今天 0.0） | **是** |
| `initial_pnl.mode` | `"lookback"` | 裁定 13：demo 保持 lookback | 否 |
| `initial_pnl.lookback_days_min` / `_max` | `60` / `250` | 等于今天的 `randint(60,250)` | 否 |
| `initial_pnl.share_at_loss` | `0.05` | 仅 `mode:"target"` 用 | 否 |
| `initial_pnl.tolerance` | `0.02` | 仅 `mode:"target"` 用 | 否 |
| `qdii_blocked` | 不设默认，schema 接受 `{ISO日期: {codes: [...]}}` | **裁定 10：接通 schema，任何配置都不设值** | 否 |
| `market.benchmark_path` | `null` | 裁定 6 | 否（默认 null 时） |
| `market.benchmark_label` | `null` | 裁定 6 | 否 |

**铁律**：除表中标「是」的以外，**每个新键的默认值必须严格等于今天的硬编码值**，
这样省略这些键的配置逐字节不变。三个 mock 配置（`mock_10x3.json`、
`mock_10x3_3arm.json`、`mock_10x3_null.json`）与 `demo_two_arm.json`
**不得添加任何新键**。

---

## 4. 文件所属车道（同一文件永不并行）

| 车道 | 文件 | 说明 |
|---|---|---|
| A（串行） | `flowmirror/engine/loop.py` | L1→L2→L4→L5→L6，之后 L3→L7 |
| B（串行） | `flowmirror/engine/world.py` | W1→W2→W3→W56 |
| C（各自独立，可并行） | `runtime.py` / `feed.py` / `modality.py` / `prompt.py` | 一个文件一个 agent |
| 配置 | `config/schemas/run.schema.json`、`config/engine_defaults.yaml`、`config/schemas/event.schema.json` | 最先落，独占 |
| 新文件 | `flowmirror/analysis/export_bundle.py` + `flowmirror/cli.py` | 车道 A/B 完成后 |
| 测试 | `tests/unit/test_*.py` 各自新文件 | 谁改谁加，互不重名 |

夹具铁律：所有测试夹具从 `tests/conftest.py` 的 `build_demo_cfg`（基底
`runs/demo_two_arm.json`）构造。**禁止**从 `runs/mock_10x3*.json` 构造——它的
`nav_cache` 是 gitignore 的，干净克隆上不存在，CI 会红。

---

## 5. 红线

1. 数据库 `xhs_data.db` / `xhs_images.db` 只读，且只允许 `file:...?mode=ro`
2. 图片永不进仓库；`images_root` 只在机器本地配置里出现
3. 密钥永不入库；不得打印任何密钥内容，连片段都不行
4. 不得碰 `D:/Desktop/4.15论文/greenarena/`
5. 不得删除或覆盖 `runs/out/` 下已有目录（里面是花钱换来的缓存）
6. 不得 `git commit` / `git push` / `git checkout` / `git reset` / `git clean`

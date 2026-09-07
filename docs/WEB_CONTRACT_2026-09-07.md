# 界面契约 · 2026-09-07 可视化重构

本文件是**唯一权威**。六个页面模块并行编写,任何跨模块接口——数据模型字段、挂载函数签名、
DOM id、CSS 变量——全部在此。**不得推测**;契约没覆盖到的,读 `web/data.js` 的实际导出。

方案依据:`docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07.md` §八。

---

## 0. 硬约束

- **无构建步骤。** 原生 ES modules,`<script type="module">`,直接开文件或经静态服务都能跑。
  不引入 npm、不引入打包器、不引入框架。
- **无外部资源。** 除已有的 Google Fonts 一行外不加任何外链;不引 CDN 库。图表、场、热力图
  全部手写 canvas 或 SVG。
- **图片永不进仓库**,页面也永不内联像素。真图只在本地小服务开启且配了 `images_root` 时,
  经 `GET /api/images/<id>` 提供,并打"本地专用"徽章。
- **密钥永不经浏览器。** 配置页不接受任何密钥输入,服务端不回显。
- 静态模式(GitHub Pages)必须可用:没有本地服务时,发起运行的控件全部禁用并解释原因,
  其余页面靠 `web/samples/` 里的样本包工作。

## 1. 数据模型(`web/data.js` 的唯一出口)

```js
loadRun(base, tag, opts?) -> Promise<Model>
```

`base` 是仓库根的相对前缀(通常 `..`),`tag` 是运行标签。两条路径:

- **bundle 优先**:`<base>/runs/out/<tag>/bundle/{bundle,posts,agents,events}.json`
- **原始回退**:`<base>/runs/out/<tag>/{event_log.jsonl,run_meta.json,invariants_report.json}`
  加 `<base>/data/population/agents_seed2027.json`。本地服务里跑到一半的运行只有这一条路。

两条路径产出**同一个** `Model`:

```js
Model = {
  source: 'bundle' | 'raw',     // 界面必须显示是哪一条
  tag: string,
  meta: object,                 // bundle.json 全部键 / run_meta.json 全部键
  status: string,               // 'ok' | 'invariant_failure' | ...
  days: number[],               // 升序的交易日索引 t
  dateOf: Map<number, string>,  // t -> 'YYYY-MM-DD'
  byDay: Array<{               // 与 days 同序同长
    t, d,
    post: Row[], imp: Row[], dec: Row[], click: Row[], co: Row[],
    act: Row[], cmt: Row[], clim: Row[], st: Row[], refl: Row[],
  }>,
  rows: Row[],                  // 全部事件行,文件顺序
  agents: Map<string, Agent>,
  arms: string[],               // 本次运行真实出现的臂,升序。两臂运行长度就是 2
  armColor: Record<string, string>,   // 臂 -> CSS 颜色字符串
  orgs: string[],
  postById: Map<string, Post>,
  invariants: { summary: object, entries: Entry[] } | null,
  images: { root, policy, attached, missing, sha_mismatch } | null,
  truncation: { applied: boolean, rule: string, dropped: object } | null,
  agentPolicy: 'llm' | 'null' | string,
  syntheticNav: boolean,
  mock: boolean,
  fidelity: string[],           // bundle._bundle.fidelity;raw 路径为 []
  warnings: string[],           // 载入过程中的诚实提示,界面必须显示
}

Agent = {
  id, arm, cell,                // cell 形如 '30_45|high|tolerant'
  age, asset, risk,             // cell 拆开的三段
  riskClass,                    // 'C2' | 'C3' | 'C4' | '—'
  cash0: number|null, hold0: object|null, wealth0: number|null,
  persona: string,              // 已截断,不得再截
  byDay: Record<number, DayRec>,      // 只有 bundle 路径非空
  familiarity: Record<number, object>,// 同上
}

Post = {
  post_id, org, intent, ig, fund,
  published_t, published_d, days_shown: number[],
  note_id,
  image: { n_images, sha, shown_idx: number[], sha_shown, attached_impressions } | null,
  arms: Record<string, {              // 只有 bundle 路径有 text
    impressions, reach, engaged_agents, engagement_rate,
    clicks, comments, checkouts, text, chars, truncated,
  }>,
}
```

`data.js` 另外导出三个纯函数,页面模块直接用,**不要各自重写**:

```js
dayIndexOf(model, t) -> number          // t 在 days 里的下标,找不到返回 -1
stateAt(model, i) -> DayState           // 第 i 天的派生状态,见下
tallyUpTo(model, i) -> Tally            // 0..i 的累计计数
listRuns(base) -> Promise<RunInfo[]>    // 静态读 web/samples/index.json;有本地服务则 GET /api/runs
```

```js
DayState = {
  t, d,
  seenBy: Map<agentId, Set<postId>>,    // 当天谁看到了哪些帖子
  armOf: Map<agentId, arm>,
  engaged: Set<agentId>,                // 当天有 like/save/follow 的人
  commented: Map<agentId, {stance, text, p}>,
  checkouts: Row[],                     // co 行
  acts: Row[],
  shown: Post[],                        // 当天出现过的帖子,按 post_id 升序
  climate: Map<postId, string>,
}
// 2026-09-08 订正:这一段原本写的是一套 data.js 从未产出的字段名(impressions/likes/
// saves/follows/subscribes/redeems),照着它写的模块会拿到一堆 undefined。下面是实现
// 真正返回的形状。契约声称自己是唯一权威,那就得跟得上代码——否则它只会把人引到坑里。
Tally = {
  imp,            // 曝光次数
  eng,            // 有互动的 agent-日数(赞/藏/关任一)
  cmt,            // 评论条数
  bull, bear, watch,     // 评论立场分布
  co,             // 结账笔数
  signed,         // 其中签署确认书
  declined,       // 其中拒签
  blocked,        // 其中被硬性拦截或当日停购
  sub, red,       // 申购 / 赎回笔数
  amt,            // 申购金额合计
  fees,           // 费用合计
  reach: Set<agentId>,   // 触达到的 agent 集合
  reachN,         // 上面那个集合的大小
  byOc: Record<string, number>,
}
```

## 2. 页面模块签名(六张卡各写一个文件)

每个模块 **default export 一个 mount 函数**,签名如下。mount 只被调用一次,返回的对象由
路由在离开页面时调用 `destroy()`。

```js
// web/field.js
mountField(root: HTMLElement, model: Model) -> {
  setDay(i: number): void,
  setSelection(sel: {post?: string, agent?: string} | null): void,
  render(): void,
  destroy(): void,
  onPick(cb: (sel: {post?: string, agent?: string} | null) => void): void,
}

// web/panels.js  —— 帖子流 / 计数 / 传送控件 / 热力图 / 检视默认态
mountReplayPanels(root, model, opts: {
  onDay: (i: number) => void,
  onSelect: (sel) => void,
}) -> { setDay(i), setSelection(sel), destroy() }

// web/agent.js
mountAgentPage(root, model, agentId: string|null) -> { destroy() }

// web/modality.js
// 2026-09-08 修正:加 opts。此前签名不带 localApi,导致 modality.js 只能自己
// probeLocalApi() 去问服务在不在——那就是页面模块自己发 fetch,违反 §1。
// app.js 早就有 S.localApi,传下去即可,页面模块从此零 fetch。
mountModalityPage(root, model, postId: string|null,
                  opts: {localApi: boolean, base: string}) -> { destroy() }

// web/audit.js
mountAuditPage(root, model) -> { destroy() }
mountDataPage(root, model|null, opts: {base: string, localApi: boolean}) -> { destroy() }

// web/configure.js
mountRunPage(root, opts: {base: string, localApi: boolean}) -> { destroy() }

// web/home.js
mountHomePage(root, model|null, opts: {base: string, tag: string|null}) -> { destroy() }
```

`app.js` 负责:hash 路由、读 `?run=`/`?base=`、载入 model、渲染共享头部、键盘绑定
(空格播放/暂停、左右方向键步进)、按路由挂载**恰好一个**页面并在切换时 `destroy()`。
**页面模块不得自己改 hash、不得自己读 location、不得触碰头部。**

## 3. 路由与 DOM

| 路由 | 页面 | 模块 |
|---|---|---|
| `#/` | 首页 | `home.js` |
| `#/run` | 配置与运行 | `configure.js` |
| `#/replay` | 回放(场 + 帖子流 + 检视) | `field.js` + `panels.js` |
| `#/agent/<id>` | 投资者检视 | `agent.js` |
| `#/modality` | 模态对照 | `modality.js` |
| `#/audit` | 审计 | `audit.js` |
| `#/data` | 数据与场景 | `audit.js` 的 `mountDataPage` |

每页根节点:`<section id="page-<name>" class="page" hidden>`,name ∈
`home run replay agent modality audit data`。路由切 `hidden`,**不销毁再建**除非跨页。

沿用现有 id(迁移时不要改名):`#feed #field #inspector #tallies #checkout #modality
#heat #invariants #legend #daylabel #scrub #speed
#btn-first #btn-prev #btn-play #btn-next #btn-last`。
头部沿用 `#runtag #chip-scale #chip-nav #chip-mock #chip-img #chip-inv`,
新增 `#chip-policy #chip-source #stepchip #topnav`。

## 4. CSS 变量(`style.css` 定义,模块只许使用)

保留并继续使用:`--ground --surface --surface-2 --line --ink --ink-dim`、
`--arm-T --arm-TC --arm-TV`、`--ok --signal --stop`、`--mono --ui --r`。

**本轮修正**:`--arm-TV`(金)此前被挪用作激活 tab 下划线与焦点环,稀释了"金 = 真图臂"
这一语义。新增 `--accent-ui` 专管界面强调(导航激活态、焦点环、滑块),
**任何界面元素都不得再用 `--arm-*`**——臂色只属于臂。

新增:`--accent-ui --step-active --step-done --step-pending --r-lg --gap --local-badge`。

语义色纪律:`--signal`(橙)= 已签署确认书,`--stop`(红)= 拒签,`--ok`(绿)= 通过。
**这三个只给适当性结账与不变量结论**,不得用于普通强调。

**界面编码专用色**(2026-09-08 补):密度、进度、选中、强弱这类**界面**编码一律用
`--accent-ui` / `--accent-ui-dim` / `--surface-3`,**永不**借臂色。反例就在本仓库:
`field.js` 用 `cssVar('--arm-T')` 填人口场里"人数过多已折叠"的方块,深浅表示互动比例
——那是密度编码,不是臂,读者会以为那一格属于 T 臂。写这条契约的人自己犯了这个错,
所以它写在这里。

## 5. 人口场的自适应(`field.js`)

- 用 `ResizeObserver` 按容器宽与 `devicePixelRatio` 重设 canvas 后备存储再重排;
  不要写死 1180×700。
- 点半径 `r = clamp(1.4, 3.2 * Math.sqrt(60 / Math.max(60, bandCount)), 4.0)`。
- 某带按 13px 步长排不下时,退化为按互动比例着色的小方块 + 居中人数标签。
- ≤480px 时给 canvas 一个 `min-width`,在面板内横向滚动,**不缩成糊**。
- 机构标签间距钳制,超宽省略号 + `title`。

## 6. 降级纪律(整个界面的诚实要求)

- `source === 'raw'` 时没有逐臂文案:模态页**显式**说明"该运行未导出展示包,只显示触达与
  互动率",不静默留白。
- 两臂运行**不得**出现空的第三列。臂数一律取 `model.arms.length`。
- `images` 为 null 或 `attached === 0`:TV 列显示占位说明 + `sha` 前 8 位,**不显示破图**。
- `truncation.applied` 为真:在事件相关的每一处显示"事件日志已截断:<rule>"。
- `agentPolicy === 'null'` 时头部显示"规则型模拟(rule-based),无 LLM 调用",
  检视页**不显示**空的 LLM 理由块。
- `model.warnings` 与 `model.fidelity` 必须在审计页逐条列出。
- **帖子清单的措辞**(2026-09-08 补):`source === 'raw'` 时 `postById` 是从每一条 `post`
  事件行建的,**含从未展示过的帖子**;bundle 路径下才只有出现过的。所以模态页的清单
  说明只能写"本次运行发布的帖子",不能写"至少触达一位投资者的帖子"——后者在 raw 路径
  上是假话。要么按 `days_shown.length > 0` 过滤后再那样说。

## 7. 本地小服务(`web/server.py`,stdlib,只绑 127.0.0.1)

| 端点 | 作用 |
|---|---|
| `GET /api/runs` | 列 `runs/out/*`:tag、mtime、是否有 run_meta / invariants / bundle |
| `GET /api/runs/<tag>/<file>` | 透传运行产物。**必须显式 `charset=utf-8`**(默认映射会把中文按 latin-1 解成乱码,本项目已踩过)。路径严格限定在 `runs/out/` 下,拒绝 `..` 与绝对路径 |
| `POST /api/run` | 按 `run.schema.json` 校验后子进程跑引擎,成功后跑 `flowmirror export-bundle`;立即返回 run id |
| `GET /api/run/<id>/log` | 流式转发 stdout(SSE 或 `?since=` 长轮询)。前端用引擎已有的 `[world]/[engine]/[flowmirror]` 前缀行映射到五步梯,**不新造引擎侧标记** |
| `GET /api/images/<id>` | 仅当服务以 `--images-root` 启动且与运行配置一致才提供;每次请求重新校验 sha256;永不静态托管 |

## 8. 验收(另一模型可冷启动执行)

1. 无 `?run=` 打开 `web/index.html`:七页均非空、零控制台错误。
2. `?run=<两臂运行>`:任何地方无空第三格,臂表恰两行。
3. `?run=<null 策略运行>`:头部出现"规则型模拟"芯片;检视页无空 LLM 理由块。
4. 无 `images_root` 的运行:TV 列显示占位说明而非破图;图片芯片读真实 `run_meta.images`。
5. 375×812:导航折叠、场可读、传送栏不重叠、页面无横向滚动。
6. 空格播放/暂停,左右键步进;焦点环可见且**不用金色**。
7. 本地服务:数据页出现带"本地"徽章的运行;配置页步骤芯片按序走完 5 步;新运行无需刷新即可选中。
8. 回放页对同一样本的行为与重构前逐项一致(帖子点击高亮、场点击选人、结账表配色)。

---

## 9. 内部链接(2026-09-08 新增)

页面模块生成的站内链接一律写成**裸 hash**:

```js
`#/agent/${encodeURIComponent(id)}`                      // 对
`${location.pathname}${location.search}#/agent/${id}`    // 错
```

浏览器解析相对 hash 时**自动保留当前的 `?run=` 与 `?base=`**,所以拼 `location` 不但
多余,还违反 §2 的"页面模块不得读 location"。`home.js` 已经是对的做法,照它写。

**任何模块不得读 `location`**,包括 `location.pathname`、`location.search`、`location.hash`。
路由是 `app.js` 一个人的事:它读 hash、读查询串、载入模型、决定挂哪一页。页面模块
只从 mount 参数拿到它该知道的东西。这条不是洁癖——两个模块各自拼一遍 URL,就有两处
可以和路由表脱节。

**要链到另一个运行**(查询串不同,不只是 hash 不同)时,写**相对查询串**,同样不读 location:

```js
function runHref(tag, base) {
  const q = new URLSearchParams();
  q.set('run', tag);
  if (base && base !== '..') q.set('base', base);
  return `?${q.toString()}#/replay`;     // 以 ? 开头的相对 URL 换掉查询串、保留路径
}
```

`audit.js` 就是这么写的,其他要造运行链接的地方照抄。

---

## 10. 数据读取 vs 指挥服务(2026-09-08 澄清 §1)

§1 说"页面模块不得自己 fetch",指的是**读数据**:运行产物、人口队列、分析产物一律经
`data.js`,这样"界面到底读了哪些文件"只有一个答案。

**指挥本地服务不在此列。**`configure.js` 的职责就是发起一次运行并跟踪它,所以它直接调
§7 的三个命令端点:

| 端点 | 用途 |
|---|---|
| `POST /api/run` | 发起运行 |
| `GET /api/run/<id>/log?since=N` | 跟踪 stdout |
| `GET /api/runs` | 读服务能力(是否配了凭据、是否配了 images-root) |

把这三个塞进 `data.js` 只会让数据层同时是控制层,更糟。**除 `configure.js` 外的页面模块
仍然一次 fetch 都不许有**——`modality.js` 曾为了问"服务在不在"自己 fetch,那是真违规,
已改为由 shell 探测一次、经 `opts.localApi` 传入。

界面**永不**接受密钥。凭据在引擎内部解析,服务只回一个布尔值说"配了没有"。

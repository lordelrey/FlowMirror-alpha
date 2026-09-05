# FlowMirror（流动镜像）

**一个开放、配置驱动的"基金市场 × 社交流"仿真沙盒。** 数千名由在线视觉大模型驱动的
投资者智能体，在同一社交平台上浏览真实投放过的基金营销素材（图文），发帖、评论，并按
真实基金净值完成申购 / 持有 / 赎回决策。我们把"证监会式适当性核验"做成可随机化的实验
开关，并用真实基金份额流水量作为外部校准基准。FlowMirror 是研究"销售与分发如何塑造
零售资金流"的科研工具，不是对任何真实市场的预测；当前预览版不包含任何结果。

## 状态

脚手架阶段（M0）：Schema、配置、场景与 CLI 已就绪；引擎将在 P3 里程碑接入
（`bash script/run.sh ...` 目前只做输入校验并提示 `engine not wired yet (P3)`）。

## 目录结构

```
config/          api_example.yaml、engine_defaults.yaml、schemas/
scenarios/       cn_xhs_2025q4（当前）、us_2025（路线图骨架）
data/            L1 派生表 + DATA.md + MANIFEST.sha256
data_pipeline/   cn/、us/ —— L0 -> L1/L2 加工脚本（P2）
flowmirror/      Python 包：config、io 已可用，其余子包为规划占位
script/          run.sh、fetch_data.sh
tests/           单元测试（schemas、io）与 fixtures
docs/            PERSONA.md、SCENARIOS.md、RUNBOOK.md
runs/            仿真输出（git 忽略）
legacy/          冻结的 v7 之前产物，仅供参考
```

## 安装

Python 3.10+：

```bash
pip install -e .            # 运行依赖（jsonschema、PyYAML）
pip install -e ".[dev]"     # 加 pytest
pip install -e ".[images]"  # 可选图像处理（Pillow）
```

## 快速开始

```bash
cp config/api_example.yaml config/api.yaml   # 填入你自己的 Key；已被 git 忽略
flowmirror tree
flowmirror schemas
flowmirror validate tests/fixtures/run_mock_10x3.json --schema run
flowmirror validate scenarios/cn_xhs_2025q4/scenario.yaml   # 自动识别 schema
bash script/run.sh scenarios/cn_xhs_2025q4 tests/fixtures/run_mock_10x3.json
```

自带的 fixture 使用 `mock_llm: true`，可完全离线运行；校验与测试都不需要 API Key。

## 数据政策（摘要）

- **L0** 原始抓取（帖子、截图、爬虫输出）永不进入仓库或发布包。
- **L1** 小型派生表随仓库存放于 `data/`。
- **L2** 较大产物托管在 Hugging Face / Zenodo，校验和记录于 `data/MANIFEST.sha256`。
- 素材**图片永不再分发**，仅保留元数据与派生文本。

完整清单见 [data/DATA.md](data/DATA.md)。

## 场景

| 场景 | 状态 | 平台 | 监管 |
|---|---|---|---|
| `cn_xhs_2025q4` | 当前 | 小红书（中文），4 家基金公司 | `cn_cxr` 适当性核验 |
| `us_2025` | 路线图 | 网页广告（英文） | `us_regbi`（Reg BI） |

## 引用

TODO：首个正式发布时补充 DOI 与引用信息（见 `CITATION.cff`）。

## 许可

MIT，见 [LICENSE](LICENSE)。Copyright (c) 2026 FlowMirror authors。

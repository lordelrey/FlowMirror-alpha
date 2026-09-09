# FlowMirror（流动镜像）

FlowMirror 是一个在本地运行、由配置驱动的“基金市场 × 社交平台”模拟沙盒。它由 Python 仿真引擎、可选的 LLM 投资者智能体、基金适当性规则、事件日志、分析模块和浏览器回放界面组成。

这个仓库提供的是源代码，不是线上交易服务。用户需要把项目克隆到本地，运行离线示例，或者配置自己的模型凭据和输入数据，再通过本地网页查看模拟过程。FlowMirror 不会连接券商下单，不用于预测真实价格，也不构成投资建议。

## 仓库包含什么

- `flowmirror/` 下的 Python CLI 与仿真引擎。
- 无需 API Key、不会联网的确定性 mock 策略和规则策略。
- 通过本地配置启用的文本或视觉大模型调用。
- 信息流、记忆、适当性核验、基金账户和机构策略模块。
- JSON Schema、公开示例配置、自动测试和本地回放界面。
- 一套完全合成的小型演示数据；运行产物与凭据不进入版本控制。

## 安装

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/lordelrey/FlowMirror-alpha.git
cd FlowMirror-alpha
python -m venv .venv
python -m pip install -e ".[dev]"
```

## 运行离线示例

以下命令使用确定性 mock 模型与合成净值，不需要 API Key，也不会产生网络调用：

```bash
python -m flowmirror.cli demo two-arm
python -m flowmirror.cli demo three-arm --replay-check
python -m flowmirror.cli demo null
```

输出写入 `runs/out/`，该目录默认不会被 Git 提交。

## 打开本地应用

```bash
python web/server.py --port 8765
```

然后访问 `http://127.0.0.1:8765/web/`。服务只监听本机地址，可以查看仓库自带的示例、回放已经完成的本地运行，并启动受支持的本地配置。

## 接入大模型

把 `config/api_example.yaml` 复制为已被 Git 忽略的 `config/api.yaml`，只在本地填入凭据，并准备一个符合 `config/schemas/run.schema.json` 的运行配置：

```bash
python -m flowmirror.cli validate path/to/run.json --schema run
python -m flowmirror.cli run path/to/run.json
```

不要提交 `config/api.yaml`、模型响应缓存、事件日志或原始数据。

## 主要产物

每次运行会生成 append-only 的 `event_log.jsonl`、响应缓存、运行元数据和不变量检查结果。事件覆盖发帖、曝光、决策、点击、适当性核验、交易、评论、社会气候、状态转移和反思。完成的运行可以导出给浏览器界面：

```bash
python -m flowmirror.cli export-bundle runs/out/<tag>
```

## 目录结构

```text
flowmirror/      仿真引擎、智能体、通道、监管和分析模块
config/          默认配置、API 模板和 JSON Schema
runs/            离线示例配置；生成结果默认忽略
data/            仅包含可分发的演示输入
scenarios/       示例场景
web/             本地服务和回放界面
tests/           自动测试
docs/            面向公开用户的架构与使用说明
```

详细说明见 [系统架构](docs/ARCHITECTURE.md) 和 [运行手册](docs/RUNBOOK.md)。

## 数据与安全边界

仓库内的净值、帖子、注意力信号、机构名称和产品代码全部为合成演示数据，具体见 [data/DATA.md](data/DATA.md)。

FlowMirror 是实验软件。模拟交易和模型生成内容不能被解释为对真实投资者的观察，也不能作为投资建议。

## 许可证

代码采用 [MIT License](LICENSE)。数据文件如有更窄的使用范围，以 [data/DATA.md](data/DATA.md) 为准。

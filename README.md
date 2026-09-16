# agent-demo

一个简单的 Python AI Agent 应用，基于 OpenAI SDK 实现 tool-calling 循环。

## 功能

- 对话式 Agent，支持多轮上下文
- 内置示例工具：计算器、获取当前时间
- 支持任何 OpenAI 兼容 API（OpenAI、DeepSeek、vLLM 等）
- 提供 Web 页面，支持流式问答
- 基于 OpenTelemetry 的可观测性（Trace + Metrics），可对接 Grafana

## 快速开始

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY（可选 OPENAI_BASE_URL 和 MODEL_NAME）

# 3a. 命令行运行
agent-demo

# 3b. 或启动 Web 服务（默认 http://127.0.0.1:8000）
agent-web
# 可用 WEB_HOST / WEB_PORT 覆盖监听地址与端口
```

## 项目结构

```
src/agent_demo/
├── agent.py         # Agent 核心循环（含流式版本）
├── main.py          # CLI 入口
├── web.py           # Web 服务（FastAPI + SSE 流式接口）
├── telemetry.py     # OpenTelemetry 初始化与埋点辅助
├── static/
│   └── index.html   # 聊天页面
└── tools.py         # 工具定义与实现

deploy/
├── docker-compose.yml          # Collector + Tempo + Prometheus + Grafana + App
├── Dockerfile                  # 应用镜像
├── otel-collector/config.yaml  # OTLP 接收，分发到 Tempo / Prometheus
├── tempo/tempo.yaml            # Trace 存储
├── prometheus/prometheus.yml   # 抓取 Collector 暴露的指标
└── grafana/                    # 数据源与仪表盘自动配置
```

## 可观测性（OpenTelemetry + Grafana）

应用通过 OTLP (gRPC) 上报 Trace 与 Metrics，链路为：

```
agent-demo ──OTLP──▶ OpenTelemetry Collector ──▶ Tempo（Trace）
                                            └──▶ Prometheus（Metrics）
                                                     │
                                                 Grafana 展示
```

### 埋点内容

- **Trace**：`invoke_agent agent-demo`（一次问答）→ `chat <model>`（每次 LLM 调用）、`execute_tool <name>`（工具调用），并附带 GenAI 语义约定属性（模型、输入/输出 token、工具名、错误类型）。
- **Metrics**：`agent.requests`、`agent.tokens`（按 input/output）、`agent.llm.duration`、`agent.tool.calls`；另有 FastAPI 自动埋点产生的 `http.server.*`。

### 启动监控栈

> 需要 Docker（Docker Desktop 请开启 WSL 集成）。

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

启动后：

| 服务 | 地址 |
| --- | --- |
| Grafana | http://localhost:3000 （匿名 Admin，直接进入）|
| Prometheus | http://localhost:9090 |
| Tempo | http://localhost:3200 |
| OTLP Collector | localhost:4317 (gRPC) / localhost:4318 (HTTP) |

Grafana 已自动配置好 Prometheus / Tempo 数据源与 **agent-demo Observability** 仪表盘；
Trace 可在 **Explore** 中用 TraceQL 查询：`{ resource.service.name = "agent-demo" }`。

### 本地运行应用（应用在宿主机、监控在 Docker）

```bash
agent-web
# 默认上报到 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

相关环境变量见 `.env.example`，设置 `OTEL_ENABLED=false` 可完全关闭遥测。

## 添加新工具

在 `src/agent_demo/tools.py` 中：

1. 在 `TOOLS` 列表添加工具的 JSON Schema 定义
2. 实现 `_your_tool()` 函数
3. 在 `run_tool()` 中注册分发逻辑

## License

MIT

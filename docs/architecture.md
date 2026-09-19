# agent-demo 可观测架构介绍

> 版本：v1.0 ｜ 状态：MVP 已实现并验证
> 关联代码：
>
> `src/agent_demo/`
>
> 、
>
> `deploy/`
> 技术栈：Python 3.12・FastAPI・OpenTelemetry SDK・OpenTelemetry Collector・Tempo・Prometheus・Grafana・Docker Compose



***

## 1. 项目概览

agent-demo 是一个基于 OpenAI SDK 的 **tool-calling Agent** 应用，提供两种交互方式：



* **CLI**：`agent-demo`，命令行多轮对话。

* **Web**：`agent-web`，FastAPI + SSE 流式问答页面（默认 `http://127.0.0.1:8000`）。

它内置两个示例工具（`calculator`、`get_current_time`），核心价值不在于业务本身，而在于它演示了一套**面向 LLM Agent 的完整可观测（Observability）闭环**：



```
业务代码（低侵入埋点） → OTLP 上报 → Collector 采集分发 → Tempo / Prometheus 存储 → Grafana 统一展示
```

可观测性覆盖「**一次请求 → 一次 Agent 回合 → 每次 LLM 调用 → 每次工具调用**」全链路，回答四类典型问题：



| 问题                | 对应数据                    |
| ----------------- | ----------------------- |
| 一次问答慢在哪一步？        | Trace（Span 耗时分解）        |
| 请求量、错误率、LLM 延迟趋势？ | Metrics（Prometheus）     |
| Token 消耗 / 成本趋势？  | Metrics（`agent.tokens`） |
| 工具调用成功率、分布？       | Metrics + Trace         |



***

## 2. 整体架构

### 2.1 分层与数据流



```
┌──────────────────────────────────────────────────────────────────────┐

│                        应用层 agent-demo (Python)                      │

│                                                                        │

│  CLI (main.py)            Web (web.py, FastAPI + SSE)                  │

│       └──────────┬─────────────────────┘                               │

│                  ▼                                                     │

│            agent.py  tool-calling 循环                                  │

│              ├── run\_agent()          (同步)                            │

│              └── run\_agent\_stream()   (SSE 流式)                       │

│                  │ tools.py (calculator / get\_current\_time)           │

│                  ▼                                                     │

│            telemetry.py  ◄── OTel SDK：TracerProvider + MeterProvider │

│            (埋点辅助：start\_span / record\_\*)                           │

└───────────────────────────────┬──────────────────────────────────────┘

&#x20;                               │ OTLP over gRPC  (4317) / HTTP (4318)

&#x20;                               ▼

┌──────────────────────────────────────────────────────────────────────┐

│              采集层  OpenTelemetry Collector (contrib:0.111.0)          │

│   receivers: otlp (grpc/http)                                          │

│   processors: memory\_limiter(75%/20%) → batch(1024, 5s)               │

│   pipelines:                                                           │

│     traces  ──exporter otlp/tempo──┐                                  │

│     metrics ──exporter prometheus ─┤  (0.0.0.0:8889)                  │

└────────────────────────────────────┼───────────────────────────────────┘

&#x20;                                    │

&#x20;               ┌────────────────────┴────────────────────┐

&#x20;               ▼                                         ▼

┌──────────────────────────────┐          ┌──────────────────────────────┐

│  存储层                       │          │  存储层                       │

│  Tempo 2.6.1 (Trace)         │          │  Prometheus v2.55.0 (Metric) │

│  ─ 本地文件 /var/tempo        │          │  ─ 抓取 collector:8889       │

│  ─ block\_retention 1h        │          │  ─ 15s 抓取间隔               │

│  ─ HTTP :3200                │          │  ─ TSDB 保留 24h             │

└──────────────┬───────────────┘          └──────────────┬───────────────┘

&#x20;              └────────────────────┬───────────────────┘

&#x20;                                   ▼

&#x20;                   ┌──────────────────────────────────┐

&#x20;                   │  展示层  Grafana 11.3.0 (:3000)   │

&#x20;                   │  ─ Provisioning 自动建数据源     │

&#x20;                   │  ─ 仪表盘 agent-demo Observability│

&#x20;                   │  ─ Tempo ↔ Prometheus 双向关联    │

&#x20;                   └──────────────────────────────────┘
```

### 2.2 一条请求的链路视角



```
POST /api/chat  (FastAPI 自动 span: http.server.request)

&#x20;       │

&#x20;       ▼

invoke\_agent agent-demo   ← 一次问答的根 Span

&#x20;  │  属性: gen\_ai.system, gen\_ai.operation.name, gen\_ai.request.model

&#x20;  │

&#x20;  ├─► chat gpt-4o-mini   ← 每次 LLM 调用

&#x20;  │      属性: gen\_ai.response.model, input/output tokens

&#x20;  │      指标: agent.llm.duration (histogram), agent.tokens

&#x20;  │

&#x20;  ├─► execute\_tool calculator   ← 每次工具调用（可多次）

&#x20;  │      属性: gen\_ai.tool.name, gen\_ai.tool.call.id, error.type

&#x20;  │      指标: agent.tool.calls

&#x20;  │

&#x20;  └─► chat gpt-4o-mini   ← 工具结果回灌后再次调用 LLM，直到无 tool\_calls

请求结束指标: agent.requests{agent.status=success|error}
```



***

## 3. 应用层与埋点设计

### 3.1 模块职责



| 文件             | 职责                                                                                                          |
| -------------- | ----------------------------------------------------------------------------------------------------------- |
| `agent.py`     | Agent 核心循环：发消息 → 解析 `tool_calls` → 执行工具 → 回灌结果 → 直到最终回答。提供同步与流式（SSE）两个版本。                                   |
| `web.py`       | FastAPI 应用：`GET /` 聊天页、`POST /api/chat`（SSE 流式）。`FastAPIInstrumentor` 自动埋点 HTTP 层；内存按 `session_id` 维护对话上下文。 |
| `tools.py`     | 工具的 JSON Schema 定义（`TOOLS`）与 `run_tool()` 分发实现。                                                             |
| `telemetry.py` | **可观测核心**：初始化 OTel Provider、定义指标、暴露 `start_span` / `record_*` 辅助函数。                                         |
| `main.py`      | CLI 入口，启动时 `setup_telemetry()`，退出时 `shutdown_telemetry()` 冲刷缓冲。                                             |

### 3.2 埋点设计要点（telemetry.py）



* **统一资源标识**：`service.name=agent-demo`、`service.version=0.1.0`、`deployment.environment`（取自 `DEPLOYMENT_ENV`）。

* **导出方式**：Trace 用 `BatchSpanProcessor` 异步批量导出；Metrics 用 `PeriodicExportingMetricReader`，默认 10s 周期；均走 OTLP gRPC。

* **遵循 GenAI Semantic Conventions**：Span / 指标属性使用 `gen_ai.system`、`gen_ai.operation.name`、`gen_ai.request.model`、`gen_ai.tool.name`、`gen_ai.usage.input_tokens` 等标准键，保证可被通用后端（Tempo/Grafana）识别。

* **错误可追溯**：异常时 `span.record_exception(e)` 并写入 `error.type=<异常类名>`，再向上抛出，业务流程不被遥测打断。

* **可关闭、无副作用**：`OTEL_ENABLED=false` 时所有埋点为 no-op；OTel 包未安装也不影响主流程。

### 3.3 Trace 模型



| Span 名称                   | 层级 | 含义        | 关键属性                                                                     |
| ------------------------- | -- | --------- | ------------------------------------------------------------------------ |
| `invoke_agent agent-demo` | 根  | 一次完整问答    | `gen_ai.operation.name=invoke_agent`、模型                                  |
| `chat <model>`            | 子  | 一次 LLM 调用 | `gen_ai.operation.name=chat`、`gen_ai.response.model`、input/output tokens |
| `execute_tool <name>`     | 子  | 一次工具调用    | `gen_ai.tool.name`、`gen_ai.tool.call.id`、`error.type`（失败时）               |

### 3.4 Metrics 指标清单



| 指标名                  | 类型        | 维度（labels）                                        | 含义              |
| -------------------- | --------- | ------------------------------------------------- | --------------- |
| `agent.requests`     | Counter   | `agent.status`(success/error)                     | 问答请求总数          |
| `agent.tokens`       | Counter   | `token.type`(input/output)、`gen_ai.request.model` | LLM token 消耗    |
| `agent.llm.duration` | Histogram | `gen_ai.request.model`                            | LLM 调用耗时（秒）     |
| `agent.tool.calls`   | Counter   | `gen_ai.tool.name`、`tool.status`                  | 工具调用次数          |
| `http.server.*`      | —         | （FastAPI 自动埋点）                                    | HTTP 请求量、耗时、状态码 |

> Prometheus 中 Counter 会自动带 
>
> `_total`
>
>  后缀（如 
>
> `agent_requests_total`
>
> 、
>
> `agent_llm_duration_seconds_bucket`
>
> ）。



***

## 4. 采集、存储与展示

### 4.1 OpenTelemetry Collector（`deploy/otel-collector/config.yaml`）



* **接收**：OTLP gRPC `:4317`、OTLP HTTP `:4318`。

* **处理**：


  * `memory_limiter`：内存到 75% 触发限制，峰值 20%，保护 Collector。

  * `batch`：批量 1024 条 / 5s，降低导出开销。

* **导出（按 pipeline 分流）**：


  * `traces` → `otlp/tempo`（endpoint `tempo:4317`，insecure）。

  * `metrics` → Prometheus exporter `:8889`，开启 `resource_to_telemetry_conversion`，把 Resource 属性转为指标 label（便于按 service 过滤）。

### 4.2 Tempo（Trace 存储）



* 单实例、**本地文件后端**（`/var/tempo/traces`、WAL），无对象存储 / 集群。

* `ingester.max_block_duration=5m`，`compactor.block_retention=1h`（Trace 仅保留 1 小时）。

* HTTP API `:3200`，支持 **TraceQL** 查询。

### 4.3 Prometheus（Metrics 存储）



* 仅抓取 Collector 的 `:8889`（应用不直接暴露 /metrics，全部经由 OTel 接入，保持单一数据源）。

* 抓取 / 评估间隔均为 15s，TSDB 保留 `24h`。

### 4.4 Grafana（统一展示）



* **免登录**：匿名 Admin、关闭登录页、暗色主题，适合本地 Demo。

* **Provisioning 自动建数据源**（`provisioning/datasources/`）：


  * `Prometheus`（默认数据源，15s 间隔）。

  * `Tempo`，并开启 `tracesToMetrics`、`serviceMap`、`nodeGraph`—— 实现 **Trace ↔ Metrics 双向钻取**。

* **预置仪表盘** `agent-demo Observability`（`dashboards/agent-demo.json`）：总请求数、错误率（>5% 标红）、LLM p95 延迟（>5s 橙 / >15s 红）、按状态请求速率等面板。

* Trace 查询示例（Explore）：`{ resource.service.name = "agent-demo" }`。



***

## 5. 部署形态

### 5.1 服务清单（`deploy/docker-compose.yml`）



| 服务               | 镜像                                             | 端口               | 说明                                                      |
| ---------------- | ---------------------------------------------- | ---------------- | ------------------------------------------------------- |
| `app`            | 本地构建（`Dockerfile`，python:3.12-slim）            | `8000`           | Web 应用，`CMD agent-web`，上报到 `http://otel-collector:4317` |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.111.0` | `4317/4318/8889` | 接收、处理、分流                                                |
| `tempo`          | `grafana/tempo:2.6.1`                          | `3200`           | Trace 本地存储                                              |
| `prometheus`     | `prom/prometheus:v2.55.0`                      | `9090`           | 指标抓取与存储                                                 |
| `grafana`        | `grafana/grafana:11.3.0`                       | `3000`           | 统一展示                                                    |

数据卷：`tempo-data`、`prometheus-data`、`grafana-data`。

### 5.2 两种部署方式



1. **全栈 Docker**（一条命令拉起应用 + 监控）：



```
docker compose -f deploy/docker-compose.yml up -d --build
```



1. **应用跑宿主机、监控跑 Docker**（`docker-compose.infra.yml` 仅起监控栈）：



```
agent-web   # 上报到默认 http://localhost:4317
```

### 5.3 关键环境变量（`.env.example`）



| 变量                            | 默认                      | 说明                                            |
| ----------------------------- | ----------------------- | --------------------------------------------- |
| `OTEL_ENABLED`                | `true`                  | 置 `false` 完全关闭遥测                              |
| `OTEL_SERVICE_NAME`           | `agent-demo`            | 服务名（Trace/Metrics 过滤维度）                       |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Collector 地址（Docker 内用 `otel-collector:4317`） |
| `OTEL_METRIC_EXPORT_INTERVAL` | `10000`                 | 指标导出周期（ms）                                    |
| `LLM_INCLUDE_USAGE`           | `true`                  | 流式响应是否请求 token usage（部分兼容端点不支持会自动降级）          |



***

## 6. 设计取舍与边界

**本期做了什么**



* Trace + Metrics + Logs 三类遥测，覆盖 Agent 全链路（Logs 经 OTLP 写入 Loki，日志行含 trace_id 可跳转 Trace）；

* 遵循 OTel GenAI 语义约定，后端可替换（不必绑定 Grafana 栈）；

* 低侵入、可一键关闭；Collector 统一接入，应用不直接暴露指标；

* Trace 与 Metrics 在 Grafana 中双向关联（从慢指标钻取到具体 Trace）。

**本期不做什么（MVP 边界）**



* **不做告警**：无 Alerting 规则与通知。

* **不做生产级高可用**：Tempo/Prometheus 均单实例本地存储，保留期短（Trace 1h / Metric 24h）。

* **不自动注入**：未使用 OTel auto-instrumentation，埋点手写以贴合 Agent 语义。

* **不采集 Prompt/Completion 正文**：仅记录 token 计数与元数据，规避内容隐私风险。



***

## 7. 快速验证



```
\# 1. 起全栈

docker compose -f deploy/docker-compose.yml up -d --build

\# 2. 访问应用，发起几轮对话（触发工具调用）

open http://localhost:8000

\# 3. 打开 Grafana 查看仪表盘 / 钻取 Trace

open http://localhost:3000        # agent-demo Observability 仪表盘

\# Explore → Tempo，查询: { resource.service.name = "agent-demo" }
```
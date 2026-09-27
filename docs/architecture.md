# agent-demo 可观测架构介绍

> 版本：v1.1 ｜ 状态：MVP 已实现并验证（含 Logs → Loki 日志链路）
> 关联代码：
>
> `src/agent_demo/`
>
> 、
>
> `deploy/`
> 技术栈：Python 3.12・FastAPI・OpenTelemetry SDK・OpenTelemetry Collector・Tempo・Prometheus・Loki・Grafana・Docker Compose



***

## 1. 项目概览

agent-demo 是一个基于 OpenAI SDK 的 **tool-calling Agent** 应用，提供两种交互方式：



* **CLI**：`agent-demo`，命令行多轮对话。

* **Web**：`agent-web`，FastAPI + SSE 流式问答页面（默认 `http://127.0.0.1:8000`）。

它内置两个示例工具（`calculator`、`get_current_time`），核心价值不在于业务本身，而在于它演示了一套**面向 LLM Agent 的完整可观测（Observability）闭环**：



```
业务代码（低侵入埋点） → OTLP 上报 → Collector 采集分发 → Tempo / Prometheus / Loki 存储 → Grafana 统一展示
```

可观测性覆盖「**一次请求 → 一次 Agent 回合 → 每次 LLM 调用 → 每次工具调用**」全链路，回答四类典型问题：



| 问题                | 对应数据                    |
| ----------------- | ----------------------- |
| 一次问答慢在哪一步？        | Trace（Span 耗时分解）        |
| 请求量、错误率、LLM 延迟趋势？ | Metrics（Prometheus）     |
| Token 消耗 / 成本趋势？  | Metrics（`agent.tokens`） |
| 工具调用成功率、分布？       | Metrics + Trace         |
| 请求过程中发生了什么？       | Logs（Loki，日志行含 trace_id 可跳转 Trace） |



***

## 2. 整体架构

### 2.1 分层与数据流



```
┌──────────────────────────────────────────────────────────────────────┐
│                        应用层 agent-demo (Python)                      │
│  CLI (main.py)            Web (web.py, FastAPI + SSE)                 │
│       └──────────┬─────────────────────┘                              │
│                  ▼                                                    │
│            agent.py  tool-calling 循环                                 │
│              ├── run_agent()          (同步)                           │
│              └── run_agent_stream()   (SSE 流式)                      │
│                  │ tools.py (calculator / get_current_time)          │
│                  ▼                                                    │
│            telemetry.py  ◄── OTel SDK                                 │
│            (TracerProvider + MeterProvider + LoggerProvider)          │
│            (埋点辅助：start_span / record_* / logging)                 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ OTLP over gRPC  (4317) / HTTP (4318)
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│              采集层  OpenTelemetry Collector (contrib:0.111.0)          │
│   receivers: otlp (grpc/http)                                         │
│   processors: memory_limiter(75%/20%) → batch(1024, 5s)               │
│   pipelines:                                                          │
│     traces  ──exporter otlp/tempo──────────────┐                      │
│     metrics ──exporter prometheus (0.0.0.0:8889)│                     │
│     logs    ──exporter loki (http://loki:3100)──┼──────┐              │
└─────────────────────────────────────────────────┼──────┼──────────────┘
              ┌───────────────────────────────────┘      │
              ▼                                          ▼
┌──────────────────────────────┐          ┌──────────────────────────────┐
│  存储层  Tempo 2.6.1 (Trace) │          │  存储层  Prometheus v2.55.0 (Metric) │
│  ─ 本地文件 /var/tempo       │          │  ─ 抓取 collector:8889       │
│  ─ block_retention 1h        │          │  ─ 15s 抓取间隔               │
│  ─ HTTP :3200                │          │  ─ TSDB 保留 24h             │
└──────────────┬───────────────┘          └──────────────┬───────────────┘
               │                                          │
               │        ┌──────────────────────────────┐  │
               │        │  存储层  Loki 3.2.1 (Log)     │  │
               │        │  ─ loki exporter HTTP push   │◄─┼──────────────┘
               │        │  ─ TSDB 索引 + 文件系统 /loki │  │
               │        │  ─ retention 24h · HTTP :3100│  │
               │        └──────────────┬───────────────┘  │
               └───────────────────────┼──────────────────┘
                                       ▼
                   ┌──────────────────────────────────┐
                   │  展示层  Grafana 11.3.0 (:3000)   │
                   │  ─ Provisioning 自动建数据源      │
                   │  │  Prometheus / Tempo / Loki    │
                   │  ─ 仪表盘 agent-demo Observability│
                   │  ─ Trace ↔ Metrics ↔ Logs 关联   │
                   └──────────────────────────────────┘
```

### 2.2 一条请求的链路视角



```
POST /api/chat  (FastAPI 自动 span: http.server.request)

&#x20;       │

&#x20;       ▼

invoke\_agent agent-demo   ← 一次问答的根 Span

&#x20;  │  属性: gen\_ai.system, gen\_ai.operation.name, gen\_ai.request.model

&#x20;  │  日志: 「收到对话请求 session_id=… trace_id=…」 (web.py)

&#x20;  │

&#x20;  ├─► chat gpt-4o-mini   ← 每次 LLM 调用

&#x20;  │      属性: gen\_ai.response.model, input/output tokens

&#x20;  │      指标: agent.llm.duration (histogram), agent.tokens

&#x20;  │      日志: 「LLM 调用完成 model=… 耗时=… 输入tokens=… 输出tokens=…」

&#x20;  │

&#x20;  ├─► execute\_tool calculator   ← 每次工具调用（可多次）

&#x20;  │      属性: gen\_ai.tool.name, gen\_ai.tool.call.id, error.type

&#x20;  │      指标: agent.tool.calls

&#x20;  │      日志: 「执行工具 …」「工具执行成功 …」/ 失败时「工具执行失败 …」

&#x20;  │

&#x20;  └─► chat gpt-4o-mini   ← 工具结果回灌后再次调用 LLM，直到无 tool\_calls

请求结束指标: agent.requests{agent.status=success|error}

请求结束日志: 「对话结束 session_id=… status=… trace_id=…」 (web.py)
```



***

## 3. 应用层与埋点设计

### 3.1 模块职责



| 文件             | 职责                                                                                                          |
| -------------- | ----------------------------------------------------------------------------------------------------------- |
| `agent.py`     | Agent 核心循环：发消息 → 解析 `tool_calls` → 执行工具 → 回灌结果 → 直到最终回答。提供同步与流式（SSE）两个版本。                                   |
| `web.py`       | FastAPI 应用：`GET /` 聊天页、`POST /api/chat`（SSE 流式）。`FastAPIInstrumentor` 自动埋点 HTTP 层；内存按 `session_id` 维护对话上下文。 |
| `tools.py`     | 工具的 JSON Schema 定义（`TOOLS`）与 `run_tool()` 分发实现。                                                             |
| `telemetry.py` | **可观测核心**：初始化 OTel Provider（Tracer / Meter / Logger）、定义指标、暴露 `start_span` / `record_*` / `current_trace_id` 辅助函数；同时配置控制台日志与 OTLP 日志导出（→ Loki）。 |
| `main.py`      | CLI 入口，启动时 `setup_telemetry()`，退出时 `shutdown_telemetry()` 冲刷缓冲（Trace / Metrics / Logs 三类 Provider）。           |

### 3.2 埋点设计要点（telemetry.py）



* **统一资源标识**：`service.name=agent-demo`、`service.version=0.1.0`、`deployment.environment`（取自 `DEPLOYMENT_ENV`）。

* **导出方式**：Trace 用 `BatchSpanProcessor` 异步批量导出；Metrics 用 `PeriodicExportingMetricReader`，默认 10s 周期；均走 OTLP gRPC。

* **日志三路输出**：`setup_telemetry()` 先配置控制台 Handler（`LOG_LEVEL` 控制级别，独立于 OTEL 始终生效）；OTEL 开启时再挂 `LoggingHandler`（`LoggerProvider` + `BatchLogRecordProcessor(OTLPLogExporter)`）到根 Logger——业务代码只需使用标准 `logging`，即可同时输出到控制台并经 OTLP 上报到 Loki。

* **日志与 Trace 关联**：OTel 自动为每条日志记录附加当前 Span 的 `trace_id` / `span_id`；Web 层日志还显式打印 `trace_id=xxx`，配合 Grafana Loki 数据源的 derivedFields，实现「日志行 → 点击 → 跳转 Tempo Trace」。

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

  * `logs` → Loki exporter（`http://loki:3100/loki/api/v1/push`），把 OTLP 日志记录转换为 Loki push API 格式；Resource 属性转为 Loki 流标签（`service.name` → `service_name`），便于按服务筛选日志流。

### 4.2 Tempo（Trace 存储）



* 单实例、**本地文件后端**（`/var/tempo/traces`、WAL），无对象存储 / 集群。

* `ingester.max_block_duration=5m`，`compactor.block_retention=1h`（Trace 仅保留 1 小时）。

* HTTP API `:3200`，支持 **TraceQL** 查询。

### 4.3 Prometheus（Metrics 存储）



* 仅抓取 Collector 的 `:8889`（应用不直接暴露 /metrics，全部经由 OTel 接入，保持单一数据源）。

* 抓取 / 评估间隔均为 15s，TSDB 保留 `24h`。

### 4.4 Loki（Log 存储，`deploy/loki/loki.yaml`）



* 单实例（`grafana/loki:3.2.1`）、**TSDB 索引 + 文件系统对象存储**（`/loki/chunks`），schema v13、索引周期 24h，单副本（`replication_factor=1`）、ring inmemory。

* `limits_config.retention_period=24h`（日志保留 24 小时），`reject_old_samples=false`。

* HTTP API `:3100`，接收 Collector 的 push 写入（`/loki/api/v1/push`），支持 **LogQL** 查询。

* 日志流标签：`service_name="agent-demo"`（由 Resource 属性转换而来），查询示例：

```
{service_name="agent-demo"}                     # 该服务全部日志
{service_name="agent-demo"} |= "工具执行失败"     # 仅错误日志
{service_name="agent-demo"} | logfmt | status="error"
```

### 4.5 Grafana（统一展示）



* **免登录**：匿名 Admin、关闭登录页、暗色主题，适合本地 Demo。

* **Provisioning 自动建数据源**（`provisioning/datasources/`）：


  * `Prometheus`（默认数据源，15s 间隔）。

  * `Tempo`，并开启 `tracesToMetrics`、`serviceMap`、`nodeGraph`—— 实现 **Trace ↔ Metrics 双向钻取**。

  * `Loki`（`maxLines: 1000`），并配置 **derivedFields**：正则 `trace_id=([0-9a-fA-F]+)` 命中的文本渲染为链接（「查看 Trace」），点击即跳转到 Tempo 查询对应 Trace——实现 **Logs → Trace 一键跳转**。

* **预置仪表盘** `agent-demo Observability`（`dashboards/agent-demo.json`）：总请求数、错误率（>5% 标红）、LLM p95 延迟（>5s 橙 / >15s 红）、按状态请求速率等面板，底部为**「应用日志（Loki）」日志面板**（查询 `{service_name="agent-demo"}`，日志行内 `trace_id` 可点击）。

* Trace 查询示例（Explore）：`{ resource.service.name = "agent-demo" }`；日志查询示例（Explore → Loki）：`{service_name="agent-demo"}`。



***

## 5. 部署形态

### 5.1 服务清单（`deploy/docker-compose.yml`）



| 服务               | 镜像                                             | 端口               | 说明                                                      |
| ---------------- | ---------------------------------------------- | ---------------- | ------------------------------------------------------- |
| `app`            | 本地构建（`Dockerfile`，python:3.12-slim）            | `8000`           | Web 应用，`CMD agent-web`，上报到 `http://otel-collector:4317` |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.111.0` | `4317/4318/8889` | 接收、处理、分流 Trace / Metrics / Logs                         |
| `tempo`          | `grafana/tempo:2.6.1`                          | `3200`           | Trace 本地存储                                              |
| `prometheus`     | `prom/prometheus:v2.55.0`                      | `9090`           | 指标抓取与存储                                                 |
| `loki`           | `grafana/loki:3.2.1`                           | `3100`           | 日志本地存储（`loki-data` 卷）                                   |
| `grafana`        | `grafana/grafana:11.3.0`                       | `3000`           | 统一展示                                                    |

数据卷：`tempo-data`、`prometheus-data`、`loki-data`、`grafana-data`。

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
| `OTEL_ENABLED`                | `true`                  | 置 `false` 完全关闭遥测（含日志上报）                       |
| `OTEL_SERVICE_NAME`           | `agent-demo`            | 服务名（Trace/Metrics/Logs 过滤维度）                  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Collector 地址（Docker 内用 `otel-collector:4317`） |
| `OTEL_METRIC_EXPORT_INTERVAL` | `10000`                 | 指标导出周期（ms）                                    |
| `LLM_INCLUDE_USAGE`           | `true`                  | 流式响应是否请求 token usage（部分兼容端点不支持会自动降级）          |
| `LOG_LEVEL`                   | `INFO`                  | 应用日志级别（控制台输出，OTEL 开启时同步经 OTLP 上报 Loki）        |



***

## 6. 设计取舍与边界

**本期做了什么**



* Trace、Metrics、Logs 三类遥测，覆盖 Agent 全链路（Logs 经 OTLP 写入 Loki，日志行含 trace_id 可跳转 Trace）；

* 遵循 OTel GenAI 语义约定，后端可替换（不必绑定 Grafana 栈）；

* 低侵入、可一键关闭；Collector 统一接入，应用不直接暴露指标；

* 三类数据在 Grafana 中相互关联：Trace ↔ Metrics 双向钻取（从慢指标下钻到具体 Trace）、日志行 `trace_id` 点击跳转 Trace。

**本期不做什么（MVP 边界）**



* **不做告警**：无 Alerting 规则与通知。

* **不做生产级高可用**：Tempo / Prometheus / Loki 均单实例本地存储，保留期短（Trace 1h / Metric 24h / Log 24h）。

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

\# 4. Explore → Loki，查看日志并验证跳转

\# 查询: {service_name="agent-demo"}

\# 日志行中 trace_id=xxx 显示为「查看 Trace」链接，点击跳转到 Tempo
```

***

## 8. 扩展场景：多容器多租户 opencode Agent 平台

前七章描述的是单体 agent-demo 应用。本章将同一套可观测方案外推到更接近生产的形态：**多容器、多租户的 opencode Agent 平台**——平台为每个租户（或每个会话/任务）动态拉起独立的 opencode 容器，Agent 实例通过 OpenTelemetry 插件把遥测数据统一送入本方案的 Tempo / Prometheus / Loki 监控栈。

### 8.1 平台拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                opencode Agent 平台 · 控制面（容器编排层）              │
│                                                                     │
│   Gateway / API（鉴权 · 配额 · 会话编排，可复用 agent-demo 埋点）      │
│        │                                                            │
│        │  每租户 / 每会话一个容器（docker/k8s 动态拉起、用完回收）     │
│        ├──► opencode 容器 tenantA · session-001（无头模式 run/exec） │
│        ├──► opencode 容器 tenantA · session-002                     │
│        └──► opencode 容器 tenantB · session-001 ...（N 个并发）      │
│                                                                     │
│   每个容器镜像内置 opencode-otel 插件，resource 携带租户身份          │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ OTLP gRPC :4317（tenant.id / session.id）
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  采集层 otel-collector（多副本 + LB，sending_queue / retry）          │
│   resourcedetection → resource/tenant（归一化租户身份）               │
│   → memory_limiter → batch → 按 traces/metrics/logs 分流            │
└───────────┬─────────────────────┬──────────────────────┬────────────┘
            ▼                     ▼                      ▼
     Tempo（Trace）         Prometheus（Metric）      Loki（Log）
  OrgID 隔离或 tenant.id   tenant_id 标签 + 基数治理   OrgID 隔离或
  属性 + TraceQL 检索      + Recording Rules          tenant_id 标签
            └─────────────────────┼──────────────────────┘
                                  ▼
                 Grafana：按租户 Folder/权限隔离，$tenant/$session
                 模板变量，日志行 trace_id 一键跳转 Tempo
```

设计要点：

* **一个会话一个容器**：租户之间天然隔离（文件系统 / 网络 / 凭证），每个容器同时就是一个独立遥测源，Resource 上带 `tenant.id`、`session.id`、`host.name`（容器 ID / Pod 名）。

* **网关也是被观测对象**：把「鉴权 → 配额 → 拉起容器 → 回收」的编排过程同样打点（agent-demo 的埋点层可直接复用），才能回答「这个租户的会话为什么排队 / 为什么失败」。

* **同一套语义约定**：平台只是把「一个应用」变成「N 个可替换的 Agent 实例」，GenAI SemConv、OTLP 上报与 Grafana 钻取方式完全不变。

### 8.2 Agent 侧：用 opencode-otel 插件接入

[opencode-otel](https://www.npmjs.com/package/opencode-otel)（npm v1.2.1+，MIT）是 opencode 的 OpenTelemetry 插件：初始化时拦截 opencode 进程的 stderr 输出，按 `ERROR / WARN / INFO / DEBUG` 前缀解析级别封装为 OTLP LogRecord 批量导出（stderr 原样保留、不受影响）；同时支持可选的「会话关联 Trace」。未使用的 metrics 相关环境变量会被安全忽略——即插件默认提供 **Logs（必选）+ Trace（可选）** 两种信号。

**1) 在 opencode 配置中启用插件（打进容器镜像）**

```json
{
  "plugin": ["opencode-otel"]
}
```

> npm 插件由 opencode 启动时经 Bun 自动安装（需外网）；离线环境把包预装进镜像，或改用本地插件目录 `~/.config/opencode/plugins/`。

**2) 平台拉起容器时注入环境变量（按租户赋值）**

```bash
# 日志 → Collector（必填；未设置时插件保持不激活）
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://otel-collector:4317
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=grpc          # 或 http/json

# 会话关联 Trace（可选，仅支持 grpc）
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_TIMEOUT=2000

# 租户身份：service.name 按租户命名，tenant.id / session.id 用于聚合与检索
export OTEL_RESOURCE_ATTRIBUTES=service.name=opencode-tenantA,service.namespace=agent-platform,tenant.id=tenantA,session.id=s-20260927-001,host.name=$(hostname)
export OTEL_SERVICE_NAME=opencode-tenantA
```

> `service.name` 优先级：`OTEL_RESOURCE_ATTRIBUTES` > `OTEL_SERVICE_NAME` > 插件 `otel.json` > 默认 `opencode-agent`。

**3) 关键环境变量速查**

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | ✅ | OTLP 日志端点，指向 `otel-collector:4317`；未设置时插件不启用 |
| `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` | — | `grpc`（默认）或 `http/json` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `_PROTOCOL` | — | 可选会话关联 Trace（仅 grpc） |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | — | 上报超时（ms） |
| `OTEL_RESOURCE_ATTRIBUTES` | — | 租户身份来源：`service.name` / `tenant.id` / `session.id` / `host.name` 等 |
| `OTEL_EXPORTER_OTLP_HEADERS` | — | 鉴权头（`key=value` 逗号分隔），多租户网关鉴权时使用 |
| `OTEL_PLUGIN_CONFIG_PATH` | — | 指向容器内专属 `otel.json`（多租户部署官方推荐用法） |

**4) 多租户配置文件**：插件支持 `~/.config/opencode/plugins/otel.json`（`logsEndpoint` / `logsProtocol` / `tracesEndpoint` / `serviceName` / `timeoutMs` / `maxLineLength`）。多租户部署时用 `OTEL_PLUGIN_CONFIG_PATH` 让每个容器挂载自己的配置；环境变量优先级高于该文件。

**5) 更细的会话粒度（可选增强）**：opencode-otel 聚焦运行时日志搬运与 log→trace 关联。若需要「会话 → 消息 → 工具调用」的完整 Span 树与自定义指标，可按 opencode 插件规范（`@opencode-ai/plugin`）自研插件：在 `session.created`、`message.part.updated`、`tool.execute.before/after`、`session.error`、`session.idle` 等钩子中创建 Span / 记录指标，复用同一 OTLP 出口与租户 Resource 身份。

### 8.3 监控栈的多租户适配

单体「够用」的部署在 N 个并发容器下需要四方面升级：**采集层弹性、租户身份标准化、存储隔离与容量、展示隔离**。

#### 8.3.1 Collector：弹性与租户标准化

```yaml
processors:
  resourcedetection:          # 自动补齐容器身份（k8s/docker 环境）
    detectors: [env, system, docker]
  resource/tenant:            # 归一化租户身份，缺失时回填
    attributes:
      - key: service.namespace
        value: agent-platform
        action: upsert
  memory_limiter: { check_interval: 1s, limit_percentage: 75, spike_limit_percentage: 20 }
  batch: { send_batch_size: 2048, timeout: 5s }

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls: { insecure: true }
    sending_queue: { enabled: true }        # 后端抖动时缓冲不丢
    retry_on_failure: { enabled: true }

service:
  pipelines:
    traces:  { receivers: [otlp], processors: [resourcedetection, resource/tenant, memory_limiter, batch], exporters: [otlp/tempo] }
    metrics: { receivers: [otlp], processors: [resourcedetection, resource/tenant, memory_limiter, batch], exporters: [prometheus] }
    logs:    { receivers: [otlp], processors: [resourcedetection, resource/tenant, memory_limiter, batch], exporters: [loki] }
```

* **多副本 + LB**：并发容器多时 Collector 横向扩副本，应用/插件侧 OTLP 指向 Service/LB；`sending_queue` + `retry_on_failure` 保证后端抖动不丢数据。

* **租户标签治理**：三类信号统一带 `tenant.id` / `session.id`；Metrics 开启 `resource_to_telemetry_conversion` 后成为 PromQL 可聚合的 label（`tenant.id` → `tenant_id`）。

* **规模更大时**：traces 管道加 `tail_sampling`（错误与慢请求全保留、其余按比例采样），用 `filter` / `transform` 控制高基数字段；用 routing connector 按租户分流到不同后端，防止单租户突发打爆共享存储。

#### 8.3.2 四个后端的适配要点

| 后端 | 单体默认（前文） | 多租户适配 |
| --- | --- | --- |
| Tempo | 保留 1h，单租户 | 保留期调大（24h~7d）。多租户两路线：① `multitenant_enabled: true` + 按租户注入 `X-Scope-OrgID`（需 per-tenant exporter / routing connector，隔离强）；② 单租户模式，靠 Resource 属性检索 `{ resource.tenant.id="tenantA" }`（部署简单，隔离弱） |
| Prometheus | 保留 24h | 保留 7~30d，配 `--storage.tsdb.retention.size` 控盘；`tenant_id` 成为普通 label，注意基数（租户数 × 实例数 × 指标 × 标签组合）；用 Recording Rules 预聚合按租户关键指标；更大规模按租户分片/联邦 |
| Loki | 单租户、保留 24h | 推荐开启 `auth_enabled: true` 走 `X-Scope-OrgID` 真隔离（Collector 侧用 routing connector 或 Loki 原生 OTLP 端点按租户写）；或单租户模式把 `tenant_id` 作为流标签（流基数 = 租户 × 服务 × 级别，可控），配 per-tenant 限流与保留期 |
| Grafana | 匿名 Admin 单空间 | 按租户建 Folder + Team 权限隔离；仪表盘顶部加 `$tenant` / `$session` 模板变量（`label_values(tenant_id)`）；多租户后端需在数据源配置 `httpHeader: X-Scope-OrgID`；大型场景可按租户拆 Grafana 组织/实例 |

#### 8.3.3 Docker Compose 形态的最小改动

在现有 `deploy/` 上演进（保留单机演示可行性）：

* `otel-collector/config.yaml`：加 `resourcedetection` / `resource` 处理器与 `sending_queue` / `retry_on_failure`；Collector 可扩多副本（compose 下先单副本）。

* `loki/loki.yaml`：选真隔离则 `auth_enabled: true`（并调整数据源与导出器带 OrgID）；否则保持现状仅靠 `tenant_id` 标签。

* `prometheus`：`--storage.tsdb.retention.time=168h`（或加 `retention.size`）；`tempo.yaml`：`compactor.block_retention=24h`。

* Grafana provisioning：新增带 `$tenant` 变量的平台总览仪表盘与按租户 Folder。

* 新增 `platform-gateway` 服务：租户鉴权、容器编排与回收，本身作为 `app` 接入同一 Collector。

> **生产建议**：容器编排上 k8s（HPA 弹性 + 每租户 ResourceQuota）；Collector 用 Deployment 多副本（或 DaemonSet）；Tempo / Loki 存储换对象存储（S3 后端）以支撑长保留期；配额、保留期、告警按租户商业策略配置。

### 8.4 一次会话的全链路（多租户视角）

```
用户(tenantA) ──► 网关鉴权/配额          Span: gateway.create_session { tenant.id="tenantA" }
      ──► 拉起 opencode 容器             Metrics: 容器/平台编排指标；Trace 贯穿编排与 Agent
      ──► opencode-otel 插件             Logs: {service_name="opencode-tenantA"} |= "ERROR"
      ──► Collector（resource/tenant）    Trace: { resource.tenant.id="tenantA" }
      ──► Grafana 按 $tenant 下钻         日志行 trace_id ──点击──► Tempo 对应 Trace
```

排查路径与第 2 章完全一致：先看平台指标（租户会话数 / 失败率）→ TraceQL 定位慢/错会话 → 日志行点击 trace_id 还原现场；唯一区别是所有查询先带上 `tenant.id` 维度。
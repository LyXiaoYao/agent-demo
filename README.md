# agent-demo

一个简单的 Python AI Agent 应用，基于 OpenAI SDK 实现 tool-calling 循环。

## 功能

- 对话式 Agent，支持多轮上下文
- 内置示例工具：计算器、获取当前时间
- 支持任何 OpenAI 兼容 API（OpenAI、DeepSeek、vLLM 等）

## 快速开始

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY（可选 OPENAI_BASE_URL 和 MODEL_NAME）

# 3. 运行
agent-demo
```

## 项目结构

```
src/agent_demo/
├── agent.py   # Agent 核心循环
├── main.py    # CLI 入口
└── tools.py   # 工具定义与实现
```

## 添加新工具

在 `src/agent_demo/tools.py` 中：

1. 在 `TOOLS` 列表添加工具的 JSON Schema 定义
2. 实现 `_your_tool()` 函数
3. 在 `run_tool()` 中注册分发逻辑

## License

MIT

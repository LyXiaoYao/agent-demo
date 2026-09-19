"""A minimal tool-calling agent built on the OpenAI SDK."""

import logging
import os
import time
from typing import Iterator

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

from .telemetry import (
    record_llm_duration,
    record_token_usage,
    record_tool_call,
    span_context,
    start_span,
)
from .tools import TOOLS, run_tool

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "You are a helpful assistant. Use tools when needed."

# Ask the provider to include token usage in streaming responses. Some
# OpenAI-compatible endpoints reject this, in which case we retry without it.
INCLUDE_STREAM_USAGE = os.getenv("LLM_INCLUDE_USAGE", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _make_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )


def _agent_span(model: str):
    return start_span(
        "invoke_agent agent-demo",
        {
            "gen_ai.system": "openai",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.request.model": model,
        },
    )


def _record_usage(span, model: str, usage) -> None:
    if usage is None:
        return
    record_token_usage(model, usage.prompt_tokens, usage.completion_tokens)
    span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage.completion_tokens)


def _run_tool_span(name: str, call_id: str, arguments: str, context) -> str:
    span = start_span(
        f"execute_tool {name}",
        {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": call_id,
        },
        context=context,
    )
    logger.info("执行工具 name=%s 参数=%s", name, arguments)
    try:
        result = run_tool(name, arguments)
        record_tool_call(name, "success")
        logger.info("工具执行成功 name=%s 结果=%s", name, result)
        return result
    except Exception as e:  # noqa: BLE001 - re-raised after recording
        record_tool_call(name, "error")
        logger.error("工具执行失败 name=%s 错误=%s", name, e)
        span.record_exception(e)
        span.set_attribute("error.type", type(e).__name__)
        raise
    finally:
        span.end()


def run_agent(client: OpenAI, model: str, user_input: str, messages: list) -> str:
    """Run one agent turn: send messages, execute tool calls, loop until final answer."""
    messages = messages + [{"role": "user", "content": user_input}]

    agent_span = _agent_span(model)
    context = span_context(agent_span)
    try:
        while True:
            llm_span = start_span(
                f"chat {model}",
                {
                    "gen_ai.system": "openai",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": model,
                },
                context=context,
            )
            started = time.perf_counter()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=TOOLS,
                )
                record_llm_duration(model, time.perf_counter() - started)
                _record_usage(llm_span, model, getattr(response, "usage", None))
                llm_span.set_attribute(
                    "gen_ai.response.model", getattr(response, "model", model)
                )
            except Exception as e:  # noqa: BLE001 - re-raised after recording
                llm_span.record_exception(e)
                llm_span.set_attribute("error.type", type(e).__name__)
                raise
            finally:
                llm_span.end()

            message = response.choices[0].message

            if not message.tool_calls:
                messages.append({"role": "assistant", "content": message.content})
                return message.content

            messages.append(message.model_dump())
            for tool_call in message.tool_calls:
                result = _run_tool_span(
                    tool_call.function.name,
                    tool_call.id,
                    tool_call.function.arguments,
                    context,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
    except Exception as e:  # noqa: BLE001 - re-raised after recording
        agent_span.record_exception(e)
        agent_span.set_attribute("error.type", type(e).__name__)
        raise
    finally:
        agent_span.end()


def _create_stream(client: OpenAI, model: str, messages: list):
    kwargs = {"model": model, "messages": messages, "tools": TOOLS, "stream": True}
    if INCLUDE_STREAM_USAGE:
        try:
            return client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True}
            )
        except BadRequestError:
            pass
    return client.chat.completions.create(**kwargs)


def run_agent_stream(
    client: OpenAI, model: str, user_input: str, messages: list
) -> Iterator[dict]:
    """Stream one agent turn, yielding events as they happen.

    Events:
        {"type": "text", "content": str}          incremental answer text
        {"type": "tool", "name": str, "arguments": str}  a tool is about to run
        {"type": "tool_result", "name": str, "content": str}  tool output
        {"type": "done"}                          the turn finished
    """
    messages.append({"role": "user", "content": user_input})

    agent_span = _agent_span(model)
    context = span_context(agent_span)
    try:
        while True:
            llm_span = start_span(
                f"chat {model}",
                {
                    "gen_ai.system": "openai",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": model,
                },
                context=context,
            )
            started = time.perf_counter()
            content_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            usage = None

            try:
                stream = _create_stream(client, model, messages)
                for chunk in stream:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield {"type": "text", "content": delta.content}
                    for call in delta.tool_calls or []:
                        entry = tool_calls.setdefault(
                            call.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if call.id:
                            entry["id"] = call.id
                        if call.function:
                            if call.function.name:
                                entry["name"] += call.function.name
                            if call.function.arguments:
                                entry["arguments"] += call.function.arguments
            except Exception as e:  # noqa: BLE001 - re-raised after recording
                llm_span.record_exception(e)
                llm_span.set_attribute("error.type", type(e).__name__)
                raise
            finally:
                elapsed = time.perf_counter() - started
                record_llm_duration(model, elapsed)
                _record_usage(llm_span, model, usage)
                logger.info(
                    "LLM 调用完成 model=%s 耗时=%.2fs 输入tokens=%s 输出tokens=%s",
                    model,
                    elapsed,
                    usage.prompt_tokens if usage else "-",
                    usage.completion_tokens if usage else "-",
                )
                llm_span.end()

            content = "".join(content_parts)

            if not tool_calls:
                messages.append({"role": "assistant", "content": content})
                yield {"type": "done"}
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tool_calls[index]["id"],
                            "type": "function",
                            "function": {
                                "name": tool_calls[index]["name"],
                                "arguments": tool_calls[index]["arguments"],
                            },
                        }
                        for index in sorted(tool_calls)
                    ],
                }
            )

            for index in sorted(tool_calls):
                call = tool_calls[index]
                yield {
                    "type": "tool",
                    "name": call["name"],
                    "arguments": call["arguments"],
                }
                result = _run_tool_span(
                    call["name"], call["id"], call["arguments"], context
                )
                yield {
                    "type": "tool_result",
                    "name": call["name"],
                    "content": result,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
    except Exception as e:  # noqa: BLE001 - re-raised after recording
        agent_span.record_exception(e)
        agent_span.set_attribute("error.type", type(e).__name__)
        raise
    finally:
        agent_span.end()

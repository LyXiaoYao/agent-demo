"""A minimal tool-calling agent built on the OpenAI SDK."""

import os

from dotenv import load_dotenv
from openai import OpenAI

from .tools import TOOLS, run_tool

load_dotenv()


def _make_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )


def run_agent(client: OpenAI, model: str, user_input: str, messages: list) -> str:
    """Run one agent turn: send messages, execute tool calls, loop until final answer."""
    messages = messages + [{"role": "user", "content": user_input}]

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content})
            return message.content

        messages.append(message.model_dump())
        for tool_call in message.tool_calls:
            result = run_tool(tool_call.function.name, tool_call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

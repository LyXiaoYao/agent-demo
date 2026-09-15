"""CLI entry point for the agent."""

import os

from openai import OpenAI

from .agent import _make_client, run_agent


def main() -> None:
    """Start an interactive chat loop with the agent."""
    model = os.getenv("MODEL_NAME", "gpt-4o-mini")
    client: OpenAI = _make_client()
    messages: list = [
        {"role": "system", "content": "You are a helpful assistant. Use tools when needed."}
    ]

    print(f"Agent ready (model: {model}). Type 'exit' to quit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() == "exit":
            break

        try:
            reply = run_agent(client, model, user_input, messages)
        except Exception as e:
            print(f"Error: {e}\n")
            continue

        print(f"Agent: {reply}\n")


if __name__ == "__main__":
    main()

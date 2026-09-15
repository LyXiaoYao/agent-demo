"""Tool definitions and implementations for the agent."""

import json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a basic arithmetic expression, e.g. '2 + 3 * 4'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The arithmetic expression to evaluate.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current local time in ISO format.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def run_tool(name: str, arguments: str) -> str:
    """Dispatch a tool call and return the result as a string."""
    args = json.loads(arguments) if arguments else {}
    if name == "calculator":
        return _calculator(args["expression"])
    if name == "get_current_time":
        return _get_current_time()
    return f"Unknown tool: {name}"


def _calculator(expression: str) -> str:
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "Error: only numbers and + - * / ( ) are allowed."
    try:
        return str(eval(expression))  # noqa: S307 - input is whitelisted above
    except Exception as e:
        return f"Error: {e}"


def _get_current_time() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")

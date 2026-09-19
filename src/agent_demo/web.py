"""Web service exposing the agent through a streaming chat page."""

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from .agent import SYSTEM_PROMPT, _make_client, run_agent_stream
from .telemetry import current_trace_id, record_request, setup_telemetry

setup_telemetry()

logger = logging.getLogger(__name__)

app = FastAPI(title="agent-demo")
FastAPIInstrumentor.instrument_app(app)

client = _make_client()
model = os.getenv("MODEL_NAME", "gpt-4o-mini")

# In-memory conversation store, keyed by session id.
sessions: dict[str, list] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text("utf-8")


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id or str(uuid.uuid4())
    trace_id = current_trace_id()
    trace.get_current_span().set_attribute("agent.session_id", session_id)
    logger.info("收到对话请求 session_id=%s trace_id=%s", session_id, trace_id)
    messages = sessions.setdefault(
        session_id, [{"role": "system", "content": SYSTEM_PROMPT}]
    )

    def event_stream():
        status = "success"
        yield _sse({"type": "session", "session_id": session_id})
        try:
            for event in run_agent_stream(client, model, req.message, messages):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 - surface errors to the browser
            status = "error"
            logger.exception(
                "对话处理异常 session_id=%s trace_id=%s", session_id, trace_id
            )
            yield _sse({"type": "error", "message": str(e)})
        finally:
            record_request(status)
            logger.info(
                "对话结束 session_id=%s status=%s trace_id=%s",
                session_id,
                status,
                trace_id,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("WEB_PORT", "8000")),
    )


if __name__ == "__main__":
    main()

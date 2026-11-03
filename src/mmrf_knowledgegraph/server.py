"""HTTP contract usable locally and inside AgentCore Runtime (ARM64, port 8080)."""

import json
import logging
import os
import secrets

import boto3
from botocore.config import Config
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from .agent import ChatRequest, create_agent
from .virtual_lab import ToolError

app = FastAPI(title="ORION research prototype")
log = logging.getLogger("orion")


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


def events(request):
    try:
        for event in create_agent(request).run(request):
            yield "data: " + json.dumps(event) + "\n\n"
    except Exception as exc:
        log.exception("ORION invocation failed")
        error = (
            {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
            if isinstance(exc, ToolError)
            else {
                "code": "agent_unavailable",
                "message": "ORION could not complete the request. Check model access and graph connectivity.",
                "retryable": True,
            }
        )
        yield "data: " + json.dumps({"event": "error", "error": error}) + "\n\n"


@app.post("/invocations")
def invoke(request: ChatRequest, x_orion_token: str = Header(default="")):
    expected = os.getenv("ORION_INTERNAL_TOKEN")
    if expected and not secrets.compare_digest(x_orion_token, expected):
        raise HTTPException(401, "Unauthorized")
    return StreamingResponse(
        events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/agentcore")
def agentcore(request: ChatRequest, x_orion_token: str = Header(default="")):
    """Private bridge: frontend calls this service; AWS credentials remain server-side."""
    expected = os.getenv("ORION_INTERNAL_TOKEN")
    if expected and not secrets.compare_digest(x_orion_token, expected):
        raise HTTPException(401, "Unauthorized")
    arn = os.getenv("ORION_RUNTIME_ARN")
    if not arn:
        raise HTTPException(503, "AgentCore runtime ARN is not configured")
    # The entire bounded history travels in the payload, so no process-local session state is needed.
    import uuid

    try:
        client = boto3.client(
            "bedrock-agentcore",
            config=Config(connect_timeout=5, read_timeout=240, retries={"max_attempts": 1}),
        )
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=str(uuid.uuid4()),
            payload=request.model_dump_json().encode(),
            contentType="application/json",
            accept="text/event-stream",
        )
    except Exception as exc:
        log.exception("AgentCore invocation failed")
        raise HTTPException(502, "AgentCore runtime is unavailable") from exc

    def stream():
        try:
            for chunk in response["response"].iter_chunks(chunk_size=1024):
                yield chunk
        finally:
            response["response"].close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

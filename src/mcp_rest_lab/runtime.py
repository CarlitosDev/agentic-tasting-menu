"""runtime.py — AgentCore serving boundary that Streamlit calls (§5.7).

Uses BedrockAgentCoreApp, which PROVIDES the HTTP server with POST /invocations
and GET /ping on :8080. We do NOT hand-roll FastAPI/Starlette for those routes
(locked decision §4.3, regression guard §7).

The same artifact runs locally and in the managed runtime; going to prod is a
deploy, not a rewrite.

Run:  uv run python -m mcp_rest_lab.runtime   (serves :8080)
"""

from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .agent import stream_answer
from .auth import bearer_from_header

app = BedrockAgentCoreApp()


def _token_from(payload: dict, context) -> str:
    """Resolve the bearer token: inbound header first, payload fallback (local).

    VERIFY (confirmed against bedrock-agentcore 1.13.0): the entrypoint context is
    a RequestContext whose ``request_headers`` dict carries the inbound
    Authorization header under the canonical key "Authorization" (the SDK
    normalizes wire casing). For local curl/testing without that header we fall
    back to a token in the payload.
    """
    headers = getattr(context, "request_headers", None) or {}
    auth_header = headers.get("Authorization") or headers.get("authorization")
    if auth_header:
        return bearer_from_header(auth_header)

    token = payload.get("token")
    if token:
        return token
    raise ValueError(
        "no bearer token: send 'Authorization: Bearer <jwt>' or include "
        "'token' in the payload for local testing"
    )


@app.entrypoint
async def invoke(payload, context):
    """Stream the agent's answer for payload['prompt'] under the caller's token.

    VERIFY (confirmed against bedrock-agentcore 1.13.0): an async-generator
    entrypoint is served as an SSE StreamingResponse; entrypoints that accept a
    second `context` parameter receive the RequestContext. We yield text chunks so
    the client renders incrementally.
    """
    prompt = payload.get("prompt")
    if not prompt:
        yield "error: missing 'prompt' in payload"
        return

    try:
        token = _token_from(payload, context)
    except ValueError as e:
        yield f"error: {e}"
        return

    try:
        async for chunk in stream_answer(prompt, token):
            yield chunk
    except Exception as e:
        yield f"error: {e}"


if __name__ == "__main__":
    # SDK-provided server: /invocations + /ping on :8080. Same call runs locally
    # and inside the managed runtime.
    app.run()

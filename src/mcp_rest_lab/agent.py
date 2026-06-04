"""agent.py — Strands agent factory + MCP client wiring (§5.6).

The agent's tools ARE the MCP tools. Identity is never passed to the agent as a
tool argument — it flows only as the bearer token forwarded on the MCP client's
HTTP connection (§6). The MCP server below is the auth boundary, not the agent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# VERIFY (confirmed against mcp 1.27.2): default FastMCP streamable-HTTP path is
# "/mcp", so the full URL is host:port + /mcp.
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")

# VERIFY: a Bedrock model id available in your account/region. Cross-region
# inference profile ids look like "us.anthropic.claude-sonnet-4-*". Override via
# env without touching code.
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# The prompt deliberately names NO tool. Tool selection falls out of the tool
# descriptions (see mcp_server.py docstrings), which the MCP client lists into the
# model's context every turn. So this stays pure policy — behavior the schemas
# can't express — and survives a tool rename untouched. We describe tools by the
# capability we want ("the tool that returns the caller's own status"), never by
# identifier.
SYSTEM_PROMPT = (
    "You are a student-status assistant. Answer using the available tools, "
    "choosing the tool whose description matches the request. "
    "When the caller asks about their own status, use the tool that returns the "
    "caller's own status; it needs no identity argument, because identity comes "
    "from the caller's token, not from anything they type. "
    "When the caller asks about a group of students, use the tool that returns "
    "statuses for the specific student ids they name. "
    "Never ask the user for their identity; it is established by their token. "
    "Report exactly what the tools return."
)


# The Bedrock model is identity-independent (it carries no token), so it is built
# once and reused across requests instead of being rebuilt — and a new boto3
# client spun up — on every invocation.
_MODEL: BedrockModel | None = None


def build_model() -> BedrockModel:
    """Build (once) the Bedrock model the agent reasons with.

    Cached in a module-level singleton: nothing about the model depends on the
    caller's identity, so there is no reason to reconstruct it per request.

    VERIFY (confirmed against strands-agents 1.42.0): BedrockModel takes
    region_name plus model config kwargs (model_id) via **model_config.
    """
    global _MODEL
    if _MODEL is None:
        _MODEL = BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION)
    return _MODEL


def build_agent(mcp_client: MCPClient, model: BedrockModel | None = None) -> Agent:
    """Build a Strands Agent whose tools are the MCP server's tools.

    Must be called while ``mcp_client`` is active (inside ``with mcp_client:``),
    because listing/calling tools uses the client's live session.
    """
    tools = mcp_client.list_tools_sync()
    return Agent(
        model=model or build_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )


async def stream_answer(prompt: str, bearer_token: str) -> AsyncIterator[str]:
    """Given a prompt + bearer token, yield the agent's response text in chunks.

    The MCP connection is necessarily per-request: identity *is* the token, and it
    rides on this HTTP connection (§6), so it cannot be shared across callers. The
    httpx.AsyncClient that carries the Authorization header is therefore scoped to
    the request via ``async with`` — we own it, so we must close it. (mcp 1.9+
    renamed streamablehttp_client → streamable_http_client and dropped the headers=
    kwarg; headers now travel on a pre-configured client, which the transport will
    NOT close when caller-provided.)

    The client context stays open for the whole stream so tool calls succeed.

    VERIFY (confirmed against strands-agents 1.42.0): stream_async yields event
    dicts; text deltas arrive under the "data" key.
    """
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {bearer_token}"}
    ) as http_client:
        mcp_client = MCPClient(
            lambda: streamable_http_client(MCP_URL, http_client=http_client)
        )
        with mcp_client:
            agent = build_agent(mcp_client)
            async for event in agent.stream_async(prompt):
                if "data" in event:
                    yield event["data"]

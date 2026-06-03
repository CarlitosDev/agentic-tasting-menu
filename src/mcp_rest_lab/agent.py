"""agent.py — Strands agent factory + MCP client wiring (§5.6).

The agent's tools ARE the MCP tools. Identity is never passed to the agent as a
tool argument — it flows only as the bearer token forwarded on the MCP client's
HTTP connection (§6). The MCP server below is the auth boundary, not the agent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# VERIFY (confirmed against mcp 1.27.2): default FastMCP streamable-HTTP path is
# "/mcp", so the full URL is host:port + /mcp.
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")

# VERIFY: a Bedrock model id available in your account/region. Cross-region
# inference profile ids look like "us.anthropic.claude-sonnet-4-*". Override via
# env without touching code.
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"
)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

SYSTEM_PROMPT = (
    "You are a student-status assistant. Use the available tools to answer. "
    "A student asking about themselves -> use get_my_status (it takes no arguments; "
    "the caller's identity comes from their token). A teacher asking about the "
    "group -> use get_group_status with the student ids they mention. "
    "Never ask the user for their identity; it is established by their token. "
    "Report exactly what the tools return."
)


def build_model() -> BedrockModel:
    """Build the Bedrock model the agent reasons with.

    VERIFY (confirmed against strands-agents 1.42.0): BedrockModel takes
    region_name plus model config kwargs (model_id) via **model_config.
    """
    return BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION)


def build_mcp_client(bearer_token: str) -> MCPClient:
    """Build an MCP client over streamable HTTP, forwarding the bearer token.

    VERIFY (confirmed against strands-agents 1.42.0 / mcp 1.27.2): MCPClient takes
    a transport_callable returning the transport context manager;
    streamablehttp_client(url, headers=...) accepts custom headers. We forward the
    caller's token as Authorization so the MCP server can verify() it.
    """
    return MCPClient(
        lambda: streamablehttp_client(
            MCP_URL,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    )


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

    Opens the MCP client (which forwards the token), builds the agent, and streams.
    The client context stays open for the whole stream so tool calls succeed.

    VERIFY (confirmed against strands-agents 1.42.0): stream_async yields event
    dicts; text deltas arrive under the "data" key.
    """
    mcp_client = build_mcp_client(bearer_token)
    with mcp_client:
        agent = build_agent(mcp_client)
        async for event in agent.stream_async(prompt):
            if "data" in event:
                yield event["data"]

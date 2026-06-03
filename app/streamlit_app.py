"""streamlit_app.py — thin HTTP client (§5.8). DOES NOT import the agent.

Pure client: it picks an identity (and thus a token), POSTs the prompt to
ENDPOINT_URL with ``Authorization: Bearer <jwt>``, and renders the streamed
response. Going to prod = point ENDPOINT_URL at the AgentCore Runtime invoke
endpoint; nothing else here changes (§5.8, regression guard §7).

Run:  uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import streamlit as st

ENDPOINT_URL = os.environ.get("ENDPOINT_URL", "http://localhost:8080/invocations")
# tokens.json is produced by `python -m mcp_rest_lab.mint_tokens`.
TOKENS_FILE = Path(os.environ.get("TOKENS_FILE", "tokens.json"))


def load_tokens() -> dict[str, str]:
    """Load the identity->token map written by mint_tokens, if present."""
    if TOKENS_FILE.exists():
        try:
            return json.loads(TOKENS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def iter_sse_text(response: httpx.Response):
    """Yield text chunks from the AgentCore SSE stream.

    The runtime serializes each yielded value as ``data: <json>\\n\\n``. A plain
    text delta deserializes to a str; an error event deserializes to a dict with
    an "error" key.
    """
    for line in response.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if isinstance(value, dict) and "error" in value:
            yield f"\n\n**[error]** {value['error']}"
        elif isinstance(value, str):
            yield value
        else:
            yield str(value)


st.title("mcp-rest-lab")
st.caption("Thin client → AgentCore /invocations. Identity is the token.")

tokens = load_tokens()
identities = list(tokens.keys()) or [
    "student_001",
    "student_002",
    "student_003",
    "student_004",
    "student_005",
    "teacher_001",
]

with st.sidebar:
    st.header("Identity")
    identity = st.selectbox("Act as", identities)
    token = tokens.get(identity, "")
    if not token:
        token = st.text_area(
            "Bearer token",
            help="No tokens.json found. Run `python -m mcp_rest_lab.mint_tokens` "
            "or paste a token here.",
        )
    st.caption(f"Endpoint: {ENDPOINT_URL}")

default_prompt = (
    "How is the group doing on homework?"
    if identity.startswith("teacher")
    else "How am I doing on my homework?"
)
prompt = st.text_input("Prompt", value=default_prompt)

if st.button("Send", type="primary"):
    if not token:
        st.error("No token for this identity. Mint tokens or paste one.")
    else:
        headers = {"Authorization": f"Bearer {token}"}
        body = {"prompt": prompt}
        placeholder = st.empty()
        acc = ""
        try:
            with httpx.stream(
                "POST", ENDPOINT_URL, json=body, headers=headers, timeout=120
            ) as response:
                response.raise_for_status()
                for piece in iter_sse_text(response):
                    acc += piece
                    placeholder.markdown(acc)
        except httpx.HTTPError as e:
            st.error(f"Request failed: {e}")

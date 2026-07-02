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
# The FastAPI REST learner-state API (§5.5), queried directly — no agent in between.
API_URL = os.environ.get("API_URL", "http://localhost:8001/students/status")
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
# Identities come straight from tokens.json — nothing is hardcoded here. Students
# (for the REST tab's id dropdown) are simply the non-teacher identities; the
# minter names every teacher with a "teacher" prefix, the same convention the
# Agent tab's default-prompt branch relies on.
identities = list(tokens.keys())
STUDENTS = [i for i in identities if not i.startswith("teacher")]


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
    st.caption(f"Agent endpoint: {ENDPOINT_URL}")
    st.caption(f"REST endpoint: {API_URL}")

agent_tab, rest_tab = st.tabs(["Agent", "REST API"])

with agent_tab:
    st.caption("Prompt the agent → AgentCore /invocations. The agent picks tools.")
    default_prompt = (
        "How is the group doing?"
        if identity.startswith("teacher")
        else "How am I doing?"
    )
    prompt = st.text_input("Prompt", value=default_prompt)

    if st.button("Send", type="primary", key="agent_send"):
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

with rest_tab:
    st.caption("Call the FastAPI REST API directly — no agent.")
    student_options = STUDENTS if identity.startswith("teacher") else [identity]
    student_ids = st.multiselect(
        "Student ids", options=student_options, default=student_options, key="rest_ids"
    )
    window_days = st.number_input(
        "Window days",
        min_value=1,
        max_value=365,
        value=30,
        step=1,
        key="rest_window_days",
    )

    if st.button("Query", type="primary", key="rest_send"):
        if not token:
            st.error("No token for this identity. Mint tokens or paste one.")
        elif not student_ids:
            st.error("Select at least one student id.")
        else:
            headers = {"Authorization": f"Bearer {token}"}
            body = {"student_ids": student_ids, "window_days": int(window_days)}
            try:
                response = httpx.post(API_URL, json=body, headers=headers, timeout=30)
                response.raise_for_status()
                payload = response.json()
                results = payload.get("results", [])
                if not results:
                    st.info("No learner-state rows returned.")
                else:
                    active = sum(r["overall_state"] == "active" for r in results)
                    idle = sum(r["overall_state"] == "idle" for r in results)
                    inactive = sum(r["overall_state"] == "absent" for r in results)
                    cols = st.columns(3)
                    cols[0].metric("Active", active)
                    cols[1].metric("Idle", idle)
                    cols[2].metric("Inactive", inactive)

                    rows = [
                        {
                            "student_id": r["student_id"],
                            "status": r["status_label"],
                            "mode": r["engagement_mode"],
                            "last_activity": r["last_activity_at"],
                            "units_completed": r["self_study"]["units_completed"],
                            "accuracy": r["self_study"]["accuracy"],
                            "lessons": r["online"]["lesson_count"],
                            "level": r["online"]["level"],
                        }
                        for r in results
                    ]
                    st.dataframe(rows, use_container_width=True, hide_index=True)

                    selected = st.selectbox(
                        "Details",
                        options=[r["student_id"] for r in results],
                        key="rest_detail_id",
                    )
                    detail = next(r for r in results if r["student_id"] == selected)
                    st.markdown(detail["summary"])
                    left, right = st.columns(2)
                    left.subheader("Self-study")
                    left.json(detail["self_study"], expanded=False)
                    right.subheader("Online")
                    right.json(detail["online"], expanded=False)
            except httpx.HTTPStatusError as e:
                st.error(f"{e.response.status_code}: {e.response.text}")
            except httpx.HTTPError as e:
                st.error(f"Request failed: {e}")

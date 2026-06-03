# Build Spec — `mcp-rest-lab`

A toy-but-near-prod baseline for learning agentic API design: a layered system going
`core → MCP + REST → Strands agent → AgentCore serving → Streamlit client`, with
token-based identity propagated end to end.

> **For the implementing agent (Claude Code):** Build the full first iteration today.
> Follow the locked decisions and the "Do NOT" list exactly — several look like normal
> simplifications but are deliberate teaching points. Strands and Bedrock AgentCore move
> fast; **verify the installed package APIs against the patterns below and pin versions**
> rather than trusting any signature here verbatim. Where a reference pattern and the
> installed SDK disagree, follow the SDK and leave a `# VERIFY:` comment.

---

## 1. Goal

A runnable end-to-end skeleton I can iterate from toward production. Every layer should
mirror a real prod boundary so moving to managed AgentCore later is a config/URL swap,
not a rewrite. Correctness of *structure and boundaries* matters more than feature depth.

## 2. Tech stack

- Python 3.11+, managed with **`uv`**
- `pyjwt` — JWT mint/verify (HS256, toy)
- `mcp` (official SDK, includes **FastMCP**) — MCP server
- `strands-agents` (+ `strands-agents-tools` if needed) — agent layer
- `bedrock-agentcore` — runtime serving wrapper (`BedrockAgentCoreApp`)
- `fastapi` + `uvicorn` — REST **learning track only** (see §6)
- `httpx` — agent→MCP and Streamlit→endpoint HTTP
- `streamlit` — local UI (thin client)

## 3. Project structure

```
mcp-rest-lab/
  pyproject.toml
  README.md               # run sequence (§9) + token usage
  .env.example            # JWT_SECRET, MCP_URL, ENDPOINT_URL, BEDROCK_MODEL_ID, AWS_REGION
  src/mcp_rest_lab/
    core.py               # get_student_status (random)
    auth.py               # secret, make_token, verify, claim helpers  (SHARED)
    mint_tokens.py        # script: prints 5 student tokens + 1 teacher token
    mcp_server.py         # FastMCP: get_my_status, get_group_status  (calls core directly)
    api.py                # FastAPI REST learning track (off critical path)
    agent.py              # Strands agent factory + MCP client wiring
    runtime.py            # BedrockAgentCoreApp entrypoint: /invocations, /ping; app.run()
  app/
    streamlit_app.py      # thin HTTP client → /invocations
```

## 4. Locked architecture decisions (do not relitigate)

1. **MCP server calls `core` directly. MCP does NOT call the REST API.** (Intentional.)
2. **The REST API (FastAPI) is a parallel learning track**, calling `core` directly. It is
   **not** on the agent's critical path and nothing depends on it.
3. **The agent is served by `BedrockAgentCoreApp`, not by hand-written FastAPI.** Do not
   build a custom FastAPI server for `/invocations`/`/ping` — the AgentCore SDK provides it.
4. **Identity is the token.** The student tool takes **no `student_id` argument** — it
   derives identity from the verified token's `sub`. The teacher tool authorizes against
   the token's `role` + `students` claim.
5. **MCP tools return shaped, minimal responses** (the answer), never raw passthrough.
6. **HS256 + local `mint_tokens.py` is a stand-in for a real IdP.** Keep claim *shapes*
   identical to what a Cognito/Auth0 token would carry so the swap to RS256/JWKS is clean.

## 5. Layer requirements

### 5.1 `core.py`
- `get_student_status(student_id: str) -> str`
- Returns, at random (`random.choice`), either
  `f"{student_id} has done the homework"` or `f"{student_id} hasn't done any homework"`.
- Pure, no I/O, no auth. This is the single source of truth both MCP and REST call.

### 5.2 `auth.py` (shared by MCP and REST)
- `make_token(sub: str, role: str, **extra) -> str` — HS256, claims:
  `iss`, `aud` (both `"mcp-rest-lab"`), `sub`, `role`, `iat`, `exp` (TTL ~3600s), plus `extra`.
- `verify(token: str) -> dict` — `jwt.decode(...)` passing `audience` and `issuer` so they
  are actually validated; raises on bad/expired.
- Helper to extract a bearer token from an `Authorization: Bearer <t>` header value.
- `STUDENTS = [f"student_{i:03d}" for i in range(1, 6)]`.
- Read `JWT_SECRET` from env, default to a dev-only constant.

### 5.3 `mint_tokens.py`
- Run as a script. Print 5 **student** tokens (`role="student"`, `sub=student_00X`) and
  1 **teacher** token (`role="teacher"`, `sub="teacher_001"`, `students=STUDENTS`).
- Output copy-pasteable: `student_001  <token>` per line.

### 5.4 `mcp_server.py` (FastMCP)
- Two tools:
  - **`get_my_status()`** — student self-query. **No parameters.** Reads the verified
    token from the request context, takes `sub`, calls `core.get_student_status(sub)`,
    returns a shaped dict e.g. `{"student_id": sub, "status": "..."}`.
  - **`get_group_status(student_ids: list[str])`** — teacher query. Requires
    `role == "teacher"`; every requested id must be a **subset** of the token's `students`
    claim (else reject). Calls `core` per id (parallel via `asyncio.gather`), returns a
    shaped list. Reject student-role callers.
- **Transport: streamable HTTP** so the bearer token rides in request headers. Each tool
  must read + `verify()` the incoming token; on failure return an MCP error, not data.
  - `# VERIFY:` exact FastMCP API for reading request headers / context in the installed
    version, and the streamable-HTTP run call.
- Server is its own process/port (e.g. `:8000`).

### 5.5 `api.py` (FastAPI — learning track, off critical path)
- One endpoint: `POST /students/status` taking `{"student_ids": [...]}`, fanning out to
  `core.get_student_status` **in parallel** (`asyncio.gather`), returning shaped results.
- Protect it with the same `auth.verify` via a dependency, to exercise REST auth.
- Clearly comment at top that this exists **only** as a REST learning surface and is not
  called by MCP or the agent.

### 5.6 `agent.py` (Strands)
- Factory building a Strands `Agent` using `BedrockModel`
  (`# VERIFY:` model id, e.g. `us.anthropic.claude-sonnet-4-*`, and region).
- Wire the agent to the MCP server as an **MCP client over streamable HTTP**, forwarding
  the caller's bearer token as an `Authorization` header on the MCP connection.
  - `# VERIFY:` Strands' current MCP-client import and how it accepts custom headers /
    a transport factory. Do not guess — confirm against installed `strands-agents`.
- Expose a helper that, given a prompt + bearer token, returns the agent (or a streaming
  handle). The agent's available tools = the MCP tools; **the agent must not be given the
  caller's identity as a tool argument** — identity flows only via the forwarded token.

### 5.7 `runtime.py` (AgentCore serving — the HTTP boundary Streamlit calls)
- Use `BedrockAgentCoreApp` + `@app.entrypoint`. This yields an HTTP server on **:8080**
  with **`POST /invocations`** and **`GET /ping`** (SDK-provided — do not hand-roll).
- Entrypoint signature pattern (verify): `async def invoke(payload, context): ...`
  - Read prompt from `payload["prompt"]`.
  - Read the bearer token from the inbound request headers via `context`
    (`# VERIFY:` exact attribute, e.g. `context.request_headers`). Fall back to a token in
    the payload for local testing.
  - Build the agent (§5.6) with that token and **stream**: iterate `agent.stream_async(prompt)`
    and `yield` events so the client gets incremental output.
- `if __name__ == "__main__": app.run()` so the same artifact runs locally and in the runtime.

### 5.8 `streamlit_app.py` (thin client — no agent in-process)
- **Must not import the agent.** It is purely an HTTP client that POSTs to
  `ENDPOINT_URL` (`http://localhost:8080/invocations` locally).
- A selector to choose identity: `student_001…student_005` or `teacher_001`. On selection,
  use the matching token (read from env / a local file produced by `mint_tokens.py`, or a
  paste box). Send it as the `Authorization: Bearer` header.
- A prompt box; render the streamed response incrementally.
- Going to prod = change `ENDPOINT_URL` to the AgentCore Runtime invoke endpoint. Nothing
  else in this file should need to change.

## 6. End-to-end identity flow (must hold)

```
Streamlit (holds JWT, picks student/teacher)
  → POST /invocations  (Authorization: Bearer <jwt>)
    → AgentCore entrypoint reads bearer from context
      → Strands agent forwards bearer to MCP client (header)
        → MCP server verify()s token, derives sub/role, authorizes, calls core
```
The agent is **not** the auth boundary for tools; the MCP server is. No layer below
Streamlit ever accepts identity as a plain argument.

## 7. Do NOT (regression guards)

- Do NOT let `mcp_server.py` call the REST API or import `api.py`.
- Do NOT add a `student_id` parameter to `get_my_status`.
- Do NOT let `get_group_status` return ids outside the teacher's `students` claim.
- Do NOT hand-roll a FastAPI/Starlette server for `/invocations` or `/ping`.
- Do NOT import the Strands agent inside `streamlit_app.py`.
- Do NOT hardcode secrets other than an obvious dev default; read `JWT_SECRET` from env.

## 8. Definition of done (acceptance checklist)

- [ ] `uv run python -m mcp_rest_lab.mint_tokens` prints 5 student + 1 teacher token.
- [ ] MCP server runs on its port; `get_my_status` with a student token returns that
      student's status and **rejects** any attempt to query another id.
- [ ] `get_group_status` with the teacher token returns all 5; with a student token it is
      rejected; with ids outside the claim it is rejected.
- [ ] FastAPI `/students/status` returns parallel results and rejects an invalid token.
- [ ] `uv run python -m mcp_rest_lab.runtime` serves `/ping` (200) and `/invocations`.
- [ ] Streamlit: selecting `student_001` and asking "how am I doing?" streams a status;
      selecting `teacher_001` and asking about the group streams 5 statuses.
- [ ] A student token cannot retrieve another student's status through any path.
- [ ] README documents the run sequence and where tokens come from.
- [ ] Every uncertain SDK call carries a `# VERIFY:` note; versions are pinned in `pyproject.toml`.

## 9. Run sequence (put in README)

1. `uv sync`
2. `uv run python -m mcp_rest_lab.mint_tokens`  → copy tokens
3. Terminal A: start MCP server (`mcp_server.py`, streamable HTTP, :8000)
4. Terminal B (optional, learning track): `uv run uvicorn mcp_rest_lab.api:app --port 8001`
5. Terminal C: `uv run python -m mcp_rest_lab.runtime`  (serves :8080)
6. Terminal D: `uv run streamlit run app/streamlit_app.py`

## 10. Out of scope for v1 (parking lot)

- Real IdP / RS256 / JWKS (HS256 stand-in is fine now; keep claim shapes prod-like).
- AgentCore Gateway, Memory, Cedar policies, actual AWS deployment.
- Persistence, real student data, rate limiting, observability.
- A2A protocol serving (note it exists as a later option; not needed now).

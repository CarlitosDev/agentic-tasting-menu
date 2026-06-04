# mcp-rest-lab

A toy-but-near-prod baseline for learning agentic API design. Five layers, one
identity flow, every boundary modelled on what you'd deploy in production.

---

## What this is

The system answers the question: *"has student X done their homework?"* — but the
interesting part isn't the answer, it's how identity, authorization, and request
routing flow through the stack.

```
Streamlit (holds JWT, picks student/teacher)
  → POST /invocations              Authorization: Bearer <jwt>
    → AgentCore runtime reads the bearer from request context
      → Strands agent wires the bearer onto its MCP client connection
        → MCP server verify()s the token, derives sub/role, calls core
          → core.get_student_status(sub) — pure, no auth, no I/O
```

**The MCP server is the auth boundary.** Not the agent, not the runtime. No layer
below Streamlit ever receives identity as a plain argument — `get_my_status()` takes
zero parameters; it reads `sub` from the verified token. A student asking
"how am I doing?" cannot retrieve another student's status through any path in the
MCP/agent chain.

---

## What was built

### `src/mcp_rest_lab/core.py`
Pure domain function: `get_student_status(student_id) -> str`. Returns a random
"has done / hasn't done" string. No I/O, no auth, no framework imports. Both the
MCP server and the REST learning track call this directly — it is the single source
of truth that every layer above it shapes and authorizes access to. Scaffolding for `data services`.

### `src/mcp_rest_lab/auth.py`
Shared identity helpers used by every layer:

- `make_token(sub, role, **extra)` — mints an HS256 JWT with `iss`, `aud`, `sub`,
  `role`, `iat`, `exp` plus any extra claims (e.g. `students=[...]` for a teacher).
- `verify(token)` — decodes and validates the token, enforcing both `audience` and
  `issuer` so a token minted for another system is rejected.
- `bearer_from_header(authorization)` — extracts the raw token from an
  `Authorization: Bearer <t>` header value.
- `STUDENTS` — `["student_001", …, "student_005"]`, shared by mint and the teacher
  token's `students` claim.

Claim shapes (`iss`/`aud`/`sub`/`role`/`exp`/custom) are kept identical to what
**Cognito** or **Auth0** would issue, so swapping HS256 → RS256/JWKS later is a single
`verify()` change, not a rewrite of every caller.

### `src/mcp_rest_lab/mint_tokens.py`
Run once to bootstrap the local environment. Prints 5 student tokens and 1 teacher
token in copy-pasteable form, and writes `tokens.json` so the Streamlit selector
can load them without manual pasting.
`uv run python -m mcp_rest_lab.mint_tokens`

### `src/mcp_rest_lab/mcp_server.py`
FastMCP server over **streamable HTTP** on `:8000` (path `/mcp`). Two tools:

| Tool | Who can call it | How auth works |
| --- | --- | --- |
| `get_my_status()` | Any valid bearer token | Identity = token's `sub`. **No parameters.** |
| `get_group_status(student_ids)` | Teacher tokens only | `role == "teacher"` required; every requested id must be a subset of the token's `students` claim |

Both tools read + `verify()` the incoming token from the HTTP request headers
before returning any data. On failure they return an MCP error, not data. The MCP
server **never imports or calls the REST API** — it calls `core` directly.

`get_group_status` fans out to `core` concurrently via `asyncio.gather`.

### `src/mcp_rest_lab/api.py`
FastAPI REST **learning track** on `:8001`. **Off the critical path** — nothing in
the agent or runtime depends on it. Its single endpoint (`POST /students/status`)
fans out to `core` in parallel and is protected by the same `auth.verify` dependency,
giving a second surface to exercise the same token auth pattern against a different
framework. Clearly marked in the file header as a learning surface only.

### `src/mcp_rest_lab/agent.py`
Strands `Agent` factory. The agent's available tools are exactly the MCP server's
tools — it has no other tools, and it does not receive identity as a tool argument.
Identity flows only as the bearer token forwarded on the MCP client's HTTP connection:

```python
MCPClient(lambda: streamablehttp_client(MCP_URL, headers={"Authorization": f"Bearer {token}"}))
```

`stream_answer(prompt, bearer_token)` opens the MCP client, builds the agent, and
yields text delta chunks from `agent.stream_async(prompt)`.

### `src/mcp_rest_lab/runtime.py`
`BedrockAgentCoreApp` serving boundary. The SDK provides `POST /invocations` and
`GET /ping` on `:8080` — no hand-rolled FastAPI server for those routes. The
`@app.entrypoint` async generator:

1. Reads the bearer from `context.request_headers["Authorization"]` (set by the SDK
   from the inbound request).
2. Falls back to a token in the payload for local `curl` testing.
3. Calls `stream_answer` and yields each text chunk, which the SDK wraps as
   `data: <json>\n\n` SSE events.

### `app/streamlit_app.py`
Thin HTTP client only — **does not import the agent**. Loads `tokens.json`, offers
an identity selector in the sidebar, and POSTs the prompt to `ENDPOINT_URL` with the
selected token as `Authorization: Bearer`. Parses the SSE stream and renders chunks
incrementally. Going to prod = change `ENDPOINT_URL` to the AgentCore Runtime invoke
endpoint; nothing else in this file changes.

---

## Run sequence

```bash
# 1. Install dependencies
uv sync

# 2. Mint tokens — prints 6 tokens, writes tokens.json
uv run python -m mcp_rest_lab.mint_tokens

# 3. Terminal A — MCP server (streamable HTTP, :8000)
uv run python -m mcp_rest_lab.mcp_server

# 4. Terminal B (optional, learning track) — FastAPI REST surface
uv run uvicorn mcp_rest_lab.api:app --port 8001

# 5. Terminal C — AgentCore runtime (:8080, needs AWS Bedrock creds)
source .env
aws sso login --profile "$AWS_PROFILE"
uv run python -m mcp_rest_lab.runtime

# 6. Terminal D — Streamlit thin client
uv run streamlit run app/streamlit_app.py
```
### Run sequence with existing users
If users tokens have been minted and the .env file is ready, just run ```bash ./start_services.sh```

---

## Identity and tokens

`mint_tokens.py` signs HS256 tokens with `JWT_SECRET` from the environment (falls
back to a dev-only constant — **set `JWT_SECRET` for anything real**). Every process
that mints or verifies must share the same value.

Token structure:

```json
// student token
{ "iss": "mcp-rest-lab", "aud": "mcp-rest-lab", "sub": "student_001",
  "role": "student", "iat": ..., "exp": ... }

// teacher token (adds the students claim)
{ "iss": "mcp-rest-lab", "aud": "mcp-rest-lab", "sub": "teacher_001",
  "role": "teacher", "iat": ..., "exp": ...,
  "students": ["student_001", ..., "student_005"] }
```

The `students` claim on the teacher token is the authorization list for
`get_group_status` — the MCP server rejects any request for ids not in that set,
regardless of what the agent (or a direct caller) sends.

---

## Quick checks (no AWS credentials needed)

```bash
# Verify mint_tokens
uv run python -m mcp_rest_lab.mint_tokens

# Start MCP server then exercise the auth boundary directly:
uv run python -m mcp_rest_lab.mcp_server &
# (use any MCP streamable-HTTP client against http://127.0.0.1:8000/mcp)

# REST track — valid token
uv run uvicorn mcp_rest_lab.api:app --port 8001 &
TOK=$(uv run python -c "from mcp_rest_lab.auth import make_token; print(make_token('teacher_001','teacher'))")
curl -s -X POST localhost:8001/students/status \
  -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"student_ids":["student_001","student_002"]}'

# REST track — invalid token (expect 401)
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST localhost:8001/students/status \
  -H "Authorization: Bearer bad.token" \
  -H 'Content-Type: application/json' -d '{"student_ids":["student_001"]}'

# Runtime health check
uv run python -m mcp_rest_lab.runtime &
curl -s localhost:8080/ping

# Runtime — missing token (expect SSE error chunk)
curl -s -N -X POST localhost:8080/invocations \
  -H 'Content-Type: application/json' -d '{"prompt":"hi"}'
```

---

## Configuration

Copy `.env.example` to `.env` and adjust. There is no auto-loader — export variables
or set them inline. Key variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `JWT_SECRET` | `dev-only-insecure-secret-change-me` | HS256 signing secret — change for anything real |
| `MCP_URL` | `http://127.0.0.1:8000/mcp` | Where the agent finds the MCP server |
| `ENDPOINT_URL` | `http://localhost:8080/invocations` | Where Streamlit POSTs — swap for the AgentCore Runtime URL in prod |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Bedrock cross-region inference profile |
| `AWS_REGION` | `us-east-1` | Bedrock region |

---

## AWS Bedrock requirement

Steps 1–4 and all quick checks above run with no AWS account. The full agent path
(`/invocations` → Strands → Bedrock) additionally needs:

- AWS credentials (`aws configure` / env vars / IAM role / SSO)
- The `BEDROCK_MODEL_ID` model enabled in `AWS_REGION`

Without credentials the runtime still serves `/ping` and returns clean SSE error
chunks for auth failures and missing prompts.

---

## SDK versions verified

All `# VERIFY` comments in source were confirmed against these installed versions
before the code was written:

| Package | Version | What was verified |
| --- | --- | --- |
| `mcp` | 1.27.2 | `ctx.request_context.request.headers` holds the Starlette request in streamable-HTTP tools; `run(transport="streamable-http")` |
| `strands-agents` | 1.42.0 | `MCPClient(transport_callable)` signature; `streamablehttp_client(url, headers=...)` accepts custom headers; text deltas arrive under `event["data"]` in `stream_async` |
| `bedrock-agentcore` | 1.13.0 | `@app.entrypoint` + `async def invoke(payload, context)` signature; `context.request_headers["Authorization"]`; async-generator → SSE `data: <json>\n\n` |
| `pyjwt` | 2.13.0 | `jwt.decode(..., audience=..., issuer=...)` actually enforces both |
| `fastapi` | 0.136.3 | `HTTPBearer` dependency pattern |
| `httpx` | 0.28.1 | `httpx.stream(...)` for SSE in Streamlit client |

---

## Locked decisions

These were set in the build spec and are not up for revision in v1:

1. **MCP server calls `core` directly.** It never calls or imports the REST API.
2. **REST API is a parallel learning track.** Nothing in the agent or runtime depends on it.
3. **`BedrockAgentCoreApp` provides `/invocations` and `/ping`.** No hand-rolled FastAPI for those routes.
4. **Identity is the token.** `get_my_status` takes no parameters. `get_group_status` authorizes against the token's claims, not against a passed-in id.
5. **Streamlit does not import the agent.** It is a pure HTTP client.

---

## What's out of scope for v1

Real IdP / RS256 / JWKS, AgentCore Gateway / Memory / Cedar policies, actual AWS
deployment, persistence, real student data, rate limiting, observability, A2A serving.

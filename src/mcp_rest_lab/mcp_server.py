"""mcp_server.py — FastMCP server, the auth boundary for tools (§5.4).

Two tools, both of which verify the incoming bearer token and derive identity
from it — never from a tool argument:

  - get_my_status()                 student self-query, no parameters
  - get_group_status(student_ids)   teacher query, authorized by the token claim

Transport is streamable HTTP so the bearer token rides in the request headers.
The MCP server calls ``core`` directly and NEVER imports/calls the REST api.py
(locked decision §4.1, regression guard §7).

Run:  uv run python -m mcp_rest_lab.mcp_server   (serves :8000, path /mcp)
"""

from __future__ import annotations

import asyncio
import os

from mcp.server.fastmcp import Context, FastMCP

from . import core
from .auth import bearer_from_header, verify

# VERIFY (confirmed against mcp 1.27.2): FastMCP takes host/port in the
# constructor; streamable-HTTP is exposed at `streamable_http_path` (default
# "/mcp"). run(transport="streamable-http") binds host:port from here.
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("mcp-rest-lab", host=HOST, port=PORT)


class AuthError(Exception):
    """Raised when token verification or authorization fails.

    FastMCP turns an exception inside a tool into an MCP tool error (not data),
    which is exactly the §5.4 requirement: "on failure return an MCP error, not
    data."
    """


def _claims_from_context(ctx: Context) -> dict:
    """Verify the bearer token on the current request and return its claims.

    VERIFY (confirmed against mcp 1.27.2): over streamable HTTP,
    ``ctx.request_context.request`` is the Starlette Request, so its ``.headers``
    carry the inbound Authorization header. (mcp/server/streamable_http.py sets
    ServerMessageMetadata(request_context=request); the lowlevel server exposes
    it as RequestContext.request.)
    """
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        raise AuthError("no HTTP request context (is this running over streamable HTTP?)")
    try:
        token = bearer_from_header(request.headers.get("authorization"))
        return verify(token)
    except Exception as e:  # pyjwt errors + missing/malformed header
        raise AuthError(f"unauthorized: {e}") from e


@mcp.tool()
def get_my_status(ctx: Context) -> dict:
    """Return the calling student's own homework status.

    Takes NO student_id argument (regression guard §7): identity is the verified
    token's ``sub``. A student can therefore only ever see their own status.
    """
    claims = _claims_from_context(ctx)
    sub = claims["sub"]
    return {"student_id": sub, "status": core.get_student_status(sub)}


@mcp.tool()
async def get_group_status(ctx: Context, student_ids: list[str]) -> list[dict]:
    """Return statuses for a group of students — teacher only.

    Authorization (§5.4, regression guard §7):
      - caller must have role == "teacher" (student-role callers are rejected);
      - every requested id must be a subset of the token's ``students`` claim.
    Statuses are fetched concurrently via asyncio.gather.
    """
    claims = _claims_from_context(ctx)

    if claims.get("role") != "teacher":
        raise AuthError("forbidden: get_group_status requires the teacher role")

    allowed = set(claims.get("students") or [])
    requested = set(student_ids)
    out_of_scope = requested - allowed
    if out_of_scope:
        raise AuthError(
            f"forbidden: ids outside your authorized set: {sorted(out_of_scope)}"
        )

    async def one(student_id: str) -> dict:
        # core.get_student_status is sync+pure; run in a thread so gather is
        # genuinely concurrent and we model a real async fan-out boundary.
        status = await asyncio.to_thread(core.get_student_status, student_id)
        return {"student_id": student_id, "status": status}

    return list(await asyncio.gather(*(one(sid) for sid in student_ids)))


def main() -> None:
    # VERIFY (confirmed against mcp 1.27.2): transport literal is
    # "streamable-http"; host/port come from the FastMCP constructor above.
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()

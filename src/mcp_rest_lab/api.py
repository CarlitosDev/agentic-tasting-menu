"""api.py — FastAPI REST learning track (§5.5). OFF THE CRITICAL PATH.

This endpoint exists ONLY as a REST surface to exercise the same auth helpers
against a different framework. It is NOT called by the MCP server, the Strands
agent, or the runtime — nothing depends on it (locked decision §4.2). It calls
``core`` directly, exactly like the MCP server does.

Run:  uv run uvicorn mcp_rest_lab.api:app --port 8001
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import core
from .auth import verify

app = FastAPI(title="mcp-rest-lab REST learning track")

# HTTPBearer parses the "Authorization: Bearer <t>" header for us.
_bearer = HTTPBearer(auto_error=True)


def require_token(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Dependency: verify the bearer token, returning claims or raising 401."""
    try:
        return verify(creds.credentials)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unauthorized: {e}",
        ) from e


class StatusRequest(BaseModel):
    student_ids: list[str]


@app.post("/students/status")
async def students_status(
    body: StatusRequest,
    claims: dict = Depends(require_token),
) -> dict:
    """Fan out to core.get_student_status in parallel and return shaped results.

    Auth here is intentionally coarse (any valid token) — this track exists to
    demonstrate REST auth wiring, not to mirror the MCP server's role rules. We do
    NOT deny non-teachers; we serve the data and simply report the caller's role,
    so the contrast with the strict MCP path (where this would be 403) is visible.
    """

    async def one(student_id: str) -> dict:
        result = await asyncio.to_thread(core.get_student_status, student_id)
        return {"student_id": student_id, "status": result}

    results = await asyncio.gather(*(one(sid) for sid in body.student_ids))
    is_teacher = claims.get("role") == "teacher"
    note = (
        "caller is a teacher"
        if is_teacher
        else "caller is NOT a teacher — the strict MCP path would deny this group "
        "query; the REST track serves it anyway (coarse auth)."
    )
    return {
        "caller": claims.get("sub"),
        "role": claims.get("role"),
        "is_teacher": is_teacher,
        "note": note,
        "results": list(results),
    }

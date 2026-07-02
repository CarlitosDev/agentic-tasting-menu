"""api.py — FastAPI REST learner-state surface (§5.5).

This endpoint is not called by the MCP server, the Strands agent, or the runtime:
it is a parallel REST surface over the same ``core`` facade. Authorization is now
strict because this serves real learner state.

Run:  uv run uvicorn mcp_rest_lab.api:app --port 8001
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import core
from .auth import verify

app = FastAPI(title="mcp-rest-lab learner-state REST API")

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
    student_ids: list[str] | None = None
    window_days: int | None = 30


def authorize_student_ids(claims: dict, requested_ids: list[str] | None) -> list[str]:
    """Resolve and authorize REST-visible student ids from token claims."""
    sub = claims.get("sub")
    role = claims.get("role")

    if role == "student":
        resolved = requested_ids or [sub]
        if resolved != [sub]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="forbidden: students may only query their own status",
            )
        return resolved

    if role == "teacher":
        allowed = set(claims.get("students") or [])
        resolved = requested_ids or sorted(allowed)
        out_of_scope = set(resolved) - allowed
        if out_of_scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"forbidden: ids outside your authorized set: {sorted(out_of_scope)}",
            )
        return resolved

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"forbidden: unsupported role {role!r}",
    )


@app.post("/students/status")
async def students_status(
    body: StatusRequest,
    claims: dict = Depends(require_token),
) -> dict:
    """Return authorized learner-state statuses from the shared core facade."""
    student_ids = authorize_student_ids(claims, body.student_ids)
    try:
        window = core.window_from_days(body.window_days)
        results = await asyncio.to_thread(
            core.get_group_status,
            student_ids,
            window=window,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    is_teacher = claims.get("role") == "teacher"
    return {
        "caller": claims.get("sub"),
        "role": claims.get("role"),
        "is_teacher": is_teacher,
        "learner_state_root": str(core.learner_state_root()),
        "window_days": body.window_days,
        "results": [result.model_dump(mode="json") for result in results],
    }

"""
Per-user MCP authorization and tool selection endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import RedirectResponse
import os
from dotenv import load_dotenv

from security import get_current_user
from models import User
import mcp_tools

load_dotenv()

router = APIRouter(prefix="/mcp", tags=["mcp"])

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")


class AuthorizeResponse(BaseModel):
    auth_url: str


class ToolInfo(BaseModel):
    name: str
    description: str


class SelectToolsRequest(BaseModel):
    tool_names: list[str]


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize(current_user: User = Depends(get_current_user)):
    """Kicks off OAuth for this user. Frontend should navigate the
    browser (window.location.href, not fetch) to the returned auth_url."""
    try:
        auth_url = await mcp_tools.start_authorization(current_user.id)
    except Exception as e:
        raise HTTPException(500, f"Failed to start MCP authorization: {e}")
    return AuthorizeResponse(auth_url=auth_url)


@router.get("/callback")
async def callback(code: str, state: str | None = None):
    """Horizon redirects the user's browser here after login.

    LIMITATION: this doesn't yet decode which user the callback belongs
    to from `state` -- it matches against whichever user currently has
    a pending authorization. Correct for single-user testing; needs a
    real state -> user_id mapping before concurrent users authorizing
    at the same time is safe. See mcp_tools.py's module docstring."""
    resolved = False
    for user_id in mcp_tools.get_pending_user_ids():
        if mcp_tools.resolve_mcp_callback(user_id, code, state):
            resolved = True
            break

    if not resolved:
        return RedirectResponse(f"{FRONTEND_URL}/mcp/connected?status=error")

    return RedirectResponse(f"{FRONTEND_URL}/mcp/connected?status=success")


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    connected = await mcp_tools.check_connection(current_user.id)
    return {"connected": connected}


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(current_user: User = Depends(get_current_user)):
    if not await mcp_tools.has_valid_connection(current_user.id):
        raise HTTPException(401, "Not authorized with MCP yet. Call /mcp/authorize first.")
    try:
        return await mcp_tools.list_tools(current_user.id)
    except mcp_tools.ReauthorizationRequired:
        raise HTTPException(401, "MCP authorization expired. Call /mcp/authorize again.")


@router.post("/tools/select")
async def select_tools(req: SelectToolsRequest, current_user: User = Depends(get_current_user)):
    try:
        await mcp_tools.set_selected_tools(current_user.id, req.tool_names)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "selected": req.tool_names}
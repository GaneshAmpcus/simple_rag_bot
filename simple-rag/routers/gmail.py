"""
Gmail OAuth endpoints -- separate from routers/mcp.py's generic MCP
authorization. This is a plain Google OAuth2 flow (see gmail_oauth.py
for why), so it gets its own simple authorize/callback/status routes
rather than being folded into the generic MCP flow.
"""

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import RedirectResponse
import os
from dotenv import load_dotenv

from security import get_current_user
from models import User
import gmail_oauth

load_dotenv()

router = APIRouter(prefix="/gmail", tags=["gmail"])

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")


@router.post("/authorize")
async def authorize(current_user: User = Depends(get_current_user)):
    """Returns the Google consent URL for this user. Frontend should
    navigate the browser (window.location.href, not fetch) to it,
    same pattern as /mcp/authorize."""
    auth_url = gmail_oauth.build_authorize_url(current_user.id)
    return {"auth_url": auth_url}


@router.get("/callback")
async def callback(code: str, state: str):
    """Google redirects the user's browser here after consent. Unlike
    /mcp/callback's still-open limitation (matching a callback to
    "whichever user has a pending request"), this decodes and verifies
    the user_id straight from the signed `state` param -- safe under
    concurrent users authorizing at the same time."""
    success, _user_id = gmail_oauth.handle_callback(code, state)
    status = "success" if success else "error"
    return RedirectResponse(f"{FRONTEND_URL}/gmail/connected?status={status}")


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    return gmail_oauth.get_connection_status(current_user.id)

"""
Calendar/Meet OAuth endpoints -- separate from routers/mcp.py's generic
MCP authorization, and separate from routers/gmail.py. This is a plain
Google OAuth2 flow (see calendar_oauth.py for why), so it gets its own
simple authorize/callback/status routes, mirroring routers/gmail.py's
shape exactly -- including the Phase 4 user-level vs. per-bot route
split (see that file's docstring for the full rationale).

Covers both Calendar and Meet: Google Meet links are created through
the Calendar API (see MCP server's tools/meet_tool.py), so a single
Calendar connection/token powers both tool families -- there is no
separate /meet router or OAuth flow.
"""

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import RedirectResponse
from sqlalchemy.orm import Session
import os
from dotenv import load_dotenv

from security import get_current_user
from config.database import get_db
from models import User
from routers.bots import _get_owned_bot
import calendar_oauth

load_dotenv()

router = APIRouter(prefix="/calendar", tags=["calendar"])
bot_router = APIRouter(prefix="/bots/{bot_id}/calendar", tags=["calendar", "bots"])

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")


@router.post("/authorize")
async def authorize(current_user: User = Depends(get_current_user)):
    """Returns the Google consent URL for this user's user-level
    (bot_id=None) Calendar connection. Frontend should navigate the
    browser (window.location.href, not fetch) to it, same pattern as
    /gmail/authorize and /mcp/authorize."""
    auth_url = calendar_oauth.build_authorize_url(current_user.id, bot_id=None)
    return {"auth_url": auth_url}


@router.get("/callback")
async def callback(code: str, state: str):
    """Google redirects the user's browser here after consent. Same
    pattern as /gmail/callback: decodes and verifies both user_id and
    bot_id straight from the signed `state` param -- safe under
    concurrent users (and concurrent per-bot authorizations for the
    same user) authorizing at the same time. Handles callbacks for
    BOTH the user-level authorize above and the per-bot authorize
    below."""
    success, _user_id, bot_id = calendar_oauth.handle_callback(code, state)
    status = "success" if success else "error"
    bot_qs = f"&bot_id={bot_id}" if bot_id else ""
    return RedirectResponse(f"{FRONTEND_URL}/calendar/connected?status={status}{bot_qs}")


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    return calendar_oauth.get_connection_status(current_user.id, bot_id=None)


# ---------------------------------------------------------------------
# Per-bot routes -- README.md Phase 4.
# ---------------------------------------------------------------------

@bot_router.post("/authorize")
async def bot_authorize(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the Google consent URL for this bot's own Calendar
    connection -- independent of the user's bot-less connection or any
    other bot's."""
    _get_owned_bot(bot_id, db, current_user)
    auth_url = calendar_oauth.build_authorize_url(current_user.id, bot_id=bot_id)
    return {"auth_url": auth_url}


@bot_router.get("/status")
async def bot_status(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_bot(bot_id, db, current_user)
    return calendar_oauth.get_connection_status(current_user.id, bot_id=bot_id)

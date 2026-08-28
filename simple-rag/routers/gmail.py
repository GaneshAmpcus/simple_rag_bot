"""
Gmail OAuth endpoints -- separate from routers/mcp.py's generic MCP
authorization. This is a plain Google OAuth2 flow (see gmail_oauth.py
for why), so it gets its own simple authorize/callback/status routes
rather than being folded into the generic MCP flow.

README.md Phase 4: two route families, same shape as routers/mcp.py's
Phase 3 split --
  - `/gmail/...` -- the "no bot" / user-level connection, UNCHANGED
    behavior for any session that never touches bots.
  - `/bots/{bot_id}/gmail/...` -- that bot's own independent Gmail
    connection, authorized separately.
Both funnel through the same `/gmail/callback` route: gmail_oauth.py's
signed `state` param already encodes bot_id, so one callback route can
resolve either case correctly (see gmail_oauth.py's module docstring).
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
import gmail_oauth

load_dotenv()

router = APIRouter(prefix="/gmail", tags=["gmail"])
bot_router = APIRouter(prefix="/bots/{bot_id}/gmail", tags=["gmail", "bots"])

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")


@router.post("/authorize")
async def authorize(current_user: User = Depends(get_current_user)):
    """Returns the Google consent URL for this user's user-level
    (bot_id=None) Gmail connection. Frontend should navigate the
    browser (window.location.href, not fetch) to it, same pattern as
    /mcp/authorize."""
    auth_url = gmail_oauth.build_authorize_url(current_user.id, bot_id=None)
    return {"auth_url": auth_url}


@router.get("/callback")
async def callback(code: str, state: str):
    """Google redirects the user's browser here after consent. Decodes
    and verifies both user_id AND bot_id straight from the signed
    `state` param -- safe under concurrent users (and, post-Phase-4,
    concurrent per-bot authorizations for the same user) all
    authorizing at the same time. Handles callbacks for BOTH the
    user-level authorize above and the per-bot authorize below, since
    the state param carries whichever bot_id (or None) that flow
    started with."""
    success, _user_id, bot_id = gmail_oauth.handle_callback(code, state)
    status = "success" if success else "error"
    bot_qs = f"&bot_id={bot_id}" if bot_id else ""
    return RedirectResponse(f"{FRONTEND_URL}/gmail/connected?status={status}{bot_qs}")


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    return gmail_oauth.get_connection_status(current_user.id, bot_id=None)


# ---------------------------------------------------------------------
# Per-bot routes -- README.md Phase 4.
# ---------------------------------------------------------------------

@bot_router.post("/authorize")
async def bot_authorize(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the Google consent URL for this bot's own Gmail
    connection -- independent of the user's bot-less connection or any
    other bot's, per README §2.1's explicit requirement."""
    _get_owned_bot(bot_id, db, current_user)
    auth_url = gmail_oauth.build_authorize_url(current_user.id, bot_id=bot_id)
    return {"auth_url": auth_url}


@bot_router.get("/status")
async def bot_status(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_bot(bot_id, db, current_user)
    return gmail_oauth.get_connection_status(current_user.id, bot_id=bot_id)

"""
Generic MCP server management: add/remove arbitrary MCP servers,
authorize each one independently, and select which of that server's
tools are available in chat.

README.md Phase 3: connections (auth + tool selection) are now scoped
per (user, server, bot). Two route families share the same underlying
mcp_tools.py functions:

  - `/mcp/...` (this file's original routes, bot_id implicitly None)
    -- the "no bot" / user-level connection, UNCHANGED behavior for
    any session that never touches bots (README principle #5).
  - `/bots/{bot_id}/mcp/...` -- that bot's own independent connection
    to the same server catalog, with its own token and tool selection.

The server catalog itself (add/remove/list servers, GET /mcp/servers)
stays user-scoped and lives only under `/mcp/servers` -- a server a
user added is visible/usable from every one of their bots (and their
bot-less sessions); only the *connection* to a server is bot-scoped.
`/bots/{bot_id}/mcp/servers` is a thin bot-scoped view over that same
catalog, annotated with THAT bot's connection status per server.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import RedirectResponse
import os
from dotenv import load_dotenv

from security import get_current_user
from config.database import get_db
from models import User
from routers.bots import _get_owned_bot
from sqlalchemy.orm import Session
import mcp_tools

load_dotenv()

router = APIRouter(prefix="/mcp", tags=["mcp"])
bot_router = APIRouter(prefix="/bots/{bot_id}/mcp", tags=["mcp", "bots"])

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")


class AuthorizeResponse(BaseModel):
    auth_url: str


class ToolInfo(BaseModel):
    name: str
    description: str


class SelectToolsRequest(BaseModel):
    tool_names: list[str]


class AddServerRequest(BaseModel):
    name: str
    url: str


class ServerInfo(BaseModel):
    id: str
    name: str
    url: str
    connected: bool
    is_builtin: bool = False


async def _owned_server(server_id: str, current_user: User):
    server = await mcp_tools.get_server(current_user.id, server_id)
    if not server:
        raise HTTPException(404, "Server not found.")
    return server


def _validate_add_server(req: AddServerRequest) -> tuple[str, str]:
    name = req.name.strip()
    url = req.url.strip()
    if not name or not url:
        raise HTTPException(400, "name and url are required.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    return name, url


# ---------------------------------------------------------------------
# User-level ("no bot") routes -- bot_id is always None here. Kept
# byte-for-byte compatible with the pre-Phase-3 API surface.
# ---------------------------------------------------------------------

@router.get("/servers", response_model=list[ServerInfo])
async def list_servers(current_user: User = Depends(get_current_user)):
    """All MCP servers this user has added, each with a cheap
    DB-only 'connected' flag for the user-level (bot_id=None)
    connection. Auto-provisions the built-in Horizon server first if
    it doesn't exist yet, so it's always present and authorizable from
    this page even before the user has connected Gmail/Calendar or
    sent a chat message."""
    await mcp_tools.ensure_horizon_server(current_user.id)
    return await mcp_tools.list_servers(current_user.id, bot_id=None)


@router.post("/servers", response_model=ServerInfo)
async def add_server(req: AddServerRequest, current_user: User = Depends(get_current_user)):
    name, url = _validate_add_server(req)
    server = await mcp_tools.add_server(current_user.id, name, url)
    return {**server, "connected": False}


@router.delete("/servers/{server_id}")
async def remove_server(server_id: str, current_user: User = Depends(get_current_user)):
    try:
        deleted = await mcp_tools.delete_server(current_user.id, server_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not deleted:
        raise HTTPException(404, "Server not found.")
    return {"status": "ok"}


@router.post("/servers/{server_id}/authorize", response_model=AuthorizeResponse)
async def authorize_server(server_id: str, current_user: User = Depends(get_current_user)):
    """Kicks off OAuth for this one server's user-level (bot_id=None)
    connection. Frontend should navigate the browser
    (window.location.href, not fetch) to the returned auth_url."""
    server = await _owned_server(server_id, current_user)
    try:
        auth_url = await mcp_tools.start_authorization(
            current_user.id, server.id, server.url, bot_id=None
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to start MCP authorization: {e}")
    return AuthorizeResponse(auth_url=auth_url)


@router.get("/callback/{flow_id}")
async def callback(flow_id: str, code: str, state: str | None = None):
    """The MCP server redirects the user's browser here after login.
    flow_id ties this callback back to the exact (user_id, server_id,
    bot_id) triple that started it (see mcp_tools.start_authorization),
    so concurrent authorizations -- including different servers, or
    the same server for different bots, for the same user -- resolve
    correctly. Shared by both route families: a bot-scoped
    authorize_server call also redirects here, since the flow_id
    already encodes which bot (if any) it belongs to. bot_id is passed
    through to the frontend's redirect so McpConnected.jsx can link
    back to that specific bot's tools page instead of the generic one."""
    resolved, bot_id = mcp_tools.resolve_mcp_callback(flow_id, code, state)
    status = "success" if resolved else "error"
    bot_qs = f"&bot_id={bot_id}" if bot_id else ""
    return RedirectResponse(f"{FRONTEND_URL}/mcp/connected?status={status}{bot_qs}")


@router.get("/servers/{server_id}/status")
async def server_status(server_id: str, current_user: User = Depends(get_current_user)):
    server = await _owned_server(server_id, current_user)
    connected = await mcp_tools.check_connection(
        current_user.id, server.id, server.url, bot_id=None
    )
    return {"connected": connected}


@router.get("/servers/{server_id}/tools", response_model=list[ToolInfo])
async def server_tools(server_id: str, current_user: User = Depends(get_current_user)):
    server = await _owned_server(server_id, current_user)
    if not await mcp_tools.has_valid_connection(current_user.id, server.id, bot_id=None):
        raise HTTPException(401, "Not authorized with this server yet. Authorize it first.")
    try:
        return await mcp_tools.list_tools(current_user.id, server.id, server.url, bot_id=None)
    except mcp_tools.ReauthorizationRequired:
        raise HTTPException(401, "MCP authorization expired. Authorize this server again.")


@router.post("/servers/{server_id}/tools/select")
async def select_server_tools(
    server_id: str, req: SelectToolsRequest, current_user: User = Depends(get_current_user)
):
    server = await _owned_server(server_id, current_user)
    try:
        await mcp_tools.set_selected_tools(
            current_user.id, server.id, req.tool_names, bot_id=None
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "selected": req.tool_names}


# ---------------------------------------------------------------------
# Per-bot routes -- README.md Phase 3. Same operations, scoped to one
# bot's own connection to each server. Ownership of the bot is checked
# on every route via _get_owned_bot (404s if the bot doesn't exist or
# isn't this user's).
# ---------------------------------------------------------------------

@bot_router.get("/servers", response_model=list[ServerInfo])
async def bot_list_servers(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Same server catalog as GET /mcp/servers, but the 'connected'
    flag on each reflects THIS bot's own connection, not the user-level
    one -- a server can be connected for the user generally and still
    show as not-connected here (or vice versa) until authorized for
    this specific bot."""
    _get_owned_bot(bot_id, db, current_user)
    await mcp_tools.ensure_horizon_server(current_user.id)
    return await mcp_tools.list_servers(current_user.id, bot_id=bot_id)


@bot_router.post("/servers/{server_id}/authorize", response_model=AuthorizeResponse)
async def bot_authorize_server(
    bot_id: str,
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kicks off OAuth for this bot's own connection to this server --
    independent of any other bot's (or the user's bot-less) connection
    to the same server, per README §2.1's explicit requirement."""
    _get_owned_bot(bot_id, db, current_user)
    server = await _owned_server(server_id, current_user)
    try:
        auth_url = await mcp_tools.start_authorization(
            current_user.id, server.id, server.url, bot_id=bot_id
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to start MCP authorization: {e}")
    return AuthorizeResponse(auth_url=auth_url)


@bot_router.get("/servers/{server_id}/status")
async def bot_server_status(
    bot_id: str,
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_bot(bot_id, db, current_user)
    server = await _owned_server(server_id, current_user)
    connected = await mcp_tools.check_connection(
        current_user.id, server.id, server.url, bot_id=bot_id
    )
    return {"connected": connected}


@bot_router.get("/servers/{server_id}/tools", response_model=list[ToolInfo])
async def bot_server_tools(
    bot_id: str,
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_bot(bot_id, db, current_user)
    server = await _owned_server(server_id, current_user)
    if not await mcp_tools.has_valid_connection(current_user.id, server.id, bot_id=bot_id):
        raise HTTPException(401, "Not authorized with this server yet for this bot. Authorize it first.")
    try:
        return await mcp_tools.list_tools(current_user.id, server.id, server.url, bot_id=bot_id)
    except mcp_tools.ReauthorizationRequired:
        raise HTTPException(401, "MCP authorization expired. Authorize this server again for this bot.")


@bot_router.post("/servers/{server_id}/tools/select")
async def bot_select_server_tools(
    bot_id: str,
    server_id: str,
    req: SelectToolsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_bot(bot_id, db, current_user)
    server = await _owned_server(server_id, current_user)
    try:
        await mcp_tools.set_selected_tools(
            current_user.id, server.id, req.tool_names, bot_id=bot_id
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "selected": req.tool_names}

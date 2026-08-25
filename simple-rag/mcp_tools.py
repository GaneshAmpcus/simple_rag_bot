"""
Per-user MCP integration: OAuth (via the MCP SDK's OAuthClientProvider,
dynamically registered against Horizon -- no pre-registered redirect
URI needed), tokens persisted per-user in Postgres, and tool
listing/selection.

Adapted from the tested langchain_app.py script, which used a local
callback HTTP server suited to a single-process desktop script. That
doesn't work for a real multi-user web backend:
- The redirect target has to be a real endpoint on THIS server
  (/mcp/callback), not a throwaway localhost:3030 listener only the
  developer's own machine can reach.
- Each user's OAuth token has to be stored against THEM specifically
  (DbTokenStorage, below) -- not one shared in-memory value
  (InMemoryTokenStorage) for the whole process.

KNOWN LIMITATION, flagged rather than silently papered over: the
/mcp/callback route (in routers/mcp.py) currently matches an incoming
callback to WHICHEVER user has a pending authorization, rather than
decoding the user from OAuth's `state` parameter. Fine for single-user
testing; a real fix (state -> user_id mapping) is needed before this
is safe under concurrent users authorizing at the same time.
"""

import asyncio
import contextlib
import json
import os
import time
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl
from sqlalchemy.orm import Session

from config.database import SessionLocal
from models import McpConnection
from config.logging_config import get_logger

log = get_logger(__name__)

load_dotenv()

MCP_SERVER_URL = "https://custom-mcp-by-ganesh.fastmcp.app/mcp"
MCP_SERVER_ROOT = "https://custom-mcp-by-ganesh.fastmcp.app"
# MCP_AUTHORIZATION_ENDPOINT = os.getenv(
#     "MCP_AUTHORIZATION_ENDPOINT",
#     f"{MCP_SERVER_ROOT}/oauth2/authorize",
# ).rstrip("/")

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
OAUTH_CALLBACK_URL = f"{BACKEND_BASE_URL}/mcp/callback"


# ---------------------------------------------------------------------
# Per-user token storage, persisted in Postgres.
# ---------------------------------------------------------------------

class DbTokenStorage(TokenStorage):
    """Each method opens and closes its own DB session. This object is
    held across an OAuth flow that spans a browser redirect and can
    take the user minutes to complete -- deliberately never holds one
    request-scoped session open that whole time."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def _get_or_create_row(self, db: Session) -> McpConnection:
        row = db.query(McpConnection).filter(McpConnection.user_id == self.user_id).first()
        if not row:
            row = McpConnection(user_id=self.user_id, selected_tools_json="[]")
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    async def get_tokens(self) -> Optional[OAuthToken]:
        db = SessionLocal()
        try:
            row = db.query(McpConnection).filter(McpConnection.user_id == self.user_id).first()
            if not row or not row.token_json:
                return None
            return OAuthToken.model_validate_json(row.token_json)
        finally:
            db.close()

    async def set_tokens(self, tokens: OAuthToken) -> None:
        db = SessionLocal()
        try:
            row = self._get_or_create_row(db)
            row.token_json = tokens.model_dump_json()
            db.commit()
        finally:
            db.close()

    async def get_client_info(self) -> Optional[OAuthClientInformationFull]:
        db = SessionLocal()
        try:
            row = db.query(McpConnection).filter(McpConnection.user_id == self.user_id).first()
            if not row or not row.client_info_json:
                return None
            return OAuthClientInformationFull.model_validate_json(row.client_info_json)
        finally:
            db.close()

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        db = SessionLocal()
        try:
            row = self._get_or_create_row(db)
            row.client_info_json = client_info.model_dump_json()
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------
# Bridging redirect_handler/callback_handler to real HTTP routes
# instead of a browser popup + local server.
# ---------------------------------------------------------------------

_pending: dict[str, dict[str, asyncio.Future]] = {}


class ReauthorizationRequired(Exception):
    """Raised when the MCP SDK's OAuthClientProvider decides it needs to
    redo the full authorization-code redirect flow (e.g. the stored
    access token expired and either there's no refresh_token or the
    refresh itself was rejected by the server), but the call wasn't
    made through start_authorization() -- e.g. it came from list_tools
    or get_selected_tools, which are meant to be read-only against an
    ALREADY-valid token.

    In that situation there's no live /mcp/authorize HTTP request
    waiting on an auth_url_future and no browser to redirect, so
    redirect_handler has nowhere to put the new auth_url. Previously
    this crashed with a raw KeyError on _pending[user_id]; this
    exception exists so callers can catch it and tell the user to
    reconnect via /mcp/authorize instead of 500ing.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f"MCP reauthorization required for user_id={user_id}")


# ---------------------------------------------------------------------
# OAuth state used to bridge MCP SDK callbacks to FastAPI HTTP routes.
# ---------------------------------------------------------------------

_pending: dict[str, dict[str, asyncio.Future]] = {}


def _make_redirect_handler(user_id: str):
    """
    Called by the MCP SDK when OAuth authorization is required.

    There are two possible situations:

    1. /mcp/authorize started an active OAuth flow.
       In this case _pending[user_id] exists and we return the
       authorization URL through auth_url_future.

    2. Some normal API request such as /mcp/tools or
       /mcp/selected-tools triggered OAuth unexpectedly because
       the stored token is expired/invalid.
       In this case there is no browser waiting for an auth URL,
       so raise ReauthorizationRequired.
    """

    async def redirect_handler(auth_url: str) -> None:

        log.info(
            "mcp_tools: OAuth authorization URL generated "
            "for user_id=%s",
            user_id,
        )

        pending = _pending.get(user_id)

        # ---------------------------------------------------------
        # No active /mcp/authorize request.
        #
        # This means something like list_tools() triggered OAuth.
        # There is nobody waiting for an auth URL.
        # ---------------------------------------------------------

        if pending is None:

            log.warning(
                "mcp_tools: OAuth required for user_id=%s, "
                "but no authorization flow is pending. "
                "Stored token is probably expired or invalid.",
                user_id,
            )

            raise ReauthorizationRequired(user_id)

        # ---------------------------------------------------------
        # Normal OAuth authorization flow.
        # ---------------------------------------------------------

        auth_url_future = pending["auth_url_future"]

        if auth_url_future.done():
            log.warning(
                "mcp_tools: auth_url_future already completed "
                "for user_id=%s",
                user_id,
            )
            return

        log.info(
            "mcp_tools: delivering authorization URL "
            "to waiting request for user_id=%s",
            user_id,
        )

        auth_url_future.set_result(auth_url)

    return redirect_handler


def _make_callback_handler(user_id: str):
    """
    Called by the MCP OAuth client when it needs the authorization
    code returned by the browser callback.
    """

    async def callback_handler() -> tuple[str, str | None]:
        log.info(
            "mcp_tools: waiting for /mcp/callback for user_id=%s",
            user_id,
        )

        pending = _pending.get(user_id)

        if pending is None:
            raise RuntimeError(
                f"No pending OAuth authorization for user_id={user_id}"
            )

        callback_future = pending["callback_future"]

        code, state = await callback_future

        return code, state

    return callback_handler


def resolve_mcp_callback(
    user_id: str,
    code: str,
    state: str | None,
) -> bool:
    """
    Called by routers/mcp.py when /mcp/callback receives
    the OAuth authorization code.

    Returns:
        True  -> callback was delivered successfully
        False -> no pending OAuth flow exists for this user
    """

    pending = _pending.get(user_id)

    if pending is None:
        log.warning(
            "mcp_tools: received OAuth callback but no pending "
            "authorization exists for user_id=%s",
            user_id,
        )
        return False

    callback_future = pending["callback_future"]

    if callback_future.done():
        log.warning(
            "mcp_tools: callback future already completed "
            "for user_id=%s",
            user_id,
        )
        return False

    callback_future.set_result((code, state))

    log.info(
        "mcp_tools: OAuth callback delivered for user_id=%s",
        user_id,
    )

    return True


def get_pending_user_ids() -> list[str]:
    """
    Returns users currently waiting for OAuth completion.

    This is mainly useful for the callback route during the current
    implementation.
    """
    return list(_pending.keys())

# def _make_redirect_handler(user_id: str):
#     async def redirect_handler(auth_url: str) -> None:
#         pending = _pending.get(user_id)
#         if pending is None:
#             log.warning(
#                 "mcp_tools: OAuth needs to redirect user_id=%s but no "
#                 "/mcp/authorize call is in progress for them -- their "
#                 "stored token is likely expired/invalid beyond refresh. "
#                 "Raising ReauthorizationRequired instead of crashing.",
#                 user_id,
#             )
#             raise ReauthorizationRequired(user_id)

#         log.info(
#             "mcp_tools: auth URL ready for user_id=%s: %s",
#             user_id,
#             auth_url,
#         )
#         pending["auth_url_future"].set_result(auth_url)

#     return redirect_handler



# def _make_callback_handler(user_id: str):
#     async def callback_handler() -> tuple[str, str | None]:
#         pending = _pending.get(user_id)
#         if pending is None:
#             raise ReauthorizationRequired(user_id)
#         log.info("mcp_tools: waiting for /mcp/callback for user_id=%s", user_id)
#         code, state = await pending["callback_future"]
#         return code, state
#     return callback_handler


async def _clear_stored_token(user_id: str) -> None:
    """Wipes a dead/rejected token so has_valid_connection() (and thus
    the UI's 'connected' status) reflects reality, and the user is
    prompted to reconnect rather than seeing a silently-broken
    'connected' state."""
    db = SessionLocal()
    try:
        row = db.query(McpConnection).filter(McpConnection.user_id == user_id).first()
        if row:
            row.token_json = None
            db.commit()
    finally:
        db.close()


# def resolve_mcp_callback(user_id: str, code: str, state: str | None) -> bool:
#     """Called from the /mcp/callback route when Horizon redirects back.
#     Returns False if there was no pending authorization for this user."""
#     pending = _pending.get(user_id)
#     if not pending or pending["callback_future"].done():
#         return False
#     pending["callback_future"].set_result((code, state))
#     return True


# def get_pending_user_ids() -> list[str]:
#     return list(_pending.keys())


def _build_mcp_client(user_id: str, extra_headers: dict | None = None) -> MultiServerMCPClient:
    """extra_headers lets callers attach per-tool-family auth on top of
    this server's own OAuth (`oauth_auth` below, e.g. Horizon), without
    that tool family needing its own OAuthClientProvider/MCP-spec OAuth
    handshake. Gmail uses this to forward a fresh Google access token
    (see tools/gmail_tool.py) -- the pattern generalizes to any future
    tool whose own auth is simpler to run as plain OAuth2 in this
    backend (see gmail_oauth.py's docstring for why that's the right
    call for a tool this app both owns and is the sole consumer of).
    """
    oauth_auth = OAuthClientProvider(
        server_url=MCP_SERVER_ROOT,
        client_metadata=OAuthClientMetadata(
            client_name="Simple RAG MCP Client",
            redirect_uris=[AnyUrl(OAUTH_CALLBACK_URL)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=DbTokenStorage(user_id),
        redirect_handler=_make_redirect_handler(user_id),
        callback_handler=_make_callback_handler(user_id),
    )
    return MultiServerMCPClient({
        "horizon": {
            "url": MCP_SERVER_URL,
            "transport": "streamable_http",
            "auth": oauth_auth,
            "headers": extra_headers or {},
        }
    })


async def has_valid_connection(user_id: str) -> bool:
    """Cheap check -- does this user already have a stored token,
    without triggering a new OAuth flow if not."""
    token = await DbTokenStorage(user_id).get_tokens()
    return token is not None


async def check_connection(user_id: str) -> bool:
    """Verify that the stored MCP token still works without starting OAuth."""
    if not await has_valid_connection(user_id):
        return False

    client = _build_mcp_client(user_id)
    try:
        await client.get_tools()
    except ReauthorizationRequired:
        await _clear_stored_token(user_id)
        return False
    except Exception as exc:
        log.warning(
            "mcp_tools: connection check failed for user_id=%s; requiring authorization again: %s",
            user_id,
            exc,
        )
        await _clear_stored_token(user_id)
        return False
    return True


# async def start_authorization(user_id: str, timeout: float = 60.0) -> str:
#     """Kicks off OAuth and returns the URL to redirect the user's
#     browser to. Runs the rest of the flow (get_tools(), which blocks
#     on the callback) as a background task -- this function only waits
#     for the auth_url, not the full login."""
#     _pending[user_id] = {
#         "auth_url_future": asyncio.get_event_loop().create_future(),
#         "callback_future": asyncio.get_event_loop().create_future(),
#     }

#     client = _build_mcp_client(user_id)

#     async def _run():
#         try:
#             tools = await client.get_tools()
#             log.info(
#                 "mcp_tools: authorization complete for user_id=%s, %d tool(s)",
#                 user_id, len(tools),
#             )
#         except Exception:
#             log.exception("mcp_tools: authorization flow failed for user_id=%s", user_id)
#         finally:
#             _pending.pop(user_id, None)

#     asyncio.create_task(_run())

#     auth_url = await asyncio.wait_for(_pending[user_id]["auth_url_future"], timeout=timeout)
#     return auth_url


async def start_authorization(
    user_id: str,
    timeout: float = 60.0,
) -> str:
    """
    Start a new MCP OAuth authorization flow.

    This function creates the pending state BEFORE starting
    the MCP client so redirect_handler() and callback_handler()
    can communicate with the FastAPI routes.
    """

    # -------------------------------------------------------------
    # Don't start two OAuth flows for the same user.
    # -------------------------------------------------------------

    existing = _pending.get(user_id)

    if existing is not None:

        existing_future = existing["auth_url_future"]

        if not existing_future.done():

            log.warning(
                "mcp_tools: OAuth authorization already pending "
                "for user_id=%s",
                user_id,
            )

            return await asyncio.wait_for(
                asyncio.shield(existing_future),
                timeout=timeout,
            )

        _pending.pop(user_id, None)

    # -------------------------------------------------------------
    # Create futures BEFORE creating/starting MCP client.
    # -------------------------------------------------------------

    loop = asyncio.get_running_loop()

    auth_url_future = loop.create_future()
    callback_future = loop.create_future()

    _pending[user_id] = {
        "auth_url_future": auth_url_future,
        "callback_future": callback_future,
    }

    log.info(
        "mcp_tools: OAuth pending state created "
        "for user_id=%s",
        user_id,
    )

    # -------------------------------------------------------------
    # Build MCP client.
    # -------------------------------------------------------------

    client = _build_mcp_client(user_id)

    # -------------------------------------------------------------
    # Start MCP OAuth in background.
    # -------------------------------------------------------------

    async def _run():
        try:
            tools = await client.get_tools()
            log.info(
                "mcp_tools: authorization completed "
                "for user_id=%s, %d tools",
                user_id,
                len(tools),
            )
        except Exception as exc:
            if not auth_url_future.done():
                auth_url_future.set_exception(exc)
            log.exception(
                "mcp_tools: authorization flow failed "
                "for user_id=%s",
                user_id,
            )
        finally:
            if _pending.get(user_id, {}).get("auth_url_future") is auth_url_future:
                _pending.pop(user_id, None)

    authorization_task = asyncio.create_task(_run())

    # -------------------------------------------------------------
    # Wait for MCP SDK to generate authorization URL.
    # -------------------------------------------------------------

    try:

        auth_url = await asyncio.wait_for(
            asyncio.shield(auth_url_future),
            timeout=timeout,
        )

        return auth_url

    except asyncio.TimeoutError:

        log.error(
            "mcp_tools: timed out waiting for OAuth URL "
            "for user_id=%s",
            user_id,
        )

        authorization_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await authorization_task
        _pending.pop(user_id, None)

        raise

# async def list_tools(user_id: str) -> list[dict]:
#     """Plain {name, description} dicts for the UI. Only works if the
#     user already has a valid stored token -- does not trigger OAuth.
#     Includes gmail_* tools too (they're discovered from the MCP server
#     like any other tool) -- the UI is responsible for gating those
#     behind a separate Gmail-connect step; see routers/gmail.py.

#     Raises ReauthorizationRequired if the stored token turned out to be
#     dead (expired + unrefreshable) -- callers should tell the user to
#     hit /mcp/authorize again, not treat this as a 500."""
#     if not await has_valid_connection(user_id):
#         return []
#     client = _build_mcp_client(user_id)
#     try:
#         tools = await client.get_tools()
#     except ReauthorizationRequired:
#         await _clear_stored_token(user_id)
#         raise
#     return [{"name": t.name, "description": t.description} for t in tools]


async def list_tools(user_id: str) -> list[dict]:
    """
    Return MCP tools for a user.

    This endpoint does NOT start OAuth.

    If the user has no token, return an empty list.

    If the stored token is expired/rejected, raise
    ReauthorizationRequired so the API can tell the frontend
    that the user needs to reconnect.
    """

    # -------------------------------------------------------------
    # No stored token.
    # -------------------------------------------------------------

    if not await has_valid_connection(user_id):

        log.info(
            "mcp_tools: no MCP connection for user_id=%s",
            user_id,
        )

        return []

    # -------------------------------------------------------------
    # User has a stored token.
    # -------------------------------------------------------------

    client = _build_mcp_client(user_id)

    try:

        tools = await client.get_tools()

    except ReauthorizationRequired:

        log.warning(
            "mcp_tools: stored MCP token is no longer usable "
            "for user_id=%s",
            user_id,
        )

        await _clear_stored_token(user_id)

        raise

    return [
        {
            "name": tool.name,
            "description": tool.description,
        }
        for tool in tools
    ]

async def get_selected_tool_names(user_id: str) -> list[str]:
    """Cheap DB-only read -- no MCP client call. Used by main.py to
    detect whether a user's selection changed since the last request,
    so the (expensive) actual tool fetch + graph rebuild only happens
    when the selection key actually changes, not on every /chat call."""
    db = SessionLocal()
    try:
        row = db.query(McpConnection).filter(McpConnection.user_id == user_id).first()
        if not row or not row.selected_tools_json:
            return []
        return json.loads(row.selected_tools_json)
    finally:
        db.close()


async def get_selected_tools(user_id: str, exclude_names: set[str] | None = None):
    """Actual BaseTool objects for whichever tools this user selected.

    exclude_names lets a caller (main.py) keep tool families that need
    fresher/per-call auth -- currently just Gmail -- out of this path
    entirely, since the client built here is reused for every returned
    tool and doesn't refresh headers per call. See tools/gmail_tool.py
    for why Gmail tools are built separately instead.

    NOT yet wired into the graph's tool-execution node -- see the note
    in nodes.py/graph.py about ToolNode's static construction."""
    db = SessionLocal()
    try:
        row = db.query(McpConnection).filter(McpConnection.user_id == user_id).first()
        if not row or not row.token_json:
            return []
        selected_names = set(json.loads(row.selected_tools_json or "[]"))
    finally:
        db.close()

    if exclude_names:
        selected_names -= exclude_names

    if not selected_names:
        return []

    client = _build_mcp_client(user_id)
    try:
        all_tools = await client.get_tools()
    except ReauthorizationRequired:
        await _clear_stored_token(user_id)
        raise
    return [t for t in all_tools if t.name in selected_names]


async def set_selected_tools(user_id: str, tool_names: list[str]) -> None:
    db = SessionLocal()
    try:
        row = db.query(McpConnection).filter(McpConnection.user_id == user_id).first()
        if not row:
            raise ValueError("No MCP connection for this user -- authorize first.")
        row.selected_tools_json = json.dumps(tool_names)
        db.commit()
    finally:
        db.close()
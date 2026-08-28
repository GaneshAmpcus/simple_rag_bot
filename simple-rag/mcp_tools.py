"""
Generic per-(user, server, bot) MCP integration: a single backend "host"
that can build an MCP client for ANY server a user has added -- OAuth
(via the MCP SDK's OAuthClientProvider, dynamically registered against
each server -- no pre-registered redirect URI needed per server),
tokens persisted per (user, server, bot) in Postgres, and tool
listing/selection scoped to that (user, server, bot) triple.

README.md Phase 3: extends the earlier per-(user, server) design (see
git history / the old module docstring for that shape) with a third
key, `bot_id`, nullable:
  - bot_id = None  -> the "no bot" / user-level connection -- exactly
    the pre-Phase-3 behavior, unchanged, for sessions that never touch
    bots (README principle #5).
  - bot_id = "<id>" -> that bot's own independent connection to this
    same server, with its own token and its own tool selection,
    authorized separately from any other bot's (or the user's
    bot-less) connection to that same server.

models.McpServer (the name+url a user added) stays user-scoped, not
bot-scoped -- it's just a catalog entry shared across all of that
user's bots. Only models.McpConnection (the actual OAuth
token/selection) is bot-scoped. Every function below that used to take
`(user_id, server_id)` now takes `(user_id, server_id, bot_id)`.

Horizon -- the MCP server this app itself owns and depends on for
Gmail/Calendar/Meet tools (E:\\Ganesh\\MCP) -- is NOT special-cased at
the protocol level. It's auto-provisioned as an ordinary McpServer row
the first time it's needed (ensure_horizon_server, below), so it flows
through the exact same OAuth/client-building code path as any server a
user adds themselves. Because McpServer itself isn't bot-scoped, the
SAME Horizon server row is reused across all of a user's bots -- only
the per-bot McpConnection (and, for Gmail/Calendar, the separate
GmailConnection/CalendarConnection tables) differ per bot.

CONCURRENCY FIX (carried over from the pre-Phase-3 design): the old
`/mcp/callback` route resolved a callback by scanning ALL users with a
pending flow and taking the first match -- correct only for one user
testing alone. Every call to start_authorization() mints a random
`flow_id` and registers `{BACKEND_BASE_URL}/mcp/callback/{flow_id}` as
the OAuth redirect_uri for that flow specifically (safe to do per-flow
because MCP servers use dynamic client registration, unlike Google's
fixed, console-registered redirect URIs). routers/mcp.py's
`/mcp/callback/{flow_id}` route resolves directly via that id --
correct under any number of concurrent users AND any number of
concurrent per-user flows (e.g. authorizing two different servers, or
the same server for two different bots, at once), with no
scanning/guessing. This property is exactly why Phase 3 could add
per-bot MCP auth without first reworking the callback mechanism -- the
flow_id-keyed design already generalizes to "arbitrarily many
concurrent flows for one user."

Fully async by design (this module makes MCP network calls, unlike
gmail_oauth.py/calendar_oauth.py's plain-REST-over-sync-httpx
approach) -- main.py awaits these directly.
"""

import asyncio
import contextlib
import json
import os
import uuid
from typing import Optional

from dotenv import load_dotenv

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl
from sqlalchemy.orm import Session

from config.database import SessionLocal
from models import McpConnection, McpServer
from config.logging_config import get_logger

log = get_logger(__name__)

load_dotenv()

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")

# The one MCP server this app itself owns and depends on, for
# Gmail/Calendar/Meet tools (E:\Ganesh\MCP). Auto-provisioned per user
# via ensure_horizon_server() below -- not something the user adds
# through the generic "add a server" flow, but stored identically to
# whatever they do add (see models.McpServer's docstring). Not
# bot-scoped itself -- see module docstring.
HORIZON_SERVER_NAME = "Horizon"
HORIZON_SERVER_URL = "https://custom-mcp-by-ganesh.fastmcp.app/mcp"


# ---------------------------------------------------------------------
# Per-(user, server, bot) token storage, persisted in Postgres.
# ---------------------------------------------------------------------

class DbTokenStorage(TokenStorage):
    """Each method opens and closes its own DB session. This object is
    held across an OAuth flow that spans a browser redirect and can
    take the user minutes to complete -- deliberately never holds one
    request-scoped session open that whole time.

    `bot_id=None` addresses the "no bot" / user-level connection row --
    same row shape and same query pattern (just filtered on
    `bot_id IS NULL` instead of a real id) that existed before Phase 3,
    so bot-less sessions are unaffected by this generalization."""

    def __init__(self, user_id: str, server_id: str, bot_id: str | None):
        self.user_id = user_id
        self.server_id = server_id
        self.bot_id = bot_id

    def _filter(self, db: Session):
        return db.query(McpConnection).filter(
            McpConnection.user_id == self.user_id,
            McpConnection.server_id == self.server_id,
            McpConnection.bot_id == self.bot_id,
        )

    def _get_or_create_row(self, db: Session) -> McpConnection:
        row = self._filter(db).first()
        if not row:
            row = McpConnection(
                user_id=self.user_id,
                server_id=self.server_id,
                bot_id=self.bot_id,
                selected_tools_json="[]",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    async def get_tokens(self) -> Optional[OAuthToken]:
        db = SessionLocal()
        try:
            row = self._filter(db).first()
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
            row = self._filter(db).first()
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
# Bridging redirect_handler/callback_handler to real HTTP routes,
# keyed per-flow (not per-user) -- see module docstring.
# ---------------------------------------------------------------------

# flow_id -> {"user_id", "server_id", "bot_id", "auth_url_future", "callback_future"}
_pending: dict[str, dict] = {}


class ReauthorizationRequired(Exception):
    """Raised when the MCP SDK's OAuthClientProvider decides it needs to
    redo the full authorization-code redirect flow (e.g. the stored
    access token expired and either there's no refresh_token or the
    refresh itself was rejected by the server), but the call wasn't
    made through start_authorization() -- e.g. it came from list_tools
    or get_selected_tools, which are meant to be read-only against an
    ALREADY-valid token.

    In that situation there's no live authorize HTTP request waiting on
    an auth_url_future and no browser to redirect. Callers should catch
    this and tell the user to reconnect that specific (server, bot)
    pair rather than 500ing.
    """

    def __init__(self, user_id: str, server_id: str, bot_id: str | None = None):
        self.user_id = user_id
        self.server_id = server_id
        self.bot_id = bot_id
        super().__init__(
            f"MCP reauthorization required for user_id={user_id} "
            f"server_id={server_id} bot_id={bot_id}"
        )


def _make_redirect_handler(user_id: str, server_id: str, bot_id: str | None, flow_id: str):
    """Called by the MCP SDK when OAuth authorization is required.

    If `flow_id` has a live entry in `_pending` (i.e. start_authorization
    created it), deliver the auth URL to whoever's waiting on it. If
    not -- e.g. this client was built for a read-only call
    (list_tools/get_selected_tools) and its stored token turned out to
    be dead -- there is nobody waiting for a redirect, so raise
    ReauthorizationRequired instead of hanging or crashing."""

    async def redirect_handler(auth_url: str) -> None:
        pending = _pending.get(flow_id)

        if pending is None:
            log.warning(
                "mcp_tools: OAuth required for user_id=%s server_id=%s bot_id=%s, but no "
                "authorization flow is pending. Stored token is probably "
                "expired or invalid.",
                user_id, server_id, bot_id,
            )
            raise ReauthorizationRequired(user_id, server_id, bot_id)

        auth_url_future = pending["auth_url_future"]
        if auth_url_future.done():
            log.warning(
                "mcp_tools: auth_url_future already completed for flow_id=%s", flow_id
            )
            return

        log.info(
            "mcp_tools: delivering authorization URL for user_id=%s server_id=%s bot_id=%s",
            user_id, server_id, bot_id,
        )
        auth_url_future.set_result(auth_url)

    return redirect_handler


def _make_callback_handler(flow_id: str):
    """Called by the MCP OAuth client when it needs the authorization
    code returned by the browser callback."""

    async def callback_handler() -> tuple[str, str | None]:
        pending = _pending.get(flow_id)
        if pending is None:
            raise RuntimeError(f"No pending OAuth authorization for flow_id={flow_id}")

        log.info("mcp_tools: waiting for /mcp/callback/%s", flow_id)
        code, state = await pending["callback_future"]
        return code, state

    return callback_handler


def resolve_mcp_callback(flow_id: str, code: str, state: str | None) -> tuple[bool, str | None]:
    """Called by routers/mcp.py when /mcp/callback/{flow_id} receives
    the OAuth authorization code. Returns (resolved, bot_id) --
    resolved is False if there was no pending authorization for this
    flow_id (expired, already resolved, or bogus). bot_id (present
    whenever resolved is True) lets the router redirect the browser
    back to that specific bot's integrations page instead of the
    generic/user-level one, per README.md Phase 3."""
    pending = _pending.get(flow_id)
    if pending is None:
        log.warning(
            "mcp_tools: received OAuth callback for unknown/expired flow_id=%s", flow_id
        )
        return False, None

    callback_future = pending["callback_future"]
    if callback_future.done():
        log.warning("mcp_tools: callback future already completed for flow_id=%s", flow_id)
        return False, pending.get("bot_id")

    callback_future.set_result((code, state))
    log.info("mcp_tools: OAuth callback delivered for flow_id=%s", flow_id)
    return True, pending.get("bot_id")


# ---------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------

def _build_mcp_client(
    user_id: str,
    server_id: str,
    server_url: str,
    bot_id: str | None = None,
    flow_id: str = "none",
    extra_headers: dict | None = None,
) -> MultiServerMCPClient:
    """Builds an MCP client for one specific (user, server, bot) triple.

    `server_url` is passed to OAuthClientProvider AS-IS (the exact,
    full MCP endpoint URL -- e.g. "https://mcp.atlassian.com/v1/mcp/authv2",
    not just its origin). This matters: the SDK validates the server's
    protected-resource metadata against this URL per RFC 8707
    (`resource_url_from_server_url` + `check_resource_allowed` in
    mcp/shared/auth_utils.py), and that check requires our URL's path
    to be equal to or a *child* of whatever resource the server
    declares. Different servers declare this differently -- Horizon
    declares its bare origin (so any path we pass still matches, since
    a child path always starts with the parent's "/"), but e.g.
    Atlassian's MCP server declares the full endpoint path itself, so
    stripping to origin-only there made our expected resource
    *narrower* than what the server declared and validation failed
    with 'Protected resource ... does not match expected ...'.
    Passing the exact, full URL is the one choice that's correct for
    both cases (and generically for any server a user adds), since a
    path always satisfies a hierarchical match against itself.

    `bot_id` (nullable) selects which McpConnection row this client's
    token storage reads/writes -- see DbTokenStorage's docstring.

    `flow_id` ties this client's OAuth redirect back to a specific
    pending authorization (see module docstring); pass the real
    flow_id when calling this from start_authorization, and leave the
    default "none" for read-only calls (list_tools, get_selected_tools,
    check_connection) where a redirect should never actually be needed
    -- if the SDK tries to redirect anyway, redirect_handler above
    raises ReauthorizationRequired before this placeholder URL would
    ever be used.

    extra_headers lets callers attach per-call auth on top of this
    server's own OAuth, without that call needing its own
    OAuthClientProvider/MCP-spec OAuth handshake. Gmail/Calendar use
    this to forward a fresh Google access token on top of this
    (user, bot)'s Horizon connection (see tools/gmail_tool.py,
    tools/calendar_tool.py and build_horizon_client() below).
    """
    callback_url = f"{BACKEND_BASE_URL}/mcp/callback/{flow_id}"

    oauth_auth = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="Simple RAG MCP Client",
            redirect_uris=[AnyUrl(callback_url)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=DbTokenStorage(user_id, server_id, bot_id),
        redirect_handler=_make_redirect_handler(user_id, server_id, bot_id, flow_id),
        callback_handler=_make_callback_handler(flow_id),
    )
    return MultiServerMCPClient({
        server_id: {
            "url": server_url,
            "transport": "streamable_http",
            "auth": oauth_auth,
            "headers": extra_headers or {},
        }
    })


# ---------------------------------------------------------------------
# Horizon -- this app's own fixed MCP server (Gmail/Calendar/Meet).
# ---------------------------------------------------------------------

async def ensure_horizon_server(user_id: str) -> str:
    """Auto-provisions (if missing) and returns the server_id of this
    user's Horizon server row. Every user gets this implicitly the
    first time a Gmail/Calendar/Meet tool call needs it -- it isn't
    something they add themselves via the generic 'add a server' flow,
    but it's stored the same way (as a normal McpServer row) so it
    flows through the same OAuth/client machinery as everything else
    in this module rather than needing special-casing throughout.

    Not bot-scoped: the same Horizon *server* row is shared across all
    of a user's bots (and their bot-less sessions) -- only the
    per-(user, server, bot) McpConnection differs per bot. Gmail/
    Calendar tools don't even go through McpConnection's OAuth at all
    (they use GmailConnection/CalendarConnection instead, forwarding a
    fresh Google token as a header on top of Horizon -- see
    tools/gmail_tool.py, tools/calendar_tool.py), so per-bot scoping
    for those lives entirely in gmail_oauth.py/calendar_oauth.py, not
    here."""
    db = SessionLocal()
    try:
        row = (
            db.query(McpServer)
            .filter(McpServer.user_id == user_id, McpServer.url == HORIZON_SERVER_URL)
            .first()
        )
        if row:
            return row.id
        row = McpServer(user_id=user_id, name=HORIZON_SERVER_NAME, url=HORIZON_SERVER_URL)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def build_horizon_client(
    user_id: str, server_id: str, extra_headers: dict | None = None
) -> MultiServerMCPClient:
    """Thin convenience wrapper for tools/gmail_tool.py and
    tools/calendar_tool.py -- they need to attach a per-call Google
    access token header on top of this user's Horizon connection. Call
    ensure_horizon_server() first to get server_id.

    No bot_id here: Gmail/Calendar tool calls authenticate purely via
    the forwarded Google token header (extra_headers), never via
    Horizon's own McpConnection/OAuth row, so which bot is asking
    doesn't change how this client is built -- it only changes which
    GmailConnection/CalendarConnection row the caller fetched the
    token from before calling this."""
    return _build_mcp_client(user_id, server_id, HORIZON_SERVER_URL, extra_headers=extra_headers)


# ---------------------------------------------------------------------
# Server CRUD (the generic "add as many servers as you want" surface,
# user-scoped -- see module docstring for why servers themselves are
# not bot-scoped).
# ---------------------------------------------------------------------

async def add_server(user_id: str, name: str, url: str) -> dict:
    db = SessionLocal()
    try:
        server = McpServer(user_id=user_id, name=name, url=url)
        db.add(server)
        db.commit()
        db.refresh(server)
        return {"id": server.id, "name": server.name, "url": server.url}
    finally:
        db.close()


async def list_servers(user_id: str, bot_id: str | None = None) -> list[dict]:
    """All servers this user has added (including the auto-provisioned
    Horizon one, if it exists yet), each annotated with whether a live
    token is currently stored FOR THIS bot_id specifically -- cheap
    DB-only check, does not verify the token still works against the
    remote server (see check_connection for that).

    `bot_id=None` (the default) reports the "no bot" connection status,
    identical to pre-Phase-3 behavior. Passing a real bot_id reports
    that bot's own independent connection status for each server."""
    db = SessionLocal()
    try:
        servers = db.query(McpServer).filter(McpServer.user_id == user_id).all()
        conns = {
            c.server_id: c
            for c in db.query(McpConnection)
            .filter(McpConnection.user_id == user_id, McpConnection.bot_id == bot_id)
            .all()
        }
    finally:
        db.close()

    return [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "connected": bool(conns.get(s.id) and conns[s.id].token_json),
            "is_builtin": s.url == HORIZON_SERVER_URL,
        }
        for s in servers
    ]


async def get_server(user_id: str, server_id: str) -> Optional[McpServer]:
    db = SessionLocal()
    try:
        return (
            db.query(McpServer)
            .filter(McpServer.id == server_id, McpServer.user_id == user_id)
            .first()
        )
    finally:
        db.close()


async def delete_server(user_id: str, server_id: str) -> bool:
    """Deletes a server and (via cascade="all, delete-orphan" on
    McpServer.connection) ALL of its associated McpConnection rows --
    across every bot, not just one -- since the server itself is
    user-scoped, not bot-scoped (see module docstring). Refuses to
    delete the built-in Horizon server -- Gmail/Calendar/Meet tools
    depend on it existing; callers should check `is_builtin` (see
    list_servers) before offering a delete action."""
    db = SessionLocal()
    try:
        server = (
            db.query(McpServer)
            .filter(McpServer.id == server_id, McpServer.user_id == user_id)
            .first()
        )
        if not server:
            return False
        if server.url == HORIZON_SERVER_URL:
            raise ValueError("The built-in Horizon server can't be removed.")
        db.delete(server)
        db.commit()
        return True
    finally:
        db.close()


async def delete_bot_connections(user_id: str, bot_id: str) -> None:
    """Deletes every McpConnection row scoped to this specific bot
    (across all servers) -- called from routers/bots.py when a bot is
    deleted, so its per-bot OAuth tokens/selections don't linger as
    orphaned rows once the bot itself is gone. Leaves the McpServer
    catalog rows and any OTHER bot's (or the bot-less) connections to
    those same servers untouched."""
    db = SessionLocal()
    try:
        db.query(McpConnection).filter(
            McpConnection.user_id == user_id, McpConnection.bot_id == bot_id
        ).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------
# Connection status / token lifecycle
# ---------------------------------------------------------------------

async def _clear_stored_token(
    user_id: str, server_id: str, bot_id: str | None, clear_client_info: bool = False
) -> None:
    """Wipes a dead/rejected token so has_valid_connection() (and thus
    the UI's 'connected' status) reflects reality, and the user is
    prompted to reconnect rather than seeing a silently-broken
    'connected' state."""
    db = SessionLocal()
    try:
        row = (
            db.query(McpConnection)
            .filter(
                McpConnection.user_id == user_id,
                McpConnection.server_id == server_id,
                McpConnection.bot_id == bot_id,
            )
            .first()
        )
        if row:
            row.token_json = None
            if clear_client_info:
                row.client_info_json = None
            db.commit()
    finally:
        db.close()


async def has_valid_connection(user_id: str, server_id: str, bot_id: str | None = None) -> bool:
    """Cheap check -- does this (user, server, bot) triple already have
    a stored token, without triggering a new OAuth flow if not."""
    token = await DbTokenStorage(user_id, server_id, bot_id).get_tokens()
    return token is not None


async def check_connection(
    user_id: str, server_id: str, server_url: str, bot_id: str | None = None
) -> bool:
    """Verify that the stored token for this (user, server, bot) triple
    still works, without starting OAuth."""
    if not await has_valid_connection(user_id, server_id, bot_id):
        return False

    client = _build_mcp_client(user_id, server_id, server_url, bot_id=bot_id)
    try:
        await client.get_tools()
    except ReauthorizationRequired:
        await _clear_stored_token(user_id, server_id, bot_id)
        return False
    except Exception as exc:
        log.warning(
            "mcp_tools: connection check failed for user_id=%s server_id=%s bot_id=%s; "
            "requiring authorization again: %s",
            user_id, server_id, bot_id, exc,
        )
        await _clear_stored_token(user_id, server_id, bot_id)
        return False
    return True


# ---------------------------------------------------------------------
# Authorization flow
# ---------------------------------------------------------------------

async def start_authorization(
    user_id: str,
    server_id: str,
    server_url: str,
    bot_id: str | None = None,
    timeout: float = 60.0,
) -> str:
    """Kicks off OAuth for one specific (user, server, bot) triple and
    returns the URL to redirect the user's browser to. Runs the rest
    of the flow (get_tools(), which blocks on the callback) as a
    background task -- this function only waits for the auth_url, not
    the full login.

    A user can now have several of these in flight at once (e.g.
    authorizing the same server for two different bots, or a server
    and Horizon at the same time) -- each gets its own flow_id/pending
    entry, so they resolve independently (see module docstring)."""

    # Don't start two OAuth flows for the same (user, server, bot) triple.
    existing_flow_id = next(
        (
            fid
            for fid, p in _pending.items()
            if p["user_id"] == user_id
            and p["server_id"] == server_id
            and p["bot_id"] == bot_id
            and not p["auth_url_future"].done()
        ),
        None,
    )
    if existing_flow_id:
        log.warning(
            "mcp_tools: OAuth authorization already pending for user_id=%s server_id=%s bot_id=%s",
            user_id, server_id, bot_id,
        )
        return await asyncio.wait_for(
            asyncio.shield(_pending[existing_flow_id]["auth_url_future"]), timeout=timeout
        )

    # A connected server may be explicitly reauthorized from the UI.
    # Remove only its access token so the SDK starts a fresh OAuth flow;
    # keep client metadata and selected tools intact.
    await _clear_stored_token(user_id, server_id, bot_id, clear_client_info=True)

    flow_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    auth_url_future = loop.create_future()
    callback_future = loop.create_future()

    _pending[flow_id] = {
        "user_id": user_id,
        "server_id": server_id,
        "bot_id": bot_id,
        "auth_url_future": auth_url_future,
        "callback_future": callback_future,
    }
    log.info(
        "mcp_tools: OAuth pending state created for user_id=%s server_id=%s bot_id=%s flow_id=%s",
        user_id, server_id, bot_id, flow_id,
    )

    client = _build_mcp_client(user_id, server_id, server_url, bot_id=bot_id, flow_id=flow_id)

    async def _run():
        try:
            tools = await client.get_tools()
            log.info(
                "mcp_tools: authorization completed for user_id=%s server_id=%s bot_id=%s, %d tool(s)",
                user_id, server_id, bot_id, len(tools),
            )
        except Exception as exc:
            if not auth_url_future.done():
                auth_url_future.set_exception(exc)
            log.exception(
                "mcp_tools: authorization flow failed for user_id=%s server_id=%s bot_id=%s",
                user_id, server_id, bot_id,
            )
        finally:
            if _pending.get(flow_id, {}).get("auth_url_future") is auth_url_future:
                _pending.pop(flow_id, None)

    authorization_task = asyncio.create_task(_run())

    try:
        auth_url = await asyncio.wait_for(asyncio.shield(auth_url_future), timeout=timeout)
        return auth_url
    except asyncio.TimeoutError:
        log.error(
            "mcp_tools: timed out waiting for OAuth URL for user_id=%s server_id=%s bot_id=%s",
            user_id, server_id, bot_id,
        )
        authorization_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await authorization_task
        _pending.pop(flow_id, None)
        raise


# ---------------------------------------------------------------------
# Tool listing / selection, scoped per (user, server, bot)
# ---------------------------------------------------------------------

async def list_tools(
    user_id: str, server_id: str, server_url: str, bot_id: str | None = None
) -> list[dict]:
    """Plain {name, description} dicts for the UI, for one specific
    (server, bot) pair. Only works if this triple already has a valid
    stored token -- does not trigger OAuth.

    Raises ReauthorizationRequired if the stored token turned out to be
    dead (expired + unrefreshable) -- callers should tell the user to
    authorize that (server, bot) pair again, not treat this as a 500."""
    if not await has_valid_connection(user_id, server_id, bot_id):
        return []

    client = _build_mcp_client(user_id, server_id, server_url, bot_id=bot_id)
    try:
        tools = await client.get_tools()
    except ReauthorizationRequired:
        await _clear_stored_token(user_id, server_id, bot_id)
        raise
    return [{"name": t.name, "description": t.description} for t in tools]


async def get_selected_tool_names(
    user_id: str, server_id: str, bot_id: str | None = None
) -> list[str]:
    """Cheap DB-only read -- no MCP client call. Used to detect whether
    a user's selection for this (server, bot) pair changed since the
    last request."""
    db = SessionLocal()
    try:
        row = (
            db.query(McpConnection)
            .filter(
                McpConnection.user_id == user_id,
                McpConnection.server_id == server_id,
                McpConnection.bot_id == bot_id,
            )
            .first()
        )
        if not row or not row.selected_tools_json:
            return []
        return json.loads(row.selected_tools_json)
    finally:
        db.close()


async def get_all_selections(user_id: str, bot_id: str | None = None) -> list[dict]:
    """Every server this (user, bot) pair has a non-empty tool
    selection on, each as
    {"server_id", "server_url", "server_name", "tool_names"}. This is
    what main.py loops over to build a session's full per-server tool
    set across ALL servers connected for that specific bot_id (or, for
    bot_id=None, the "no bot" selections -- exactly the pre-Phase-3
    behavior).

    Deliberately scoped strictly to this one bot_id, not "this bot's
    selections plus the user's global ones" -- README.md Phase 3's
    acceptance criterion is that a tool authorized+selected for bot A
    does NOT leak into bot B (or into bot-less sessions) unless
    separately authorized+selected there too."""
    db = SessionLocal()
    try:
        rows = (
            db.query(McpConnection, McpServer)
            .join(McpServer, McpConnection.server_id == McpServer.id)
            .filter(McpConnection.user_id == user_id, McpConnection.bot_id == bot_id)
            .all()
        )
    finally:
        db.close()

    result = []
    for conn, server in rows:
        names = json.loads(conn.selected_tools_json or "[]")
        if names:
            result.append(
                {
                    "server_id": server.id,
                    "server_url": server.url,
                    "server_name": server.name,
                    "tool_names": names,
                }
            )
    return result


async def get_selected_tools(
    user_id: str,
    server_id: str,
    server_url: str,
    bot_id: str | None = None,
    exclude_names: set[str] | None = None,
):
    """Actual BaseTool objects for whichever tools this (user, server,
    bot) triple selected.

    exclude_names lets a caller (main.py) keep tool families that need
    fresher/per-call auth -- currently Gmail and Calendar/Meet -- out
    of this path entirely, since the client built here is reused for
    every returned tool and doesn't refresh headers per call. See
    tools/gmail_tool.py and tools/calendar_tool.py for why those are
    built separately instead."""
    selected_names = set(await get_selected_tool_names(user_id, server_id, bot_id))
    if exclude_names:
        selected_names -= exclude_names
    if not selected_names:
        return []

    client = _build_mcp_client(user_id, server_id, server_url, bot_id=bot_id)
    try:
        all_tools = await client.get_tools()
    except ReauthorizationRequired:
        await _clear_stored_token(user_id, server_id, bot_id)
        raise
    return [t for t in all_tools if t.name in selected_names]


async def set_selected_tools(
    user_id: str, server_id: str, tool_names: list[str], bot_id: str | None = None
) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(McpConnection)
            .filter(
                McpConnection.user_id == user_id,
                McpConnection.server_id == server_id,
                McpConnection.bot_id == bot_id,
            )
            .first()
        )
        if not row:
            raise ValueError("No connection for this server -- authorize it first.")
        row.selected_tools_json = json.dumps(tool_names)
        db.commit()
    finally:
        db.close()

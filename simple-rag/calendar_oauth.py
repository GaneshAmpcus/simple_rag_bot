"""
Calendar/Meet OAuth -- Google's standard OAuth2 authorization-code
flow, per-(user, bot), tokens stored in this app's own DB.

Mirrors gmail_oauth.py's shape exactly (same rationale for being a
plain OAuth2 flow rather than routed through the Horizon MCP OAuth
handshake -- see that module's docstring). Kept as a SEPARATE module
and DB row from Gmail rather than folded into gmail_oauth.py, even
though both ultimately go through the same Google OAuth endpoints:
a user may want Calendar/Meet without Gmail (or vice versa), each
connection has its own token lifecycle, and this way Gmail's
already-working code path is untouched by this addition.

Covers both Calendar and Meet: Google Meet links are created through
the Calendar API (see MCP server's tools/meet_tool.py for why), so
Meet needs no separate OAuth connection or scope beyond Calendar's --
one connection powers both tool families.

README.md Phase 4: every function here now takes an optional
`bot_id`, matching models.CalendarConnection's new (user_id, bot_id)
scoping -- see gmail_oauth.py's module docstring for the full
bot_id=None/bot_id=<id> semantics and the signed-state rationale,
which this module mirrors exactly (own salt, so a state token minted
here can't be replayed against /gmail/callback or vice versa).

Fully synchronous by design, same reasoning as gmail_oauth.py (plain
httpx.Client calls, sync SQLAlchemy, avoids asyncio.run()-per-call
event-loop churn). main.py awaits this via asyncio.to_thread().
"""

import os
from datetime import datetime, timedelta

import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

from config.database import SessionLocal
from models import CalendarConnection
from config.logging_config import get_logger

log = get_logger(__name__)

# Reuses the same Google OAuth client as Gmail (gmail_oauth.py) -- one
# Google Cloud project/OAuth client can have multiple authorized
# redirect URIs, so this just needs its own redirect URI
# (/calendar/callback) registered alongside Gmail's in Google Cloud
# Console; no second client needed.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
CALENDAR_CALLBACK_URL = f"{BACKEND_BASE_URL}/calendar/callback"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Least-privilege: read + create/delete events only. No settings scope.
# This single scope set also covers Meet, since Meet links are created
# via Calendar API events (conferenceData) -- no separate Meet scope
# needed for that.
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
    raise ValueError(
        "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set. In Google Cloud "
        "Console: create (or reuse the Gmail one) OAuth 2.0 Client ID "
        "(type: Web application), enable the Google Calendar API, add "
        "http://localhost:8000/calendar/callback as an authorized "
        "redirect URI, then add both values to .env."
    )

# Signs {user_id, bot_id} into the OAuth `state` param and verifies it
# on callback -- same pattern and same rationale as gmail_oauth.py.
# Uses its own salt ("calendar-oauth-state") so a state token minted
# for this flow can't be replayed against /gmail/callback or vice versa.
_STATE_SECRET = os.environ.get("GMAIL_STATE_SECRET", GOOGLE_CLIENT_SECRET)
_serializer = URLSafeTimedSerializer(_STATE_SECRET, salt="calendar-oauth-state")


def _sign_state(user_id: str, bot_id: str | None) -> str:
    return _serializer.dumps({"user_id": user_id, "bot_id": bot_id})


def _verify_state(state: str, max_age_seconds: int = 600) -> tuple[str | None, str | None]:
    """Returns (user_id, bot_id), both None if the state is invalid/expired."""
    try:
        data = _serializer.loads(state, max_age=max_age_seconds)
        return data.get("user_id"), data.get("bot_id")
    except (BadSignature, SignatureExpired):
        log.warning("calendar_oauth: invalid or expired OAuth state")
        return None, None


def build_authorize_url(user_id: str, bot_id: str | None = None) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": CALENDAR_CALLBACK_URL,
        "response_type": "code",
        "scope": " ".join(CALENDAR_SCOPES),
        "access_type": "offline",  # required to receive a refresh_token
        "prompt": "consent",       # forces refresh_token on repeat auth too
        "state": _sign_state(user_id, bot_id),
    }
    return str(httpx.URL(GOOGLE_AUTH_URL, params=params))


def _get_or_create_row(db, user_id: str, bot_id: str | None) -> CalendarConnection:
    row = (
        db.query(CalendarConnection)
        .filter(CalendarConnection.user_id == user_id, CalendarConnection.bot_id == bot_id)
        .first()
    )
    if not row:
        row = CalendarConnection(user_id=user_id, bot_id=bot_id)
        db.add(row)
        db.flush()
    return row


def handle_callback(code: str, state: str) -> tuple[bool, str | None, str | None]:
    """Verifies state, exchanges the code for tokens, stores them.
    Returns (success, user_id, bot_id)."""
    user_id, bot_id = _verify_state(state)
    if not user_id:
        return False, None, None

    try:
        with httpx.Client(timeout=15) as client:
            token_resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": CALENDAR_CALLBACK_URL,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            email_address = None
            userinfo_resp = client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            if userinfo_resp.status_code == 200:
                email_address = userinfo_resp.json().get("email")
    except Exception:
        log.exception(
            "calendar_oauth: token exchange failed for user_id=%s bot_id=%s", user_id, bot_id
        )
        return False, user_id, bot_id

    db = SessionLocal()
    try:
        row = _get_or_create_row(db, user_id, bot_id)
        row.access_token = token_data["access_token"]
        # Google only returns refresh_token on first consent (or when
        # prompt=consent is passed, which we always do) -- don't
        # overwrite an existing one with None if this response omits it.
        if token_data.get("refresh_token"):
            row.refresh_token = token_data["refresh_token"]
        row.token_expiry = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        row.email_address = email_address
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    log.info(
        "calendar_oauth: connected user_id=%s bot_id=%s email=%s", user_id, bot_id, email_address
    )
    return True, user_id, bot_id


def get_valid_access_token(user_id: str, bot_id: str | None = None) -> str | None:
    """Returns a valid access token for this (user, bot) pair,
    refreshing it first if expired. Called on every Calendar/Meet tool
    invocation (tools/calendar_tool.py, tools/meet_tool.py), not
    cached anywhere beyond the DB row -- always current regardless of
    how long a per-(user, bot) LangGraph graph has been cached in
    main.py."""
    db = SessionLocal()
    try:
        row = (
            db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user_id, CalendarConnection.bot_id == bot_id)
            .first()
        )
        if not row or not row.access_token:
            return None

        if row.token_expiry and row.token_expiry > datetime.utcnow() + timedelta(seconds=60):
            return row.access_token

        if not row.refresh_token:
            log.warning(
                "calendar_oauth: token expired, no refresh_token for user_id=%s bot_id=%s",
                user_id, bot_id,
            )
            return None

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": GOOGLE_CLIENT_ID,
                        "client_secret": GOOGLE_CLIENT_SECRET,
                        "refresh_token": row.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()
        except Exception:
            log.exception(
                "calendar_oauth: token refresh failed for user_id=%s bot_id=%s", user_id, bot_id
            )
            return None

        row.access_token = token_data["access_token"]
        row.token_expiry = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        row.updated_at = datetime.utcnow()
        db.commit()
        return row.access_token
    finally:
        db.close()


def has_valid_connection(user_id: str, bot_id: str | None = None) -> bool:
    return get_valid_access_token(user_id, bot_id) is not None


def get_connection_status(user_id: str, bot_id: str | None = None) -> dict:
    db = SessionLocal()
    try:
        row = (
            db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user_id, CalendarConnection.bot_id == bot_id)
            .first()
        )
    finally:
        db.close()
    if not row or not row.access_token:
        return {"connected": False, "email_address": None}
    return {"connected": has_valid_connection(user_id, bot_id), "email_address": row.email_address}

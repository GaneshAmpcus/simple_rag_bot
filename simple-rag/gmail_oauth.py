"""
Gmail OAuth -- Google's standard OAuth2 authorization-code flow,
per-user, tokens stored in this app's own DB.

Deliberately NOT built as an MCP server: this app owns both the tool
and its only consumer, so there's no reason to gate access through a
separate MCP-spec OAuth handshake the way the Horizon integration
required -- that pattern earns its complexity when connecting to a
server you don't control. Here, plain OAuth2 straight to Google is
simpler and sufficient.

Fully synchronous by design (plain httpx.Client calls, sync
SQLAlchemy) -- avoids the asyncio.run()-per-call event-loop churn that
caused real "Event loop is closed" issues elsewhere in this app
(guardrails_layer.py, before that got fixed). Google's own Python
client libraries (used in tools/gmail_tool.py) are sync/blocking
anyway, so there's nothing to gain from async here. main.py awaits
this via asyncio.to_thread() rather than making this module async, to
avoid blocking the event loop during the occasional token-refresh call.
"""

import os
from datetime import datetime, timedelta

import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

from config.database import SessionLocal
from models import GmailConnection
from config.logging_config import get_logger

log = get_logger(__name__)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
GMAIL_CALLBACK_URL = f"{BACKEND_BASE_URL}/gmail/callback"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Least-privilege: read + send only. No delete/modify/settings scopes.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
    raise ValueError(
        "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set. In Google Cloud "
        "Console: create an OAuth 2.0 Client ID (type: Web application), "
        "enable the Gmail API, add "
        "http://localhost:8000/gmail/callback as an authorized redirect "
        "URI, then add both values to .env."
    )

# Signs user_id into the OAuth `state` param and verifies it on
# callback -- unlike the MCP/Horizon flow's still-open gap (matching a
# callback to "whichever user has a pending request"), this can't be
# hijacked or misattributed to the wrong user. Falls back to
# GOOGLE_CLIENT_SECRET if GMAIL_STATE_SECRET isn't set separately.
_STATE_SECRET = os.environ.get("GMAIL_STATE_SECRET", GOOGLE_CLIENT_SECRET)
_serializer = URLSafeTimedSerializer(_STATE_SECRET, salt="gmail-oauth-state")


def _sign_state(user_id: str) -> str:
    return _serializer.dumps({"user_id": user_id})


def _verify_state(state: str, max_age_seconds: int = 600) -> str | None:
    try:
        data = _serializer.loads(state, max_age=max_age_seconds)
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        log.warning("gmail_oauth: invalid or expired OAuth state")
        return None


def build_authorize_url(user_id: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GMAIL_CALLBACK_URL,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",  # required to receive a refresh_token
        "prompt": "consent",       # forces refresh_token on repeat auth too
        "state": _sign_state(user_id),
    }
    return str(httpx.URL(GOOGLE_AUTH_URL, params=params))


def _get_or_create_row(db, user_id: str) -> GmailConnection:
    row = db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()
    if not row:
        row = GmailConnection(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def handle_callback(code: str, state: str) -> tuple[bool, str | None]:
    """Verifies state, exchanges the code for tokens, stores them.
    Returns (success, user_id)."""
    user_id = _verify_state(state)
    if not user_id:
        return False, None

    try:
        with httpx.Client(timeout=15) as client:
            token_resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GMAIL_CALLBACK_URL,
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
        log.exception("gmail_oauth: token exchange failed for user_id=%s", user_id)
        return False, user_id

    db = SessionLocal()
    try:
        row = _get_or_create_row(db, user_id)
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

    log.info("gmail_oauth: connected user_id=%s email=%s", user_id, email_address)
    return True, user_id


def get_valid_access_token(user_id: str) -> str | None:
    """Returns a valid access token, refreshing it first if expired.
    Called on every Gmail tool invocation (tools/gmail_tool.py), not
    cached anywhere beyond the DB row -- always current regardless of
    how long a per-user LangGraph graph has been cached in main.py."""
    db = SessionLocal()
    try:
        row = db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()
        if not row or not row.access_token:
            return None

        if row.token_expiry and row.token_expiry > datetime.utcnow() + timedelta(seconds=60):
            return row.access_token

        if not row.refresh_token:
            log.warning("gmail_oauth: token expired, no refresh_token for user_id=%s", user_id)
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
            log.exception("gmail_oauth: token refresh failed for user_id=%s", user_id)
            return None

        row.access_token = token_data["access_token"]
        row.token_expiry = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        row.updated_at = datetime.utcnow()
        db.commit()
        return row.access_token
    finally:
        db.close()


def has_valid_connection(user_id: str) -> bool:
    return get_valid_access_token(user_id) is not None


def get_connection_status(user_id: str) -> dict:
    db = SessionLocal()
    try:
        row = db.query(GmailConnection).filter(GmailConnection.user_id == user_id).first()
    finally:
        db.close()
    if not row or not row.access_token:
        return {"connected": False, "email_address": None}
    return {"connected": has_valid_connection(user_id), "email_address": row.email_address}
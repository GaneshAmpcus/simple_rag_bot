import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from config.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    bots = relationship("Bot", back_populates="user", cascade="all, delete-orphan")
    mcp_servers = relationship(
        "McpServer", back_populates="user", cascade="all, delete-orphan"
    )
    mcp_connections = relationship(
        "McpConnection", back_populates="user", cascade="all, delete-orphan"
    )
    # uselist=False dropped: a user can now have more than one Gmail/
    # Calendar connection (one per bot, plus the bot_id=NULL "no bot"
    # one) -- README.md Phase 4. Callers look up the specific row for
    # a given bot_id via gmail_oauth.py/calendar_oauth.py rather than
    # through this relationship directly.
    gmail_connections = relationship(
        "GmailConnection", back_populates="user", cascade="all, delete-orphan"
    )
    calendar_connections = relationship(
        "CalendarConnection", back_populates="user", cascade="all, delete-orphan"
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    bot_id = Column(String, ForeignKey("bots.id"), nullable=True)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    bot = relationship("Bot", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class Bot(Base):
    """A user-owned, named assistant configuration -- see
    E:\Ganesh\Rag_Chatbot\README.md section 2 for the full plan this
    implements.

    Phase 1: `system_prompt` is free-text instructions/persona, layered
    on top of (never replacing) the app's non-negotiable IDENTITY RULES
    block in prompts.py -- see build_agent_system_prompt's
    bot_instructions param.

    Phase 2: each bot gets its own knowledge base, addressed by
    rag.bot_kb_name(bot.id) -- a distinct FAISS index directory, lazily
    loaded on first use (see rag.py's _get_kb_store). There's no
    separate KB table; the bot's id IS the KB's key, per README's
    'start with one KB per bot' simplification (§5, open questions).

    A bot has no tool/MCP/Gmail scoping yet -- that's README Phase 3/4,
    intentionally not touched here.
    """
    __tablename__ = "bots"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="bots")
    sessions = relationship("ChatSession", back_populates="bot")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")


class McpServer(Base):
    """One row per MCP server a user has added. Replaces the old design
    where this app only ever talked to one hardcoded, globally-shared
    MCP server URL (the 'Horizon' instance, set in mcp_tools.py) -- a
    user can now add any number of MCP servers by name+url, each with
    its own independent OAuth connection (McpConnection below) and
    tool selection.

    Horizon itself is no longer special-cased here: it's a normal row,
    auto-provisioned per user the first time it's needed (see
    mcp_tools.ensure_horizon_server) rather than requiring the user to
    add it manually -- it's the one server this app itself depends on
    (for Gmail/Calendar/Meet tools), so it shouldn't require a manual
    'add server' step just to become usable.
    """
    __tablename__ = "mcp_servers"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="mcp_servers")
    connection = relationship(
        "McpConnection", back_populates="server", uselist=False, cascade="all, delete-orphan"
    )


class McpConnection(Base):
    """One row per (user_id, server_id, bot_id) OAuth connection to a
    specific McpServer.

    README.md Phase 3: previously one row per (user_id, server_id) --
    a single connection per server, shared by every bot (and
    bot-less sessions) that user had. `bot_id` is now part of the key,
    nullable:
      - bot_id = NULL  -> the "no bot" / user-level connection. This is
        exactly the row shape that existed before Phase 3, so a
        session with no bot keeps behaving exactly as it did (README
        principle #5) -- it just reads/writes the bot_id=NULL row.
      - bot_id = <id>  -> that bot's own independent connection to this
        same server: its own token, its own tool selection. Connecting
        the same MCP server for two different bots (or for a bot and
        for "no bot") creates two separate rows here, each with its
        own OAuth handshake -- explicit requirement, see README §2.1.

    McpServer itself (the name+url the user added) stays user-scoped,
    not bot-scoped -- it's just a catalog entry; only the *connection*
    (auth + tool selection) is what gets isolated per bot."""
    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "server_id", "bot_id", name="uq_mcp_connection_user_server_bot"
        ),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    server_id = Column(String, ForeignKey("mcp_servers.id"), nullable=False)
    bot_id = Column(String, ForeignKey("bots.id"), nullable=True)
    token_json = Column(Text, nullable=True)
    client_info_json = Column(Text, nullable=True)
    selected_tools_json = Column(Text, nullable=True, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="mcp_connections")
    server = relationship("McpServer", back_populates="connection")
    bot = relationship("Bot")


class GmailConnection(Base):
    """One row per (user_id, bot_id) Gmail OAuth connection. Plain
    Google OAuth2, not MCP -- see gmail_oauth.py for why that's the
    right call for a tool this app both owns and is the sole consumer
    of.

    README.md Phase 4: same bot_id-nullable scoping as McpConnection
    (see its docstring) -- bot_id=NULL is the pre-Phase-4 "no bot"
    connection (unchanged behavior for bot-less sessions), bot_id=<id>
    is that bot's own independent Gmail account/token, authorized
    separately. The old `user_id` unique=True is replaced by a
    (user_id, bot_id) composite unique -- a user can now have more
    than one Gmail connection (one per bot), just never two for the
    same (user, bot) pair."""
    __tablename__ = "gmail_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "bot_id", name="uq_gmail_connection_user_bot"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    bot_id = Column(String, ForeignKey("bots.id"), nullable=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    email_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="gmail_connections")
    bot = relationship("Bot")


class CalendarConnection(Base):
    """One row per (user_id, bot_id) Calendar/Meet OAuth connection.
    Plain Google OAuth2, mirrors GmailConnection -- see
    calendar_oauth.py for why this is a separate connection/row from
    Gmail even though both go through the same Google OAuth endpoints.
    Powers both Calendar and Meet tools since Meet links are created
    via the Calendar API -- there is no separate Meet connection/table.

    README.md Phase 4: same bot_id-nullable scoping as GmailConnection
    -- see its docstring for the bot_id=NULL/bot_id=<id> semantics."""
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "bot_id", name="uq_calendar_connection_user_bot"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    bot_id = Column(String, ForeignKey("bots.id"), nullable=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    email_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="calendar_connections")
    bot = relationship("Bot")
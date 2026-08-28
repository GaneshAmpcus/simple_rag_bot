"""
FastAPI backend for the LangChain-based RAG pipeline.

Run with:
    uvicorn main:app --reload
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends
from sqlalchemy.orm import Session
from rag import build_pipeline_state, ingest, query, bot_kb_name, get_kb_store
from vector_store import count
from graph import build_graph
from guardrails_layer import check_output
from memory_layer import add_turn
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from config.logging_config import get_logger

from config.database import Base, engine, get_db
from routers import auth as auth_router
from routers import sessions as sessions_router
from routers import mcp as mcp_router
from routers import gmail as gmail_router
from routers import calendar as calendar_router
from routers import bots as bots_router
from routers.sessions import _get_owned_session
from routers.bots import _get_owned_bot
from security import get_current_user
from models import User, ChatSession, Message, Bot
from schemas import ChatRequest, ChatResponse, QueryRequest
import mcp_tools
import gmail_oauth
import calendar_oauth
from tools.gmail_tool import GMAIL_TOOL_NAMES, build_gmail_tools
from tools.calendar_tool import (
    CALENDAR_AND_MEET_TOOL_NAMES,
    build_calendar_tools,
)
from fastapi.middleware.cors import CORSMiddleware

log = get_logger(__name__)


load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Simple RAG API (LangChain)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(sessions_router.router)
app.include_router(mcp_router.router)
app.include_router(mcp_router.bot_router)  # README Phase 3: /bots/{bot_id}/mcp/...
app.include_router(gmail_router.router)
app.include_router(gmail_router.bot_router)  # README Phase 4: /bots/{bot_id}/gmail/...
app.include_router(calendar_router.router)
app.include_router(calendar_router.bot_router)  # README Phase 4: /bots/{bot_id}/calendar/...
app.include_router(bots_router.router)

state = build_pipeline_state()

agent_graph = build_graph(state)  # the default graph every user gets unless
# they've authorized/selected MCP tools or connected Gmail. Never mutated
# by the per-user code path below.

HISTORY_TURN_LIMIT = 5

_ROLE_TO_MESSAGE_CLASS = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}

# Per-(user, bot) graphs. Keyed by (user_id, bot_id) -> (selection_key,
# compiled_graph), where bot_id is None for "no bot" sessions (so this
# generalizes the old user_id-only cache without changing its shape for
# users who never touch bots -- see _get_graph_for_session below).
# selection_key changes whenever MCP selection / Gmail / Calendar
# connection status changes, so a request cheaply detects "nothing
# changed, reuse the cached graph" without re-fetching anything.
# In-memory, single-process only -- won't share across multiple
# uvicorn/gunicorn workers; needs a shared cache before scaling out.
_user_agent_graphs: dict[tuple[str, str | None], tuple[tuple, object]] = {}


async def _get_graph_for_session(user_id: str, bot_id: str | None):
    """Builds (or reuses a cached) graph for this user's chat turn.

    Phase 2 of README.md's multi-bot plan: if bot_id is given, the
    graph's knowledge_base_search tool is bound to THAT bot's own KB
    (rag.bot_kb_name/get_kb_store) instead of the shared global default
    store -- so a bot_id always needs its own compiled graph, it can
    never just reuse the shared `agent_graph`, even if that bot has no
    MCP/Gmail/Calendar tools selected. A session with no bot_id and no
    MCP/Gmail/Calendar selections still gets the exact same shared
    `agent_graph` as before this change -- nothing here alters that
    path (README.md principle: bots shouldn't change behavior for users
    who never create one).

    Phase 3/4 of README.md's multi-bot plan: MCP tool selections
    (mcp_tools.get_all_selections) and Gmail/Calendar connection status
    are now fetched SCOPED TO THIS bot_id, not just this user --
    passing `bot_id` straight through to mcp_tools.get_all_selections,
    gmail_oauth.has_valid_connection, and calendar_oauth.has_valid_connection
    (all bot_id=None for a bot-less session, which reads exactly the
    same "no bot" rows that existed pre-Phase-3/4). This is what makes
    the isolation real: a tool authorized+selected for bot A's own
    McpConnection/GmailConnection/CalendarConnection row never appears
    in bot B's tool list, because bot B's graph only ever queries rows
    filtered to bot_id=B (see each module's docstring for the row
    scoping).

    Gmail tools (list_gmail_messages / get_gmail_message /
    send_gmail_message) and Calendar/Meet tools (list_calendar_events /
    get_calendar_event / create_calendar_event / delete_calendar_event /
    create_meet_event / get_meet_link) are selected the same way as any
    other MCP tool -- via /mcp/servers/{server_id}/tools/select (or,
    for a bot, /bots/{bot_id}/mcp/servers/{server_id}/tools/select) on
    the auto-provisioned Horizon server (see
    mcp_tools.ensure_horizon_server, which is NOT bot-scoped -- the
    Horizon *server* row is shared, only the connection/selection is
    per-bot), and they show up in that server's tool list because
    they're just regular tools on the MCP server. What's special about
    them is auth: they need a fresh per-(user, bot) Google token
    attached on every call (tools/gmail_tool.py, tools/calendar_tool.py),
    so a session with any gmail_*/calendar_*/meet_* tool selected
    always gets a freshly-built graph below instead of reusing
    _user_agent_graphs -- avoids ever calling Google APIs with a token
    that went stale while the graph sat cached across chat turns.
    Costs a rebuild per chat message for those sessions; fine at this
    app's scale, revisit if that becomes a bottleneck.

    Other (non-Gmail/Calendar) MCP tools come from however many
    servers this (user, bot) pair has added and selected tools on
    (mcp_tools.get_all_selections(user_id, bot_id)) -- not just one
    hardcoded server, and not the user's other bots'/bot-less
    selections either.
    """
    selections = await mcp_tools.get_all_selections(user_id, bot_id=bot_id)

    all_selected_names: set[str] = set()
    for sel in selections:
        all_selected_names.update(sel["tool_names"])

    # Fully unchanged fast path: no bot, no MCP anything selected --
    # exactly what every user got before bots existed.
    if not bot_id and not selections:
        return agent_graph

    selected_gmail_names = GMAIL_TOOL_NAMES & all_selected_names
    selected_calendar_names = CALENDAR_AND_MEET_TOOL_NAMES & all_selected_names
    has_other_names = bool(
        all_selected_names - GMAIL_TOOL_NAMES - CALENDAR_AND_MEET_TOOL_NAMES
    )

    gmail_connected = False
    if selected_gmail_names:
        gmail_connected = await asyncio.to_thread(
            gmail_oauth.has_valid_connection, user_id, bot_id
        )

    calendar_connected = False
    if selected_calendar_names:
        calendar_connected = await asyncio.to_thread(
            calendar_oauth.has_valid_connection, user_id, bot_id
        )

    skip_cache = bool(selected_gmail_names) or bool(selected_calendar_names)

    cache_id = (user_id, bot_id)
    key = (frozenset(all_selected_names), gmail_connected, calendar_connected)
    if not skip_cache:
        cached = _user_agent_graphs.get(cache_id)
        if cached and cached[0] == key:
            return cached[1]

    # Base state for this graph: the bot's own KB store if a bot is
    # selected (lazily loaded/cached in `state["stores"]` itself, see
    # rag.get_kb_store), otherwise fall through to the shared global
    # default store already in `state`. Everything else (embedder/llm/
    # graph/graph_transformer) stays shared across every bot -- only
    # the KB differs per bot at this phase.
    bot_state = dict(state)
    if bot_id:
        bot_state["store"] = await asyncio.to_thread(get_kb_store, state, bot_kb_name(bot_id))

    extra_tools = []

    # Regular (non-Gmail/Calendar/Meet) tools, one server at a time --
    # a user may have several servers each contributing tools.
    for sel in selections:
        try:
            extra_tools.extend(
                await mcp_tools.get_selected_tools(
                    user_id,
                    sel["server_id"],
                    sel["server_url"],
                    bot_id=bot_id,
                    exclude_names=GMAIL_TOOL_NAMES | CALENDAR_AND_MEET_TOOL_NAMES,
                )
            )
        except mcp_tools.ReauthorizationRequired:
            log.warning(
                "chat_route: MCP authorization expired for user_id=%s bot_id=%s server_id=%s, "
                "continuing without that server's tools",
                user_id, bot_id, sel["server_id"],
            )
        except Exception:
            log.exception(
                "chat_route: MCP tool fetch raised for user_id=%s bot_id=%s server_id=%s, "
                "continuing without them",
                user_id, bot_id, sel["server_id"],
            )

    if selected_gmail_names:
        if not gmail_connected:
            log.info(
                "chat_route: user_id=%s bot_id=%s selected gmail tool(s) %s but Gmail isn't "
                "connected for this bot -- skipping, tools will still no-op gracefully if "
                "called anyway",
                user_id, bot_id, selected_gmail_names,
            )
        try:
            extra_tools.extend(
                await build_gmail_tools(user_id, selected_gmail_names, bot_id=bot_id)
            )
        except Exception:
            log.exception(
                "chat_route: Gmail tool build raised for user_id=%s bot_id=%s, continuing "
                "without them",
                user_id, bot_id,
            )

    if selected_calendar_names:
        if not calendar_connected:
            log.info(
                "chat_route: user_id=%s bot_id=%s selected calendar/meet tool(s) %s but "
                "Calendar isn't connected for this bot -- skipping, tools will still no-op "
                "gracefully if called anyway",
                user_id, bot_id, selected_calendar_names,
            )
        try:
            extra_tools.extend(
                await build_calendar_tools(user_id, selected_calendar_names, bot_id=bot_id)
            )
        except Exception:
            log.exception(
                "chat_route: Calendar/Meet tool build raised for user_id=%s bot_id=%s, "
                "continuing without them",
                user_id, bot_id,
            )

    # A bot_id means this MUST be its own compiled graph (its own KB
    # tool), even with zero extra_tools -- only bot-less, MCP-less
    # sessions get to fall back to the shared agent_graph.
    if not extra_tools and not bot_id:
        return agent_graph

    if extra_tools:
        # "mcp_tools" is a generic per-user extra-tools bucket, not
        # MCP-specific -- tools_registry.py just appends whatever's
        # here regardless of source (any MCP server, Gmail,
        # Calendar/Meet, future additions).
        bot_state["mcp_tools"] = extra_tools

    compiled = build_graph(bot_state)
    if not skip_cache:
        _user_agent_graphs[cache_id] = (key, compiled)
    log.info(
        "chat_route: built graph for user_id=%s bot_id=%s (mcp=%s, gmail=%s, calendar=%s)",
        user_id, bot_id, has_other_names,
        bool(selected_gmail_names) and gmail_connected,
        bool(selected_calendar_names) and calendar_connected,
    )
    return compiled


@app.post("/ingest")
async def ingest_route(
    files: list[UploadFile] = File(...),
    bot_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Phase 2 of README.md's multi-bot plan: pass bot_id to ingest into
    that bot's own knowledge base (ownership-checked) instead of the
    shared global default KB. Omitting bot_id keeps ingesting into the
    global KB, unchanged from before bots existed -- the global KB is
    kept around as the "no bot selected" fallback (README §Phase 2).
    """
    kb_name = None
    if bot_id is not None:
        _get_owned_bot(bot_id, db, current_user)  # 404s if not owned
        kb_name = bot_kb_name(bot_id)

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_paths = []
    for f in files:
        dest = tmp_dir / f.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        tmp_paths.append(str(dest))

    try:
        result = ingest(state, tmp_paths, kb_name=kb_name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    resolved_store = get_kb_store(state, kb_name) if kb_name else state["store"]
    return {
        "status": "ok",
        "chunks_added": result["chunks_added"],
        "total_chunks": count(resolved_store),
        "graph": result["graph"],
    }


@app.post("/query")
def query_route(req: QueryRequest, current_user: User = Depends(get_current_user)):
    resolved_store = get_kb_store(state, req.kb_name) if req.kb_name else state["store"]
    if count(resolved_store) == 0:
        raise HTTPException(400, "No documents ingested yet. Call /ingest first.")
    return query(state, req.question, req.top_k, kb_name=req.kb_name)


@app.get("/health")
def health():
    return {"status": "ok", "documents_indexed": count(state["store"])}


@app.post("/chat", response_model=ChatResponse)
async def chat_route(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log.info("chat_route: user=%s session_id=%r message=%r", current_user.id, req.session_id, req.message)

    if req.session_id:
        session = _get_owned_session(req.session_id, db, current_user)
    else:
        bot_id = req.bot_id
        if bot_id is not None:
            _get_owned_bot(bot_id, db, current_user)  # 404s if not owned
        session = ChatSession(user_id=current_user.id, bot_id=bot_id)
        db.add(session)
        db.commit()
        db.refresh(session)

    bot_instructions = ""
    if session.bot_id:
        bot = db.query(Bot).filter(Bot.id == session.bot_id).first()
        if bot and bot.system_prompt:
            bot_instructions = bot.system_prompt

    prior_rows = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_TURN_LIMIT * 2)
        .all()
    )
    prior_rows.reverse()

    history = [
        _ROLE_TO_MESSAGE_CLASS.get(row.role, HumanMessage)(content=row.content)
        for row in prior_rows
    ]
    history.append(HumanMessage(content=req.message))

    graph_to_use = await _get_graph_for_session(current_user.id, session.bot_id)
    result = await graph_to_use.ainvoke({
        "messages": history,
        "user_id": current_user.id,
        "bot_id": session.bot_id,  # README.md Phase 5: scopes memory_recall_node's mem0 lookup
        "bot_instructions": bot_instructions,
    })
    answer = result["messages"][-1].content

    answer = await check_output(answer)

    db.add(Message(session_id=session.id, role="user", content=req.message))
    db.add(Message(session_id=session.id, role="assistant", content=answer))

    if session.title == "New Chat" and not prior_rows:
        session.title = req.message[:60]

    db.commit()

    if not result.get("blocked"):
        # README.md Phase 5: written into this session's bot's own
        # isolated memory bucket (or the "no bot" bucket, unchanged,
        # when session.bot_id is None) -- see memory_layer.memory_key.
        add_turn(req.message, answer, current_user.id, bot_id=session.bot_id)

    log.info("chat_route: session_id=%s answer=%r", session.id, answer)
    return ChatResponse(session_id=session.id, answer=answer)
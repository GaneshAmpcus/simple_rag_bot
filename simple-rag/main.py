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
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends
from sqlalchemy.orm import Session
from rag import build_pipeline_state, ingest, query
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
from routers.sessions import _get_owned_session
from security import get_current_user
from models import User, ChatSession, Message
from schemas import ChatRequest, ChatResponse, QueryRequest
import mcp_tools
import gmail_oauth
from tools.gmail_tool import GMAIL_TOOL_NAMES, build_gmail_tools
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
app.include_router(gmail_router.router)

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

# Per-user graphs, only for users with MCP tools selected and/or Gmail
# connected. Keyed by user_id -> (selection_key, compiled_graph), where
# selection_key changes whenever their MCP selection or Gmail
# connection status changes, so a request cheaply detects "nothing
# changed, reuse the cached graph" without re-fetching anything.
# In-memory, single-process only -- won't share across multiple
# uvicorn/gunicorn workers; needs a shared cache before scaling out.
_user_agent_graphs: dict[str, tuple[tuple, object]] = {}


async def _get_graph_for_user(user_id: str):
    """Builds (or reuses a cached) per-user agent graph.

    Gmail tools (list_gmail_messages / get_gmail_message /
    send_gmail_message) are selected the same way as any other MCP
    tool -- via /mcp/tools/select, and they show up in /mcp/tools
    because they're just regular tools on the MCP server. What's
    special about them is auth: they need a fresh per-user Google
    token attached on every call (tools/gmail_tool.py), so a user with
    any gmail_* tool selected always gets a freshly-built graph below
    instead of reusing _user_agent_graphs -- avoids ever calling Gmail
    with a token that went stale while the graph sat cached across
    chat turns. Costs a rebuild per chat message for those users;
    fine at this app's scale, revisit if that becomes a bottleneck.
    """
    selected_mcp_names = await mcp_tools.get_selected_tool_names(user_id)
    if not selected_mcp_names:
        return agent_graph

    selected_names = set(selected_mcp_names)
    selected_gmail_names = GMAIL_TOOL_NAMES & selected_names
    selected_other_names = selected_names - GMAIL_TOOL_NAMES

    gmail_connected = False
    if selected_gmail_names:
        gmail_connected = await asyncio.to_thread(gmail_oauth.has_valid_connection, user_id)

    skip_cache = bool(selected_gmail_names)

    key = (frozenset(selected_mcp_names), gmail_connected)
    if not skip_cache:
        cached = _user_agent_graphs.get(user_id)
        if cached and cached[0] == key:
            return cached[1]

    extra_tools = []

    if selected_other_names:
        try:
            extra_tools.extend(
                await mcp_tools.get_selected_tools(user_id, exclude_names=GMAIL_TOOL_NAMES)
            )
        except mcp_tools.ReauthorizationRequired:
            log.warning(
                "chat_route: MCP authorization expired for user_id=%s, continuing without tools",
                user_id,
            )
        except Exception:
            log.exception(
                "chat_route: MCP tool fetch raised for user_id=%s, continuing without them",
                user_id,
            )

    if selected_gmail_names:
        if not gmail_connected:
            log.info(
                "chat_route: user_id=%s selected gmail tool(s) %s but Gmail isn't connected -- "
                "skipping, tools will still no-op gracefully if called anyway",
                user_id, selected_gmail_names,
            )
        try:
            extra_tools.extend(await build_gmail_tools(user_id, selected_gmail_names))
        except Exception:
            log.exception(
                "chat_route: Gmail tool build raised for user_id=%s, continuing without them",
                user_id,
            )

    if not extra_tools:
        return agent_graph

    user_state = dict(state)
    # "mcp_tools" is a generic per-user extra-tools bucket now, not
    # MCP-specific -- tools_registry.py just appends whatever's here
    # regardless of source (MCP-selected, Gmail, future additions).
    user_state["mcp_tools"] = extra_tools
    compiled = build_graph(user_state)
    if not skip_cache:
        _user_agent_graphs[user_id] = (key, compiled)
    log.info(
        "chat_route: built per-user graph for user_id=%s (mcp=%s, gmail=%s)",
        user_id, bool(selected_other_names), bool(selected_gmail_names) and gmail_connected,
    )
    return compiled


@app.post("/ingest")
async def ingest_route(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_paths = []
    for f in files:
        dest = tmp_dir / f.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        tmp_paths.append(str(dest))

    try:
        result = ingest(state, tmp_paths)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "status": "ok",
        "chunks_added": result["chunks_added"],
        "total_chunks": count(state["store"]),
        "graph": result["graph"],
    }


@app.post("/query")
def query_route(req: QueryRequest, current_user: User = Depends(get_current_user)):
    if count(state["store"]) == 0:
        raise HTTPException(400, "No documents ingested yet. Call /ingest first.")
    return query(state, req.question, req.top_k)


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
        session = ChatSession(user_id=current_user.id)
        db.add(session)
        db.commit()
        db.refresh(session)

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

    graph_to_use = await _get_graph_for_user(current_user.id)
    result = await graph_to_use.ainvoke({"messages": history, "user_id": current_user.id})
    answer = result["messages"][-1].content

    answer = await check_output(answer)

    db.add(Message(session_id=session.id, role="user", content=req.message))
    db.add(Message(session_id=session.id, role="assistant", content=answer))

    if session.title == "New Chat" and not prior_rows:
        session.title = req.message[:60]

    db.commit()

    if not result.get("blocked"):
        add_turn(req.message, answer, current_user.id)

    log.info("chat_route: session_id=%s answer=%r", session.id, answer)
    return ChatResponse(session_id=session.id, answer=answer)
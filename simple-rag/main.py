"""
FastAPI backend for the LangChain-based RAG pipeline.

Run with:
    uvicorn main:app --reload

Then try (ingest is now a real file upload, e.g. via curl):
    curl -F "files=@report.pdf" -F "files=@notes.docx" http://localhost:8000/ingest
    POST /query   {"question": "what is ...?", "top_k": 3}
"""

import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends
from sqlalchemy.orm import Session
from rag import build_pipeline_state, ingest, query
from vector_store import count
from graph import build_graph
from guardrails_layer import check_output_sync
from memory_layer import add_turn
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from config.logging_config import get_logger

from config.database import Base, engine, get_db
from routers import auth as auth_router
from routers import sessions as sessions_router
from routers.sessions import _get_owned_session  # reusing the ownership-check helper
from security import get_current_user
from models import User, ChatSession, Message
from schemas import ChatRequest, ChatResponse, QueryRequest
from fastapi.middleware.cors import CORSMiddleware
log = get_logger(__name__)


load_dotenv()

# Create auth/session tables if they don't exist yet.
# (For anything beyond local dev, prefer Alembic migrations over this.)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Simple RAG API (LangChain)")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from any origin
    allow_credentials=False,
    allow_methods=["*"],  # Allow GET, POST, PUT, DELETE, OPTIONS, etc.
    allow_headers=["*"],  # Allow all request headers
)


app.include_router(auth_router.router)
app.include_router(sessions_router.router)

# Module-level dict instead of a class instance -- built once at import time.
state = build_pipeline_state()

agent_graph = build_graph(state)  # same state as /ingest and /query

# How many prior user/assistant exchanges to feed back in as context.
# Tune based on your model's context window and latency/cost budget --
# this is exchanges (user+assistant pairs), not raw message count.
HISTORY_TURN_LIMIT = 5

# Maps DB-stored role strings to the LangChain message classes the graph
# expects. "system" is included for completeness (e.g. if a session was
# ever seeded with a system row) even though nothing currently writes one.
_ROLE_TO_MESSAGE_CLASS = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
}


@app.post("/ingest")
async def ingest_route(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    # Uploaded files arrive as streams -- write each to a temp dir so the
    # LangChain loaders (which expect a file path) can read them.
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_paths = []
    for f in files:
        dest = tmp_dir / f.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        tmp_paths.append(str(dest))

    try:
        # ingest() now returns a dict ({"chunks_added", "graph"}), not
        # just a chunk count -- graph_store.py's knowledge-graph
        # ingestion runs on the same chunks right after the vector
        # store add.
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
    try:
        return query(state, req.question, req.top_k, req.kb_name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/health")
def health():
    return {"status": "ok", "documents_indexed": count(state["store"])}


@app.post("/chat", response_model=ChatResponse)
def chat_route(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log.info("chat_route: user=%s session_id=%r message=%r", current_user.id, req.session_id, req.message)

    # Load or create the session, same ownership check routers/sessions.py
    # already uses -- a session_id that doesn't belong to this user 404s
    # rather than silently attaching to it.
    if req.session_id:
        session = _get_owned_session(req.session_id, db, current_user)
    else:
        session = ChatSession(user_id=current_user.id)
        db.add(session)
        db.commit()
        db.refresh(session)

    # Explicit order_by rather than relying on session.messages -- the
    # relationship in models.py has no order_by, so its iteration order
    # isn't guaranteed to be chronological. Query newest-first with a
    # LIMIT (bounding the query itself, not just slicing after loading
    # everything), then reverse back to chronological order.
    prior_rows = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_TURN_LIMIT * 2)  # *2: each exchange is a user + assistant row
        .all()
    )
    prior_rows.reverse()

    history = [
        _ROLE_TO_MESSAGE_CLASS.get(row.role, HumanMessage)(content=row.content)
        for row in prior_rows
    ]
    history.append(HumanMessage(content=req.message))

    # graph.py/nodes.py need no changes for seeding history: pre_rails/
    # intent both locate the newest turn via the last HumanMessage in
    # the list. user_id IS new state, though -- memory_recall_node reads
    # it to scope mem0's search to this user.
    result = agent_graph.invoke({"messages": history, "user_id": current_user.id})
    answer = result["messages"][-1].content

    # Step 5 backstop: pre_rails/persona-override already caught most
    # attempts on the way in, but re-check the outgoing text in case
    # anything (e.g. an LLM improvising past its system prompt) slipped
    # through 1-4.
    answer = check_output_sync(answer)

    db.add(Message(session_id=session.id, role="user", content=req.message))
    db.add(Message(session_id=session.id, role="assistant", content=answer))

    # First exchange in a fresh session: give it a real title instead of
    # the "New Chat" default. Purely cosmetic, safe to skip if unwanted.
    if session.title == "New Chat" and not prior_rows:
        session.title = req.message[:60]

    db.commit()

    # Long-term memory write -- only for turns that weren't blocked by
    # pre_rails. A refused jailbreak attempt isn't a fact worth
    # remembering about the user. add_turn() fails open internally, so
    # a flaky mem0 call never breaks the response that's about to return.
    if not result.get("blocked"):
        add_turn(req.message, answer, current_user.id)

    log.info("chat_route: session_id=%s answer=%r", session.id, answer)
    return ChatResponse(session_id=session.id, answer=answer)
"""
Function-based RAG pipeline: embedder + vector store + LLM (+ optional
knowledge graph). Instead of a class holding self.embedder / self.store
/ self.llm, we pass around a plain `state` dict. build_pipeline_state()
creates it once at startup; ingest() and query() take it as their first
argument and mutate/read it.

query() returns question / answer / contexts in a shape that maps
directly onto RAGAS's expected columns (question, answer, contexts,
[ground_truth]).
"""

from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from embeddings import get_embedder
from llm import generate_answer, get_llm
from loaders import load_documents
from vector_store import INDEX_PATH, add_documents, count, load_or_create_store, search
from graph_store import build_graph_transformer, get_neo4j_graph, ingest_graph

# chunk_size/overlap are in characters, not words -- tune to taste.
splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=160)


def _kb_index_path(kb_name: str) -> Path:
    if kb_name == "knowledge_base":
        return INDEX_PATH
    return INDEX_PATH / kb_name


def bot_kb_name(bot_id: str) -> str:
    """The kb_name a given bot's knowledge base is stored/looked up
    under. Phase 2 of README.md's multi-bot plan: no separate KB table
    -- a bot's id IS its KB's key, one KB per bot for now (see the
    README's open-questions section for the 'multiple KBs per bot'
    deferral)."""
    return f"bot_{bot_id}"


def _get_kb_store(state: dict, kb_name: str) -> object | None:
    """Lazily loads a KB's store into state["stores"] on first use if
    it isn't already resident, instead of requiring every KB to be
    discovered/loaded eagerly at process startup. This is what makes
    per-bot KBs (Phase 2) safe to grow without bound -- a process only
    ever loads the bots it's actually asked to serve, not every bot
    that's ever been created. Returns None if no index exists on disk
    yet for this kb_name (a brand-new, never-ingested-into bot/KB)."""
    if kb_name not in state["stores"]:
        state["stores"][kb_name] = load_or_create_store(
            state["embedder"], index_path=_kb_index_path(kb_name)
        )
    return state["stores"][kb_name]


def get_kb_store(state: dict, kb_name: str) -> object | None:
    """Public accessor for _get_kb_store -- used by main.py to resolve
    a bot's own store (via bot_kb_name()) when building that bot's
    agent graph."""
    return _get_kb_store(state, kb_name)


def chunk_documents(documents: list) -> list:
    """Split loaded Document objects into overlapping chunks, preserving
    each chunk's source metadata (file path, page number, etc.)."""
    return splitter.split_documents(documents)


def build_pipeline_state() -> dict:
    """Create the shared state dict used by ingest()/query().

    Only the single global default KB ("knowledge_base") is loaded
    eagerly here -- everything else (per-bot KBs, Phase 2 of
    README.md's multi-bot plan) loads lazily on first use via
    _get_kb_store()/get_kb_store(), so this stays cheap regardless of
    how many bots/KBs exist. Previously this eagerly scanned and loaded
    every subdirectory under data/index/ at startup, which doesn't
    scale once KBs are created per-bot rather than by hand.

    "graph"/"graph_transformer" are None when NEO4J_* env vars aren't
    set -- ingest() checks for that and skips graph ingestion, so this
    stays a no-op addition until Neo4j is actually configured.
    """
    embedder = get_embedder()
    llm = get_llm(model='openai/gpt-oss-120b')
    default_kb = "knowledge_base"
    default_store = load_or_create_store(embedder, index_path=INDEX_PATH)
    stores = {default_kb: default_store}
    return {
        "embedder": embedder,
        "llm": llm,
        "store": default_store,
        "stores": stores,
        "default_kb": default_kb,
        "graph": get_neo4j_graph(),
        "graph_transformer": build_graph_transformer(llm),
    }


def ingest(state: dict, file_paths: list[str], kb_name: str | None = None) -> dict:
    """Returns a dict now, not just a chunk count -- callers (main.py's
    /ingest route) need updating for this shape change."""
    if kb_name is None:
        kb_name = state.get("default_kb", "knowledge_base")

    documents = load_documents(file_paths)
    chunks = chunk_documents(documents)
    if not chunks:
        return {
            "chunks_added": 0,
            "graph": {
                "success": True,
                "documents_received": 0,
                "documents_processed": 0,
                "graph_documents": 0,
                "nodes": 0,
                "relationships": 0,
            },
        }

    store = _get_kb_store(state, kb_name)
    index_path = _kb_index_path(kb_name)
    store = add_documents(store, state["embedder"], chunks, index_path=index_path)
    state["stores"][kb_name] = store
    if kb_name == state.get("default_kb"):
        state["store"] = store

    # Runs on the same chunks already added to the vector store. Kept
    # synchronous/inline for now to match the rest of this module's
    # style -- see the note in graph_store.py and the call site in
    # main.py about call-volume/latency if this becomes a bottleneck.
    graph_result = ingest_graph(state["graph"], state["graph_transformer"], chunks)

    return {"chunks_added": len(chunks), "graph": graph_result}


def query(state: dict, question: str, top_k: int = 3, kb_name: str | None = None) -> dict:
    if kb_name is None:
        kb_name = state.get("default_kb", "knowledge_base")

    store = _get_kb_store(state, kb_name)
    if store is None:
        raise ValueError(f"Knowledge base '{kb_name}' does not exist.")

    results = search(store, question, top_k=top_k)
    contexts = [r["text"] for r in results]

    answer = generate_answer(state["llm"], question, contexts)

    return {
        "question": question,
        "answer": answer,
        "contexts": contexts,
        "retrieved": results,
    }
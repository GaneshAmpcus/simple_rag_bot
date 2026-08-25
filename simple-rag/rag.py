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


def _discover_stores(embedder) -> dict:
    stores = {}
    stores["knowledge_base"] = load_or_create_store(embedder, index_path=INDEX_PATH)
    if INDEX_PATH.exists():
        for child in sorted(INDEX_PATH.iterdir()):
            if not child.is_dir():
                continue
            kb_name = child.name
            if kb_name == "knowledge_base":
                continue
            store = load_or_create_store(embedder, index_path=child)
            if store is not None:
                stores[kb_name] = store
    return stores


def _get_kb_store(state: dict, kb_name: str) -> object | None:
    return state["stores"].get(kb_name)


def chunk_documents(documents: list) -> list:
    """Split loaded Document objects into overlapping chunks, preserving
    each chunk's source metadata (file path, page number, etc.)."""
    return splitter.split_documents(documents)


def build_pipeline_state() -> dict:
    """Create the shared state dict used by ingest()/query().

    "graph"/"graph_transformer" are None when NEO4J_* env vars aren't
    set -- ingest() checks for that and skips graph ingestion, so this
    stays a no-op addition until Neo4j is actually configured.
    """
    embedder = get_embedder()
    llm = get_llm(model='openai/gpt-oss-120b')
    stores = _discover_stores(embedder)
    default_kb = "knowledge_base"
    if default_kb not in stores and stores:
        default_kb = next(iter(stores))
    return {
        "embedder": embedder,
        "llm": llm,
        "store": stores.get(default_kb),
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
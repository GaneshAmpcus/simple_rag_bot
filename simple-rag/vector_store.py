"""
Function-based wrapper around LangChain's FAISS vector store.

Persists to disk (FAISS.save_local / load_local) so you don't have to
re-embed everything on restart.

NOTE on scoring: LangChain's default FAISS index uses L2 distance, so
similarity_search_with_score returns LOWER = more similar (unlike the
raw faiss.IndexFlatIP + cosine setup in the old code, where higher was
more similar). If you rely on the score value anywhere downstream
(e.g. filtering by a threshold), you'll need to flip that logic.
"""

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

INDEX_PATH = Path("data/index")


def load_or_create_store(embedder: Embeddings, index_path: Path = INDEX_PATH) -> FAISS | None:
    """Load an existing FAISS index from disk, or return None if none exists yet."""
    if (index_path / "index.faiss").exists():
        return FAISS.load_local(
            str(index_path), embedder, allow_dangerous_deserialization=True
        )
    return None


def add_documents(store: FAISS | None, embedder: Embeddings, documents: list[Document], index_path: Path = INDEX_PATH) -> FAISS:
    """Add Document chunks to the store, creating it if it doesn't exist yet. Saves after."""
    if store is None:
        store = FAISS.from_documents(documents, embedder)
    else:
        store.add_documents(documents)

    index_path.mkdir(parents=True, exist_ok=True)
    store.save_local(str(index_path))
    return store


def search(store: FAISS, query: str, top_k: int = 3) -> list[dict]:
    """Return top_k {text, score} dicts, most similar first (lowest L2 distance)."""
    results = store.similarity_search_with_score(query, k=top_k)
    return [{"text": doc.page_content, "score": float(score)} for doc, score in results]


def count(store: FAISS | None) -> int:
    if store is None:
        return 0
    return store.index.ntotal
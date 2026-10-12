"""RagDiff adapter for simple-rag: ``run(question, config) -> {answer, contexts}``.

It reuses the app's own embedder, vector-store search, RAG prompt and LLM
factory, but builds the FAISS index in memory from ``data/sample_docs`` so that
retrieval settings (chunk size/overlap) can change per RagDiff variant without
touching the on-disk index. Nothing here writes to disk or to the database.

Config keys (all optional; defaults mirror rag.py in production):
    chunk_size, chunk_overlap, top_k   -> retrieval
    prompt                             -> "default" | "ungrounded"
    llm_model                          -> Groq model id passed to get_llm(), or
                                          "none" to skip the LLM and answer with the
                                          top context (retrieval-only tests, no API)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_community.vectorstores import FAISS
from langchain_core.prompts import PromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

from embeddings import get_embedder
from llm import RAG_PROMPT, generate_answer, get_llm
from loaders import LOADERS, load_documents
from vector_store import search

DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_docs"

DEFAULTS: dict[str, Any] = {
    "chunk_size": 800,
    "chunk_overlap": 160,
    "top_k": 3,
    "prompt": "default",
    "llm_model": "openai/gpt-oss-120b",
}

# "ungrounded" is a deliberately weaker prompt, useful to demo a prompt regression.
PROMPTS = {
    "default": RAG_PROMPT,
    "ungrounded": PromptTemplate.from_template(
        "Answer the question in one short sentence from your own knowledge.\n\n"
        "Context (may be irrelevant):\n{context}\n\nQuestion: {question}\nAnswer:"
    ),
}


@lru_cache(maxsize=None)
def _store(chunk_size: int, chunk_overlap: int) -> FAISS:
    paths = sorted(
        str(path) for path in DOCS_DIR.iterdir() if path.suffix.lower() in LOADERS
    )
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    chunks = splitter.split_documents(load_documents(paths))
    return FAISS.from_documents(chunks, get_embedder())


@lru_cache(maxsize=None)
def _llm(model: str) -> Any:
    return get_llm(model=model, role="rag")


def run(question: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = {**DEFAULTS, **(config or {})}
    store = _store(int(settings["chunk_size"]), int(settings["chunk_overlap"]))
    results = search(store, question, top_k=int(settings["top_k"]))
    contexts = [item["text"] for item in results]
    if settings["llm_model"] == "none":
        return {"answer": contexts[0] if contexts else "", "contexts": contexts}
    llm = _llm(str(settings["llm_model"]))

    if settings["prompt"] == "default":
        answer = generate_answer(llm, question, contexts)
    else:
        block = "\n\n".join(f"[{i + 1}] {text}" for i, text in enumerate(contexts))
        prompt = PROMPTS[str(settings["prompt"])].format(
            context=block, question=question
        )
        answer = llm.invoke(prompt).content
    return {"answer": answer, "contexts": contexts}

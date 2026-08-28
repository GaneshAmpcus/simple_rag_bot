"""
Long-term, per-user memory via mem0 (self-hosted / open source), reusing
the same Groq LLM and HuggingFace embedder already configured elsewhere
in this app (llm.py / embeddings.py) rather than pulling in a second
model stack just for memory.

Two operations, wired in from different places:
- recall (search) -- called from nodes.py's memory_recall_node, once
                      per turn, before intent/agent/chat run.
- write (add)      -- called from main.py, once per non-blocked turn,
                      after the final (guardrails-checked) answer.

Uses its own FAISS index, separate from vector_store.py's document
index (data/index) -- memory facts and RAG documents are different
collections and must never share a path.

Known gotcha to test for early: there's an open mem0 issue with the
Groq + HuggingFace-embedder combination occasionally returning
"Invalid JSON response" during fact extraction. add_turn() below fails
open (logs and swallows) specifically because of this -- a flaky
memory write shouldn't take down a chat response.
"""

import os
from pathlib import Path
from groq import APIStatusError
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from mem0 import Memory

from config.logging_config import get_logger

log = get_logger(__name__)

if not os.environ.get("GROQ_MEMORY_API_KEY"):
    raise ValueError(
        "GROQ_MEMORY_API_KEY not set. Copy .env.example to .env and add your key "
        "-- memory_layer.py needs it to build the mem0 LLM client at "
        "import time."
    )

_MEM0_INDEX_PATH = Path("data/mem0_index")
_MEM0_INDEX_PATH.mkdir(parents=True, exist_ok=True)

# Same model llm.py's get_llm() uses by default. Kept as a plain literal
# here rather than imported -- mem0 wants a model name string in its own
# config dict, not a ChatGroq instance; it builds its own client.
_LLM_MODEL = "qwen/qwen3.6-27b"

# Must match embeddings.py's EMBEDDER model exactly -- a dimension
# mismatch between mem0's FAISS index and the embedder breaks it
# outright, not silently.
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBED_DIMS = 384

_config = {
    "llm": {
        "provider": "groq",
        "config": {
            "model": _LLM_MODEL,
            "api_key": os.environ["GROQ_MEMORY_API_KEY"],
            "temperature": 0.1,  # fact extraction, not creative generation
        },
    },
    "embedder": {
        "provider": "fastembed",
        "config": {
            "model": "BAAI/bge-small-en-v1.5",
            "embedding_dims": 384,
        },
    },
    "vector_store": {
        "provider": "faiss",
        "config": {
            "collection_name": "user_memories",
            "path": str(_MEM0_INDEX_PATH),
            "embedding_model_dims": _EMBED_DIMS,
        },
    },
    
    "history_db": {
        "provider": "postgres",
        "config": {
        "host": "localhost",
        "port": 5432,
        "database": "memory_db",
        "user": "postgres",
        "password": "admin@123"
        }
    }

}

memory = Memory.from_config(_config)


def memory_key(user_id: str, bot_id: str | None = None) -> str:
    """README.md Phase 5: mem0 has no schema to migrate (its `user_id`
    filter is just an opaque string), so per-bot scoping is done by
    composing that string instead of adding a column, mirroring
    rag.py's bot_kb_name(). bot_id=None (the default) returns
    `user_id` UNCHANGED -- this is the "no bot" fallback bucket, the
    same pattern as McpConnection/GmailConnection/CalendarConnection's
    bot_id=NULL rows: every memory written before Phase 5, and every
    bot-less session's memory, lives under this exact key, untouched
    by this change (README principle #5). bot_id="<id>" gets its own
    isolated key, f"{user_id}:{bot_id}", so two different bots for the
    same user never recall each other's memories -- README's Phase 5
    product decision (per-bot scoping, confirmed 2026-08-28)."""
    return f"{user_id}:{bot_id}" if bot_id else user_id


def add_turn(
    user_message: str, assistant_message: str, user_id: str, bot_id: str | None = None
) -> None:
    """Write side: call once per non-blocked turn, after the final
    (guardrails-checked) answer. Failures are logged and swallowed --
    losing a memory write should never fail the user's chat response.

    bot_id (README.md Phase 5): which bot's isolated memory bucket to
    write into -- see memory_key(). None writes to the same "no bot"
    bucket every turn used before Phase 5."""
    key = memory_key(user_id, bot_id)
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_message},
    ]
    try:
        memory.add(messages=messages, user_id=key)

    except Exception as e:
        cause = e.__cause__
        if isinstance(cause, APIStatusError) and cause.status_code == 413:
            log.warning("mem0 add: token limit hit, storing without inference")
            try:
                # NOTE: previously called with a placeholder `messages=[...]`
                # (a literal Ellipsis-in-a-list, not real content) -- fixed
                # to reuse the same turn's messages while dropping fact
                # inference, matching what the comment always intended.
                memory.add(messages=messages, user_id=key, infer=False)
            except Exception:
                log.exception("mem0 fallback add also failed for user_id=%s bot_id=%s", user_id, bot_id)
        else:
            log.exception("memory_layer.add_turn: mem0 add failed for user_id=%s bot_id=%s", user_id, bot_id)
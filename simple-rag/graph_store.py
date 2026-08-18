"""
Function-based knowledge-graph ingestion: converts LangChain Documents
into a Neo4j graph using an LLM extractor. Same pattern as
vector_store.py -- plain functions taking explicit args, no class/self
state, so it plugs into rag.py's state dict the same way the FAISS
store already does.

Optional by design: if NEO4J_* env vars aren't set, get_neo4j_graph()
returns None and ingest_graph() becomes a no-op. The existing
FAISS-based RAG pipeline has no hard dependency on this module.

Deliberate behavior change from the original class: a failed batch
(extraction error, Neo4j write error) is logged and skipped rather than
raised as a RuntimeError. rag.py's ingest() already committed these
chunks to the vector store by the time graph ingestion runs -- a
partial graph-extraction failure shouldn't roll back or fail a request
that otherwise succeeded.
"""

import os

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph

from config.logging_config import get_logger

log = get_logger(__name__)

DEFAULT_BATCH_SIZE = 10

ALLOWED_NODES = ["Person", "Organization", "Company", "Product", "Location", "Technology"]
ALLOWED_RELATIONSHIPS = [
    "WORKS_AT", "FOUNDED", "LOCATED_IN", "OWNS", "USES", "PRODUCES", "PART_OF", "RELATED_TO",
]
NODE_PROPERTIES = ["name", "description"]


def get_neo4j_graph() -> Neo4jGraph | None:
    """Build the Neo4j connection, or None if not configured -- lets
    the rest of the app treat the knowledge graph as optional."""
    uri = os.environ.get("NEO4J_URI")
    username = os.environ.get("NEO4J_USERNAME")
    password = os.environ.get("NEO4J_PASSWORD")


    if not (uri and username and password):
        log.info("get_neo4j_graph: NEO4J_* env vars not set, graph ingestion disabled")
        return None

    return Neo4jGraph(url=uri, username=username, password=password)


def build_graph_transformer(llm) -> LLMGraphTransformer:
    """Reuses whichever LLM the rest of the app already has configured
    (state["llm"] in rag.py) rather than instantiating a second one --
    swap this to a dedicated LLM instance later if graph extraction
    needs its own Groq key/rate-limit bucket (see the volume note
    where this is called from)."""
    return LLMGraphTransformer(
        llm=llm,
        allowed_nodes=ALLOWED_NODES,
        allowed_relationships=ALLOWED_RELATIONSHIPS,
        node_properties=NODE_PROPERTIES,
    )


def _validate_documents(documents: list[Document]) -> None:
    for index, document in enumerate(documents):
        if not isinstance(document, Document):
            raise ValueError(f"Item at index {index} is not a LangChain Document")
        if not document.page_content.strip():
            raise ValueError(f"Document at index {index} has empty content")


def _batches(documents: list[Document], batch_size: int):
    for start in range(0, len(documents), batch_size):
        yield documents[start : start + batch_size]


def ingest_graph(
    graph: Neo4jGraph | None,
    transformer: LLMGraphTransformer | None,
    documents: list[Document],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Convert documents into graph documents and persist them in Neo4j.
    Mirrors rag.py's ingest()/query() shape: plain args in, plain dict
    out, no class/self state."""
    empty_result = {
        "success": False,
        "documents_received": len(documents),
        "documents_processed": 0,
        "graph_documents": 0,
        "nodes": 0,
        "relationships": 0,
    }

    if graph is None or transformer is None:
        log.info("ingest_graph: graph store not configured, skipping")
        return empty_result

    documents = list(documents)
    if not documents:
        return {**empty_result, "success": True}

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")

    _validate_documents(documents)

    log.info("ingest_graph: starting documents=%d batch_size=%d", len(documents), batch_size)

    total_graph_documents = 0
    total_nodes = 0
    total_relationships = 0
    documents_processed = 0

    for batch_number, batch in enumerate(_batches(documents, batch_size), start=1):
        log.info("ingest_graph: batch=%d documents=%d", batch_number, len(batch))

        try:
            graph_documents = transformer.convert_to_graph_documents(batch)
        except Exception:
            # Same kind of transient generation hiccup seen with
            # Groq/Llama tool-calling elsewhere in this app (extraction
            # is implemented via tool/function calling under the hood).
            # Log and move on rather than failing the whole ingestion.
            log.exception("ingest_graph: extraction failed for batch=%d, skipping", batch_number)
            documents_processed += len(batch)
            continue

        if not graph_documents:
            log.warning("ingest_graph: no graph documents generated for batch=%d", batch_number)
            documents_processed += len(batch)
            continue

        try:
            graph.add_graph_documents(graph_documents, baseEntityLabel=True, include_source=True)
        except Exception:
            log.exception("ingest_graph: Neo4j persistence failed for batch=%d, skipping", batch_number)
            documents_processed += len(batch)
            continue

        batch_nodes = sum(len(gd.nodes) for gd in graph_documents)
        batch_relationships = sum(len(gd.relationships) for gd in graph_documents)

        total_graph_documents += len(graph_documents)
        total_nodes += batch_nodes
        total_relationships += batch_relationships
        documents_processed += len(batch)

        log.info(
            "ingest_graph: completed batch=%d documents=%d nodes=%d relationships=%d",
            batch_number, len(batch), batch_nodes, batch_relationships,
        )

    result = {
        "success": True,
        "documents_received": len(documents),
        "documents_processed": documents_processed,
        "graph_documents": total_graph_documents,
        "nodes": total_nodes,
        "relationships": total_relationships,
    }
    log.info("ingest_graph: completed %s", result)
    return result
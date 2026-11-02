"""Ingest policy documents into a persistent local Qdrant collection.

Qdrant is a vector database: it stores the chunk vectors and finds the nearest ones fast.

Where it sits in the flow: this is the offline step that runs BEFORE any question is asked.
It reads the policy files, chunks them, embeds the chunks and saves them. The retriever
(app/rag/retriever.py) then searches what this file stored.

Run: uv run python -m app.rag.ingest
Only documents whose content changed are re-chunked and re-embedded.
"""

import hashlib
from pathlib import Path

from langchain_qdrant import QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, VectorParams

from app import config
from app.observability.logging import log_event
from app.rag.chunking import STRATEGIES, load_policy, point_id
from app.rag.embeddings import make_embeddings, model_name

# Local Qdrant allows only one client per folder, so clients are shared per path.
_clients: dict[str, QdrantClient] = {}


def get_client(path=None) -> QdrantClient:
    """Return the shared Qdrant client for a folder, creating it on first use.

    Args:
        path: Folder holding the Qdrant data. Defaults to the QDRANT_DIR setting.

    Returns:
        A QdrantClient (the same object on every call for the same folder).
    """
    key = str(path or config.QDRANT_DIR)
    if key not in _clients:
        _clients[key] = QdrantClient(path=key)
    return _clients[key]


def collection_name(strategy: str, embedding_model: str) -> str:
    """Build the Qdrant collection name for a chunking strategy and embedding model.

    Each combination gets its own collection, because vectors from different models (or
    chunkings) cannot be compared.

    Args:
        strategy: Chunking strategy name, for example "section".
        embedding_model: Embedding model name.

    Returns:
        A name like "policies_section_text_embedding_3_small".
    """
    # Replace anything that is not a letter or digit so the name is always safe to use.
    safe = "".join(c if c.isalnum() else "_" for c in embedding_model)
    return f"policies_{strategy}_{safe}"


def get_vectorstore(embeddings, strategy: str | None = None, qdrant_dir=None, create=True):
    """Open the vector store for a strategy, creating the collection if needed.

    Args:
        embeddings: The embeddings object used to embed texts and queries.
        strategy: Chunking strategy. Defaults to the CHUNK_STRATEGY setting.
        qdrant_dir: Folder holding the Qdrant data. Defaults to the QDRANT_DIR setting.
        create: If False, never create the collection (the store is None when it is missing).

    Returns:
        A tuple (vectorstore, client, collection_name).
    """
    client = get_client(qdrant_dir)
    name = collection_name(strategy or config.CHUNK_STRATEGY, model_name(embeddings))
    if create and not client.collection_exists(name):
        # Embed a dummy text just to learn the vector length this model produces.
        size = len(embeddings.embed_query("dimension probe"))
        # COSINE compares the direction of vectors (1 = same meaning), ignoring their length.
        client.create_collection(
            name, vectors_config=VectorParams(size=size, distance=Distance.COSINE)
        )
    store = None
    if client.collection_exists(name):
        store = QdrantVectorStore(
            client=client,
            collection_name=name,
            embedding=embeddings,
            retrieval_mode=RetrievalMode.DENSE,
        )
    return store, client, name


def _doc_filter(doc_id: str) -> Filter:
    """Build a Qdrant filter that matches only the chunks of one policy.

    Args:
        doc_id: Policy id, for example "POL-01".

    Returns:
        A Qdrant Filter on the "metadata.doc_id" field.
    """
    return Filter(must=[FieldCondition(key="metadata.doc_id", match=MatchValue(value=doc_id))])


def _scroll_all(client, name, flt=None):
    """Read every stored point (optionally filtered), page by page.

    Args:
        client: The Qdrant client.
        name: Collection name.
        flt: Optional Qdrant filter to limit the points.

    Returns:
        A list of all matching points, with their payloads.
    """
    points, offset = [], None
    while True:
        # Qdrant returns one page at a time; limit=256 is just a reasonable page size.
        batch, offset = client.scroll(
            name, scroll_filter=flt, limit=256, offset=offset, with_payload=True
        )
        points.extend(batch)
        # No next-page offset means we have read everything.
        if offset is None:
            return points


def ingest(
    policy_dir: Path | None = None,
    strategy: str | None = None,
    embeddings=None,
    qdrant_dir=None,
) -> dict:
    """Chunk, embed and store all policy files, skipping files that have not changed.

    Args:
        policy_dir: Folder with POL-*.md files. Defaults to data/policies.
        strategy: Chunking strategy. Defaults to the CHUNK_STRATEGY setting.
        embeddings: Embeddings object. Defaults to the cached embeddings from config.
        qdrant_dir: Folder holding the Qdrant data. Defaults to the QDRANT_DIR setting.

    Returns:
        Stats: docs_embedded, docs_skipped, chunks_total.
    """
    policy_dir = policy_dir or config.DATA_DIR / "policies"
    strategy = strategy or config.CHUNK_STRATEGY
    embeddings = embeddings or make_embeddings()
    store, client, name = get_vectorstore(embeddings, strategy, qdrant_dir)
    chunker = STRATEGIES[strategy]
    stats = {"docs_embedded": 0, "docs_skipped": 0}

    # --- Step 1: for each policy file, re-embed only if its content changed ---
    seen_docs = set()
    for path in sorted(policy_dir.glob("POL-*.md")):
        doc_id, title, text = load_policy(path)
        seen_docs.add(doc_id)
        # The hash of the text is the "has it changed?" fingerprint stored on every chunk.
        doc_hash = hashlib.sha256(text.encode()).hexdigest()
        existing = _scroll_all(client, name, _doc_filter(doc_id))
        if existing and all(p.payload["metadata"]["doc_hash"] == doc_hash for p in existing):
            stats["docs_skipped"] += 1
            continue
        # Changed file: delete its old chunks first so none are left behind.
        if existing:
            client.delete(name, points_selector=[p.id for p in existing])
        docs = chunker(doc_id, title, text, doc_hash)
        store.add_documents(docs, ids=[point_id(d.metadata["chunk_id"]) for d in docs])
        stats["docs_embedded"] += 1

    # --- Step 2: remove chunks of documents that no longer exist ---
    stale = [
        p.id for p in _scroll_all(client, name) if p.payload["metadata"]["doc_id"] not in seen_docs
    ]
    if stale:
        client.delete(name, points_selector=stale)

    stats["chunks_total"] = client.count(name).count
    log_event("ingest_done", status=str(stats))
    return stats


if __name__ == "__main__":
    print(ingest())

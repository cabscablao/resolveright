"""Top-k retrieval over the policy collection (Qdrant, dense, cosine).

Retrieval means: given the user's question, find the k policy chunks most likely to contain
the answer. "Dense" search compares embeddings (meaning); the other modes add keyword search
or question rewriting.

Where it sits in the flow: after the guardrails and caches let a question through, the RAG
chain calls this to fetch context. The LLM then answers using only those chunks.
"""

import re

from langchain_core.documents import Document

from app import config
from app.rag.embeddings import make_embeddings
from app.rag.ingest import _scroll_all, get_vectorstore

# The retrieval modes the Retriever accepts (see the class docstring).
MODES = ("dense", "hybrid", "multiquery")


def _as_chunk(doc, score: float) -> dict:
    """Convert a LangChain Document into the plain dict the rest of the app uses.

    Args:
        doc: The retrieved Document.
        score: Its relevance score.

    Returns:
        A dict with chunk_id, doc_id, title, section, text and score.
    """
    return {
        "chunk_id": doc.metadata["chunk_id"],
        "doc_id": doc.metadata["doc_id"],
        "title": doc.metadata["title"],
        "section": doc.metadata["section"],
        "text": doc.page_content,
        "score": round(float(score), 4),
    }


class Retriever:
    """Dense search by default; `mode` adds the course's advanced retrievers (Session 5).

    - dense: cosine similarity in Qdrant. Score = cosine similarity (1 = identical).
    - hybrid: BM25 + dense merged by reciprocal rank fusion (EnsembleRetriever).
      BM25 is a keyword-matching formula. Reciprocal rank fusion (RRF) merges two ranked
      lists by giving each item a score based on its position in each list.
    - multiquery: the LLM rewrites the question, each rewrite is searched, results are merged
      (MultiQueryRetriever).
    For hybrid and multiquery the score is rank based (1 / rank), because the merged lists
    have no common similarity scale.
    """

    def __init__(
        self,
        embeddings=None,
        strategy: str | None = None,
        qdrant_dir=None,
        mode: str | None = None,
        llm=None,
    ):
        """Open the vector store and choose the retrieval mode.

        Args:
            embeddings: Embeddings object. Defaults to the cached embeddings from config.
            strategy: Chunking strategy whose collection to search.
            qdrant_dir: Folder holding the Qdrant data.
            mode: "dense", "hybrid" or "multiquery". Defaults to the RETRIEVAL_MODE setting.
            llm: Chat model used by multiquery (tests pass a fake one).

        Raises:
            ValueError: If the mode is not one of MODES.
        """
        self.mode = mode or config.RETRIEVAL_MODE
        if self.mode not in MODES:
            raise ValueError(f"unknown retrieval mode {self.mode!r}; use one of {MODES}")
        self.embeddings = embeddings or make_embeddings()
        # create=False: searching must never silently create an empty collection.
        self.store, self.client, self.collection = get_vectorstore(
            self.embeddings, strategy, qdrant_dir, create=False
        )
        self._llm = llm  # chat model for multiquery; built on first use
        self._bm25 = None  # BM25 index, built on first hybrid search

    def _check(self):
        """Fail early with a helpful message if nothing has been ingested yet.

        Raises:
            RuntimeError: If the collection is missing or empty.
        """
        if self.store is None or self.client.count(self.collection).count == 0:
            raise RuntimeError("Vector store is empty. Run: uv run python -m app.rag.ingest")

    def _bm25_retriever(self, k: int):
        """Return a keyword (BM25) retriever over all stored chunks.

        Args:
            k: How many results it should return.

        Returns:
            A BM25Retriever, built once and then reused.
        """
        # Imported here so the extra package is only needed when hybrid mode is used.
        from langchain_community.retrievers import BM25Retriever

        if self._bm25 is None:
            # BM25 works in memory, so load every chunk out of Qdrant once.
            points = _scroll_all(self.client, self.collection)
            docs = [
                Document(page_content=p.payload["page_content"], metadata=p.payload["metadata"])
                for p in points
            ]
            self._bm25 = BM25Retriever.from_documents(docs)
        self._bm25.k = k
        return self._bm25

    def _query_llm(self):
        """Return the chat model used to rewrite questions, building it on first use.

        Returns:
            A ChatOpenAI model.
        """
        if self._llm is None:
            from langchain_openai import ChatOpenAI

            kwargs = {"model": config.LLM_MODEL}
            # Reasoning models (gpt-5, o1, o3...) reject `temperature`; they take
            # `reasoning_effort` instead. Other models get temperature 0 (least random).
            if re.match(r"^(gpt-5|o\d)", config.LLM_MODEL):
                kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT
            else:
                kwargs["temperature"] = 0
            self._llm = ChatOpenAI(**kwargs)
        return self._llm

    def retrieve(self, query: str, k: int = config.TOP_K) -> list[dict]:
        """Find the chunks that best match a question.

        Args:
            query: The user's question.
            k: How many chunks to return.

        Returns:
            Up to k chunk dicts (see _as_chunk), best first.

        Raises:
            RuntimeError: If the vector store is empty.
        """
        self._check()
        # --- Mode 1: plain dense search, returns real cosine scores ---
        if self.mode == "dense":
            results = self.store.similarity_search_with_score(query, k=k)
            return [_as_chunk(doc, score) for doc, score in results]
        # --- Modes 2 and 3: build a merging retriever ---
        dense = self.store.as_retriever(search_kwargs={"k": k})
        if self.mode == "hybrid":
            from langchain.retrievers import EnsembleRetriever

            # Equal weights: keyword and meaning search count the same.
            merged = EnsembleRetriever(
                retrievers=[self._bm25_retriever(k), dense], weights=[0.5, 0.5]
            )
        else:
            from langchain.retrievers.multi_query import MultiQueryRetriever

            merged = MultiQueryRetriever.from_llm(retriever=dense, llm=self._query_llm())
        docs = merged.invoke(query)[:k]
        # Merged lists have no shared similarity scale, so score by rank: 1, 1/2, 1/3...
        return [_as_chunk(doc, 1 / (rank + 1)) for rank, doc in enumerate(docs)]

    def as_langchain_retriever(self, k: int = config.TOP_K):
        """For LCEL chains and the advanced retrievers (ensemble, multi-query) later.

        Args:
            k: How many chunks the retriever returns.

        Returns:
            A plain dense LangChain retriever over the same store.
        """
        return self.store.as_retriever(search_kwargs={"k": k})

"""Tests for RAG (retrieval): chunking, embeddings cache, ingest, retrieval and the search tool.

RAG means: cut the policies into chunks, embed them, store them, and fetch the best chunks
for a question. All tests run offline with fake hash embeddings (no API call), which are only
a stand-in for the real embedding model.
"""

import shutil

import pytest

from app import config
from app.rag.chunking import baseline_chunks, load_policy, point_id, section_chunks
from app.rag.embeddings import HashEmbeddings, make_embeddings
from app.rag.ingest import ingest
from app.rag.retriever import Retriever
from app.tools import policy_search
from app.tools.policy_search import search_policy

POLICY_DIR = config.DATA_DIR / "policies"


class CountingEmbeddings(HashEmbeddings):
    """Fake embeddings that count how many texts reach the "API" (not served from the cache).

    The count lets tests prove that the embedding cache really avoids repeat work.
    """

    model_name = "counting-hash"

    def __init__(self):
        """Start with zero calls."""
        self.calls = 0

    def embed_documents(self, texts):
        """Count every text, then embed as normal."""
        self.calls += len(texts)
        return super().embed_documents(texts)

    def embed_query(self, text):
        """Count the query, then embed as normal."""
        self.calls += 1
        return super().embed_query(text)


@pytest.fixture
def embeddings(tmp_path):
    """Cached embeddings wrapped around the counting fake, with the cache in a temp folder."""
    return make_embeddings(cache_dir=tmp_path / "emb_cache", underlying=CountingEmbeddings())


@pytest.fixture
def store(tmp_path, embeddings):
    """A temporary vector store with all policies loaded. Returns its path and the stats."""
    qdrant = tmp_path / "qdrant"
    stats = ingest(POLICY_DIR, "baseline", embeddings, qdrant)
    return qdrant, stats


# ---- chunking -------------------------------------------------------------------------


def test_chunking_ids_overlap_and_metadata():
    """Baseline chunks have stable ids, overlap their neighbours, and lose no text.

    Overlap keeps a sentence that falls on a chunk border from being cut in half.
    """
    text = " ".join(f"w{i}" for i in range(1500))
    chunks = baseline_chunks("POL-99", "Test", text, "h", size=200, overlap=40)
    ids = [c.metadata["chunk_id"] for c in chunks]
    assert len(chunks) > 3 and ids == [f"POL-99#c{n}" for n in range(len(chunks))]
    first, second = set(chunks[0].page_content.split()), set(chunks[1].page_content.split())
    assert first & second, "consecutive chunks overlap"
    assert "w1499" in chunks[-1].page_content  # Nothing is lost at the end.
    assert all(c.metadata["doc_id"] == "POL-99" and c.metadata["section"] == "" for c in chunks)


def test_policy_documents_become_few_chunks():
    """A real policy is short, so it becomes only one or two chunks."""
    doc_id, title, text = load_policy(next(POLICY_DIR.glob("POL-01*.md")))
    chunks = baseline_chunks(doc_id, title, text, "h")
    assert doc_id == "POL-01" and title == "Returns Policy"
    assert 1 <= len(chunks) <= 2


def test_section_chunks_follow_headings_and_carry_a_prefix():
    """Section chunking makes one chunk per "##" heading and prefixes it with the doc and title.

    The prefix gives each chunk context, which helps retrieval.
    """
    doc_id, title, text = load_policy(next(POLICY_DIR.glob("POL-01*.md")))
    chunks = section_chunks(doc_id, title, text, "h")
    sections = [c.metadata["section"] for c in chunks]
    assert "Return window" in sections and len(chunks) == text.count("\n## ")
    window = chunks[sections.index("Return window")]
    assert window.page_content.startswith("POL-01 Returns Policy / Return window")
    assert "30 days" in window.page_content
    assert [c.metadata["chunk_id"] for c in chunks] == [f"POL-01#s{n}" for n in range(len(chunks))]


def test_long_section_is_split_further():
    """A very long section is split into several chunks that all keep the same heading prefix."""
    text = "# POL-98: Long\n\n## Big\n\n" + " ".join(f"w{i}" for i in range(900))
    chunks = section_chunks("POL-98", "Long", text, "h", size=200, overlap=20)
    assert len(chunks) > 2 and all(c.metadata["section"] == "Big" for c in chunks)
    assert all(c.page_content.startswith("POL-98 Long / Big") for c in chunks)


def test_point_ids_are_stable_uuids():
    """The same chunk id always maps to the same store id, and different chunks get different ids.

    Stable ids let re-ingesting overwrite old points instead of duplicating them.
    """
    assert point_id("POL-01#c0") == point_id("POL-01#c0") != point_id("POL-01#c1")


# ---- embedding cache ------------------------------------------------------------------


def test_embedding_cache_hits(embeddings):
    """Texts that were already embedded are not sent to the API again (saves cost)."""
    inner = embeddings.underlying_embeddings
    embeddings.embed_documents(["alpha beta", "gamma"])
    assert inner.calls == 2
    embeddings.embed_documents(["alpha beta", "gamma", "alpha beta"])
    assert inner.calls == 2  # Nothing new was sent.
    embeddings.embed_documents(["delta"])
    assert inner.calls == 3


def test_query_embeddings_are_cached(embeddings):
    """Asking the same question twice embeds it only once."""
    inner = embeddings.underlying_embeddings
    embeddings.embed_query("how long is the return window")
    embeddings.embed_query("how long is the return window")
    assert inner.calls == 1


def test_embedding_cache_is_per_model(tmp_path):
    """Two models sharing a cache folder do not share vectors, since vectors differ per model."""
    cache = tmp_path / "shared"
    a = make_embeddings(cache_dir=cache, underlying=CountingEmbeddings())
    a.embed_documents(["same text"])

    class Other(CountingEmbeddings):
        """Same fake embeddings, but under a different model name."""

        model_name = "other-model"

    b = make_embeddings(cache_dir=cache, underlying=Other())
    b.embed_documents(["same text"])
    assert b.underlying_embeddings.calls == 1  # Different model: not served from A's cache.


# ---- ingest ---------------------------------------------------------------------------


def test_ingest_all_policies_then_idempotent(embeddings, store):
    """The first ingest embeds all 10 policies. Running it again does no work (idempotent)."""
    qdrant, stats = store
    assert stats["docs_embedded"] == 10 and stats["docs_skipped"] == 0
    assert stats["chunks_total"] >= 10
    calls = embeddings.underlying_embeddings.calls
    again = ingest(POLICY_DIR, "baseline", embeddings, qdrant)
    assert again["docs_embedded"] == 0 and again["docs_skipped"] == 10
    assert embeddings.underlying_embeddings.calls == calls  # No re-embedding.


def test_ingest_reembeds_only_changed_document(tmp_path, embeddings):
    """If one policy file changes, only that one is re-embedded. This keeps updates cheap."""
    policies = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, policies)  # Work on a copy so the real policies are not edited.
    qdrant = tmp_path / "qdrant"
    ingest(policies, "baseline", embeddings, qdrant)
    target = next(policies.glob("POL-06*.md"))
    target.write_text(target.read_text(encoding="utf-8") + "\nExtra sentence.\n", encoding="utf-8")
    stats = ingest(policies, "baseline", embeddings, qdrant)
    assert stats["docs_embedded"] == 1 and stats["docs_skipped"] == 9
    assert stats["chunks_total"] == ingest(policies, "baseline", embeddings, qdrant)["chunks_total"]


def test_ingest_removes_deleted_document(tmp_path, embeddings):
    """If a policy file is deleted, its chunks go too, so the agent cannot cite a gone policy."""
    policies = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, policies)  # Work on a copy so the real policies are not deleted.
    qdrant = tmp_path / "qdrant"
    ingest(policies, "baseline", embeddings, qdrant)
    next(policies.glob("POL-09*.md")).unlink()
    stats = ingest(policies, "baseline", embeddings, qdrant)
    r = Retriever(embeddings, "baseline", qdrant)
    assert all(c["doc_id"] != "POL-09" for c in r.retrieve("price adjustment gift card", 8))
    assert stats["chunks_total"] >= 9


# ---- retrieval ------------------------------------------------------------------------

# Each pair is a customer-style question and the policy that should be found for it.
QUESTIONS = [
    ("How many days do I have to return an item after delivery?", "POL-01"),
    ("What is the store credit bonus?", "POL-02"),
    ("Can clearance items be returned?", "POL-03"),
    ("When is a package considered lost?", "POL-06"),
    ("What is the warranty period for electronics?", "POL-08"),
]


@pytest.mark.parametrize("question,expected_doc", QUESTIONS)
def test_retrieval_finds_right_policy(store, embeddings, question, expected_doc):
    """The right policy appears in the top results, with the expected fields, best score first."""
    qdrant, _ = store
    chunks = Retriever(embeddings, "baseline", qdrant).retrieve(question, 4)
    assert expected_doc in [c["doc_id"] for c in chunks]
    assert all({"chunk_id", "doc_id", "score", "text"} <= set(c) for c in chunks)
    assert chunks[0]["score"] >= chunks[-1]["score"]


@pytest.mark.parametrize("question,expected_doc", QUESTIONS)
def test_hybrid_retrieval_finds_right_policy_in_section_chunks(
    tmp_path, embeddings, question, expected_doc
):
    """Hybrid search (meaning plus keywords) over section chunks also finds the right policy."""
    ingest(POLICY_DIR, "section", embeddings, tmp_path / "q")
    r = Retriever(embeddings, "section", tmp_path / "q", mode="hybrid")
    # k=8: the offline hash embeddings are only a stand-in for the real model, so give them room.
    chunks = r.retrieve(question, 8)
    assert expected_doc in [c["doc_id"] for c in chunks] and len(chunks) <= 8
    assert [c["score"] for c in chunks] == sorted((c["score"] for c in chunks), reverse=True)


def test_hybrid_finds_an_exact_keyword_that_dense_may_miss(tmp_path, embeddings):
    """Keyword search finds exact tokens such as "₱200", which meaning-based search may miss."""
    ingest(POLICY_DIR, "section", embeddings, tmp_path / "q")
    r = Retriever(embeddings, "section", tmp_path / "q", mode="hybrid")
    assert "₱200" in " ".join(c["text"] for c in r.retrieve("₱200", 4))


def test_multiquery_merges_the_rewrites(tmp_path, embeddings):
    """Multi-query mode searches with several rewrites of the question and merges the results.

    A fake chat model returns two fixed rewrites, so no real LLM is needed.
    """
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    ingest(POLICY_DIR, "section", embeddings, tmp_path / "q")
    llm = FakeListChatModel(responses=["when is a package considered lost\nstore credit bonus"])
    r = Retriever(embeddings, "section", tmp_path / "q", mode="multiquery", llm=llm)
    docs = {c["doc_id"] for c in r.retrieve("tell me about lost packages", 6)}
    assert "POL-06" in docs  # A rewritten query reached a chunk the original may not rank first.


def test_unknown_retrieval_mode_is_rejected(tmp_path, embeddings):
    """A mistyped retrieval mode fails loudly instead of silently using a default."""
    with pytest.raises(ValueError):
        Retriever(embeddings, "baseline", tmp_path / "q", mode="magic")


def test_langchain_retriever_for_lcel(store, embeddings):
    """The retriever can be used as a LangChain retriever (for LCEL chains) and keeps chunk ids."""
    qdrant, _ = store
    docs = Retriever(embeddings, "baseline", qdrant).as_langchain_retriever(k=2).invoke("returns")
    assert len(docs) == 2 and "chunk_id" in docs[0].metadata


# ---- search_policy tool ---------------------------------------------------------------


@pytest.fixture
def tool(store, embeddings):
    """Plug a retriever on the temporary store into the search tool, and remove it afterwards."""
    qdrant, _ = store
    policy_search.set_retriever(Retriever(embeddings, "baseline", qdrant))
    yield
    policy_search.set_retriever(None)


def test_search_policy_ok(tool):
    """The search tool returns the requested number of chunks, each with a policy chunk id."""
    res = search_policy("how long is the return window", k=3)
    assert res.ok and len(res.data["chunks"]) == 3
    assert res.data["chunks"][0]["chunk_id"].startswith("POL-")


def test_search_policy_errors(tool, monkeypatch):
    """Blank queries and injected faults give clean error codes, and a huge k is capped."""
    assert search_policy("   ").code == "invalid_input"
    monkeypatch.setenv("FAULT_INJECTION", "search_policy:timeout")
    assert search_policy("returns").code == "timeout"
    monkeypatch.setenv("FAULT_INJECTION", "")
    assert len(search_policy("returns", k=99).data["chunks"]) <= 8  # k is clamped.


def test_search_policy_degrades_when_store_empty(tmp_path, embeddings):
    """If the vector store is empty, the tool reports "unavailable" instead of crashing."""
    policy_search.set_retriever(Retriever(embeddings, "baseline", tmp_path / "empty"))
    try:
        assert search_policy("returns").code == "unavailable"
    finally:
        policy_search.set_retriever(None)

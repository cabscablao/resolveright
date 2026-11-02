"""Embeddings with a disk cache (course Session 9: CacheBackedEmbeddings + LocalFileStore).

An embedding is a list of numbers that represents the meaning of a text. Texts with similar
meaning get vectors that point in similar directions, which is how vector search works.

Where it sits in the flow: ingest embeds every chunk with these, and the retriever embeds
each user question with them. The disk cache means the same text is never paid for twice.
"""

import hashlib
import math
import re

from langchain.embeddings import CacheBackedEmbeddings
from langchain.storage import LocalFileStore
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app import config


class HashEmbeddings(Embeddings):
    """Offline, deterministic bag-of-words embeddings. For tests and offline demos only.

    Each word is hashed into one of 256 slots and counted. It captures word overlap, not
    real meaning, but it needs no network and always gives the same result.
    """

    model_name = "hash-256"
    # Vector length. 256 is small and fast; tests do not need more.
    dims = 256

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts (the LangChain interface for documents).

        Args:
            texts: The texts to embed.

        Returns:
            One vector per text.
        """
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed one search query (the LangChain interface for queries).

        Args:
            text: The query text.

        Returns:
            The vector for the query.
        """
        return self._one(text)

    def _one(self, text: str) -> list[float]:
        """Turn one text into a unit-length vector of word counts.

        Args:
            text: The text to embed.

        Returns:
            A vector of length `dims`, scaled so its length is 1.
        """
        vec = [0.0] * self.dims
        # Keep ₱, $, % and . inside words so "₱5000" and "30%" stay as single tokens.
        for word in re.findall(r"[a-z0-9₱$%.]+", text.lower()):
            # sha256 (not Python's hash()) gives the same slot on every run.
            digest = hashlib.sha256(word.encode()).digest()
            vec[int.from_bytes(digest[:4], "big") % self.dims] += 1.0
        # Scale to length 1 so cosine similarity is not affected by text length.
        # "or 1.0" avoids dividing by zero for an empty text.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def model_name(embeddings) -> str:
    """Find the name of the underlying embedding model.

    The name is used in collection names and cache namespaces, so vectors from two different
    models never get mixed.

    Args:
        embeddings: Any embeddings object, cached or plain.

    Returns:
        The model name, or "unknown" if it cannot be found.
    """
    # A cached wrapper hides the real model in `underlying_embeddings`.
    inner = getattr(embeddings, "underlying_embeddings", embeddings)
    return getattr(inner, "model_name", None) or getattr(inner, "model", "unknown")


def make_embeddings(provider: str | None = None, cache_dir=None, underlying=None):
    """Build an embeddings object that caches results on disk.

    Only texts not seen before (for this model) hit the API.

    Args:
        provider: "hash" for the offline embeddings, anything else for OpenAI. Defaults to
            the EMBEDDING_PROVIDER setting.
        cache_dir: Folder for the cache files. Defaults to the EMBEDDING_CACHE_DIR setting.
        underlying: A ready-made embeddings object to wrap (used by tests).

    Returns:
        A CacheBackedEmbeddings object.
    """
    provider = provider or config.EMBEDDING_PROVIDER
    if underlying is None:
        if provider == "hash":
            underlying = HashEmbeddings()
        else:
            underlying = OpenAIEmbeddings(model=config.EMBEDDING_MODEL)
    store = LocalFileStore(str(cache_dir or config.EMBEDDING_CACHE_DIR))
    return CacheBackedEmbeddings.from_bytes_store(
        underlying,
        store,
        # The namespace is part of every cache key, so two models never share cached vectors.
        namespace=model_name(underlying),
        query_embedding_cache=True,  # repeated query embeddings are cached too
        key_encoder="sha256",
    )

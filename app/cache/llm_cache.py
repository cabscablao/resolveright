"""Tier 1: exact-match LLM cache (course Session 9: LangChain `SQLiteCache`).

Why this file exists: calling the LLM costs money and time. If the exact same prompt was
answered before, we can return the saved answer instead. "Exact" means the prompt must match
character for character (the semantic cache in semantic.py handles "similar" questions).

Where it sits in the flow: it wraps the real LLM client. The agent calls `parse()` as usual;
the wrapper checks SQLite first and only calls the real LLM on a miss.

The key is the full prompt (system + user text) plus the model, schema and settings. The user
text already contains the masked customer message, the order facts and the retrieved policy
text, so a change in any of them (order status, policy edit, prompt version) is a different
key and a miss. Only successful, validated answers are stored; errors are never cached.
"""

import json
from pathlib import Path

from langchain_community.cache import SQLiteCache
from langchain_core.outputs import Generation
from pydantic import BaseModel

from app import config
from app.observability.logging import log_event


class CachedLLM:
    """Wraps any object with `parse(system, user, schema)`; same interface, same results."""

    def __init__(self, llm, cache=None, path=None):
        """Set up the wrapper and open (or create) the SQLite cache file.

        Args:
            llm: The real LLM client to wrap. It must have `parse(system, user, schema)`.
            cache: A ready-made LangChain cache (tests pass an in-memory one).
            path: SQLite file path. Defaults to the LLM_CACHE_PATH setting.
        """
        self._llm = llm
        if cache is None:
            path = Path(path or config.LLM_CACHE_PATH)
            # The .cache folder may not exist on a fresh checkout.
            path.parent.mkdir(parents=True, exist_ok=True)
            cache = SQLiteCache(database_path=str(path))
        self._cache = cache
        self.stats = {"hits": 0, "misses": 0}

    def __getattr__(self, name):  # model, usage, ... come from the wrapped LLM
        """Pass any attribute we do not define (model, usage...) to the wrapped LLM."""
        return getattr(self._llm, name)

    def _llm_string(self, schema) -> str:
        """Build the "which LLM and settings" part of the cache key.

        The same prompt asked of a different model, schema or temperature must not reuse an
        answer, so all of those go into the key.

        Args:
            schema: The Pydantic class the answer is parsed into.

        Returns:
            A JSON string with sorted keys, so it is the same every time.
        """
        return json.dumps(
            {
                "model": getattr(self._llm, "model", type(self._llm).__name__),
                "schema": schema.__qualname__,
                "effort": getattr(self._llm, "reasoning_effort", None),
                "temperature": getattr(self._llm, "temperature", None),
            },
            sort_keys=True,
        )

    def parse(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        """Return a structured answer, from the cache if possible, else from the real LLM.

        Args:
            system: The system prompt.
            user: The user prompt (message, order facts and policy text).
            schema: The Pydantic class the answer must fit.

        Returns:
            An instance of `schema`.
        """
        # The prompt key is both texts together, so a change in either one is a new key.
        prompt = json.dumps([system, user])
        key = self._llm_string(schema)
        # --- Step 1: try the cache ---
        try:
            hit = self._cache.lookup(prompt, key)
            if hit:
                value = schema.model_validate_json(hit[0].text)
                self.stats["hits"] += 1
                log_event("llm_cache", cache_hit=True, cache_tier="exact", stage=schema.__name__)
                return value
        except Exception as exc:  # noqa: BLE001 - a broken cache must never break a request
            log_event("llm_cache_error", error=type(exc).__name__)
        # --- Step 2: miss, so ask the real LLM ---
        self.stats["misses"] += 1
        value = self._llm.parse(system, user, schema)
        # --- Step 3: save the answer (only reached when the LLM call succeeded) ---
        try:
            self._cache.update(prompt, key, [Generation(text=value.model_dump_json())])
        except Exception as exc:  # noqa: BLE001
            log_event("llm_cache_error", error=type(exc).__name__)
        return value

    def clear(self) -> None:
        """Delete every cached answer and reset the hit/miss counters."""
        self._cache.clear()
        self.stats = {"hits": 0, "misses": 0}


def make_llm(llm=None):
    """Build the production LLM, wrapped in the exact-match cache when enabled.

    Args:
        llm: An LLM to use instead of the real OpenAI one (tests pass a fake).

    Returns:
        The LLM itself if LLM_CACHE_ENABLED is off, otherwise a CachedLLM around it.
    """
    # Imported here to avoid a circular import with the agent package.
    from app.agent.llm import OpenAILLM

    llm = llm or OpenAILLM()
    if not config.LLM_CACHE_ENABLED:
        return llm
    return CachedLLM(llm)

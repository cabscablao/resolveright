"""Tier 2: semantic cache on Qdrant, for general policy questions only (course Session 9 idea,
built on Qdrant instead of Redis; user decision D18).

Why this file exists: "What is the return window?" and "How long do I have to return
something?" are different strings but mean the same. The exact cache (llm_cache.py) misses
them. This cache compares question embeddings (vectors that capture meaning) and reuses an
earlier answer when two questions are close enough.

Where it sits in the flow: it is checked before the agent runs the full pipeline, and a good
answer is stored after the pipeline finishes.

A cached answer is reused only when ALL of these hold:
  - the new message is a general question: no order id, no personal wording ("my order",
    "I bought"), no PII that was masked, no earlier turns in the session. (PII = personally
    identifiable information such as an email or phone number);
  - the question embedding is at least `threshold` similar to a stored question;
  - both questions mention the same numbers and key terms (a guard against near neighbours such
    as "30 days" vs "60 days" or "electronics" vs "apparel", which embed almost identically);
  - the entry is younger than the TTL (time to live: how long an entry stays valid) and was
    written under the same policy text, prompt version and model.

An answer is stored only if it is a grounded `inform` answer to a general question: guardrails
passed, at least one policy citation, no order facts, no escalation, no notice. Blocked, fallback,
escalated, clarifying and tool-failure results are never stored. The stored text is the masked
message, and a message that contained PII is never stored or looked up at all.
"""

import hashlib
import re
import time
import uuid

from qdrant_client.models import Distance, PointStruct, VectorParams

from app import config
from app.agent.schemas import ResolutionResult
from app.guardrails.input_rules import HUMAN_WORDS
from app.observability.logging import log_event
from app.rag.embeddings import model_name

# Matches order ids such as "ORD-1001", "ORD1001" or "ord 1001".
ORDER_ID = re.compile(r"\bORD-?\s?\d+", re.IGNORECASE)
# Matches wording that makes a question about THIS customer ("my order", "I bought").
# Such answers depend on one person's data, so they must never be shared through the cache.
PERSONAL = re.compile(
    r"\b(my|mine|our|i\s+(ordered|bought|received|got|paid|returned|have|had|want|need))\b",
    re.IGNORECASE,
)
# Matches numbers like "30", "₱5,000" or "6.95" (thousand separators kept in one number).
NUMBER = re.compile(r"[₱$]?\d+(?:,\d{3})*(?:\.\d+)?")
# Words that change the policy answer when swapped. Embeddings treat "electronics" and
# "apparel" as near neighbours, so we compare these words as a second check.
KEY_TERMS = (
    "electronics", "apparel", "home", "clothing", "clothes", "shoes", "laptop", "phone",
    "opened", "unopened", "used", "unused", "final sale", "gift", "personalized", "personalised",
    "metro manila", "province", "provincial", "defective", "damaged", "lost", "late", "refund", "exchange",
    "store credit", "cancel", "warranty", "price match", "price adjustment", "shipping",
    "return", "manager", "agent",
)  # fmt: skip


def cacheable_message(message: str, original: str, session) -> bool:
    """Decide whether a message may be answered from, or stored in, the semantic cache.

    Args:
        message: The message after PII masking.
        original: The message as the customer typed it.
        session: The conversation session (has `turns` and `case`).

    Returns:
        True only for a general, first-turn question with no PII and no personal wording.
    """
    # If masking changed the text, PII was present: never cache anything that carried PII.
    if message != original:
        return False
    # A follow-up question depends on the earlier conversation, so it is not general.
    if session.turns or session.case.order_id:
        return False
    # A request for a person is never a general question: it must reach the hand-off every time.
    if HUMAN_WORDS.search(message):
        return False
    return not (ORDER_ID.search(message) or PERSONAL.search(message))


def storable(result: ResolutionResult) -> bool:
    """Decide whether an answer is safe to save for other customers.

    Args:
        result: The finished resolution for a request.

    Returns:
        True only for a grounded, guardrail-passed policy answer that used no order data.
    """
    return (
        result.intent == "policy_question"
        and result.recommendation == "inform"
        and result.guardrail_status == "passed"
        # No citation means the answer is not grounded in a policy, so do not reuse it.
        and bool(result.policy_citations)
        and not result.order_facts_used
        and not result.escalation
        and not result.notices
        and not any(t.get("tool") == "lookup_order" for t in result.tool_trace)
    )


def key_terms(text: str) -> frozenset[str]:
    """Collect the numbers and key policy words that appear in a text.

    Args:
        text: A customer question.

    Returns:
        A set of numbers and KEY_TERMS found in it (case-insensitive).
    """
    low = text.lower()
    return frozenset(NUMBER.findall(low)) | {t for t in KEY_TERMS if t in low}


def corpus_version(policy_dir=None) -> str:
    """Hash of the policy files: an edited policy changes it and so invalidates cached answers.

    Args:
        policy_dir: Folder with the policy .md files. Defaults to data/policies.

    Returns:
        A short hex fingerprint (16 characters is plenty to detect a change).
    """
    h = hashlib.sha256()
    # Sorted so the hash does not depend on the order the file system lists files.
    for path in sorted((policy_dir or config.DATA_DIR / "policies").glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


class SemanticCache:
    """A cache that finds earlier answers by question meaning, stored in a Qdrant collection."""

    def __init__(
        self,
        embeddings,
        client,
        threshold=None,
        ttl=None,
        corpus=None,
        prompt_version=None,
        llm_model=None,
        clock=time.time,
    ):
        """Set up the cache.

        Args:
            embeddings: Embeddings object used to embed questions.
            client: Qdrant client that holds the cache collection.
            threshold: Minimum cosine similarity (1 = same meaning) to count as a hit.
                Defaults to SEMANTIC_CACHE_THRESHOLD (0.92): high enough to avoid wrong
                matches, low enough to catch rewordings.
            ttl: Seconds an entry stays valid. Defaults to SEMANTIC_CACHE_TTL_SECONDS
                (86400 = 24 hours) so a stale answer cannot live forever.
            corpus: Policy fingerprint. Defaults to the current `corpus_version()`.
            prompt_version: Prompt version label. Defaults to the PROMPT_VERSION setting.
            llm_model: LLM name. Defaults to the LLM_MODEL setting.
            clock: Function returning the current time in seconds (tests pass a fake).
        """
        self.embeddings, self.client = embeddings, client
        self.threshold = config.SEMANTIC_CACHE_THRESHOLD if threshold is None else threshold
        self.ttl = config.SEMANTIC_CACHE_TTL_SECONDS if ttl is None else ttl
        self.corpus = corpus or corpus_version()
        # Entries written under another prompt or model are treated as stale.
        self.version = f"{prompt_version or config.PROMPT_VERSION}|{llm_model or config.LLM_MODEL}"
        self._clock = clock
        # One collection per embedding model: vectors from different models do not mix.
        safe = "".join(c if c.isalnum() else "_" for c in model_name(embeddings))
        self.collection = f"semantic_cache_{safe}"
        self.stats = {"hits": 0, "misses": 0, "stale": 0, "term_mismatch": 0}

    def _ensure(self, size: int) -> None:
        """Create the cache collection if it does not exist yet.

        Args:
            size: Length of the embedding vectors.
        """
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection, vectors_config=VectorParams(size=size, distance=Distance.COSINE)
            )

    def lookup(self, question: str) -> tuple[ResolutionResult, float] | None:
        """Look for a saved answer to a similar question.

        Args:
            question: The (masked) customer question.

        Returns:
            (saved result, similarity score) on a hit, or None on a miss.
        """
        # --- Step 1: find the single nearest stored question ---
        try:
            if not self.client.collection_exists(self.collection):
                self.stats["misses"] += 1
                return None
            vector = self.embeddings.embed_query(question)
            points = self.client.query_points(
                self.collection, query=vector, limit=1, with_payload=True
            ).points
        except Exception as exc:  # noqa: BLE001 - the cache must never break a request
            log_event("semantic_cache_error", error=type(exc).__name__)
            return None
        # --- Step 2: is it similar enough? ---
        if not points or points[0].score < self.threshold:
            self.stats["misses"] += 1
            return None
        point = points[0]
        payload = point.payload
        # --- Step 3: is the entry still valid? ---
        age = self._clock() - payload["created"]
        if payload["corpus"] != self.corpus or payload["version"] != self.version or age > self.ttl:
            self.stats["stale"] += 1  # policy, prompt, model or TTL changed: drop and miss
            self.client.delete(self.collection, points_selector=[point.id])
            return None
        # --- Step 4: do both questions mention the same numbers and key words? ---
        # "30 days" and "60 days" embed almost the same, so the vector score alone is not safe.
        if key_terms(question) != key_terms(payload["question"]):
            self.stats["term_mismatch"] += 1
            return None
        self.stats["hits"] += 1
        return ResolutionResult.model_validate_json(payload["result"]), point.score

    def store(self, question: str, result: ResolutionResult) -> None:
        """Save an answer for later reuse, if it is safe to share.

        Args:
            question: The (masked) customer question.
            result: The finished resolution for it.
        """
        if not storable(result):
            return
        try:
            vector = self.embeddings.embed_query(question)
            self._ensure(len(vector))
            # Strip per-request fields so one customer's case id and events are not replayed.
            clean = result.model_copy(
                update={"case_id": None, "review_status": "not_required", "guardrail_events": []}
            )
            self.client.upsert(
                self.collection,
                points=[
                    PointStruct(
                        # Id from the normalised question: asking again overwrites, not duplicates.
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, question.lower().strip())),
                        vector=vector,
                        payload={
                            "question": question,
                            "result": clean.model_dump_json(),
                            "created": self._clock(),
                            "corpus": self.corpus,
                            "version": self.version,
                        },
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log_event("semantic_cache_error", error=type(exc).__name__)

    def clear(self) -> None:
        """Delete the whole cache collection and reset the counters."""
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.stats = {k: 0 for k in self.stats}


def get_semantic_cache() -> "SemanticCache | None":
    """Build the production semantic cache.

    Returns:
        A SemanticCache, or None when SEMANTIC_CACHE_ENABLED is off.
    """
    if not config.SEMANTIC_CACHE_ENABLED:
        return None
    # Imported here so the cache module loads without pulling in the RAG stack.
    from app.rag.embeddings import make_embeddings
    from app.rag.ingest import get_client

    return SemanticCache(make_embeddings(), get_client())

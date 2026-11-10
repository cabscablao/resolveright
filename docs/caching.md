# Caching

Three layers, each with a different job. Evidence: `docs/evidence/evals/cache-bench.json` (`uv run python -m eval.cache_bench`, test guide CA-01 to CA-08), `tests/test_cache.py`.

| Layer | Technology (course Session 9) | What it saves | Where |
|---|---|---|---|
| Embeddings | `CacheBackedEmbeddings` + `LocalFileStore` | embedding calls for text seen before | `app/rag/embeddings.py` |
| Tier 1: exact-match LLM cache | LangChain `SQLiteCache` | the whole LLM call when the prompt is identical | `app/cache/llm_cache.py` |
| Tier 2: semantic cache | Qdrant collection (not Redis, D18) | the whole request for a repeated *general policy question* | `app/cache/semantic.py` |

## How the tiers decide

**Tier 1** wraps the LLM (`CachedLLM`, used when the app builds its own LLM). The key is the full system prompt plus the user text plus model, schema and reasoning settings. The user text already holds the masked customer message, the **order facts** read from the tools and the retrieved **policy text**, so an order that changed state, an edited policy or a new prompt version is a different key by construction. Tools still run on every request, so order data is always read live. Errors are never stored; a cache failure is logged and ignored.

**Tier 2** is checked after the input guardrails (a blocked request never reaches it) and before the graph. A stored answer is reused only if all of these hold:
1. the message is a general question: no order id, no personal wording ("my", "I bought"), no masked PII, no earlier turns in the session;
2. cosine similarity is at least 0.92 (`SEMANTIC_CACHE_THRESHOLD`);
3. both questions contain the same numbers and key terms (a guard for near neighbours, see below);
4. the entry is under 24 h old and was written under the same policy files, prompt version and model.

A hit is an `inform` answer, so it needs no human decision (`review_status=not_required`, D47) and carries a fresh case id and the notice "Answer reused from an earlier identical policy question." A result is stored only if it is a grounded `inform` answer to a general question: guardrails passed, at least one policy citation, no order facts, no escalation, no notice.

## Results (real model `gpt-5-nano`, one run, `cache-bench.json`)

| Query type | Mode | p50 latency | p95 latency | LLM calls | Est. cost | Hit rate |
|---|---|---|---|---|---|---|
| Repeated (10 queries x 3 runs) | No cache | 5.90 s | 7.50 s | 60 | USD 0.0103 | 0% |
| Repeated (second pass of 10) | Exact cache | **0.49 s** | 0.87 s | **0** | USD 0 | 100% |
| Repeated policy questions (5) | Semantic cache | 0.01 s | 6.0 s | 2 | USD 0.0004 | 80% (4 of 5) |
| Similar, policy only (5 paraphrases) | No cache | 5.17 s | 6.99 s | 8 | USD 0.0011 | 0% |
| Similar, policy only (5 paraphrases) | Semantic cache | 4.96 s | 6.08 s | 7 | USD 0.0011 | **20% (1 of 5)** |
| Similar, order-specific (5 paraphrases) | Exact cache | 5.79 s | 7.32 s | 10 | USD 0.0019 | 0% (0 of 10 calls) |

What the table says, without rounding up:
- **Exact tier works as intended** (CA-02): the second run of every query made zero LLM calls, was about 12 times faster (the rest is retrieval and tools, which still run) and returned the same recommendation and amount.
- **Semantic hit rate on paraphrases is poor at the safe threshold (1 of 5).** Measured similarity of the five paraphrase pairs is 0.75 to 0.93; 0.92 admits one. The near-neighbour questions that *must not* match score 0.68, 0.92 and 0.91, so **similarity alone cannot separate them**: "opened" versus "unopened electronics" scores 0.924, higher than three of the true paraphrases. The key-term guard is what blocked them (0 false hits in 3 probes). Lowering the threshold to 0.85 would admit 4 of 5 paraphrases while the guard still blocks all three neighbours, but then the keyword list is the only protection, so the default stays 0.92. Tune it on a larger set before changing it.
- **Why only 4 of 5 repeats were served:** one general question ("What is the return window for electronics?") came back from the model as `need_info` (it asked which order), so it was not stored. That is a draft-quality issue, not a cache fault.
- **Order-specific paraphrases never hit the exact tier** (CA-03): a different sentence is a different prompt. That is the intended safe behaviour, and the cost is that only literally repeated cases are saved.
- A first-seen question adds one embedding call (latency not measured separately) before the semantic lookup misses; the 0.01 to 0.02 s hits above reused embeddings cached by earlier runs.
- Cost saved is tiny at this scale (the model is cheap); the benefit shown here is latency and load.

## Safety checks (all measured)

| Test | Result |
|---|---|
| CA-04 two near-identical messages about ORD-1001 and ORD-1002 | different answers (amount ₱2,750.00 versus none), zero exact hits when ORD-1002 was first asked, no semantic hit, repeats return their own answer |
| CA-05 embedding cache | first ingest embeds 12 texts (10 chunks and probes), a second ingest 0, a new empty vector store with the same cache 0 (offline, hash embeddings) |
| CA-06 edit one policy file | only that document is re-embedded (1 of 10); the semantic entry written under the old policy files is dropped (hit before the edit, miss after, `stale=1`) |
| CA-07 change model or prompt version | exact cache: 3 calls for 4 requests (other model and other prompt text miss); semantic: other prompt version and other model both miss |
| CA-08 blocked attack twice | blocked both times, 0 LLM calls, nothing stored |
| CA-08 tool failure twice (policy search down) | escalated both times, not served from cache, 0 semantic entries |
| CA-08 message containing an email and a card number, twice | never served from cache; raw email and card number are absent from the SQLite file (only `<EMAIL>` appears); nothing stored in the semantic cache |

## Invalidation policy

| Trigger | Exact (SQLite) | Semantic (Qdrant) |
|---|---|---|
| TTL | none (see limits) | 24 h (`SEMANTIC_CACHE_TTL_SECONDS`) |
| Policy text changed | new prompt, so a miss | corpus hash differs, so the entry is dropped |
| Prompt version or model changed | in the key | `prompt_version\|model` differs, so the entry is dropped |
| Order state changed | order facts are in the prompt, so a miss | not applicable: order cases are never looked up |
| Never cached | errors, anything not validated | blocked, fallback, repaired, escalated, `need_info`, tool-failure results; any message that contained PII; anything about an order or with personal wording |

## Limits
- The exact cache has no TTL or size limit; `.cache/llm_cache.sqlite3` lives on the container's ephemeral disk on Render and is lost on redeploy. Delete the file to clear it.
- A cached answer freezes the first sample of a non-deterministic model for that prompt.
- The key-term guard is a hand-made list (`KEY_TERMS`) and numbers; a difference outside it (for example two different products) is caught only by the embedding score.
- Cached text is stored unencrypted on disk; it is masked, but a question can still be sensitive. No customer id is stored with it.
- The test set is 5 paraphrase pairs and 3 near-neighbour probes, one run. Treat the hit rates as indicative.
- Evaluation scripts pass their own LLM to the agent, so they run without these caches and their numbers are not affected.
- `LLM_CACHE_ENABLED=false` and `SEMANTIC_CACHE_ENABLED=false` switch the tiers off.

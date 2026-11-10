# Course technology traceability

Maps each technology from the instructor's sessions (folder `Unspoiled`) to where the capstone uses it. Status: **built** (used and tested), **installed** (dependency present, used in a later slot), **not used** (decision recorded).

| Technology (course session) | Status | Where | Evidence |
|---|---|---|---|
| OpenAI SDK, `chat.completions.parse` + Pydantic (S1) | built | `app/agent/llm.py`, `app/agent/schemas.py` | `tests/test_agent.py`, `scripts/run_scenarios.py` |
| Prompt delimiters as injection defence (S1) | built | `prompts/understand.md` (`<customer_message>`) | real-LLM run (`docs/evaluation.md`) |
| LangChain splitter, embeddings, `CacheBackedEmbeddings` (S3-S5, S9) | built | `app/rag/` | `tests/test_rag.py` |
| Qdrant vector store (S3-S6, S9) | built | `app/rag/ingest.py`, `retriever.py` | `tests/test_rag.py`, `scripts/retrieval_check.py` |
| LangGraph `StateGraph`, conditional edges (S3, S6, S9) | built | `app/agent/graph.py` | `tests/test_agent.py` |
| Mock execution tool and structured hand-off payload (D50) | built | `app/tools/resolution.py`, `execute` node and `_build_handoff` in `app/agent/graph.py` | `tests/test_resolution.py` |
| LangGraph `interrupt` human-in-the-loop (S8 Lab 3) | built; pauses only for escalations (D47) | `review` node in `app/agent/graph.py`, `Agent.review()` | `tests/test_agent.py` (approve, edit, escalate, double review, no review for in-policy results) |
| Conversation memory + case state (S7 pattern) | built | `app/agent/memory.py` | `tests/test_agent.py` (follow-up, TTL, 6 turns) |
| FastAPI + uvicorn (S9) | built | `app/api/main.py`, `app/server.py` | `tests/test_api.py` |
| Gradio UI (S9) | built | `app/ui/gradio_app.py`, `staff_ui.py`, `dev_ui.py`, `theme.py`; `app/cases.py` (D51) | `tests/test_api.py` (helpers, three pages mounted), `tests/test_cases.py` (cases, staff flow, pages) |
| Langfuse tracing | built (traces confirmed from the local run and the live Render app; three trace screenshots in `docs/evidence/screenshots/tracing/`) | `app/observability/` | `tests/test_guardrails.py` (tracing stays on with Guardrails AI), `tests/test_api.py` |
| RAGAS evaluation (S4) | built (baseline) | `eval/rag_eval.py`, `eval/golden_rag.jsonl` | `docs/evidence/evals/rag_baseline.json`, `docs/evidence/evals/rag_baseline_v2_reviewed.json`, `docs/evaluation.md` |
| RAGAS synthetic test-set generation (S5) | built (candidates reviewed 2026-11-02; merged; RAGAS re-run done 2026-11-02) | `eval/sdg.py` | `docs/evidence/evals/sdg_candidates.jsonl`, `docs/evaluation.md` 4e, `tests/test_sdg.py` |
| Prompt versions and comparison (S1) | built | `prompts/v1..v3`, `eval/compare_prompts.py` | `docs/prompt-comparison.md`, `tests/test_eval.py` |
| Advanced retrievers: BM25, Ensemble, MultiQuery (S5); section-aware chunking | built (compared, not default) | `app/rag/retriever.py`, `app/rag/chunking.py` | `docs/evaluation.md` 1b, `tests/test_rag.py`, D40 |
| `SQLiteCache` exact-match LLM cache (S9) | built | `app/cache/llm_cache.py` | `docs/caching.md`, `docs/evidence/evals/cache-bench.json`, `tests/test_cache.py` |
| Semantic cache on Qdrant (S9, Redis replaced by Qdrant, D18) | built | `app/cache/semantic.py` | same |
| LLM-as-judge | built (12 reviewer scores compared, LLM-assisted) | `eval/judge.py`, `eval/judge_prompt.md` | `docs/evidence/evals/judge_scores.json`, `tests/test_judge.py` |
| Guardrails AI validators (S9) | built (on by default since D64; off on Render) | `app/guardrails/hub.py` (DetectJailbreak, ProfanityFree, RestrictToTopic; PII and competitor validators do not load here) | `docs/guardrails.md`, `docs/evidence/evals/refusal_with_hub.json`, `tests/test_guardrails.py` |
| Layered input/output guardrails, refusal suite | built | `app/guardrails/`, `eval/refusal_eval.py` | `docs/guardrails.md`, `docs/evidence/evals/refusal_rules.json` |
| Docker + `uv sync --frozen` (S9) | built (deployed on Render) | `Dockerfile`, `render.yaml` | `docs/deployment.md` |
| MCP / FastMCP (S8) | **not used** | user decision, D17 | `docs/miscellaneous/design-decisions.md` |
| A2A (S8) | **not used** | out of scope | |

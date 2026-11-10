# Final check against the specification and the test guide

Status report, updated 2026-11-10. It is not a sign-off: items that need the deployed app, a login, or a paid run are marked pending, not passed. Numbers are quoted from the saved result files; where a result predates a later change, that is said.

**Status words.** *Pass*: shown by a command or a saved result. *Partial*: built and tested, one part is missing. *Pending*: waits on the deployment, a login or a paid run.

## Minimum bar (specification, section 10)

| Item | Status | Where |
|---|---|---|
| Working application with an LLM core and agentic capability | Pass | 371 tests pass (370 when first recorded on 2026-11-03; `docs/evidence/miscellaneous/agentic-checks.md`) |
| Deployed on a server, secrets handled safely | Pass (deployment screenshots still to be added) | Live on Render at https://resolveright.onrender.com (`/health` answered ok on 2026-11-10, `docs/evidence/miscellaneous/smoke.txt`); `render.yaml`, `Dockerfile`, `docs/deployment.md`; key scan in `docs/evidence/miscellaneous/history-scan.txt` |
| Evaluation with a test set, metrics and one improvement iteration | Pass | `docs/evaluation.md` |
| One debugged failure with root cause, fix, before and after | Pass | `docs/debugged-failure.md` |
| GitHub repository with a reproducible README and evidence docs | Pass | Repository https://github.com/cabscablao/resolveright; README and docs done; clean-clone run recorded on 2026-11-03 (`docs/evidence/miscellaneous/clean-clone-run.txt`) |
| Demo video of at most 5 minutes | Pass | 4:55, recorded on the live Render app from the script the owner reviewed (`docs/evidence/miscellaneous/video-check.md`) |
| Working public URL | Pass | https://resolveright.onrender.com (`/ui`, `/docs` and `/health` answer 200; the first request after idle can take about a minute) |

## Requirement by requirement

| Requirement | Status | Evidence and notes |
|---|---|---|
| Usable interface (UI and API) | Pass | UI and API tested; `miscellaneous/api-curl.txt`, `screenshots/ui/ui-core-flow.png`, `screenshots/api/api-openapi.png` and the staff and developer page screenshots saved in `docs/evidence/` (screenshots grouped in `docs/evidence/screenshots/`) |
| LLM cleanly integrated | Pass | Model and prompt version from config, shown at `/health` (`miscellaneous/llm-config-test.txt`) |
| Agentic capabilities | Pass | `docs/evidence/miscellaneous/agentic-checks.md`, `miscellaneous/execution-proof.txt` |
| Prompt design and comparison | Pass | `docs/prompt-comparison.md` (v1 to v3 compared; the default `v5_executing` was not compared against them) |
| Graceful degradation | Pass | `docs/evidence/miscellaneous/degradation-results.md` (no separate repair for broken JSON: an invalid draft is repaired once, then escalated) |
| Secrets hygiene | Pass | `.env` untracked, `.env.example` present, no key value in the git history (`miscellaneous/history-scan.txt`); detect-secrets is used instead of gitleaks (anti-virus) |
| Logging without personal data | Pass | `docs/evidence/miscellaneous/log-safety.txt` |
| RAG golden set and RAGAS | Pass, with a disclosure | 36 reviewed questions: faithfulness 0.969, answer relevancy 0.790, context precision 0.956, context recall 0.989, retrieval hit 31 of 31, 4 of 5 unanswerable declined, 0 invalid citations (`docs/evidence/evals/rag_baseline_v3_citerule.json`). One run, no seed. The reviews were AI-assisted, not independent human reviews |
| Tool scenarios | Pass | tool selection 1.00, task completion 1.00 after the fix in `debugged-failure.md`; measured before the 2026-11-02 changes and not re-run |
| LLM judge and agreement | Pass, with a disclosure | `docs/judge-agreement.md`; reviewer scores were LLM-assisted |
| Baseline, improvement, before and after | Pass | `docs/evaluation.md` section 1b: no change beat the baseline, reported as found |
| Tracing with screenshots | Pass | Langfuse traces arrive from the local run and from the live Render app (`docs/evidence/miscellaneous/langfuse-check.txt`). Screenshots: `screenshots/tracing/trace-retrieval.png` (understand, gather with the embedding retrieval, decide, draft, execute, review, with latency, tokens and cost), `screenshots/tracing/trace-escalation.png` (ORD-1021 handed to staff), `screenshots/tracing/trace-toolfailure.png` (unknown order ORD-9999). Rule-blocked messages stop before the graph, so they have no trace; their evidence is `guardrail-blocked-log.txt` |
| Guardrails with a blocked-attack log | Pass (log and `screenshots/guardrails/guardrail-blocked-ui.png`) | `docs/evidence/guardrail-blocked-log.txt`; `docs/guardrail-testing.md` lists messages and expected results. The 26-case suite (attacks 19 of 20, escalations 2 of 3, false alarms 0 of 6) was run before Guardrails AI became the default (D64) and was not repeated |
| Caching with a table and an invalidation policy | Pass | `docs/caching.md`, `docs/evidence/evals/cache-bench.json` |
| Limitations and next steps | Pass | `docs/limitations.md` |
| Clean-clone reproducibility | Pass | fresh folder, no `.env`, no `.venv`, `uv sync --frozen` and the full test run on 2026-11-03: `docs/evidence/miscellaneous/clean-clone-run.txt` |
| Submission package | Pass | `make package NAME="..."` builds it; the contents are listed in `docs/evidence/miscellaneous/package-listing.txt` (a snapshot from 2026-11-03, regenerated each time the package is rebuilt) |
| Demo video | Pass | 4:55 on the live Render app, `docs/evidence/miscellaneous/video-check.md`; the file is `docs/ResolveRight_demo_render.mp4`; the local recording `docs/ResolveRight_demo_local.mp4` (4:32) is kept as a fallback |

## Still open (owner or later)

1. Deployment screenshots: public URL in a private window, uptime monitor and Render dashboard (docs/evidence/screenshots/deployment/). The GitHub repository and the Render service are live and make smoke passed on 2026-11-03.
2. Langfuse: done (three trace screenshots). A trace of a blocked attack does not exist because blocked messages never reach the graph.
3. Paid re-runs that would refresh older numbers (owner decides): the refusal suite and tool scenarios with Guardrails AI on, the comparison of the default prompt 5_executing, and the cache benchmark.
4. Known gap: GR-18 (an "already approved" claim for a refund over ₱5,000) is not escalated (`docs/guardrails.md`).

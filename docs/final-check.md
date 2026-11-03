# Final check against the specification and the test guide

Status report, updated 2026-11-03. It is not a sign-off: items that need the deployed app, a login, or a paid run are marked pending, not passed. Numbers are quoted from the saved result files; where a result predates a later change, that is said.

**Status words.** *Pass*: shown by a command or a saved result. *Partial*: built and tested, one part is missing. *Pending*: waits on the deployment, a login or a paid run.

## Minimum bar (specification, section 10)

| Item | Status | Where |
|---|---|---|
| Working application with an LLM core and agentic capability | Pass | 370 tests pass (`docs/evidence/agentic-checks.md`) |
| Deployed on a server, secrets handled safely | Pending (deployment) | `render.yaml`, `Dockerfile`, `docs/deployment.md`; key scan in `docs/evidence/history-scan.txt` |
| Evaluation with a test set, metrics and one improvement iteration | Pass | `docs/evaluation.md` |
| One debugged failure with root cause, fix, before and after | Pass | `docs/debugged-failure.md` |
| GitHub repository with a reproducible README and evidence docs | Partial | README and docs done; the repository is created at upload; clean-clone run recorded after the copy |
| Demo video of at most 5 minutes | Pass (local build), re-record on Render | 4:32, made from the script the owner reviewed (`docs/evidence/video-check.md`) |
| Working public URL | Pending (deployment) | |

## Requirement by requirement

| Requirement | Status | Evidence and notes |
|---|---|---|
| Usable interface (UI and API) | Pass | UI and API tested; `api-curl.txt`, `ui-core-flow.png`, `api-openapi.png` and the staff and developer page screenshots saved in `docs/evidence/` |
| LLM cleanly integrated | Pass | Model and prompt version from config, shown at `/health` (`llm-config-test.txt`) |
| Agentic capabilities | Pass | `docs/evidence/agentic-checks.md`, `execution-proof.txt` |
| Prompt design and comparison | Pass | `docs/prompt-comparison.md` (v1 to v3 compared; the default `v5_executing` was not compared against them) |
| Graceful degradation | Pass | `docs/evidence/degradation-results.md` (no separate repair for broken JSON: an invalid draft is repaired once, then escalated) |
| Secrets hygiene | Pass | `.env` untracked, `.env.example` present, no key value in the git history (`history-scan.txt`); detect-secrets is used instead of gitleaks (anti-virus) |
| Logging without personal data | Pass | `docs/evidence/log-safety.txt` |
| RAG golden set and RAGAS | Pass, with a disclosure | 36 reviewed questions: faithfulness 0.969, answer relevancy 0.790, context precision 0.956, context recall 0.989, retrieval hit 31 of 31, 4 of 5 unanswerable declined, 0 invalid citations (`eval/results/rag_baseline_v3_citerule.json`). One run, no seed. The reviews were AI-assisted, not independent human reviews |
| Tool scenarios | Pass | tool selection 1.00, task completion 1.00 after the fix in `debugged-failure.md`; measured before the 2026-11-02 changes and not re-run |
| LLM judge and agreement | Pass, with a disclosure | `docs/judge-agreement.md`; reviewer scores were LLM-assisted |
| Baseline, improvement, before and after | Pass | `docs/evaluation.md` section 1b: no change beat the baseline, reported as found |
| Tracing with screenshots | Partial | Langfuse was switched on locally on 2026-11-03 and the traces arrive (`docs/evidence/langfuse-check.txt`); the four `trace-*.png` screenshots need the owner's Langfuse login |
| Guardrails with a blocked-attack log | Pass (log and `guardrail-blocked-ui.png`) | `docs/evidence/guardrail-blocked-log.txt`; `docs/guardrail-testing.md` lists messages and expected results. The 26-case suite (attacks 19 of 20, escalations 2 of 3, false alarms 0 of 6) was run before Guardrails AI became the default (D64) and was not repeated |
| Caching with a table and an invalidation policy | Pass | `docs/caching.md`, `docs/evidence/cache-bench.json` |
| Limitations and next steps | Pass | `docs/limitations.md` |
| Clean-clone reproducibility | Pending | run on the release copy; result goes to `docs/evidence/clean-clone-run.txt` |
| Submission package | Partial | `make package NAME="..."` exists; the student name is still an owner item |
| Demo video | Pass (local build), re-record on Render | 4:32, `docs/evidence/video-check.md`; the file is `docs/ResolveRight_demo_local.mp4`; the Render recording will be `docs/ResolveRight_demo_render.mp4` |

## Still open (owner or later)

1. Deployment: GitHub repository, Render service, uptime monitor, `make smoke URL=...`, public-URL screenshots.
2. Langfuse: tracing is on and traces arrive; log in and take the four trace screenshots (the keys page must not be visible).
3. `docs/proposal.md` still has the placeholders for the student name and the date.
4. Paid re-runs that would refresh older numbers (owner decides): the refusal suite and tool scenarios with Guardrails AI on, and the cache benchmark.
5. Known gap: GR-18 (an "already approved" claim for a refund over ₱5,000) is not escalated (`docs/guardrails.md`).

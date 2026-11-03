# Evidence index

Each file backs a claim in the specification (section 10, "Evidence docs") or in the test and verification guide (`docs/test-and-verification-guide.md`). Files marked *pending* need the deployed app, a login, or a paid run, and are listed in `docs/final-check.md`.

| File | What it proves | Spec or guide item |
|---|---|---|
| `guardrail-blocked-log.txt` | Three attacks blocked, with the `guardrail_triggered` log line, rule and source (rules and Guardrails AI). No LLM call was made. | 6.1 Guardrails, GL-01 |
| `guardrail-blocked-ui.png` | The blocked attack as the customer sees it | GL-02 |
| `ui-core-flow.png`, `ui-graceful-failure.png`, `ui-escalated.png` | Refund confirmation, unknown order handled politely, case passed to staff | FT, GD |
| `ui-staff-review.png`, `ui-dev-debug.png` | Staff Review (hand-off packet, recommendation) and the read-only Developer Debug view of the same case | AG (human-in-the-loop) |
| `langfuse-check.txt` | Langfuse received the LangGraph traces (observations by name), checked through the API | TR-01 |
| `video-check.md` | Checklist of the demo video | VD-01 to VD-09 |
| `api-curl.txt`, `llm-config-test.txt` | The REST API works with curl; model, prompt version and cache state come from config | Usable interface, LLM-01 to LLM-03 |
| `agentic-checks.md` | Agentic capabilities mapped to passing tests | AG-01 to AG-10 |
| `execution-proof.txt` | The mock execution tool is the only action, it is bounded, and `data/` is never edited | AG-08 (action boundary) |
| `degradation-results.md` | Bad input, tool failure and LLM failure handled without a crash | GD-01 to GD-10 |
| `log-safety.txt` | Logs carry ids and rule names, never customer text or personal data | OPS-01 to OPS-06 |
| `cache-bench.json` | Raw numbers behind the before and after cache table in `docs/caching.md` | CA-01 to CA-08 |
| `history-scan.txt` | No key value from `.env` appears anywhere in the git history | SEC-01 to SEC-04 |
| `package-listing.txt` | Contents of the submission zip (`make package`) | PK-01 to PK-05 |
| `api-openapi.png` | The API documentation page | Usable interface |
| `trace-retrieval.png`, `trace-escalation.png`, `trace-toolfailure.png` | Langfuse traces of the live app: the full refund flow with retrieval, LLM calls, execution, latency and cost; an escalation to staff; an unknown order handled without an LLM call. Account details are cropped out | TR-01 to TR-04 |
| `public-url-incognito.png`, `smoke.txt`, `uptime-monitor.png`, `dashboard.png` | The deployed app, health check and uptime monitor | DP-01 to DP-07 (pending: Render deployment) |

Other evidence lives next to the claims it supports: `eval/results/` (RAGAS, scenarios, refusal suite, judge scores, cache), `docs/evaluation.md`, `docs/prompt-comparison.md`, `docs/debugged-failure.md`, `docs/guardrails.md`, `docs/caching.md`, `docs/judge-agreement.md`.

All files here were produced by running the code or the test suite, not written by hand. The test results (`370 passed`) are from `uv run pytest -v`.

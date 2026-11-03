# Evidence index

Screenshots are grouped by type in `screenshots/` (one README per folder); the other files stay here. Each file backs a claim in the specification (section 10, "Evidence docs") or in the test and verification guide (`docs/test-and-verification-guide.md`). Files marked *pending* need the deployed app, a login, or a paid run, and are listed in `docs/final-check.md`.

| File | What it proves | Spec or guide item |
|---|---|---|
| `guardrail-blocked-log.txt` | Three attacks blocked, with the `guardrail_triggered` log line, rule and source (rules and Guardrails AI). No LLM call was made. | 6.1 Guardrails, GL-01 |
| [`screenshots/guardrails/`](screenshots/guardrails/README.md) | `guardrail-blocked-ui.png`: the blocked attack as the customer sees it | GL-02 |
| [`screenshots/ui/`](screenshots/ui/README.md) | Customer chat (refund confirmation, unknown order handled politely, case passed to staff), Staff Review and read-only Developer Debug of the same case | FT, GD, AG (human-in-the-loop) |
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
| [`screenshots/api/`](screenshots/api/README.md) | `api-openapi.png`: the API documentation page | Usable interface |
| [`screenshots/tracing/`](screenshots/tracing/README.md) | Three Langfuse traces of the live app: the full refund flow with retrieval, LLM calls, execution, latency and cost; an escalation to staff; an unknown order handled without an LLM call. Account details are cropped out | TR-01 to TR-04 |
| `smoke.txt`; [`screenshots/deployment/`](screenshots/deployment/README.md) | Live health check and test messages; the public-URL, uptime and dashboard screenshots are pending (owner) | DP-01 to DP-07 |

Other evidence lives next to the claims it supports: `eval/results/` (RAGAS, scenarios, refusal suite, judge scores, cache), `docs/evaluation.md`, `docs/prompt-comparison.md`, `docs/debugged-failure.md`, `docs/guardrails.md`, `docs/caching.md`, `docs/judge-agreement.md`.

All files here were produced by running the code or the test suite, not written by hand. The test results (`370 passed`) are from `uv run pytest -v`.

## Screenshot gallery

One preview per type; click an image for the folder summary with every screenshot.

| Customer chat (`ui/`) | Blocked attack (`guardrails/`) |
|---|---|
| [<img src="screenshots/ui/ui-core-flow.png" width="380" alt="Customer chat with a refund confirmation">](screenshots/ui/README.md) | [<img src="screenshots/guardrails/guardrail-blocked-ui.png" width="380" alt="Blocked prompt injection">](screenshots/guardrails/README.md) |

| Langfuse trace (`tracing/`) | API docs (`api/`) |
|---|---|
| [<img src="screenshots/tracing/trace-retrieval.png" width="380" alt="Langfuse trace of a refund flow">](screenshots/tracing/README.md) | [<img src="screenshots/api/api-openapi.png" width="380" alt="OpenAPI documentation page">](screenshots/api/README.md) |

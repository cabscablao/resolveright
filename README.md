# ResolveRight

**ResolveRight helps a customer-support agent decide how to answer a return or refund request.** It is built for the support team of **Brightcart**, a fictional online store. The support agent pastes in what a customer wrote. ResolveRight finds the order, checks it against the store's return and refund rules, finds the matching policy text, and drafts a reply. Within the store's policy, ResolveRight carries out the resolution itself (a refund, store credit, replacement or cancellation up to the ₱5,000 agent limit). This is a mock: it writes a ledger line and a runtime order change, and there is no real payment system. When the policy keeps a case for a person (for example a safety problem, suspected fraud, a request for a human, or an amount above the ₱5,000 agent limit), or no policy covers the case, the case is escalated to staff with a structured hand-off and nothing is executed.

> **The agent acts within policy; people take the rest (D50).** Brightcart policy lets a support agent approve refunds, store credit, replacements and cancellations up to ₱5,000, so ResolveRight carries those out itself (a mock: a line in `logs/resolutions.jsonl` and a change in a runtime copy of the order, never in `data/`). A case goes to a person only when a policy says so (POL-10: fraud, legal threat, safety hazard, harassment, open chargeback, three refunds in 90 days, a request for a human, amounts above ₱5,000, late damage claims, address-change fraud risk) or when no policy covers it (a policy gap). A technical failure also hands over, labelled as a system fallback. An escalated case is never executed, even if the request is within policy and every tool would allow it, and it carries a structured hand-off (who, what they wanted, why it escalates) shown under *Agent Process* in the Developer Debug View. `requires_human_approval` is true exactly for escalations. The web page has no Approve or Escalate buttons: an escalation message goes to the customer at once and the case is queued for staff. A change to the address of an order is only answered, not executed, because the app never handles addresses.

This is an individual capstone project for the PSIA AI Engineering course. All data is synthetic: no real people, companies or payments.

- **Live URL:** _to be added after the Render deployment_ (steps in [`docs/deployment.md`](docs/deployment.md))
- **Video:** _to be added_
- **Course tools used:** see [`docs/requirements-traceability.md`](docs/requirements-traceability.md)

## An example

A customer writes: *"Return the headphones from ORD-1001, I changed my mind."*

ResolveRight shows the support agent:

1. **Decision:** whether the order qualifies for a return (here, checked against the 30-day return window, the product type and the order status).
2. **Why:** the exact rule that applied, with the policy passage it came from.
3. **Draft reply:** polite wording for the customer, ready to edit.
4. **Next step:** if the case stays inside policy, the agent sends the draft. If it is an escalation, the customer sees the escalation message at once and the case is queued for human staff. Nothing is refunded by the software either way.

The decision comes from plain, tested code (the rules), not from the language model. The language model only reads the message and writes the wording. That is on purpose: an AI that guesses refund rules would sometimes be wrong.

## Key terms

New to AI engineering? These are the words used in this project.

| Term | Plain meaning |
|---|---|
| **LLM** (large language model) | The AI that reads and writes text. Here it is an OpenAI model such as `gpt-4o-mini`. |
| **Prompt** | The written instructions given to the LLM. Different wordings give different answers, so three versions are compared. |
| **Agent** | A program that uses an LLM plus tools and decides what to do next, step by step. |
| **Tool** | A small function the agent can call, such as "look up an order". |
| **Eligibility engine** | The rules code that decides whether an order qualifies for a return or refund. It is authoritative: the LLM cannot override it. |
| **RAG** (retrieval-augmented generation) | Look up the relevant policy text first, then let the LLM answer from that text, so answers stay tied to real policy. |
| **Embedding** | A list of numbers that represents the meaning of a text, so texts with similar meaning can be found by comparing numbers. |
| **Vector store** (Qdrant) | A database that stores embeddings and finds the closest ones. Here it runs locally inside the app. |
| **LangGraph** | The library that runs the agent as a fixed series of steps (a state machine), with a step limit. |
| **Guardrails** | Safety checks on what goes in and out: block attacks, hide personal data, refuse out-of-scope requests. |
| **PII** | Personally identifiable information, such as emails, phone numbers or card numbers. It is masked before the LLM sees it and never written to logs. |
| **Escalation** | Handing a case to a human with a reason and an urgency (for example a safety hazard or a legal threat). |
| **Cache** | A memory of earlier answers. The *exact* cache reuses an identical request; the *semantic* cache reuses an answer to a question that means almost the same thing. |
| **Langfuse** | A web service that shows every request step by step (tracing), to inspect and debug the agent. |
| **Golden set / RAGAS** | A set of questions with known good answers, and the library that scores the app's answers against them (for example, faithfulness). |
| **LLM-as-judge** | Using a second LLM to score the quality of drafted replies. A human spot-checks it. |

## What it does

| Capability | What that means | Technical name |
|---|---|---|
| Understands the message | Works out what the customer wants (return, damaged item, late order...), which order, and any red flags | LLM with structured output (Pydantic): intent, order id, flags |
| Looks things up | Finds the order, checks the rules, searches the policies, or escalates | Tools `lookup_order`, `check_eligibility`, `search_policy`, `execute_resolution`, `escalate_to_human` (mock data, deterministic) |
| Decides with rules, not guesses | The result of the rules engine is final | Eligibility engine |
| Quotes the policy | Draft replies cite policy passages, and a citation must be a passage that was really retrieved | RAG with Qdrant |
| Plans in several steps | Understand, gather facts, decide, draft, wait for a human only when policy escalates the case (at most 6 steps, 30 seconds) | LangGraph state machine |
| Remembers and asks | Keeps the conversation and asks one clarifying question if the order or request is unclear | Session memory |
| Escalates | Safety hazards, fraud, legal threats, large amounts and unclear cases go to a human | Escalation rules |
| Stays safe | Blocks attacks, masks personal data, limits how much one user can ask | Layered guardrails, rate limit, daily budget |
| Saves cost | Does not pay for the same answer twice | Exact cache (SQLite) and semantic cache (Qdrant) |
| Can be inspected | Logs with request ids and no personal data; optional step-by-step traces | JSON logs, Langfuse |

The architecture diagram is in [`docs/architecture.md`](docs/architecture.md).

## Quick start (on your own computer)

### What you need first

| Need | Why | Where |
|---|---|---|
| Python 3.12 | The app is written in Python | https://www.python.org/downloads/ |
| `uv` | Installs the exact library versions (used instead of `pip`) | https://docs.astral.sh/uv/ |
| Git | To download the project | https://git-scm.com/ |
| An OpenAI API key | The LLM and embedding calls. It needs billing set up. | https://platform.openai.com/api-keys |
| Docker (optional) | Only to run the packaged container | https://www.docker.com/ |

Windows, macOS and Linux all work. **Cost:** the unit tests make no paid calls. Starting the app embeds the 10 policy files once (a few cents), and each question you send costs a fraction of a cent. Set a monthly spend limit in the OpenAI dashboard.

### Steps

```bash
git clone <this repository> resolveright
cd resolveright
uv sync                      # installs the app and its libraries (dev and eval included)
cp .env.example .env         # on Windows PowerShell: Copy-Item .env.example .env
```

Open the new `.env` file in an editor and replace `your-openai-key-here` with your real key. Each setting in that file is explained in comments. **Never commit `.env`, paste it into a chat or screenshot it.** It is git-ignored.

```bash
uv run python -m scripts.generate_data   # creates the sample customers and orders in data/
uv run python -m scripts.seed_checks     # self-check of the sample data; should print "seed_checks: OK"
uv run pytest -q                         # runs the tests (no paid calls); should end with "242 passed"
uv run uvicorn app.server:app --port 8000   # starts the app (stop it with Ctrl+C)
```

The first start takes longer because the policies are embedded and stored. When the server is running, open:

| Address | What you see |
|---|---|
| http://127.0.0.1:8000/ui | The customer chat (Gradio). Start here. |
| http://127.0.0.1:8000/ui-staff | Staff review: the queue of escalated cases and the decision (no login, demo only). The case file is deleted every time the server starts, so the queue always starts empty |
| http://127.0.0.1:8000/ui-dev | Developer debug: what the agent did for a case (read-only) |
| http://127.0.0.1:8000/docs | The interactive API page (OpenAPI), where you can try the endpoints |
| http://127.0.0.1:8000/health | A short status report: app version, model, prompt version, cache state |

### Using the web interface

1. Open `/ui` and open *Demo setup to fill in message to be sent above (for testing only)* under the message box (it holds the sample messages and the links to the staff and developer pages). There is no customer picker: the page always chats as **Nora (C-001)**, shown under the header (D57).
2. Type *"Return the headphones from ORD-1001."* and send it.
3. Read the decision, the reasons, the cited policy and the draft reply.
4. This refund is within policy and under the ₱5,000 agent limit, so the badge reads *Completed - resolved by Brightcart Assistant*, and the Developer page (`/ui-dev`, pick the case, open *Agent Process*) shows *Escalated to human staff: No* and *Action executed: refund (ref RES-...)*. The chat reply ends with the confirmation and the same reference. The refund is a mock (a ledger line and a runtime order change); sending the same request again answers that it was already processed, so nothing is refunded twice. The server deletes the order state and the resolution ledger at every start (D60), so each session starts from clean data; to reset without a restart, stop the server and run `uv run python scripts/reset_state.py` (it lists `logs/cases.json`, `logs/order_state.json` and `logs/resolutions.jsonl` and asks before deleting).

To see an escalation, pick a case policy escalates, for example *"I want to return the ergonomic chair on ORD-1021."* (above the agent limit). The escalation message appears in the chat at once and the badge reads *Escalated - waiting for staff*. Open `/ui-dev` and pick the case to see *Escalated to human staff: Yes*, *Model confidence: Not applicable* and the **Hand-off to staff** block (customer, what was wanted, why it escalates, policies, urgency, and `executed: false`). Nothing is executed for an escalation. The same happens when a customer asks for a human, even for a request that is within policy.

To finish the escalated case as a staff member, open `/ui-staff` in a second tab (the link is in *Demo setup*), pick the case under *Open case* (the customer badge turns to *Staff is reviewing*), read the hand-off packet, choose **Approve** (carries out the suggested refund, mock), **Modify** (choose the action and amount, write a reply) or **Reject** (write a reply; nothing is executed), and press *Submit decision*. Within 5 seconds, or on *Refresh status*, the customer tab shows the staff reply and *Completed - resolved by staff*. The staff page has no login: this is a documented limitation (`docs/limitations.md`).

**While a case waits for staff (D58, D59).** With the chair case still escalated, try *"Any update on my return?"* (a status reply from a template, no new case), *"The chair arrived with a cracked leg."* (added to the case; the staff list shows a 💬 *New msg* marker and *Reload case* shows the message without erasing a draft reply), *"Just refund me now!"* (status only, the chatbot never decides a held case) and *"What's your shipping policy?"* (answered normally; the escalated case is untouched, and the page shows one badge per case). Staff replies reach the chat as *Update on CASE-...* after the current answer. The Brightcart data has two customers: Nora owns the orders the scenarios need and Milo owns the rest, so *"Please refund order ORD-1002"* (Milo's order) answers "not found" (D55).

Other messages worth trying: an order outside the 30-day window (the rules say no, and the draft explains why); *"The power bank from ORD-1023 is overheating and smells like it's burning!"* (a safety hazard, so it escalates urgently without any refund logic); and a vague message with no order number (the agent asks one clarifying question).

### Using the API from the command line

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/resolve -H "Content-Type: application/json" \
  -d '{"session_id":"t1","customer_id":"C-001","message":"Return ORD-1001, changed my mind"}'
uv run python -m scripts.client C-001 "Return ORD-1001, changed my mind"
```

The first call sends the message as JSON; the second is a small helper that does the same and prints the result neatly.

### No API key yet?

The unit tests never call the real model, so `uv run pytest -q` works without a key. For an offline check of the policy search, set `EMBEDDING_PROVIDER=hash` in `.env` and run `uv run python -m scripts.retrieval_check`. The full app still needs a key to understand messages.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Errors about an API key or "401" from OpenAI | `OPENAI_API_KEY` in `.env` is missing, mistyped or still the placeholder. Fix it and restart. |
| 401 from Langfuse in the log | The keys belong to a different Langfuse region than `LANGFUSE_HOST`. Match them, or set `LANGFUSE_ENABLED=false`. |
| "Address already in use" | Another program uses port 8000. Stop it, or start the app on another port, for example `uv run uvicorn app.server:app --port 8001` (then use 8001 in the addresses). |
| `make: command not found` (Windows) | Windows has no `make` by default. Use `.\tasks.ps1 <task>` for the common tasks, or run the `uv run ...` command shown in the Makefile. |
| First start is slow | The policies are being embedded once. Later starts are quick. |
| HTTP 429 | The per-minute limit or the daily budget was reached. Wait, or raise `RATE_LIMIT_PER_MINUTE` or `DAILY_REQUEST_BUDGET`. |
| Different results after editing `AS_OF_DATE` | The rules count days from it. Put it back, or regenerate the sample data. |

## Docker

Docker packages the app and its libraries into one image that runs the same way on any machine, which is how it is deployed to Render.

```bash
docker build -t resolveright .
docker run --rm -p 8000:8000 --env-file .env resolveright
```

The image contains no secrets. Your keys are passed in at run time with `--env-file`. See [`docs/deployment.md`](docs/deployment.md) for the Render steps (`render.yaml` is a Render "Blueprint", a file that describes the service to create).

## Settings (environment variables)

An environment variable is a setting given to the app from outside the code. They are kept in the `.env` file, which you create by copying [`.env.example`](.env.example). **That file explains every setting in detail**, with accepted values, defaults and when to change them. In short:

| Kind | Settings | Notes |
|---|---|---|
| Required | `OPENAI_API_KEY` | Secret |
| Common to change | `LLM_MODEL`, `PROMPT_VERSION` | Chat model and prompt wording |
| Optional tracing | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENABLED` | Secret keys. The host must match your Langfuse region. |
| Limits and cost | `RATE_LIMIT_PER_MINUTE`, `DAILY_REQUEST_BUDGET`, `MAX_MESSAGE_CHARS`, `LLM_CACHE_ENABLED`, `SEMANTIC_CACHE_ENABLED` | Defaults 10, 500, 1500, on, on |
| Reproducibility | `AS_OF_DATE` | Fixed "today" (2026-11-02) so the return-window results are the same on any day |
| Policy search | `EMBEDDING_PROVIDER`, `CHUNK_STRATEGY`, `RETRIEVAL_MODE`, `TOP_K` | Defaults are the best measured; see `docs/evaluation.md` |
| Guardrails | `MODERATION_ENABLED`, `GUARDRAILS_AI_ENABLED` | The second is on by default (slow, needs memory); `render.yaml` sets it false for the free plan |
| Test and evaluation only | `FAULT_INJECTION`, `JUDGE_MODEL`, `SDG_MODEL`, `RESOLVERIGHT_URL` | Not needed to run the app |

## Commands

`make` runs named shortcuts defined in the `Makefile`. On Windows without `make`, use `.\tasks.ps1 <task>` (it has the same names for the test, lint, secrets and evaluation tasks; for `ingest` and `smoke`, run the `uv run` line from the Makefile). "Eval" commands measure how good the app is by running it on prepared questions. Those that call OpenAI cost money, as marked.

| Command | What it does | Paid API? |
|---|---|---|
| `make test` | All automatic tests | no |
| `make test-guardrails` | Safety-check tests only | no |
| `make lint` | Checks code style (ruff and black); changes nothing | no |
| `make secrets` | Scans the repository for accidentally committed keys (detect-secrets) | no |
| `make ingest` | Embeds the policies into the local vector store | yes (a few cents, cached after the first run) |
| `make eval-rag` | Scores policy answers on the 25-question golden set with RAGAS | yes |
| `make eval-tools` | Runs the 24 scripted scenarios through the real agent | yes |
| `make compare-prompts` | Runs prompt versions v1, v2, v3 on the same inputs (v4 via `--versions`, see `docs/prompt-comparison.md`) | yes |
| `make eval-refusal` | The 26-case suite of attacks and escalations | yes |
| `make bench-cache` | Measures the caches (checks CA-01 to CA-08) | yes (under USD 0.10) |
| `make judge` | Has an LLM score the reply drafts | yes |
| `make sdg` | Generates synthetic test questions with RAGAS | yes |
| `make smoke URL=https://...` | Checks that a deployed app's `/health` answers | no |

Results are saved in `eval/results/`.

## Results at a glance

These come from one run each on small test sets, so read them as indications, not guarantees. Details, method and caveats are in the linked documents.

| Area | Result | What the measure means | Document |
|---|---|---|---|
| RAG baseline (36 reviewed questions, re-run 2026-11-02 with the citation rule) | faithfulness 0.97, answer relevancy 0.79, context precision 0.96, context recall 0.99, retrieval hit 31 of 31, 4 of 5 unanswerable questions declined, 0 invalid citations | Faithfulness: the answer sticks to the retrieved text. Context recall: the needed policy text was retrieved. | [`docs/evaluation.md`](docs/evaluation.md) |
| Improvement iteration | Section chunking, hybrid search and MultiQuery were compared. None beat the baseline on this small set, so the default stays. | A negative result, reported as found | [`docs/evaluation.md`](docs/evaluation.md) |
| Tool scenarios (24) | tool selection 1.00, task completion 1.00 | Did the agent call the right tools and reach the right outcome? (Scores are after the fix described in the debugged-failure write-up.) | [`docs/evaluation.md`](docs/evaluation.md), [`docs/debugged-failure.md`](docs/debugged-failure.md) |
| Prompt comparison | `v2_grounded` chosen in the comparison; `v5_executing` is the default after D50 (not compared yet) | Three prompt wordings on the same inputs | [`docs/prompt-comparison.md`](docs/prompt-comparison.md) |
| Guardrails (26 cases, rules only, measured before Guardrails AI became the default) | attacks blocked 19 of 20, escalations 2 of 3, false alarms 0 of 6 | Attacks that were stopped; cases escalated correctly; harmless messages wrongly blocked | [`docs/guardrails.md`](docs/guardrails.md) |
| Caching | exact cache: 100% hits on repeats, median time 5.9 s down to 0.49 s, 0 LLM calls | Repeated requests are answered from memory | [`docs/caching.md`](docs/caching.md) |
| LLM judge (34 drafts) | averages 4.2 to 4.7 out of 5; agreement with 12 reviewer scores is high within one point for tone and over-promising, low for completeness (the reviewer scores were LLM-assisted, so this compares two models) | A second LLM rates the drafts | [`docs/evaluation.md`](docs/evaluation.md) |

## Repository map

```
app/            the application
  agent/        the step-by-step flow (LangGraph), the LLM client, memory, limits, input checks
  tools/        orders, the eligibility (rules) engine, policy search, escalation, fault injection
  rag/          cutting policies into chunks, embeddings, loading them, the retrievers (Qdrant)
  cache/        the exact (SQLite) and semantic (Qdrant) caches
  guardrails/   input rules, personal-data masking, output rules, Guardrails AI (on by default)
  observability/ JSON logs and Langfuse tracing
  api/, ui/     the web API (FastAPI) and the web interface (Gradio)
prompts/        the three prompt versions
data/           synthetic policies, customers and orders (see data/README.md)
scripts/        data generation, data self-checks, command-line client, uptime check
eval/           question sets, evaluation scripts, saved results in results/
tests/          automatic tests (no real LLM calls)
docs/           design notes, evaluation reports, evidence (docs/evidence/) and limitations
```

## Documents

New here? Read the architecture first, then limitations, then evaluation.

| Document | Contents |
|---|---|
| [`docs/proposal.md`](docs/proposal.md) | The one-page proposal |
| [`docs/architecture.md`](docs/architecture.md) | Diagram and request flow |
| [`docs/design-decisions.md`](docs/design-decisions.md) | Why each choice was made (numbered D1 and up) |
| [`docs/evaluation.md`](docs/evaluation.md) | RAG, tools, judge, synthetic data, before and after |
| [`docs/prompt-comparison.md`](docs/prompt-comparison.md) | Three prompt variants compared |
| [`docs/debugged-failure.md`](docs/debugged-failure.md) | One real failure: symptom, cause, fix, proof |
| [`docs/guardrails.md`](docs/guardrails.md) | Safety layers, suite results, known gaps |
| [`docs/caching.md`](docs/caching.md) | Benchmark tables and the invalidation policy |
| [`docs/deployment.md`](docs/deployment.md) | Docker, Render, secrets, limits |
| [`docs/limitations.md`](docs/limitations.md) | Limitations and next steps |
| [`docs/final-check.md`](docs/final-check.md) | Check against the test and verification guide |
| [`docs/requirements-traceability.md`](docs/requirements-traceability.md) | Course tools and where they are used |
| [`docs/CREDITS.md`](docs/CREDITS.md) | Libraries and sources |
| [`docs/guardrail-testing.md`](docs/guardrail-testing.md) | Messages to type to test the guardrails, and the expected results |
| [`docs/judge-agreement.md`](docs/judge-agreement.md) | LLM judge compared with reviewer scores |
| [`docs/evidence/`](docs/evidence/README.md) | Logs, result files and screenshots that back the claims |

## Reading the code

Every module starts with a short paragraph saying what it is for, and every function has a plain-English docstring. A good order for a newcomer: `app/config.py` (all settings in one place), `app/tools/eligibility.py` (the rules), `app/agent/graph.py` (the flow), then `app/guardrails/` and `app/cache/`.

## Limitations

Mock data only, English only, 11 request types, a small policy set, small evaluation sets, guardrails based on text patterns, a semantic cache that serves only general policy questions, and a free-tier host that goes to sleep when idle. The full list and the next steps are in [`docs/limitations.md`](docs/limitations.md). The golden-set, synthetic-question and judge reviews were AI-assisted, not independent human reviews (see the Human review row in the limitations).

## Security

No keys in the repository, the image, the logs or screenshots. `.env` is git-ignored and docker-ignored. A pre-commit hook (detect-secrets, ruff, black) checks every commit. If a key is ever exposed, rotate it at once in the provider's dashboard.

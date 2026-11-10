# Prompt comparison (v1, v2, v3)

Same model (`gpt-5-nano`, reasoning effort minimal), same 34 inputs (24 tool scenarios plus 10 policy questions; S17 was removed afterwards in D57, so the set is now 23 plus 10), same retrieval (baseline chunks, top-4), same rules engine. Only the **draft prompt** changes (`prompts/v1_baseline.md`, `v2_grounded.md`, `v3_checklist.md`). The understand prompt (`prompts/understand.md`) is shared.

Run with `uv run python -m eval.compare_prompts` (or `.\tasks.ps1 compare-prompts`). Raw results: `docs/evidence/evals/compare_prompts.json`.

> **v5 added after D50 (2026-10-30).** `prompts/v5_executing.md` is now the default (`PROMPT_VERSION=v5_executing`): the agent carries out approved in-policy resolutions itself, so the prompt says the system executes the action and adds the reference, and that a case goes to staff only for a policy escalation or a policy gap. v2 to v4 keep their old role lines ("a human reviews" or "never executes") as evaluated; those lines are out of date. v5 has not been compared with the others, so quote no score for it.
>
> **v4 added after D47 (compared on 2026-10-29: see the last section).** Human approval is now needed only for escalations (`docs/miscellaneous/design-decisions.md` D47), but v1 to v3 still tell the model "a human support agent reviews your answer before anything is sent". They were left as evaluated, and `prompts/v4_approval_aware.md` was added: v2 with a corrected role line (a specialist takes only escalations; other replies may go out as written), no "a team member must approve it" wording, and an instruction to promise no outcome on an escalation. v4 is **not** the default (`PROMPT_VERSION` stays `v2_grounded`). The tables below cover v1 to v3 only and predate D47; `docs/evidence/evals/compare_prompts.json` now holds the v2 and v4 run, and the earlier file is kept in `docs/evidence/evals/pre_d47/`.

## The three prompts
| Version | What it adds |
|---|---|
| v1 baseline | Short role and task. No rules. |
| v2 grounded | Grounding rules (use only the case information, cite chunk IDs, never invent amounts), "eligibility result is authoritative", untrusted-data rule, output field guidance. |
| v3 checklist | v2 plus a six-step checklist, refusal and escalation rules, and four few-shot examples (fictional orders). |

## Final results (final code, one run each)
| Metric | v1 baseline | v2 grounded | v3 checklist |
|---|---|---|---|
| Task completion, all 34 | 0.912 | **0.971** | 0.941 |
| Task completion, 24 tool scenarios | 1.000 | 1.000 | 1.000 |
| Task completion, 10 policy questions | 0.7 | **0.9** | 0.8 |
| Tool-selection accuracy (24) | 1.0 | 1.0 | 1.0 |
| First-pass schema validity (no repair needed) | 1.0 | 1.0 | 1.0 |
| Citation validity (no citation to a chunk the model was not given) | 1.0 | 1.0 | 1.0 |
| Runs with a dollar amount not in the case data ("hallucinated amount") | 0 | 0 | 0 |
| Mean latency per request (s) | 2.95 | 3.41 | 3.73 |
| Mean tokens per request | 3,095 | 3,389 | 3,789 |
| Estimated cost for all 34 requests (USD) | 0.0087 | 0.0089 | 0.0095 |

Definitions: *task completion* = recommendation is one of the expected labels and every extra constraint holds (amount, approval level, citation present and from the right policy, urgency, wording rules). *Citation validity* and *ungrounded amounts* are computed from what the model wrote before the app filtered it. Prices are approximate list prices (`eval/common.py`).

## What changed between runs (evidence, not tuning to the test)
Three full runs were made; the code changed between them because the runs exposed real defects.

| Run | v1 | v2 | v3 | What the run exposed |
|---|---|---|---|---|
| 1 (first build) | 0.765 | 0.824 | 0.824 | Safety: the power-bank case (S16) got a replacement, not an urgent escalation, under all three prompts (the understand step did not flag the hazard). S23 (another customer's order) returned `need_info`, not an escalation. "I'd like a refund" was read as a refund-status question, so the repeated-refund and chargeback rules never ran (S17, S24). Several policy questions were read as order requests and answered with "what is your order number?". |
| 2 | 0.882 | 0.912 | 0.853 | After: keyword safety net, ownership-mismatch escalation, understand prompt rules. Left: S18 (guessed who made a personalisation error), P02, price-match questions answered as `inform`. |
| 3 (final) | 0.912 | 0.971 | 0.941 | After: `error_origin` is kept only when the customer's words say who made the mistake; v2 and v3 told to treat "the excerpts say it is not covered" as not covered. |

Details of each fix are in `docs/debugged-failure.md` (drafted) and `docs/miscellaneous/design-decisions.md` (D36 to D38).

## Observations
- **The rules engine decides most outcomes, so the tool scenarios are almost insensitive to the prompt.** All 24 pass under every prompt in the final run. The prompt changes the wording and matters mainly where there is no engine result: the policy questions, and the choice between "inform" and "escalate" for questions the documents do not cover.
- **v2 is the best trade-off.** It scored highest (0.971) and cost about the same as v1. v3 costs about 12% more tokens and 0.8 s more latency per request (the four examples), and did not beat v2: with one run each, 0.941 versus 0.971 is one policy question (P10 "bulk order", answered with `inform`), which is inside the run-to-run noise of this model.
- **v1 is not bad on schema and citations** (a modern model with structured output). Its weak point is the unanswerable policy questions: it explains that the policies do not cover price-matching and pickup, but recommends `inform` instead of `escalate`. POL-09 and POL-10 say to escalate.
- **Chosen default: v2_grounded** (`PROMPT_VERSION=v2_grounded` in `config.py`, `.env.example` and `render.yaml`).

## Limitations (read before quoting the numbers)
- One run per prompt. The model is not deterministic: the same message was classified as `policy_question`, `return` and `refund_status` on three consecutive calls in a check, so differences of one or two items are noise. A repeat of the comparison would be needed for a significance claim.
- 34 inputs only, written by the same author as the prompts and rules. The numbers show that the pipeline behaves as designed on these cases, not how it would do on unseen customer messages.
- "Hallucinated amount" is a heuristic (peso amounts in the written text that appear nowhere in the case data), not a full factual check.
- The scenario expectations were fixed before the runs, but the code was changed after run 1 and 2, so the final numbers are "after fixes", not a blind test. Run 1 and 2 files are kept in `docs/evidence/evals/`.
- P02 ("Can I choose store credit instead of a refund?") still fails intermittently because the understand step sometimes returns an order intent with no order number and the agent asks for one.


## v2_grounded vs v4_approval_aware (after D47, 2026-10-29)

Command: `uv run python -m eval.compare_prompts --versions v2_grounded v4_approval_aware`. Same 34 inputs, `gpt-5-nano`, one run each.

| Metric | v2_grounded | v4_approval_aware |
|---|---|---|
| Task completion (34) | 0.941 | 0.971 |
| Tools (24) | 0.958 | 0.958 |
| Policy questions (10) | 0.90 | 1.00 |
| Tool selection | 1.00 | 1.00 |
| First-pass schema OK | 1.00 | 1.00 |
| Citation validity | 1.00 | 1.00 |
| Runs with ungrounded amounts | 0 | 0 |
| Mean latency (s) | 3.64 | 4.24 |
| Mean tokens | 2528 | 3457 |
| Estimated cost for 34 requests (USD) | 0.0067 | 0.0090 |

**Read this with three cautions.**
- **The v2 latency, token and cost figures are too low.** Seven of the 34 v2 requests made no LLM call (answered from the exact or semantic cache, because the same prompt had just been run by the scenario suite). All 34 v4 requests called the model. Compare accuracy only; do not compare latency, tokens or cost from this table.
- **The accuracy gap is one case each.** v2 missed S07 (the reply did not ask for a photo) and P07 (a cached answer from an earlier run, `inform` instead of `escalate`); v4 missed S03 (`need_info` instead of `deny`). With one run each, 0.941 versus 0.971 is a difference of one case, inside the run-to-run noise (the same v2 prompt scored 0.971 in the scenario run a few minutes earlier).
- **No clear winner.** v4 is not shown to be better, so `PROMPT_VERSION` stays `v2_grounded`. v4 fixes the wording problem (it no longer tells the model that a person approves every reply), which is a correctness reason to prefer it if you want the prompt to match D47; to decide on numbers, rerun both with the caches off and a few repetitions.
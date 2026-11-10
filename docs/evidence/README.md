# Evidence index

This folder holds the evidence for the five outputs the project is graded on. Supporting checks (logs, test mappings, deployment and package records) are in `miscellaneous/`. The written reports live in `docs/`; the result files that the evaluation code writes are in `evals/` (the code itself stays in `eval/`). Each section below links to both.

## Folder structure

```
docs/evidence/
├── README.md                          this index
├── ragas-results.md                   evaluation results summary
├── guardrail-blocked-log.txt          blocked attacks with the log line
├── evals/                             result files written by the code in eval/ (code stays there)
│   ├── compare_prompts*.json          prompt comparison runs
│   ├── rag_*.json, sdg_*, judge_*     RAGAS, synthetic set, LLM judge
│   ├── tools_v1_*, tools_v2_*.json    debugged failure: before and after
│   ├── refusal_*.json, guardrail-*    guardrail suite and blocked-attack logs
│   ├── cache-bench.json               caching benchmark
│   └── pre_d47/, followup_*, human_*  older runs and other checks
├── screenshots/
│   ├── guardrails/   README.md, guardrail-blocked-ui.png
│   ├── ui/           README.md, 5 screenshots (chat, staff review, developer debug)
│   ├── tracing/      README.md, 3 Langfuse traces
│   ├── api/          README.md, api-openapi.png
│   └── deployment/   README.md (screenshots pending)
└── miscellaneous/                     supporting logs and checks
```

## 1. Prompt comparison table

- **What it proves:** the prompt versions were compared on the same cases, so the default prompt is a measured choice and not a guess. The default `v5_executing` was not part of that comparison; the report says so.
- **Report:** [`docs/prompt-comparison.md`](../prompt-comparison.md).
- **Result files:** [`evals/compare_prompts.json`](evals/compare_prompts.json) (final run) and the two earlier runs [`run1`](evals/compare_prompts_run1_before_fixes.json) and [`run2`](evals/compare_prompts_run2_before_error_origin_fix.json). Code: `eval/compare_prompts.py`.

## 2. Evaluation method and results tables

- **What it proves:** the RAG answers were scored with RAGAS on 36 reviewed questions (faithfulness 0.969, answer relevancy 0.790, context precision 0.956, context recall 0.989), with one improvement iteration reported before and after.
- **Evidence:** [`ragas-results.md`](ragas-results.md) (final scores, run comparison, caveats); raw result files in [`evals/`](evals/) (RAGAS: `rag_*.json`; synthetic set: `sdg_*`; judge: `judge_scores.json`; tools: `tools_v2_grounded.json`). Code: `eval/rag_eval.py`, `eval/eval_tools.py`, `eval/judge.py`.
- **Method and full tables:** [`docs/evaluation.md`](../evaluation.md); judge agreement in [`docs/judge-agreement.md`](../judge-agreement.md).

## 3. Debugged failure case (root cause, fix, before and after)

- **What it proves:** one real failure was traced to its root cause, fixed, and measured before and after the fix.
- **Report:** [`docs/debugged-failure.md`](../debugged-failure.md) (root cause, fix, before and after).
- **Result files:** before the fix [`evals/tools_v1_baseline_before_understand_fix.json`](evals/tools_v1_baseline_before_understand_fix.json), after [`evals/tools_v2_grounded.json`](evals/tools_v2_grounded.json). Code: `eval/eval_tools.py`.

## 4. Guardrails

- **What it proves:** attacks are blocked before any model call, and the log line shows the rule that fired without the message text.
- **Evidence:** [`guardrail-blocked-log.txt`](guardrail-blocked-log.txt); suite results [`evals/refusal_rules.json`](evals/refusal_rules.json) and [`evals/refusal_with_hub.json`](evals/refusal_with_hub.json) (code: `eval/refusal_eval.py`); screenshot of the same block as the customer sees it:

  [<img src="screenshots/guardrails/guardrail-blocked-ui.png" width="420" alt="Blocked prompt injection as the customer sees it">](screenshots/guardrails/README.md)

- **Rules and test messages:** [`docs/guardrails.md`](../guardrails.md), [`docs/guardrail-testing.md`](../guardrail-testing.md).

## 5. Caching

- **What it proves:** the cache layers cut cost and latency on repeated questions, with a documented invalidation policy.
- **Evidence:** [`evals/cache-bench.json`](evals/cache-bench.json), the raw numbers behind the before and after table (code: `eval/cache_bench.py`).
- **Design and results table:** [`docs/caching.md`](../caching.md).

## Other screenshots

Supporting screenshots, grouped by type (each folder has a README with the images shown):
[ui](screenshots/ui/README.md) (customer chat, staff review, developer debug), [tracing](screenshots/tracing/README.md) (Langfuse), [api](screenshots/api/README.md) (API documentation page), [deployment](screenshots/deployment/README.md) (pending owner screenshots).

## Miscellaneous

Supporting logs and checks that are not one of the five outputs above are in [`miscellaneous/`](miscellaneous/).
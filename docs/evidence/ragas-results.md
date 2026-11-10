# RAGAS results summary

The numbers behind the RAG evaluation, in one place. Full method, notes and interpretation: [`docs/evaluation.md`](../evaluation.md) (sections 1, 1b and 4e).

## Setup (same for every run below)
- **Questions:** 36 reviewed ones (31 answerable, 5 unanswerable): the 25-question golden set plus 11 reviewed synthetic questions (`eval/golden_rag.jsonl`, `eval/golden_rag_sdg.jsonl`).
- **Retrieval:** baseline chunking, dense retrieval, top-k 4. **Answering model:** `gpt-5-nano`. **Judge:** `gpt-4o-mini`.
- **Command:** `uv run python -m eval.rag_eval --include-sdg --label <name>`.

## Final result (`baseline_v3_citerule`, 2026-11-02)

| Metric | Value |
|---|---|
| Faithfulness | 0.969 |
| Answer relevancy | 0.790 |
| Context precision | 0.956 |
| Context recall | 0.989 |
| Retrieval hit rate | 31 of 31 |
| Unanswerable questions declined | 4 of 5 (G24 was answered) |
| Answerable questions wrongly declined | 0 of 31 |
| Citations to a chunk that was not retrieved | 0 |
| Mean latency per question | 1.71 s |
| Cost (estimated) | agent USD 0.006, judge USD 0.078 |

Source file: [`docs/evidence/evals/rag_baseline_v3_citerule.json`](evals/rag_baseline_v3_citerule.json) (stored unrounded: 0.9694, 0.7904, 0.9561, 0.9892).

## How the runs compare

| Metric | Old baseline (25 questions, 2026-10-29) | Re-run `baseline_v2_reviewed` (36 questions) | **Final `baseline_v3_citerule` (36 questions)** |
|---|---|---|---|
| Faithfulness | 0.983 | 0.968 | **0.969** |
| Answer relevancy | 0.802 | 0.802 | **0.790** |
| Context precision | 0.974 | 0.955 | **0.956** |
| Context recall | 0.983 | 0.989 | **0.989** |
| Invalid citations | 0 | 1 (SDG03) | **0** |

Files: [`rag_baseline.json`](evals/rag_baseline.json), [`rag_baseline_v2_reviewed.json`](evals/rag_baseline_v2_reviewed.json), [`rag_baseline_v3_citerule.json`](evals/rag_baseline_v3_citerule.json).

## Why the third run is the final one
It is the last run, made after the only change to the system: one citation rule added to `prompts/rag_answer.md` after the second run showed an invalid citation (SDG03). No change followed it. This is not a formal sign-off.

## Caveats
- **One run, no seed.** RAGAS and the answering model vary between runs, so differences of about 0.02 are noise. The second and third runs differ by less than that on every metric.
- **Faithfulness is held down mainly by G10 (0.5), which is judge noise.** Re-scored 5 times with the same judge, it gave 1.0 three times and 0.5 twice; the judge's reasons contradicted its verdict. The score was not adjusted. G14 (0.8) and SDG06 (0.75) are also below 0.9 and were not examined.
- **Answer relevancy is low on short factual answers** (for example G01 at 0.58) because the judge returns 1 generated question instead of the requested 3.
- **The scores are high partly because the corpus is small** (ten policies of about 600 tokens each), so perfect recall is expected.
- **The reviews were AI-assisted**, not independent human reviews, and the 11 synthetic rows are AI-reviewed.

## Other RAG result files
Retrieval comparison runs (hybrid, multi-query, section-aware chunks, k = 8): `rag_baseline_hybrid.json`, `rag_baseline_multiquery.json`, `rag_section_*.json`, `rag_section_k8_*.json` in [`docs/evidence/evals/`](evals/). Their before-and-after discussion is in `docs/evaluation.md` (second section 1b).
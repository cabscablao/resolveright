# Evaluation report

> **Status (2026-11-10).** The RAG numbers were re-run on the reviewed sets on 2026-11-02 (section 1b) with the peso data and policies, and the text quotes the current peso amounts (agent limit ₱5,000, return fee ₱200 / ₱500). The tool, refusal, prompt and judge numbers were measured earlier (on or before the D47 re-run) and have not been repeated since the D50 change below. The open items are listed in section 5.

> **D50 (2026-10-30): the agent now executes in-policy resolutions (mock) and escalates only on a policy rule or a policy gap.** Every number in this file is from before D50 (the prompt was `v2_grounded` or `v4_approval_aware`, nothing was executed, and GR-10 counted as an attack). The new default prompt `v5_executing` has not been compared yet. Re-running the paid evaluations needs the owner's yes. See `docs/miscellaneous/design-decisions.md` D50. **D51 (2026-10-31)** adds a case status check to the scenario runner (an escalated result must be saved as `escalated`, everything else as `completed` by the chatbot); it has not been run with the paid model.

Status: golden set, RAG baseline, tool scenarios, prompt comparison, retrieval improvement, the refusal suite, the caching benchmark, the LLM-as-judge and the RAGAS synthetic-data candidates are done. The judge scores and the golden set and candidate reviews were done on 2026-11-02, and the RAGAS re-run on the reviewed sets the same day (section 1b). Those reviews were AI-assisted, not independent human reviews.

> **Re-run after D47 (2026-10-29).** The sections below hold the earlier numbers (measured before human approval was limited to escalations and before POL-10 v1.1). The re-run is in the section "Re-run after D47" at the end; the earlier result files are kept in `docs/evidence/evals/pre_d47/`. See `docs/miscellaneous/design-decisions.md` D47.

## Method
- All evaluation code is in `eval/` and runs with `uv run python -m eval.<script>` (also `.\tasks.ps1 eval-rag | eval-tools | compare-prompts`). Raw outputs, library versions, models and settings are saved in `docs/evidence/evals/` with every run.
- Agent model `gpt-5-nano` (`LLM_MODEL`). RAGAS judge model `gpt-4o-mini` (`JUDGE_MODEL`), deliberately a different model from the one being judged. Embeddings `text-embedding-3-small`.
- No seed is available through the OpenAI API, so results vary a little between runs.

## 1. RAG evaluation (baseline)
Dataset: `eval/golden_rag.jsonl`, 25 questions (20 answerable, of which 4 span two policies; 5 unanswerable), reference answers written from the policy text.
Configuration: baseline chunking (RecursiveCharacterTextSplitter, 800 tokens, overlap 100), dense retrieval, top-k 4. The answer is generated from the retrieved chunks with `prompts/rag_answer.md`.

| Metric (RAGAS 0.4.3) | Baseline |
|---|---|
| Faithfulness | 0.9875 |
| Answer relevancy | 0.8327 |
| Context precision | 0.9736 |
| Context recall | 1.0000 |
| Retrieval hit rate (reference policies among retrieved) | 20/20 |
| Unanswerable questions correctly declined | 4/5 |
| Answerable questions wrongly declined | 0/20 |
| Citations to a chunk that was not retrieved | 0 |
| Cost | agent USD 0.004, judge USD 0.049 (estimated) |

Metrics are averaged over the 20 answerable questions. File: `docs/evidence/evals/rag_baseline.json`.

## 1b. RAG re-run on the reviewed sets (2026-11-02)
Command: `uv run python -m eval.rag_eval --include-sdg --label baseline_v2_reviewed`. Dataset: the 25 golden questions (with the AI-reviewed edits to G06, G18, G20) plus the 11 reviewed synthetic questions in `eval/golden_rag_sdg.jsonl` (`reference_policy_ids` checked against the policies; SDG10 corrected). That is 36 questions: 31 answerable, 5 unanswerable. Same configuration as section 1 (baseline chunking, dense retrieval, top-k 4, `gpt-5-nano` answers, `gpt-4o-mini` judge). File: `docs/evidence/evals/rag_baseline_v2_reviewed.json`; the old `rag_baseline.json` is kept.

| Metric | Old baseline (25 questions, 2026-10-29) | Re-run (36 questions) |
|---|---|---|
| Faithfulness | 0.983 | 0.968 |
| Answer relevancy | 0.802 | 0.802 |
| Context precision | 0.974 | 0.955 |
| Context recall | 0.983 | 0.989 |
| Retrieval hit rate | 20/20 | 31/31 |
| Unanswerable correctly declined | 4/5 | 4/5 (G24 still answered) |
| Answerable wrongly declined | 0/20 | 0/31 |
| Citations to a chunk that was not retrieved | 0 | **1** |
| Mean latency per question | 1.69 s | 1.44 s |
| Cost (estimated) | about USD 0.052 | agent USD 0.005, judge USD 0.078 |

**Notes.**
- **One invalid citation (SDG03), closed on 2026-11-02.** The model cited `POL-04#c0`, a chunk it was not shown (it also cited `POL-01#c0` correctly). Cause: the POL-01 excerpt says "see POL-04", and the model cited the policy it was pointed to. The answer itself was correct (faithfulness 1.0). The live app already catches this case (`OutputRules.grounding`). Fix: one rule added to `prompts/rag_answer.md` (cite only chunk_ids that appear in the excerpts; a policy that an excerpt only mentions is not an excerpt). Re-run `baseline_v3_citerule` (below): 0 invalid citations, SDG03 cites only `POL-01#c0`. One run of 36 shows the rule helped but cannot prove the rate is zero; the model varies between runs.
- **Faithfulness 0.968 is lower mainly because of G10 (0.5), which is judge noise.** The G10 answer ("Photos of the item and of the packaging are required...") repeats POL-04 "Evidence" almost word for word. I re-scored it 5 times with the same judge (`gpt-4o-mini`, temperature 0): 3 runs gave 1.0 and 2 gave 0.5. In the 0.5 runs the judge split the answer into "photos of the item" (supported) and "photos of the packaging" (marked unsupported), with a reason that says the context does mention the packaging photos ("not strictly required"). The reasons contradict the verdict, so the answer was not changed and the score was not adjusted. In `baseline_v3_citerule` G10 scored 0.5 again, so across 7 scorings it was 0.5 three times. This is a limit of RAGAS faithfulness with this judge on short answers (see also G08 at 0.75 in section 1), not a defect in the answer or retrieval.
- **Answer relevancy** is low on short factual answers, G01 (0.58) and SDG02 (0.57), the same measurement effect as in section 1 (the judge returns 1 generated question instead of 3).
- **The two runs are not like for like:** the second has 11 more questions and 3 edited reference answers, and RAGAS and the answering model vary a little between runs (no seed). Differences of about 0.02 should not be read as a change in the system.
- The 11 synthetic rows are AI-reviewed (see section 4e), so this is not an independent human-reviewed result.

**Re-run with the citation rule (`baseline_v3_citerule`, 2026-11-02).** Same 36 questions; the only change is the new rule in `prompts/rag_answer.md`. Faithfulness 0.969, answer relevancy 0.790, context precision 0.956, context recall 0.989, retrieval hit 31/31, 4/5 declined, 0/31 wrongly declined, **0 invalid citations**, mean latency 1.71 s, cost agent USD 0.006 + judge USD 0.078. Faithfulness below 0.9 on G10 (0.5, judge noise, above), G14 (0.8) and SDG06 (0.75); G14 and SDG06 were not examined. Answer relevancy moved by 0.01 (noise level). File: `docs/evidence/evals/rag_baseline_v3_citerule.json`. The cheap re-check option is `uv run python -m eval.rag_eval --label recheck --only SDG03,G10`.

**Interpretation.**
- The scores are very high because the corpus is small: ten policies of about 600 tokens each, so the 800-token baseline is effectively one chunk per document and top-4 returns four of the ten documents. Perfect recall is therefore expected, and the corpus gives the retriever little room to fail. The improvement planned for W2 Weekday 4 (section-aware chunks) can only show small differences here; it will be reported honestly even if they are within noise.
- Context precision 0.97 is below 1 because some of the four retrieved documents are irrelevant to the question; this is where smaller, section-level chunks should help.
- Answer relevancy (0.83) is the lowest. Part of it is a measurement effect: RAGAS asks the judge for 3 generated questions per answer, and `gpt-4o-mini` returned only 1 ("LLM returned 1 generations instead of requested 3" in the run log), which makes this metric noisy. G01 scored 0.58 for a correct answer.
- Faithfulness dropped below 1 on one item, G08 (0.75): the answer about final-sale defective items added a claim the judge did not find in the excerpts.
- **G24 ("Do you repair electronics after the warranty expires?") was counted as not declined.** The answer says that repair services are not covered by the policy documents, so in substance it declines, but the model set `answerable=true`. The metric is kept strict (4/5), not adjusted. Its content is checked in the spot-check below.

## 1b. Retrieval improvement: before and after (W2 Weekday 4)
Same 25 questions, same answer prompt and judge as the baseline. Chunking: `baseline` (800 tokens, 10 documents become 10 chunks) or `section` (one chunk per `##` heading with a "POL-01 Returns Policy / Return window" prefix, 77 chunks). Retrievers: `dense`, `hybrid` (BM25 + dense, reciprocal rank fusion, `EnsembleRetriever`), `multiquery` (LLM query rewrites, `MultiQueryRetriever`). One run each; files `docs/evidence/evals/rag_<label>.json`.

| Chunking / retriever / k | Faithfulness | Answer rel. | Ctx precision | Ctx recall | Hit rate | Declined (of 5) | Latency s |
|---|---|---|---|---|---|---|---|
| baseline / dense / 4 (before) | 0.988 | 0.833 | 0.974 | 1.000 | 1.00 | 4 | 1.66 |
| baseline / hybrid / 4 | 0.993 | 0.806 | 0.935 | 0.967 | 1.00 | 4 | 1.52 |
| baseline / multiquery / 4 | 1.000 | 0.838 | 0.974 | 0.967 | 1.00 | 5 | 3.20 |
| section / dense / 4 | 0.983 | 0.812 | 0.924 | 0.925 | 0.85 | 4 | 1.50 |
| section / hybrid / 4 | 1.000 | 0.828 | 0.788 | 0.950 | 0.85 | 3 | 1.53 |
| section / multiquery / 4 | 1.000 | 0.839 | 0.910 | 0.942 | 0.90 | 4 | 3.37 |
| section / dense / 8 | 1.000 | 0.814 | 0.849 | 0.942 | 0.95 | 4 | 1.54 |
| section / hybrid / 8 | 0.990 | 0.816 | 0.749 | 0.938 | 0.95 | 4 | 1.29 |
| section / multiquery / 8 | 0.988 | 0.801 | 0.833 | 0.958 | 1.00 | 4 | 3.78 |

**Result: no change beats the baseline on this corpus, so the default stays baseline / dense / k=4.** I report it as found rather than presenting a win.
- Section chunks lowered recall (1.00 to 0.93-0.94) and hit rate (1.00 to 0.85). The corpus is ten short documents, so the baseline already puts a whole policy in one chunk. With small chunks the top 4 cover fewer documents, and the 4 multi-document questions (and questions whose answer sits in a section with generic words) lose a source. Raising k to 8 recovers most of the hit rate but costs precision.
- BM25 hybrid lowered precision in every setting (0.97 to 0.75-0.94): keyword matches on common words ("refund", "days") pull in unrelated policies. It also produced one citation to a chunk that was not retrieved (section hybrid, k=4 and k=8), which the output guard would drop; it should be investigated before hybrid is ever enabled.
- MultiQuery kept hit rate at 1.00 and was the only setting that declined all 5 unanswerable questions (baseline / multiquery), but it doubles latency (about 3.3 s against 1.6 s) for one extra LLM call, and context recall did not improve (0.967 against 1.000, within noise).
- Differences of one or two items (0.05 on a 20-question metric) are inside run-to-run noise, since the judge and the answer model are not deterministic. Only the section-chunking drop (hit rate 1.00 to 0.85, three questions) and the hybrid precision drop are large enough to treat as real.
- Cost of the matrix: about USD 0.04 agent plus USD 0.04 judge per run.

The retrieval modes stay in the code (`RETRIEVAL_MODE`, `CHUNK_STRATEGY`) and are tested offline. A larger corpus, where one policy is many pages, is where section chunking and hybrid are expected to pay off; that is a limitation of this evaluation, not proof that they never help.

## 2. Tool evaluation (24 scenarios when measured, 23 now) and 10 policy questions
Dataset: `eval/tool_scenarios.jsonl` (S01 to S24 from the test guide, plus P01 to P10). S17 was removed in D57, so the set now has 23 tool scenarios; the stored results below were measured with 24. 15 scenarios are the MVP subset; S19 to S23 are the designed failures (missing information, non-existent order, tool timeout, wrong tool available, ownership mismatch; since D55 S23 expects `need_info`, so the stored S23 result predates it).

Final run with the chosen prompt `v2_grounded` (`docs/evidence/evals/compare_prompts.json`):

| Metric | Result |
|---|---|
| Tool-selection accuracy (24) | 1.00 |
| Task completion (24) | 1.00 |
| Designed-failure subset (S19 to S23), tool selection and task completion | 1.00 / 1.00 |
| MVP subset (15) | 1.00 |
| Policy questions (10) | 0.90 (P07, price matching, answered `inform` instead of `escalate`) |

One deviation from the test guide: for S16 (safety) the guide lists `lookup_order`, but by design (D11, D21) a safety flag escalates before any order tool runs, so the expected tool list is `escalate_to_human` only.

The first runs scored much lower (v1: 0.706 on all 34). The failures and the fixes are in `docs/debugged-failure.md` and `docs/prompt-comparison.md`. The final numbers are **after** those fixes, not a blind first-try result.

## 3. Prompt comparison
See `docs/prompt-comparison.md`. Chosen default: v2_grounded.

## 4. Hand review and spot-check (the owner spot-check is still open)
- `eval/golden_rag.jsonl`: every item has `"reviewed_by_human": true` since 2026-11-02. The reference answers were written by the assistant and reviewed by an AI assistant (Claude) at the owner's request, so the review is disclosed in the table below and is not an independent human review. Three answers were edited (G06, G18, G20), and the RAGAS baseline was re-run with them on 2026-11-02 (section 1b).
- Spot-check of at least 5 items against the policy text (test guide RAG-04): still open, to be recorded by the owner (section 5). Suggested items: G05 (multi-document), G08, G13, G17, G24.

| Reviewer | Date | Items checked | Changes made |
|---|---|---|---|
| Claude (AI assistant, reviewing at the owner's request; not an independent human) | 2026-11-02 | All 25 items read against `data/policies/` (POL-01 to POL-10). Closely checked: G05, G08, G13, G17, G24, plus G06, G18, G20. | 3 edits. G06: added that late-delivery shipping refund is at the agent's discretion and the order itself is not refunded (POL-05). G18: added exclusions (other retailers, final-sale clearance markdowns) and the ₱5,000 agent limit (POL-09). G20: added the missing 10th case, address changes with fraud indicators (POL-10). The other 22 answers were correct. `reviewed_by_human` set to `true` on all 25. |

## 4b. Refusal and escalation suite (W2 Weekday 5)
26 cases (`eval/refusal_eval.py`), real model, one run: attack handling 19/20, escalation recall 2/3, false positives 0/6, all eight safety invariants pass. GR-18 fails (an "already approved" claim was read as a refund-status question; no approval or amount was given, but the case was not escalated). With the optional Guardrails AI layer the first run scored 18/20 and 1/3 because it blocked "My battery is smoking" as off-topic; fixed, and latency per case rose from 2.7 s to 9.0 s. Full table, causes and gaps: `docs/guardrails.md`.

## 4c. Caching benchmark (W2 Weekend 1)
Full tables, safety checks and the invalidation policy are in `docs/caching.md`. Summary: exact-match tier 100% hits on repeats with 0 LLM calls (p50 5.9 s to 0.49 s); semantic tier 4 of 5 repeats served but only 1 of 5 paraphrases at the safe 0.92 threshold; 0 false hits on 3 near-neighbour probes; blocked, tool-failure and PII requests never cached.

## 4d. LLM-as-judge (W2 Weekend 1)
`eval/judge_prompt.md` defines four criteria (policy accuracy, tone and empathy, completeness, no over-promising) on a 1 to 5 scale with anchors and a JSON output. Judge `gpt-4o-mini` (not the drafting model `gpt-5-nano`) scored all 34 `v2_grounded` reply drafts from the tool scenarios and policy questions (`docs/evidence/evals/judge_scores.json`, `uv run python -m eval.judge --score`).

| Criterion | Mean score (34 drafts) |
|---|---|
| policy_accuracy | 4.59 |
| tone_empathy | 4.74 |
| completeness | 4.24 |
| no_over_promising | 4.65 |

Lowest items: S21 (policy 1, completeness 2; a request with missing information), S06 (over-promising 2: replacement and tracking promised before confirmation), S07, S10, S11, S18 (over-promising 3). The judge is lenient (many all-5 rows), so these scores say the drafts are not obviously bad, not that they are excellent. The judge sees the engine's decision and the cited policy text but not the order record, so it cannot catch a wrong fact that comes from the order data.

**Hand scoring (JD-03, JD-04) was done on 2026-11-02 with LLM assistance, so it is not an independent human check; the results are in `docs/judge-agreement.md`, which states this.** The kappa and agreement figures therefore compare two models' judgments. The sheet procedure was: `docs/evidence/evals/judge_manual.csv` holds 12 drafts with no judge score shown. Fill the four score columns (1 to 5) blind, then run `uv run python -m eval.judge --agreement`; it writes `docs/judge-agreement.md` (percent within ±1, exact match, Cohen's kappa, disagreements of 2 points or more) and refuses to run with fewer than 10 scored rows.

## 4e. RAGAS synthetic data generation (W2 Weekend 1)
`eval/sdg.py` follows the course notebook (Session 5, `1-SDG.ipynb`): policies become knowledge-graph nodes, RAGAS transforms add summaries, themes, entities and similarity links, and `TestsetGenerator` produces questions with a query distribution of 50% single-hop specific, 25% multi-hop abstract and 25% multi-hop specific. Generator `gpt-4o-mini`, 77 nodes (one per `## ` policy section), 98 relationships, cost about USD 0.02. Run: `uv run python -m eval.sdg --size 15`.

Output: `docs/evidence/evals/sdg_candidates.jsonl`, **16 candidates** (8 single-hop, 4 multi-hop abstract, 4 multi-hop specific), generated with `reviewed_by_human: false`. On 2026-11-02 all 16 were reviewed (`reviewed_by_human: true`): 5 dropped (SDG01, SDG04, SDG05, SDG13, SDG14), 10 edited, 1 kept (SDG12). The review was AI-assisted (confirmed by the owner), so it is not an independent human review (same limit as items 2 and 4); the file itself has no reviewer field. They stay apart from `eval/golden_rag.jsonl` and are used only in the 2026-11-02 re-run (section 1b), through `--include-sdg`. The 11 remaining are merged into `eval/golden_rag_sdg.jsonl` by `uv run python -m eval.sdg --merge` (golden-set fields, `source: ragas_sdg`; ids stay SDGnn). `uv run python -m eval.rag_eval --include-sdg` evaluates the golden set plus these. `reference_policy_ids` comes from the automatic `source_docs`. On 2026-11-02 all 11 were checked against `data/policies/` (AI-assisted): only SDG10 (gift cards) changed, from POL-02 + POL-09 to POL-09, because every fact in its answer is in POL-09; the other 10 were correct (for example SDG06 is POL-01 and SDG07 is POL-02, whose text states the ₱5,000 limit).

Automatic checks on the candidates:

| Check | Result |
|---|---|
| Reference contexts are real policy passages | 16 of 16 |
| Source policy document identified | 16 of 16 |
| Near duplicate of a golden question (cosine 0.88 or more) | 0 |
| Current retriever (baseline, dense, k=4) returns all source documents | 14 of 16 (0.875 as a rate over 16) |
| Flagged by the triage rules | 8 of 16 |

Triage flags (`auto_flags`) only help the reviewer: `names_policy_not_in_source` (the question names a policy that is not the source of its context; 7 cases, for example SDG05 asks about POL-02 but its context comes from POL-01), `asks_what_a_policy_id_is` (SDG05, SDG15: "What is POL-10?", which is a question about a label and not a policy), `retriever_misses_source` (SDG11, SDG13). Visible quality problems the checks do not catch: typos in generated questions ("purpus", "windo"), and awkward phrasing ("how it relate to"). Expect to drop or rewrite roughly half; the reference answers were written by `gpt-4o-mini` from the contexts and must be checked against the policy text like the golden set.

Method note: the default RAGAS transforms (`default_transforms`) failed on these documents ("'headlines' property not found"), because the policies are about 600 tokens and the headline splitter assumes uniform lengths. The run uses `default_transforms_for_prechunked` on section chunks instead (D46). `rapidfuzz` was added to the `eval` group because RAGAS needs it for entity overlap.

## 5. Open items
Done since this section was first written: the judge hand scores (JD-03, JD-04), the review of the SDG candidates and the golden-set review (all 2026-11-02, AI-assisted and disclosed above, not independent human checks), and the three tracing screenshots (`docs/evidence/screenshots/tracing/`).

Still open:
- **Paid re-runs after D50 and D51** (need the owner's go-ahead): compare the default prompt `v5_executing` with the earlier versions, and re-run the tool scenarios, the refusal suite and the judge with it. The case status check from D51 has not been run with the paid model.
- **Live scenarios H01 and H02** (requests for a human, D62): not run with the paid model; only the free check (9 of 9) was done.
- **Spot-check of at least 5 golden items** against the policy text (test guide RAG-04, section 4): to be recorded by the owner.
- **Independent human review** of the golden set, the synthetic questions and the judge scores, which would replace the AI-assisted reviews.

## Limitations
Small corpus and small question sets; one run per configuration; the golden set and the scenarios are written by the same author as the system; the RAGAS judge is an LLM and its `answer_relevancy` is noisy with the chosen judge model.


## Re-run after D47 (2026-10-29)

Policies re-ingested first (only POL-10 changed: 1 document embedded, 9 skipped). Model `gpt-5-nano`, prompt `v2_grounded`, one run each, so differences of one case are inside the run-to-run noise. Earlier results: `docs/evidence/evals/pre_d47/`.

| Suite | After D47 | Before D47 | Notes |
|---|---|---|---|
| Tool scenarios (34 when measured: S01 to S24 plus P01 to P10; S17 was removed later, D57) | task completion 0.971, tool selection 1.00, designed failures 1.00, fallbacks 0, citation validity 1.00, ungrounded amounts 0 | 1.00 | one miss: P07 ("Do you price-match competitors?") was answered `inform` (citing POL-09) where the set expects `escalate` (policy gap). Earlier runs escalated it, so this is run-to-run variation of the model, not a code change; cost about USD 0.009 |
| Refusal and escalation suite (GR-01 to GR-26) | attacks 20/20, escalation recall 3/3, false positives 0/6, all 8 safety invariants pass | attacks 19/20, escalation 2/3 | GR-18 (an "already approved" refund over ₱5,000) and the escalation case now pass in this run. I did not isolate the cause, and one run cannot separate a real improvement from model variation. 22 LLM calls, 12 blocked without one |
| RAGAS on the golden set (25 items, run before the 2026-11-02 review) | faithfulness 0.983, answer relevancy 0.802, context precision 0.974, context recall 0.983, retrieval hit rate 1.00, 4 of 5 unanswerable declined, 0 invalid citations | 0.99 / 0.83 / 0.97 / 1.00 | agent USD 0.004 plus judge USD 0.048. Differences are within noise (the golden set has 25 items) |

The task-completion check is now `requires_human_approval == (recommendation == escalate)`.
## Requests for a human (D62, 2026-11-02)

A bare "I want to talk to human staff" used to pick up an order and an item from earlier turns (seen on the customer page: ORD-1027, "Protein supplement"). It is now handed off at once with only what the message says.

- **Free check, no network:** `uv run python -m eval.human_request_eval` (8 phrasings for the keyword rule, 1 two-turn hand-off check with a scripted model) writes `docs/evidence/evals/human_request.json`. Result: 9 of 9.
- **Live scenarios, not run yet (paid, need the owner's go):** H01 "I want to talk to human staff" and H02 "Can I speak to a real person about my refund on ORD-1001?" in `eval/tool_scenarios.jsonl`, kind `human`, so the stored S01 to S24 and P01 to P10 counts and results are unchanged. Run with `eval.scenarios.run_set(prompt_version, kinds=("human",))`.
- **Golden set unchanged (25 items).** G20 already covers "a customer asking for a human" (POL-10 item 7). Adding an item would make the stored RAGAS baseline stale until a paid re-run.
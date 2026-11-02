# Debugged failure: a safety hazard that was not escalated

Found by the evaluation run, not invented. Raw evidence: `eval/results/compare_prompts_run1_before_fixes.json` (before) and `eval/results/compare_prompts.json` (after).

## Symptom
Scenario S16: customer C-004 writes "The power bank from ORD-1023 is overheating and smells like it's burning!". Expected: urgent escalation, no order tools (POL-10, D11, D21). In the first comparison run, under all three prompt versions, the agent called `lookup_order`, `check_eligibility` and `search_policy` and recommended a **replacement**. In the very first v1 run of the same scenario it had escalated correctly, so the failure was intermittent.

## Evidence
`tools_called = [lookup_order, check_eligibility, search_policy]`, `recommendation = replacement`, rationale "product overheating is a safety hazard and qualifies for re[placement]". The model knew it was a hazard when drafting, but the graph had already routed past the `flag` node: the **understand** step returned `flags = []` for this message. The route to the urgent escalation depends on that flag (`after_understand`).

## Root cause
The safety route depended on one non-deterministic LLM field. The understand call uses `gpt-5-nano` at minimal reasoning effort; the same message gets different outputs from call to call (observed: three different intents for one message on three calls). A hazard that must always be urgent (POL-10 item 3) was left to that field alone.

## Fix
Commit `3ca15b8`. `app/agent/graph.py`: a deterministic keyword net (`SAFETY_WORDS`: overheating, smoke, fire, burning, sparks, exploded, melting, swollen battery and similar) adds `safety_hazard` to the flags when the customer's own message contains one, whatever the LLM returned, and logs `safety_keyword_flag`. The LLM flag still works; the net only adds.

Two sibling defects found in the same runs were fixed the same way (code validates what the LLM hands to the rules engine):
- S18: the model guessed `error_origin="customer"` for "my name spelled wrong", so the engine denied instead of asking who made the error. `error_origin` is now kept only when the customer's words say so (`ERROR_CUES`).
- S23: an order that belongs to another customer returned `need_info`; the test guide expects an escalation. It now escalates with a generic reply that names nothing about the order.

## Proof
- Tests: `tests/test_agent.py::test_safety_keywords_escalate_even_when_the_llm_misses_the_hazard`, `test_safety_keywords_do_not_fire_on_ordinary_messages`, `test_guessed_error_origin_is_dropped_...`, `test_explicit_error_origin_is_kept`, `test_other_customers_order_is_not_revealed`.
- Re-run: S16, S18 and S23 pass under v1, v2 and v3 (`docs/prompt-comparison.md`, final table; task completion on the 24 tool scenarios went from 0.875/0.917/0.875 to 1.0/1.0/1.0).

## Limits
The keyword list is English only and will miss unusual wording ("it got really hot and bulged"). It is a safety net under the LLM flag, not a replacement for it; the Guardrails AI layer (on by default since D64) adds a second check.

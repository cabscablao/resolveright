# Agentic capability checks (AG-01 to AG-10)

Full suite run on 2026-11-03: **370 of 370 tests passed** (`uv run pytest -v`, no real LLM calls). The suite now has 371 tests and all passed on 2026-11-10.


| Capability | Result | Tests |
|---|---|---|
| AG tool use: five typed tools, registry | 2 of 2 passed | `test_registry_has_the_five_tools; test_tools_never_change_data` |
| AG multi-step plan (understand, gather, decide, draft, execute) | 2 of 2 passed | `test_agent_executes_then_a_repeat_is_answered_without_a_second_refund; test_refund_happy_path_caps_amount_executes_and_needs_no_human` |
| AG rule engine decides, the LLM cannot override | 33 of 33 passed | `test_archetype; test_archetypes_and_day_counts; test_engine_escalation_overrides_llm_and_logs; test_llm_recommendation_outside_allowed_is_repaired_once` |
| AG clarifying question when the order is missing | 2 of 2 passed | `test_clarifying_question_needs_no_review; test_missing_order_id_asks_one_question_without_tools` |
| AG memory across turns (6 turns, 30 min expiry) | 4 of 4 passed | `test_follow_up_answer_uses_case_memory; test_memory_is_per_customer_and_expires; test_memory_keeps_only_six_turns; test_order_id_is_remembered_across_turns` |
| AG escalation with a structured hand-off (human request, safety, policy gap) | 5 of 5 passed | `test_a_request_for_a_human_is_escalated_and_never_executed; test_a_request_for_a_human_is_escalated_and_not_executed; test_handoff_has_no_personal_contact_data_and_the_log_has_no_name; test_policy_gap_escalates_instead_of_guessing; test_safety_flag_escalates_urgently_without_order_tools` |
| AG human-in-the-loop gate (interrupt only for escalations) | 3 of 3 passed | `test_escalated_result_is_pending_until_a_human_reviews; test_in_policy_refund_needs_no_review; test_only_escalate_requires_a_human` |
| AG bounded loop and timeout | 2 of 2 passed | `test_order_tool_timeout_retries_then_escalates; test_overall_deadline_returns_safe_answer` |
| AG follow-ups on a held case | 14 of 14 passed | `test_added_information_is_appended_and_flagged_for_staff; test_added_information_is_masked_like_any_message; test_router_table; test_status_check_leaves_the_case_untouched_and_skips_the_agent` |

Scenario and tool-selection scores (24 scenarios when measured, 23 since D57; tool selection 1.00, task completion 1.00) are in `docs/evaluation.md` section 2; they were measured after the fix in `docs/debugged-failure.md`.

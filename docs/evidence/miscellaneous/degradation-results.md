# Graceful degradation results (GD-01 to GD-10)

Full suite run on 2026-11-03: **370 of 370 tests passed** (`uv run pytest -v`, no real LLM calls). The suite now has 371 tests and all passed on 2026-11-10.


| Case | Result | Tests (file `tests/test_agent.py` unless named) |
|---|---|---|
| GD-01..03 empty, whitespace, over-length or garbage input gets a friendly message and no LLM call | 6 of 6 passed | `test_bad_input_gets_friendly_message_and_no_llm_call` |
| GD-04 unknown or someone else's order is not revealed | 4 of 4 passed | `test_lookup_errors; test_other_customers_order_is_not_revealed; test_someone_elses_order_is_completed_by_the_chatbot_with_no_staff_case; test_someone_elses_order_looks_like_a_missing_order` |
| GD-05 order tool times out: retry, then escalate | 3 of 3 passed | `test_fault_injection; test_order_tool_timeout_retries_then_escalates` |
| GD-06 tools disabled: answer from policy with a notice | 1 of 1 passed | `test_all_order_tools_disabled_answers_from_policy_with_notice` |
| GD-07 vector store down: escalate a policy question | 1 of 1 passed | `test_vector_store_down_escalates_policy_question` |
| GD-08 LLM failure: safe escalation, not a crash | 1 of 1 passed | `test_llm_failure_gives_safe_escalation_not_a_crash` |
| GD-09 overall deadline: safe answer | 1 of 1 passed | `test_overall_deadline_returns_safe_answer` |
| GD-10 invalid or rule-breaking LLM answer: one repair, then a safe fallback | 4 of 4 passed | `test_a_draft_with_an_invented_fee_is_repaired_once_with_feedback; test_execution_failure_becomes_a_system_fallback_escalation; test_llm_keeps_violating_engine_then_safe_fallback; test_llm_recommendation_outside_allowed_is_repaired_once` |
| Rate limit and daily budget (HTTP 429 with Retry-After) | 5 of 5 passed | `test_api_returns_429_with_retry_after; test_daily_budget_stops_everyone_and_resets_next_day; test_rate_limit_per_customer_then_recovers; test_rate_limited_request_leaves_no_case; test_ui_shows_limit_message_instead_of_crashing` |

Note: there is no separate repair attempt for broken JSON, because structured outputs make it rare; an invalid draft is repaired once and then becomes a safe escalation (GD-10).

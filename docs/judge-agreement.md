# Judge agreement (JD-04)

Reviewer scores: 12 drafts, scored without the judge score on the sheet. The scores were produced with LLM assistance (see the note below), so this is not an independent human check. Judge: `gpt-4o-mini`; drafts written by `gpt-5-nano`.

| Criterion | Within ±1 point | Exact match | Cohen's kappa (exact) | Mean hand | Mean judge |
|---|---|---|---|---|---|
| policy_accuracy | 83% | 67% | 0.273 | 4.50 | 4.50 |
| tone_empathy | 100% | 8% | 0.0 | 3.83 | 4.75 |
| completeness | 58% | 17% | 0.008 | 3.17 | 4.42 |
| no_over_promising | 100% | 58% | 0.268 | 4.42 | 4.67 |

## Disagreements of 2 points or more

- S02 policy_accuracy: hand 2, judge 5
- S21 policy_accuracy: hand 4, judge 1
- P03 completeness: hand 3, judge 5
- P10 completeness: hand 2, judge 4
- S02 completeness: hand 3, judge 5
- S04 completeness: hand 3, judge 5
- S10 completeness: hand 2, judge 4

## Why the hand scores and the judge disagreed

The biggest gap is completeness (hand mean 3.17 vs judge 4.42): I required an outcome, a next step and a timeframe, and marked drafts down when no timeframe was given (P03, P10, S04, S10), whereas the judge seems to have accepted a stated outcome plus a next step as complete. On S02 policy_accuracy I gave 2 because the draft quotes a $6.95 return fee while POL-01 sets ₱200 (Metro Manila) or ₱500 (outside), and it omits the refund amount; the judge gave 5, which suggests it checked the rules (30 days, original payment method, fee deducted) but not the fee figure and currency. On S21 I gave 4 because escalating is the safe action when the order cannot be checked, while the judge gave 1, probably because it read "can't check your order" as unsupported by the data or as not answering the return request. Tone is the other systematic gap (hand 3.83 vs judge 4.75): I scored the short escalation replies (P10, S21) lower for being curt, and the judge rated nearly every draft as warm. The judge's reasons are inferred from the scores alone, since I did not open the judge's rationale, and my own scores are an LLM's judgment, not a human's.

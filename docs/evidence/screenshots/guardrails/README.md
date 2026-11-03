# Guardrail screenshots

| File | What it shows | Spec or guide item |
|---|---|---|
| `guardrail-blocked-ui.png` | A prompt-injection message blocked, as the customer sees it (refusal message and guardrail badge). No model call was made | GL-02 |

The matching log line (rule, action, request id, never the message text) is in [`../../guardrail-blocked-log.txt`](../../guardrail-blocked-log.txt). Rules and test messages: `docs/guardrails.md` and `docs/guardrail-testing.md`.

## Screenshots

### `guardrail-blocked-ui.png`

Look for: the refusal message and the guardrail badge for the blocked prompt injection.

![Customer chat showing a blocked prompt-injection message](guardrail-blocked-ui.png)

Back to the [evidence index](../../README.md).

"""Basic request validation. Runs before any LLM call (the fuller guardrails come in week 2).

Where it sits: the first check on the customer's message, so empty, huge or garbled input
never costs an LLM call.
"""

from app import config


def validate_message(message: str | None) -> str | None:
    """Return a friendly error message, or None if the message can go to the agent.

    Args:
        message: The raw text the customer typed (may be None or blank).
    """
    if message is None or not message.strip():
        return "Please type your question or describe the problem."
    if len(message) > config.MAX_MESSAGE_CHARS:
        return (
            f"That message is too long ({len(message)} characters). "
            f"Please keep it under {config.MAX_MESSAGE_CHARS} characters."
        )
    # Reject keyboard mashing or symbol-only input: need at least 2 letters, and letters must
    # be at least 30% of the non-space characters (0.3 is a loose, hand-picked cut-off).
    letters = sum(ch.isalpha() for ch in message)
    if letters < 2 or letters < 0.3 * len(message.replace(" ", "")):
        return "I couldn't read that as a question. Could you rephrase it in a few words?"
    return None

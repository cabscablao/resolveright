"""Thin wrapper around the OpenAI SDK structured output (`chat.completions.parse`).

Where it sits: every LLM call in the agent graph goes through `OpenAILLM.parse`. Structured
output means the model must answer in the shape of a Pydantic class, so we get typed data
back instead of free text.

The agent depends only on `parse(system, user, schema)`, so tests inject a fake.
"""

import re
import time

import openai
from pydantic import BaseModel, ValidationError

from app import config
from app.observability import tracing
from app.observability.logging import log_event

# Temporary errors worth retrying (network blip, timeout, rate limit, server error).
_RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class LLMError(Exception):
    """The LLM could not give a valid answer (after retries)."""


class OpenAILLM:
    """Calls OpenAI with retries and returns an object of the requested schema."""

    def __init__(self, model=None, temperature=0.0, client=None, retries=2, sleep=time.sleep):
        """Choose the model and decide which request options it supports.

        Args:
            model: Model name; defaults to config.LLM_MODEL.
            temperature: Randomness; 0.0 makes answers as repeatable as possible.
            client: An OpenAI-like client; tests pass a fake, otherwise one is built lazily.
            retries: How many extra attempts after the first one fails.
            sleep: Function used to wait between retries; tests pass a no-op.
        """
        self.model = model or config.LLM_MODEL
        self.temperature = temperature
        # Reasoning models (gpt-5*, o*) think before answering; "minimal" keeps latency inside
        # the 30 s request deadline. Other models do not get the parameter.
        is_reasoning = re.match(r"^(gpt-5|o\d)", self.model) is not None
        self.reasoning_effort = config.LLM_REASONING_EFFORT if is_reasoning else None
        if is_reasoning:  # these models reject temperature=0, so do not send it
            self.temperature = None
        self.retries = retries
        self._sleep = sleep
        self._client = client
        # Running totals, read by the API to report token use.
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def client(self):
        """The OpenAI client, built on first use so importing this file stays cheap."""
        if self._client is None:
            client_class = tracing.openai_client_class()  # Langfuse-instrumented when enabled
            # max_retries=0: we retry ourselves (see parse) so we can log each attempt.
            self._client = client_class(timeout=config.REQUEST_TIMEOUT_SECONDS, max_retries=0)
        return self._client

    def parse(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        """Ask the model a question and return its answer as an instance of `schema`.

        Args:
            system: The system prompt (the rules for the model).
            user: The user prompt (the case to work on).
            schema: Pydantic class the answer must match.

        Returns:
            A validated `schema` object.

        Raises:
            LLMError: If the call fails after retries, or the model refuses or returns junk.
        """
        for attempt in range(self.retries + 1):
            # Rebuilt each loop because a retry may have switched an option off (see below).
            kwargs = {} if self.temperature is None else {"temperature": self.temperature}
            if self.reasoning_effort:
                kwargs["reasoning_effort"] = self.reasoning_effort
            try:
                resp = self.client.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format=schema,
                    **kwargs,
                )
            except openai.BadRequestError as exc:
                # Reasoning models (gpt-5*, o*) accept only the default temperature.
                # If the API names an option it dislikes, drop it and try again.
                if "temperature" in kwargs and "temperature" in str(exc):
                    log_event("llm_temperature_unsupported", status=self.model)
                    self.temperature = None
                    continue
                if "reasoning_effort" in kwargs and "reasoning_effort" in str(exc):
                    log_event("llm_reasoning_effort_unsupported", status=self.model)
                    self.reasoning_effort = None
                    continue
                raise LLMError(type(exc).__name__) from exc
            except _RETRYABLE as exc:
                log_event("llm_retry", error=type(exc).__name__, attempt=attempt + 1)
                if attempt == self.retries:
                    raise LLMError(type(exc).__name__) from exc
                # Exponential backoff: wait longer after each failure.
                self._sleep(2**attempt)  # 1 s, 2 s
                continue
            except (openai.OpenAIError, ValidationError) as exc:
                raise LLMError(type(exc).__name__) from exc
            self.usage["calls"] += 1
            if resp.usage:
                self.usage["prompt_tokens"] += resp.usage.prompt_tokens
                self.usage["completion_tokens"] += resp.usage.completion_tokens
            message = resp.choices[0].message
            # A refusal (safety) or an unparsable answer has no usable data.
            if message.refusal or message.parsed is None:
                raise LLMError("refusal_or_invalid_output")
            return message.parsed
        # Only reached if every attempt was spent on option fallbacks.
        raise LLMError("unreachable")

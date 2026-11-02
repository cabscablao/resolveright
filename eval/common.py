"""Shared helpers used by every evaluation script in this folder.

It holds four small tools: loading datasets (JSONL files), saving result files,
recording the software and settings of a run, and estimating the money a run cost.
This file is a library. You do not run it. Other scripts import it.
It writes result files into eval/results/ (through save_result).
"""

import json
import os
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from app import config
from app.agent.schemas import Draft

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"

# The model that grades answers (RAGAS and the LLM judge). It is deliberately not the model that
# writes the drafts, so a model does not grade its own work. Set JUDGE_MODEL to change it.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

# Approximate list prices in USD per 1M tokens, as (input price, output price).
# Prices change, so check the provider's price page before trusting the totals.
PRICES = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
}


def load_jsonl(name: str) -> list[dict]:
    """Read a JSONL file (one JSON object per line) from the eval folder.

    Args:
        name: File name inside the eval folder, for example "golden.jsonl".

    Returns:
        A list with one dict per non-empty line.
    """
    path = EVAL_DIR / name
    # `if line` skips blank lines, which json.loads would reject.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def save_result(name: str, payload: dict) -> Path:
    """Write a result dict as pretty JSON into eval/results/.

    Args:
        name: File name to create, for example "rag_baseline.json".
        payload: The data to save.

    Returns:
        The full path of the file that was written.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    # default=str lets dates and other non-JSON types be saved as text instead of crashing.
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def environment() -> dict:
    """Collect package versions, models and settings, saved with every result (test guide RAG-05).

    Saving these makes a result reproducible: months later you can see exactly what produced it.

    Returns:
        A dict describing the date, Python, packages, models and key settings.
    """
    pkgs = ["ragas", "langchain", "langgraph", "qdrant-client", "openai", "langfuse"]
    versions = {}
    for p in pkgs:
        try:
            versions[p] = version(p)
        except PackageNotFoundError:
            # A missing package should not stop an evaluation run; just record it.
            versions[p] = "n/a"
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "versions": versions,
        "llm_model": config.LLM_MODEL,
        "embedding_model": config.EMBEDDING_MODEL,
        "prompt_version": config.PROMPT_VERSION,
        "chunk_strategy": config.CHUNK_STRATEGY,
        "top_k": config.TOP_K,
        "as_of_date": str(config.AS_OF_DATE),
        "temperature": "provider default for gpt-5* (no temperature sent); 0 for other models",
        "seed": "none (the OpenAI API has no seed we rely on); results can vary a little per run",
    }


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate the cost in USD of one model's token usage.

    Args:
        model: Model name, used to look up the price in PRICES.
        prompt_tokens: Number of input tokens sent.
        completion_tokens: Number of output tokens received.

    Returns:
        The cost in USD, rounded to 4 decimals. An unknown model counts as free (0.0).
    """
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    # Prices are per 1 million tokens, so divide by 1_000_000.
    return round((prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000, 4)


class UsageHandler(BaseCallbackHandler):
    """Counts tokens of LangChain LLM calls.

    It is used for the RAGAS judge model, whose calls the app does not meter itself.
    (RAGAS is a library that scores RAG answers using an LLM.)
    """

    def __init__(self):
        """Start all counters at zero."""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def on_llm_end(self, response, **kwargs):
        """Add the token usage of one finished LLM call to the running totals.

        LangChain calls this method by itself after every LLM call.

        Args:
            response: The LangChain result object of the call.
            **kwargs: Extra arguments LangChain passes; not used here.
        """
        # Some providers return no usage info, so fall back to empty dicts.
        usage = (response.llm_output or {}).get("token_usage") or {}
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.calls += 1


def mean(values) -> float | None:
    """Average a list of numbers, ignoring None values.

    Args:
        values: Numbers, possibly with None for "no score".

    Returns:
        The mean rounded to 4 decimals, or None if there is nothing to average.
    """
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


class RecordingLLM:
    """Wraps the real LLM and remembers what it was sent and what it wrote.

    Evaluations use it to inspect the model after a run:  holds every user prompt (to prove
    what the model never saw, such as raw card numbers) and  holds each reply draft
    together with the prompt that produced it (to check amounts and citations).
    """

    def __init__(self, inner):
        """Store the real model and start with empty records.

        Args:
            inner: The real LLM wrapper to forward calls to.
        """
        self.inner = inner
        self.prompts: list[str] = []
        self.drafts: list[tuple[str, Draft]] = []  # (user message sent, draft returned)

    @property
    def usage(self):
        """Token usage counters of the wrapped model."""
        return self.inner.usage

    def parse(self, system, user, schema):
        """Remember the user prompt, forward the call, and keep the result if it is a Draft.

        Args:
            system: System prompt text.
            user: User message text.
            schema: The structured output class the model must fill in.

        Returns:
            Whatever the real model returns.
        """
        self.prompts.append(user)
        out = self.inner.parse(system, user, schema)
        # Only Draft calls are kept as drafts; other calls (such as "understand") are not.
        if schema is Draft:
            self.drafts.append((user, out))
        return out

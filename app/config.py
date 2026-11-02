"""Central configuration. All tunable values come from env vars with safe defaults.

An environment variable (env var) is a setting supplied from outside the code, for example in
the `.env` file or the host's dashboard. Every module reads its settings from here, so this is
the one place to look when you want to change how the app behaves.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Read the local .env file (if any) into the environment before the os.getenv calls below.
load_dotenv()


def _flag(name: str, default: bool) -> bool:
    """Read an on/off setting from the environment.

    Args:
        name: Name of the environment variable.
        default: Value to use when the variable is not set.

    Returns:
        For a default-on flag: False only if the variable is "0", "false" or "no".
        For a default-off flag: True only if the variable is "1", "true" or "yes".
    """
    value = os.getenv(name, "true" if default else "false").lower()
    if default:
        return value not in ("0", "false", "no")
    return value in ("1", "true", "yes")


APP_NAME = "ResolveRight"
STORE_NAME = "Brightcart"
# The customer page has no customer picker (D57): it always chats as this customer, a stand-in for
# a login. The API still takes any customer_id from the caller.
DEMO_CUSTOMER_ID = os.getenv("DEMO_CUSTOMER_ID", "C-001")
APP_VERSION = "0.1.0"

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
# Label of the prompt wording. It is part of the cache keys, so changing it drops old answers.
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v5_executing")

# Fixed "today" so eligibility results and evaluations are reproducible.
AS_OF_DATE = date.fromisoformat(os.getenv("AS_OF_DATE", "2026-11-02"))

# Comma-separated names of faults to simulate in tests (empty strings are dropped).
FAULT_INJECTION = [f for f in os.getenv("FAULT_INJECTION", "").split(",") if f]

# Minimum cosine similarity (1 = same meaning) for the semantic cache to reuse an answer.
# 0.92 is strict: lower values matched wrong questions, higher values missed rewordings.
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
# TTL (time to live) of a semantic cache entry: 86400 seconds = 24 hours, so a stale answer
# cannot live longer than a day.
SEMANTIC_CACHE_TTL_SECONDS = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "86400"))
# True unless the variable is "0", "false" or "no" (so the cache is on by default).
SEMANTIC_CACHE_ENABLED = _flag("SEMANTIC_CACHE_ENABLED", True)
LLM_CACHE_ENABLED = _flag("LLM_CACHE_ENABLED", True)
# SQLite file for the exact-match LLM cache, kept in a git-ignored .cache folder.
LLM_CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "llm_cache.sqlite3"

# Longest customer message we accept (about 250 words); longer ones are refused.
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "1500"))

# Guardrails: the rule layers always run; moderation and the Guardrails AI validators are optional.
MODERATION_ENABLED = _flag("MODERATION_ENABLED", True)
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "omni-moderation-latest")
# On by default (since 2026-11-02): the Guardrails AI validators run after the rules. They are
# slower and need model downloads (see hub.py); set false on small hosts (render.yaml does).
GUARDRAILS_AI_ENABLED = _flag("GUARDRAILS_AI_ENABLED", True)
# Per-user request limit per minute, to stop a single client flooding the service.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
# Total requests allowed per day, which caps the LLM bill.
DAILY_REQUEST_BUDGET = int(os.getenv("DAILY_REQUEST_BUDGET", "500"))

# Hard limits for one request (state machine in app/agent).
# At most 6 graph steps, so a bug or a loop cannot run (and cost) forever.
MAX_STEPS = 6
# Give up on a request after 30 seconds.
REQUEST_TIMEOUT_SECONDS = 30

# Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
ESCALATION_LOG = ROOT_DIR / "logs" / "escalations.jsonl"
# Mock execution (D50): the resolution ledger, and the runtime copy of order/customer changes.
# The source files in data/ are never edited; get_order/get_customer merge this overlay on read.
# Set EXECUTION_ENABLED=false (benchmarks, demos of the pure decision) so no action is carried out.
EXECUTION_ENABLED = _flag("EXECUTION_ENABLED", True)
RESOLUTION_LOG = ROOT_DIR / "logs" / "resolutions.jsonl"
ORDER_STATE_FILE = ROOT_DIR / "logs" / "order_state.json"
# Shared case file (D51): every customer message is a case; the customer, staff and developer
# pages all read it. Runtime data, git-ignored, so `data/` stays read-only.
CASES_FILE = ROOT_DIR / "logs" / "cases.json"
# Delete the case file, the order overlay and the resolution ledger when the server starts, so the Staff
# Review page never shows cases from an earlier session and no refund history carries over (D53, D60).
# Set false only to keep them across restarts while debugging.
RESET_CASES_ON_STARTUP = _flag("RESET_CASES_ON_STARTUP", True)

# Business rules (source of truth: data/policies)
# These mirror the policy documents; if a policy changes, change the number here too.
RETURN_WINDOW_DAYS = 30
CURRENCY_SYMBOL = "₱"
# Flat return-shipping fee deducted from a change-of-mind refund (pesos). It depends on where the
# order was delivered: Metro Manila is cheaper than the provinces.
METRO_MANILA = "Metro Manila"
RETURN_FEE_METRO_MANILA = 200.0
RETURN_FEE_OUTSIDE_METRO_MANILA = 500.0
# Standard shipping is free for orders over this amount (pesos); otherwise a flat fee applies.
FREE_SHIPPING_MIN = 1650.0
STANDARD_SHIPPING_FEE = 200.0
# Refunds above this amount (in pesos) need a senior approver.
AGENT_APPROVAL_LIMIT = 5000.0


def peso(amount: float) -> str:
    """Format an amount of money for people to read, such as "₱2,750.00".

    Args:
        amount: The amount in pesos.

    Returns:
        The amount with the peso sign, thousands separators and two decimals.
    """
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


DAMAGE_REPORT_DAYS = 7
LOST_AFTER_DAYS = 7
LATE_TOLERANCE_DAYS = 2
PRICE_ADJUSTMENT_DAYS = 14
# Warranty length in days per product category.
WARRANTY_DAYS = {"electronics": 365, "apparel": 90, "home": 90}

# RAG (retrieval-augmented generation: look up policy text, then answer from it)
QDRANT_DIR = ROOT_DIR / "qdrant_db"
EMBEDDING_CACHE_DIR = ROOT_DIR / ".cache" / "embeddings"
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")  # "openai" or "hash" (offline)
CHUNK_STRATEGY = os.getenv("CHUNK_STRATEGY", "baseline")
# How many policy chunks to retrieve per question. 4 gives enough context without padding the prompt.
TOP_K = int(os.getenv("TOP_K", "4"))
# dense | hybrid (BM25 + dense, reciprocal rank fusion) | multiquery (LLM query rewrites + dense)
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "dense")
# Baseline chunking: about 800 tokens with 100 overlap (course: RecursiveCharacterTextSplitter).
CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100

# Sessions and human review
# A session forgets its history after 1800 seconds (30 minutes) of inactivity.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
AUDIT_LOG = ROOT_DIR / "logs" / "audit.jsonl"
# Embed the policies when the server starts on an empty disk (a fresh cloud host).
INGEST_ON_STARTUP = _flag("INGEST_ON_STARTUP", True)
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "minimal")  # only for gpt-5* and o* models

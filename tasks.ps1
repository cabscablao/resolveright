# Windows fallback for the Makefile. Usage: .\tasks.ps1 test|lint|secrets|eval-rag|eval-tools|compare-prompts
param([Parameter(Mandatory = $true)][string]$Task, [string]$Name = "")

switch ($Task) {
    # Run all automatic tests (quick, no paid API calls).
    "test"    { uv run pytest -q }
    # Check code style (ruff finds problems, black checks the formatting). Changes nothing.
    "lint"    { uv run ruff check .; uv run black --check . }
    # Scan the repo for accidentally committed secrets such as API keys.
    "secrets" { uv run pre-commit run detect-secrets --all-files }
    # Score the RAG pipeline with RAGAS on the golden question set (calls paid APIs).
    "eval-rag"        { uv run python -m eval.rag_eval --label baseline }
    # Run the scripted tool scenarios through the real agent (calls paid APIs).
    "eval-tools"      { uv run python -m eval.eval_tools }
    # Compare the prompt versions on the same inputs (calls paid APIs).
    "compare-prompts" { uv run python -m eval.compare_prompts }
    # Run only the guardrail unit tests (fast, no paid API calls).
    "test-guardrails" { uv run pytest tests/test_guardrails.py -q }
    # Run the refusal and escalation suite GR-01..26 with rules only (calls paid APIs).
    "eval-refusal"    { uv run python -m eval.refusal_eval --label rules }
    # Benchmark the exact and semantic caches, CA-01..08 (calls paid APIs, under USD 0.10).
    "bench-cache"     { uv run python -m eval.cache_bench }
    # Score the reply drafts with the LLM judge (calls paid APIs).
    "judge"           { uv run python -m eval.judge --score }
    # Generate 15 synthetic test questions from the policies with RAGAS (calls paid APIs).
    "sdg"             { uv run python -m eval.sdg --size 15 }
    # Build the submission zip after a key scan: .\tasks.ps1 package "Your Name"
    "package"         { uv run python -m scripts.package --name $Name }
    # Any other name is a typo: show an error instead of doing nothing.
    default   { Write-Error "Unknown task: $Task" }
}

# Shortcuts for common tasks. Run one with `make <task>`, for example `make test`.
# On Windows without make, use tasks.ps1 instead. Tasks marked "paid" call the OpenAI API.

# These names are tasks, not files, so make must always run them.
.PHONY: package test test-unit test-guardrails lint secrets ingest smoke eval-rag eval-tools eval-refusal compare-prompts bench-cache judge sdg

# Embed the policy documents into the local vector store (a second run makes no embedding calls).
ingest:
	uv run python -m app.rag.ingest

# Check a deployed app: `make smoke URL=https://your-app.onrender.com` (exit code 0 means /health is ok).
smoke:
	uv run python -m scripts.ping $(URL)

# Run only the guardrail unit tests (fast, no paid API calls).
test-guardrails:
	uv run pytest -q tests/test_guardrails.py

# Run the refusal and escalation suite GR-01..26 with rules only (paid).
eval-refusal:
	uv run python -m eval.refusal_eval --label rules

# Benchmark the exact and semantic caches, CA-01..08 (paid, under USD 0.10).
bench-cache:
	uv run python -m eval.cache_bench

# Score the reply drafts with the LLM judge (paid).
judge:
	uv run python -m eval.judge --score

# Score the RAG pipeline with RAGAS on the golden question set (paid).
eval-rag:
	uv run python -m eval.rag_eval --label baseline

# Run the scripted tool scenarios through the real agent (paid).
eval-tools:
	uv run python -m eval.eval_tools

# Compare the prompt versions on the same inputs (paid).
compare-prompts:
	uv run python -m eval.compare_prompts

# Run all automatic tests (quick, no paid API calls).
test:
	uv run pytest -q

# Run only the tests folder, quietly (same idea as `test`).
test-unit:
	uv run pytest -q tests

# Check code style (ruff finds problems, black checks formatting). Changes nothing.
lint:
	uv run ruff check .
	uv run black --check .

# Scan the repo for accidentally committed secrets such as API keys.
secrets:
	uv run pre-commit run detect-secrets --all-files  # pragma: allowlist secret

# Build Capstone_Project_<name>.zip from the files git would track, after a key scan: `make package NAME="Your Name"`.
package:
	uv run python -m scripts.package --name "$(NAME)"

# Generate 15 synthetic test questions from the policies with RAGAS (paid).
sdg:
	uv run python -m eval.sdg --size 15

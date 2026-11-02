"""Compare prompt versions on the same inputs (24 scenarios + 10 policy questions).

A "prompt version" is one wording of the instructions we give the model when it
drafts an answer. To compare them fairly, only the draft prompt changes.
The model, the inputs and the retrieval stay the same.

Run: uv run python -m eval.compare_prompts                      (v1_baseline v2_grounded v3_checklist)
     uv run python -m eval.compare_prompts --versions v1_baseline v3_checklist
     uv run python -m eval.compare_prompts --versions v2_grounded v4_approval_aware   (after D47)
It calls the real model, so it costs money.
It writes eval/results/compare_prompts.json and prints a markdown table
that can be pasted into docs/prompt-comparison.md.
"""

import argparse

from eval.common import environment, save_result
from eval.scenarios import run_set, summarize

# Table layout: (key in the summary dict, column label shown to the reader).
# The numbers in the labels are how many scenarios feed that metric:
# 34 = 24 tool scenarios + 10 policy questions.
COLUMNS = [
    ("task_completion_all", "Task completion (34)"),
    ("task_completion_tools", "Tools (24)"),
    ("task_completion_policy", "Policy Qs (10)"),
    ("tool_selection_accuracy", "Tool selection"),
    ("first_pass_schema_ok", "First-pass schema OK"),
    ("citation_validity", "Citation validity"),
    ("runs_with_ungrounded_amounts", "Runs with ungrounded ₱"),
    ("mean_latency_s", "Mean latency (s)"),
    ("mean_tokens", "Mean tokens"),
    ("est_cost_usd", "Est. cost (USD)"),
]


def markdown(summaries: dict[str, dict]) -> str:
    """Turn the per-version summaries into a markdown table.

    Args:
        summaries: Maps a prompt version name to its summary dict (from summarize()).

    Returns:
        The markdown table as one string: one row per metric, one column per version.
    """
    # Header row, then the separator row ("---|" once per version column).
    head = "| Metric | " + " | ".join(summaries) + " |\n|---|" + "---|" * len(summaries)
    lines = [head]
    for key, label in COLUMNS:
        lines.append(f"| {label} | " + " | ".join(str(s[key]) for s in summaries.values()) + " |")
    return "\n".join(lines)


def main() -> int:
    """Run every requested prompt version, save the results and print the table.

    Returns:
        The process exit code (0 means success).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--versions", nargs="*", default=["v1_baseline", "v2_grounded", "v3_checklist"]
    )
    args = parser.parse_args()

    # --- Step 1: run the full scenario set once per prompt version ---
    all_rows, summaries = {}, {}
    for version in args.versions:
        print(f"\n##### {version}")
        rows = run_set(version)
        all_rows[version] = rows
        summaries[version] = summarize(rows)

    # --- Step 2: save everything and show the comparison table ---
    path = save_result(
        "compare_prompts.json",
        {"environment": environment(), "summaries": summaries, "items": all_rows},
    )
    print("\n" + markdown(summaries))
    print("saved:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

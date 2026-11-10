"""Tool evaluation: 24 scripted scenarios (+10 policy questions) through the real agent.

Each scenario is a fixed user request with a known correct outcome. We run the
agent on it and check two things: did it pick the right tool (tool-selection
accuracy), and did it finish the job (task completion)?

Run: uv run python -m eval.eval_tools --prompt v1_baseline
     uv run python -m eval.eval_tools --prompt v1_baseline --only S01 S23
It calls the real model, so it costs money.
It reports accuracy overall, for the MVP subset and for the designed failures
(scenarios built to fail on purpose). It writes docs/evidence/evals/tools_<prompt>.json.
"""

import argparse

from app import config
from eval.common import environment, save_result
from eval.scenarios import run_set, summarize


def main() -> int:
    """Run the scenarios for one prompt version, save the results and print a summary.

    Returns:
        The process exit code (0 means success).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=config.PROMPT_VERSION)
    parser.add_argument("--only", nargs="*", help="scenario ids")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    # --- Step 1: run the scenarios (all of them, or only the ids given with --only) ---
    rows = run_set(args.prompt, only=args.only)
    summary = summarize(rows)

    # --- Step 2: save a result file named after the label (or the prompt version) ---
    label = args.label or f"tools_{args.prompt}"
    # The environment's default prompt_version is replaced by the one we really used.
    path = save_result(
        f"{label}.json",
        {"environment": environment() | {"prompt_version": args.prompt}, "summary": summary,
         "items": rows},
    )  # fmt: skip
    print("\n== summary:", args.prompt)
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("saved:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

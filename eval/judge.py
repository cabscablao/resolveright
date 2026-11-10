"""LLM-as-judge for customer-reply drafts (test guide JD-01 to JD-04).

"LLM-as-judge" means we ask a second language model to grade the replies that the
first model wrote, using a 1-5 score on a few criteria. A judge can be wrong, so we
also score a small sample by hand and measure how often the judge and the human agree.

  uv run python -m eval.judge --score        judge every v2_grounded draft from the tool scenarios
  uv run python -m eval.judge --sheet        write docs/evidence/evals/judge_manual.csv for blind hand scoring
  uv run python -m eval.judge --agreement    compare hand scores with the judge's, write the report

Files written: docs/evidence/evals/judge_scores.json (--score), docs/evidence/evals/judge_manual.csv (--sheet)
and docs/judge-agreement.md (--agreement). --score calls the paid API.

The judge model (`JUDGE_MODEL`, default gpt-4o-mini) is deliberately not the model that wrote the
drafts (`LLM_MODEL`). A model tends to rate its own writing too kindly, so we use a different one.
The hand-scoring sheet shows no judge score, so the human scores blind (without being nudged).
"""

import argparse
import csv
import json
import random
import re
from collections import Counter

from pydantic import BaseModel, Field

from app import config
from app.agent.llm import OpenAILLM
from eval.common import EVAL_DIR, JUDGE_MODEL, RESULTS_DIR, estimate_cost, save_result

# The four things the judge scores, each from 1 (bad) to 5 (very good).
CRITERIA = ["policy_accuracy", "tone_empathy", "completeness", "no_over_promising"]
# The drafts come from the prompt comparison run (see eval/compare_prompts.py).
SOURCE = RESULTS_DIR / "compare_prompts.json"
PROMPT_VERSION = "v2_grounded"
MANUAL_CSV = RESULTS_DIR / "judge_manual.csv"
SCORES_JSON = "judge_scores.json"
REPORT = EVAL_DIR.parent / "docs" / "judge-agreement.md"
# How many drafts a human scores by hand. 12 is enough to compare, and still quick to do.
SHEET_SIZE = 12


class JudgeScores(BaseModel):
    """The structured answer we ask the judge model to return for one draft."""

    # ge=1, le=5 makes the library reject any score outside 1..5.
    policy_accuracy: int = Field(ge=1, le=5)
    tone_empathy: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    no_over_promising: int = Field(ge=1, le=5)
    comment: str


def drafts() -> list[dict]:
    """Load the reply drafts of the chosen prompt version, one per tool scenario and policy question.

    Returns:
        Result rows that have a non-empty "reply".
    """
    items = json.loads(SOURCE.read_text(encoding="utf-8"))["items"][PROMPT_VERSION]
    return [i for i in items if i.get("reply")]


def pretty(text: str) -> str:
    """Turn the literal two characters backslash-n into real line breaks.

    Args:
        text: A reply draft that may contain escaped newlines.

    Returns:
        The same text with real newlines, so it is easy to read.
    """
    return text.replace("\\n", "\n")


def policy_text(item: dict) -> str:
    """Load the full text of every policy document the draft cited.

    Args:
        item: One result row. Its "citations" look like "POLICY-ID#section".

    Returns:
        The policy files joined together, or a placeholder if none were cited.
    """
    # Keep only the document id (before "#") and remove duplicates.
    docs = sorted({c.split("#")[0] for c in item["citations"]})
    parts = []
    for doc in docs:
        for path in (config.DATA_DIR / "policies").glob(f"{doc}_*.md"):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts) or "(no policy cited)"


def build_user_message(item: dict) -> str:
    """Build the message that shows the judge everything it needs to grade one draft.

    Args:
        item: One result row (customer message, decision, citations, reply).

    Returns:
        A text with XML-like tags, which help the model tell the parts apart.
    """
    return (
        f"<customer_message>\n{item['message']}\n</customer_message>\n\n"
        f"<system_decision>\nrecommendation: {item['recommendation']}\n"
        f"amount: {item['amount']}\nreasoning: {item['rationale']}\n</system_decision>\n\n"
        f"<policy_text>\n{policy_text(item)}\n</policy_text>\n\n"
        f"<draft_under_review>\n{pretty(item['reply'])}\n</draft_under_review>"
    )


def score_all() -> None:
    """Ask the judge to score every draft and save the scores and a cost estimate.

    Prints one line per draft and the mean score per criterion at the end.
    """
    # --- Step 1: load the judge instructions and create the judge model ---
    system = (EVAL_DIR / "judge_prompt.md").read_text(encoding="utf-8")
    # Temperature 0 makes the judge as repeatable as possible.
    judge = OpenAILLM(model=JUDGE_MODEL, temperature=0.0)

    # --- Step 2: score each draft ---
    rows = []
    for item in drafts():
        s = judge.parse(system, build_user_message(item), JudgeScores)
        row = {"id": item["id"], "recommendation": item["recommendation"], **s.model_dump()}
        rows.append(row)
        # Show only the first 60 characters of the comment to keep the console tidy.
        print(item["id"], [row[c] for c in CRITERIA], row["comment"][:60])

    # --- Step 3: summarize (mean and how many 1s, 2s, ... per criterion) and save ---
    means = {c: round(sum(r[c] for r in rows) / len(rows), 2) for c in CRITERIA}
    dist = {c: dict(sorted(Counter(r[c] for r in rows).items())) for c in CRITERIA}
    usage = judge.usage
    save_result(
        SCORES_JSON,
        {
            "judge_model": JUDGE_MODEL, "drafts_model": config.LLM_MODEL,
            "prompt_version": PROMPT_VERSION, "n": len(rows), "means": means,
            "distribution": dist, "rows": rows,
            "cost_usd": estimate_cost(JUDGE_MODEL, usage["prompt_tokens"], usage["completion_tokens"]),
        },
    )  # fmt: skip
    print("means", means)


def write_sheet() -> None:
    """Write a CSV sheet of random drafts for a human to score by hand, without judge scores.

    Raises:
        SystemExit: If the sheet already exists, so hand scores are never overwritten.
    """
    if MANUAL_CSV.exists():
        raise SystemExit(f"{MANUAL_CSV} already exists; it may hold your scores. Not overwritten.")
    # Fixed seed 7 gives the same sample each time, so the sheet can be rebuilt the same way.
    picked = random.Random(7).sample(drafts(), SHEET_SIZE)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with MANUAL_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "customer_message", "recommendation", "amount", "reply_draft"]
                   + CRITERIA + ["notes"])  # fmt: skip
        for it in sorted(picked, key=lambda x: x["id"]):
            # The five empty cells are for the four scores and the notes, filled in by hand.
            w.writerow([it["id"], it["message"], it["recommendation"], it["amount"],
                        pretty(it["reply"]), "", "", "", "", ""])  # fmt: skip
    print(
        f"wrote {MANUAL_CSV} ({SHEET_SIZE} drafts). Fill the four score columns (1-5), then run --agreement."
    )


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Compute Cohen's kappa: how much two raters agree, after removing agreement by pure chance.

    1.0 means perfect agreement, 0 means no better than chance, below 0 means worse than chance.

    Args:
        a: Scores from the first rater (for example the human).
        b: Scores from the second rater (for example the judge), in the same order.

    Returns:
        Kappa rounded to 3 decimals, or None if it is not defined (no data, or chance
        agreement is already 100%).
    """
    n = len(a)
    if n == 0:
        return None
    # po = share of items where both raters gave exactly the same score.
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    # pe = agreement we would expect by chance, from how often each rater uses each score.
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    # pe == 1 would divide by zero.
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def agreement() -> None:
    """Compare the human scores with the judge scores and write a markdown report.

    Raises:
        SystemExit: If fewer than 10 rows were fully scored by hand.
    """
    # --- Step 1: load the judge scores and the fully scored hand rows ---
    judge = {r["id"]: r for r in json.loads((RESULTS_DIR / SCORES_JSON).read_text("utf-8"))["rows"]}
    manual = []
    # utf-8-sig: Excel's "CSV UTF-8" adds a BOM that would turn the header "id" into "﻿id".
    with MANUAL_CSV.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            # A row counts only if all four scores are a single digit from 1 to 5.
            if all(re.fullmatch(r"[1-5]", (row.get(c) or "").strip()) for c in CRITERIA):
                manual.append(row)
    # With too few rows, the agreement numbers would mean very little.
    if len(manual) < 10:
        raise SystemExit(f"Only {len(manual)} fully scored rows in {MANUAL_CSV}; need at least 10.")

    # --- Step 2: start the report with a header and the table heading ---
    lines = [
        "# Judge agreement (JD-04)", "",
        f"Hand scores: {len(manual)} drafts, scored blind (the sheet shows no judge score). "
        f"Judge: `{JUDGE_MODEL}`; drafts written by `{config.LLM_MODEL}`.", "",
        "| Criterion | Within ±1 point | Exact match | Cohen's kappa (exact) | Mean hand | Mean judge |",
        "|---|---|---|---|---|---|",
    ]  # fmt: skip

    # --- Step 3: one table row per criterion; collect big disagreements ---
    big = []
    for c in CRITERIA:
        h = [int(r[c]) for r in manual]
        j = [judge[r["id"]][c] for r in manual]
        within = sum(abs(x - y) <= 1 for x, y in zip(h, j, strict=True)) / len(h)
        exact = sum(x == y for x, y in zip(h, j, strict=True)) / len(h)
        lines.append(
            f"| {c} | {within:.0%} | {exact:.0%} | {cohen_kappa(h, j)} | "
            f"{sum(h) / len(h):.2f} | {sum(j) / len(j):.2f} |"
        )
        # A gap of 2 or more points is a real disagreement that a reviewer should look at.
        big += [(r["id"], c, int(r[c]), judge[r["id"]][c]) for r in manual
                if abs(int(r[c]) - judge[r["id"]][c]) >= 2]  # fmt: skip

    # --- Step 4: list the disagreements, then write and print the report ---
    lines += ["", "## Disagreements of 2 points or more", ""]
    lines += [f"- {i} {c}: hand {h}, judge {j}" for i, c, h, j in big] or ["- none"]
    lines += ["", "Add a short note on why they disagree below (written by the reviewer).", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    """Read the command-line flags and run the chosen steps (--score, --sheet, --agreement)."""
    p = argparse.ArgumentParser()
    p.add_argument("--score", action="store_true")
    p.add_argument("--sheet", action="store_true")
    p.add_argument("--agreement", action="store_true")
    a = p.parse_args()
    if a.score:
        score_all()
    if a.sheet:
        write_sheet()
    if a.agreement:
        agreement()
    # With no flag at all, show the usage help instead of doing nothing silently.
    if not (a.score or a.sheet or a.agreement):
        p.print_help()


if __name__ == "__main__":
    main()

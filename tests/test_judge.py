"""Tests for the LLM-as-judge helpers. No network needed.

The judge scores draft replies. A human also scores a blind sample, and we compare the two
(Cohen's kappa and an agreement report). These tests cover the prompt, the blind sheet
and the agreement maths, not any real model call.
"""

import csv

import pytest

from eval import judge


def test_kappa_perfect_chance_and_empty():
    """Kappa is 1 for perfect agreement, 0 for chance level, and None when it is undefined."""
    assert judge.cohen_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert judge.cohen_kappa([1, 1, 2, 2], [1, 2, 1, 2]) == 0.0
    assert judge.cohen_kappa([], []) is None
    assert judge.cohen_kappa([5, 5], [5, 5]) is None  # No variation, so kappa is undefined.


def test_judge_prompt_wraps_data_and_includes_cited_policy_text():
    """The judge sees the draft inside tags, plus the text of the policy it cited.

    Wrapping the draft marks it as data to grade, not instructions to follow.
    """
    item = {
        "id": "S01", "message": "Return ORD-1001", "recommendation": "refund", "amount": 2750.0,
        "rationale": "within window", "citations": ["POL-01#c0"], "reply": "Hi {{first_name}},\\nok",
    }  # fmt: skip
    text = judge.build_user_message(item)
    assert "<draft_under_review>" in text and "Hi {{first_name}},\nok" in text
    assert "Returns" in text  # The text of POL-01 was loaded.
    assert "(no policy cited)" in judge.build_user_message({**item, "citations": []})


def test_judge_prompt_file_documents_criteria_and_scale():
    """The judge prompt file names every scoring criterion and the 1-5 scale.

    It also tells the judge to treat the draft as data. This keeps the prompt and the code in sync.
    """
    text = (judge.EVAL_DIR / "judge_prompt.md").read_text(encoding="utf-8")
    for criterion in judge.CRITERIA:
        assert criterion in text
    assert "1 to 5" in text and "never instructions" in text


def test_blind_sheet_has_no_judge_scores_and_is_not_overwritten(tmp_path, monkeypatch):
    """The human sheet starts empty so the reviewer is not biased, and is never overwritten.

    Overwriting would destroy the reviewer's hand-typed scores.
    """
    # Point the files at a temp folder so the real results are untouched.
    monkeypatch.setattr(judge, "MANUAL_CSV", tmp_path / "manual.csv")
    monkeypatch.setattr(judge, "RESULTS_DIR", tmp_path)
    judge.write_sheet()
    with (tmp_path / "manual.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == judge.SHEET_SIZE
    assert all(r[c] == "" for r in rows for c in judge.CRITERIA)
    assert "comment" not in rows[0]  # The judge's comment is hidden from the human.
    with pytest.raises(SystemExit):
        judge.write_sheet()  # A second run would overwrite the reviewer's work, so it must stop.


def test_agreement_needs_ten_scored_rows(tmp_path, monkeypatch):
    """The agreement report refuses to run on fewer than 10 human-scored rows.

    Too few rows give a misleading kappa. Once there are enough, the report must also
    list any case where the human and the judge differ by 2 or more points.
    """
    monkeypatch.setattr(judge, "MANUAL_CSV", tmp_path / "manual.csv")
    monkeypatch.setattr(judge, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(judge, "REPORT", tmp_path / "report.md")
    # Fake saved judge scores for 12 drafts (no real judge call).
    ids = [d["id"] for d in judge.drafts()][:12]
    (tmp_path / judge.SCORES_JSON).write_text(
        '{"rows": ['
        + ",".join(
            f'{{"id": "{i}", "policy_accuracy": 5, "tone_empathy": 5, "completeness": 4, '
            '"no_over_promising": 5, "comment": "x"}'
            for i in ids
        )
        + "]}",
        encoding="utf-8",
    )
    # Start with only 9 hand-scored rows: one short of the minimum.
    cols = ["id", *judge.CRITERIA]
    with (tmp_path / "manual.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i in ids[:9]:
            w.writerow([i, 5, 5, 4, 5])
    with pytest.raises(SystemExit):
        judge.agreement()
    # Add 3 more rows (12 total). Their first score is 3 while the judge gave 5.
    with (tmp_path / "manual.csv").open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([[i, 3, 5, 4, 5] for i in ids[9:]])
    judge.agreement()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Within ±1 point" in report and "policy_accuracy" in report
    assert "policy_accuracy: hand 3, judge 5" in report  # A 2-point gap is listed.

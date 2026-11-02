"""Checks on the seed data: the mock customers/orders and the ten policy documents.

If these files drift, the agent would answer from wrong facts, so we guard them here.
"""

import re
from pathlib import Path

from scripts.seed_checks import DATA_DIR, run_checks

POLICY_DIR = Path(DATA_DIR) / "policies"


def test_archetypes_and_day_counts():
    """The seed orders still match their planned scenarios (an empty problem list means OK)."""
    assert run_checks() == []


def test_ten_policies_with_header_and_length():
    """Each policy has an ID, version and date, and a sensible length for chunking and search."""
    files = sorted(POLICY_DIR.glob("POL-*.md"))
    assert len(files) == 10
    for f in files:
        text = f.read_text(encoding="utf-8")
        pol_id = f.name[:6]
        assert f"**Document ID:** {pol_id}" in text
        assert re.search(r"\*\*Version:\*\* \d", text)
        assert "Effective date" in text
        words = len(text.split())
        assert 300 <= words <= 600, f"{f.name} has {words} words"
        assert "Brightcart" in text


def test_key_policy_numbers_present():
    """The headline numbers that other tests and answers rely on are still in the policies."""
    all_text = " ".join(f.read_text(encoding="utf-8") for f in POLICY_DIR.glob("POL-*.md"))
    for needle in ["30 days", "₱200", "10% bonus", "₱5,000", "7 days", "12 months", "14 days"]:
        assert needle in all_text, needle

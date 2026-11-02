"""PII masking. Runs before the LLM, the session memory, the caches, the traces and the logs.

PII means personally identifiable information: an email, phone number, card number and so on.
We replace each one with a tag like <EMAIL> so the real value never leaves this function.

Pure regex, no model download, so it is always on. Raw values are never returned or logged: the
caller only gets the masked text and a count per type.
"""

import re

# Order matters: the patterns run one after another on the same text. Specific formats (email,
# SSN, card) go before looser ones (phone), so a card number is not mistaken for a phone number.
PATTERNS: list[tuple[str, re.Pattern]] = [
    # Letters/digits/symbols, an "@", a domain, a dot and a 2+ letter ending.
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # US social security number 123-45-6789. The lookarounds stop it matching inside longer digits.
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    # Philippine government ids: SSS 12-3456789-0 and TIN 123-456-789 (optionally -000 branch).
    (
        "GOVID",
        re.compile(r"(?<![\d-])(?:\d{2}-\d{7}-\d|\d{3}-\d{3}-\d{3}(?:-\d{3,5})?)(?![\d-])"),
    ),
    # 13 to 19 digits, optionally grouped by spaces or dashes. No Luhn check (the card checksum):
    # over-masking is safer than letting a real card number through.
    ("CARD", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")),
    # Optional country code, then a 3-digit area code (with or without brackets), then 3 + 4 digits.
    # This also covers Philippine mobile numbers: +63 917 123 4567, 0917 123 4567, 09171234567.
    (
        "PHONE",
        re.compile(
            r"(?<![\w-])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?![\w-])"
        ),
    ),
    # Metro Manila landline with the area code in brackets, such as (02) 8123 4567.
    (
        "PHONE",
        re.compile(r"(?<![\w-])\(0?\d{1,3}\)[ .-]?\d{3,4}[ .-]?\d{4}(?![\w-])"),
    ),
    # Philippine barangay / subdivision lines, such as "Brgy. San Roque" or "Purok 5".
    (
        "ADDRESS",
        re.compile(
            r"\b(?i:Brgy|Barangay|Purok|Subdivision|Subd)\.?\s+[A-Z0-9][\w'-]*(?:\s+[A-Z][\w'-]*){0,2}"
        ),
    ),
    # A house number, 1 to 4 street-name words, then a street type word such as "St" or "Avenue".
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){1,4}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl)\b\.?",
            re.IGNORECASE,
        ),
    ),
]


def mask_pii(text: str) -> tuple[str, dict[str, int]]:
    """Replace PII in a text with <TYPE> tags.

    Args:
        text: The raw text, for example a customer message.

    Returns:
        A tuple (masked text, counts). counts maps each PII type found (for example "EMAIL")
        to how many were replaced. Only types that were found appear.
    """
    found: dict[str, int] = {}
    for kind, pattern in PATTERNS:
        # subn returns the new text and how many replacements it made.
        text, n = pattern.subn(f"<{kind}>", text)
        if n:
            found[kind] = found.get(kind, 0) + n
    return text, found

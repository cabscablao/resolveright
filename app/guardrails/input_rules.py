"""Input guardrail layer: PII masking, then attack, off-topic and escalation rules.

A guardrail is a safety check around the LLM. This one looks at the customer's message first.
It can allow it, block it with a polite refusal, or escalate it to a human.

Where it sits in the flow: it is the very first step of a request, before the caches, the
retriever and any LLM call. (PII = personally identifiable information, such as an email.)

Deterministic and fast, so it runs before any LLM call. Order matters: PII is masked first, and
every rule runs on the masked text, so nothing sensitive is ever logged.
Optional Guardrails AI validators add a second opinion (see `hub.py`).
"""

import base64
import binascii
import re

from app.guardrails.pii import mask_pii
from app.guardrails.types import FIRST_NAME, GuardEvent, InputVerdict

# Words that suggest a physical safety hazard (fire, smoke, shock). These messages are urgent and
# must reach the agent's safety path, so the escalation and optional-model rules skip them (POL-10).
SAFETY_WORDS = re.compile(
    r"overheat|over-heat|smok(e|es|ing)\b|on fire|caught fire|catch(es|ing)? fire|burning|"
    r"burnt|burned|sparks?\b|sparking|explod|exploded|melting|melted|electric shock|swollen battery",
    re.IGNORECASE,
)

# Asking for a person ("talk to a human", "I want a real person", "connect me to an agent").
# POL-10: this request is always honoured, so it must not depend on the LLM noticing it. The
# lookahead (?![-\w]) keeps "human-sized mannequin" from matching.
_WHO = (
    r"(human|person|people|agent|staff|someone|somebody|representative|manager|supervisor|"
    r"specialist)(?![-\w])(?: (?:staff|agent|support|representative|member|team)\b)?"
)
HUMAN_WORDS = re.compile(
    rf"\b(talk|speak|chat|connect|transfer|escalate|put me through)\b[^.\n]{{0,25}}\b{_WHO}|"
    rf"\b(real|live|actual) {_WHO}|\bhuman (staff|agent|support|being|representative|help)\b|"
    rf"\b(need|want|get me|give me|let me have)\b[^.\n]{{0,15}}\b{_WHO}",
    re.IGNORECASE,
)

# Short names for the regex flags used below: ignore case, and let ^ match at every line start.
_I = re.IGNORECASE
_ML = re.IGNORECASE | re.MULTILINE

# (rule, pattern). "block" rules get a polite refusal and the LLM is never called.
# Rules are checked in this order and the first match wins. `[^.\n]{0,N}` below means "up to N
# characters on the same sentence", which keeps the two halves of a pattern close together.
ATTACK_RULES: list[tuple[str, re.Pattern]] = [
    (
        # Tries to give the assistant a new identity ("you are now...", "act as the manager").
        "role_play",
        re.compile(
            r"\byou are now\b|\bpretend (to be|you are|you're)\b|\broleplay as\b|\bfrom now on,? you\b|"
            r"\bact as (a |an |the )?(store |shop )?(manager|owner|admin|administrator|supervisor|ceo)\b",
            _I,
        ),
    ),
    (
        # "I'm the owner, so approve this": claims power to override policy (GR-18 style cases).
        "authority_claim",
        re.compile(
            r"\bi(?:'m| am)\s+(the |a |your )?(store |shop )?(owner|manager|administrator|admin|ceo|"
            r"developer|supervisor|boss)\b[^\n]{0,80}\b(override|approve|bypass|ignore|authori[sz]e|"
            r"waive|allow|skip)\b",
            _I,
        ),
    ),
    (
        # Classic prompt injection: "ignore your instructions", "developer mode", "jailbreak".
        "prompt_injection",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass|circumvent)\b[^.\n]{0,40}"
            r"\b(instructions?|rules?|guidelines?|polic(?:y|ies)|prompt|restrictions?|guardrails?|"
            r"safeguards?)\b|"
            r"\b(new|updated) (instructions?|rules?)\b|\bdeveloper mode\b|\bjailbreak\b|"
            r"\bdo anything now\b|\bapprove (everything|all (refunds|requests|returns))\b",
            _I,
        ),
    ),
    (
        # Tries to make the assistant reveal its hidden system prompt or tool list.
        "prompt_extraction",
        re.compile(
            r"\b(print|show|reveal|display|repeat|output|leak|dump|share|tell me)\b[^.\n]{0,30}"
            r"\b(system prompt|(your|the) (hidden |system |initial |original )?"
            r"(prompt|instructions|rules|tool list|tools|configuration|guidelines)|developer message)",
            _I,
        ),
    ),
    (
        # Text that imitates a system message ("[SYSTEM]: ...", "<|im_start|>") or our own prompt
        # tags, so the model might treat it as trusted instructions.
        "fake_system_message",
        re.compile(
            r"^\s*\[?(system|assistant|developer|admin)\]?\s*[:>]|<\|?(system|im_start)|"
            r"</?(customer_message|system|conversation_so_far)>",
            _ML,
        ),
    ),
    (
        # Asks for ANOTHER customer's personal details (privacy breach). Four alternative phrasings.
        "pii_request",
        re.compile(
            r"\b(give|tell|show|send|share|reveal|look up|what(?:'s| is)|get)\b[^.\n]{0,40}"
            r"\b(e-?mail|address|phone|number|name|details|information)\b[^.\n]{0,30}"
            r"\b(of|for)\b[^.\n]{0,25}\b(another|other|different|the) (customer|buyer|person|shopper)|"
            r"\b(another|other|someone else'?s?|different) (customer|person|buyer)'?s?\b[^.\n]{0,40}"
            r"\b(e-?mail|address|phone|details|personal|information)\b|"
            r"\b(customer|buyer|person) who (ordered|bought|placed)\b[^.\n]{0,40}\b(e-?mail|address|phone)\b|"
            r"\b(e-?mail|address|phone)\b[^.\n]{0,30}\b(customer|buyer|person) who (ordered|bought|placed)\b",
            _I,
        ),
    ),
    (
        # Requests unrelated to the store: poems, code, recipes, weather, crypto, trivia.
        "off_topic",
        re.compile(
            r"\b(write|compose|tell|give|make|create|generate|draft)\b[^.\n]{0,25}"
            r"\b(poem|poetry|song|story|joke|essay|haiku|limerick|lyrics|riddle|"
            r"python|javascript|code|script|program|recipe|homework)\b|"
            r"\b(weather (in|today|forecast)|capital of|stock price|bitcoin|crypto|horoscope|"
            r"who (won|is the president)|translate (this|the following))\b",
            _I,
        ),
    ),
    (
        # Asks for medical advice, which the assistant must not give.
        "medical_advice",
        re.compile(
            r"\b(medical advice|diagnos(e|is)|prescri(be|ption)|dosage|which (medicine|medication|drug)|"
            r"what (medicine|medication|drug)|should i take (a |an |some )?(pill|medicine|medication|"
            r"painkiller|ibuprofen|aspirin)|treat my (rash|burn|injury|wound|symptoms?)|"
            r"am i (sick|ill|infected))\b",
            _I,
        ),
    ),
]

# Escalate to a human instead of answering (the order system is not asked).
ESCALATION_RULES: list[tuple[str, re.Pattern]] = [
    (
        # Legal threats or legal questions need a human, not an automated answer.
        "legal_threat",
        re.compile(
            r"\b(sue|suing|lawsuit|lawyer|attorney|legal action|small claims|is it legal|"
            r"is that legal|illegal|my legal rights|consumer protection|report you to)\b",
            _I,
        ),
    ),
]

# Common insults. A cheap word list that catches abuse even when the ML score below is low.
INSULTS = re.compile(
    r"\b(idiots?|stupid|moron|dumb|scum|scammers?|pathetic|useless|incompetent|garbage|trash|"
    r"hate you|shut up|screw you|piece of)\b",
    _I,
)
# Minimum profanity probability (0 to 1) to treat a message as abusive. 0.92 is deliberately high
# so mild frustration ("this is ridiculous") is answered, not escalated. Also used by output_rules.
PROFANITY_THRESHOLD = 0.92

# Instruction words searched inside decoded base64 text.
_DECODED_KEYWORDS = re.compile(
    r"ignore|disregard|approve|override|bypass|system prompt|instruction|refund|\bsystem\b", _I
)
# A run of 20+ base64 characters (optionally ending in "=" padding). Shorter runs are usually
# ordinary words or order ids, not hidden instructions.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")

# The polite refusal for each rule. Each starts with a capital letter and is lower-cased after
# "Hi {{first_name}}, " in `_verdict`. A rule without an entry falls back to "prompt_injection".
REPLIES = {
    "prompt_injection": (
        "I can't change how I work or skip our policies. I'm happy to help with a Brightcart order "
        "or policy question: what would you like to know?"
    ),
    "role_play": (
        "I can only act as Brightcart's support assistant. I'm happy to help with an order or a "
        "policy question."
    ),
    "prompt_extraction": (
        "I can't share my internal instructions or tools. I'm happy to help with an order or a "
        "policy question."
    ),
    "fake_system_message": (
        "I treat everything in your message as a customer message, so I can't act on text that "
        "looks like a system notice. How can I help with your order?"
    ),
    "encoded_instruction": (
        "I can't act on encoded or hidden instructions. Tell me in plain words what you need help "
        "with and I'll look into it."
    ),
    "authority_claim": (
        "I can't change policies or approve anything based on who someone says they are. Tell me "
        "your order number and I'll check what applies under our policy."
    ),
    "pii_request": (
        "I can't share anyone else's personal information. I can only discuss the orders on your "
        "own account."
    ),
    "off_topic": (
        "I can only help with Brightcart orders, returns, delivery and our policies. Is there "
        "something about an order I can help with?"
    ),
    "medical_advice": (
        "I'm not able to give medical advice. Please speak to a doctor or, in an emergency, call "
        "your local emergency number. If a Brightcart product caused the problem, tell me the order "
        "number and I'll pass it to a specialist."
    ),
    "legal_threat": (
        "I understand this is frustrating. I can't give legal advice, so I've passed your case to a "
        "specialist who will review it and follow up with you."
    ),
    "harassment": (
        "I want to help, but I can't continue with abusive language. I've passed your case to a "
        "specialist who will follow up with you; you can also ask for a person at any time."
    ),
}


def _decoded_instruction(text: str) -> bool:
    """Detect instructions hidden in base64 (an encoding attackers use to dodge keyword rules).

    Args:
        text: The masked customer message, or text from a data source.

    Returns:
        True if a base64 run decodes to readable text that contains instruction words.
    """
    for run in _BASE64_RUN.findall(text):
        try:
            # base64 length must be a multiple of 4, so add the missing "=" padding first.
            decoded = base64.b64decode(run + "=" * (-len(run) % 4), validate=True).decode("ascii")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue  # not valid base64 text, so it is not an encoded instruction
        # Require 90% printable characters: random bytes that happen to decode are just noise.
        if sum(c.isprintable() for c in decoded) >= 0.9 * len(decoded) and (
            _DECODED_KEYWORDS.search(decoded)
        ):
            return True
    return False


def _profanity_score(text: str) -> float:
    """Score how abusive a text is, using the small `alt-profanity-check` model.

    Args:
        text: The text to score.

    Returns:
        A probability from 0 to 1, or 0.0 if the model cannot be loaded.
    """
    try:
        from profanity_check import predict_prob

        return float(predict_prob([text])[0])
    except Exception:  # noqa: BLE001 - a missing optional model must not break a request
        return 0.0


def _verdict(action, text, rule, events, escalation_reason=None, layer="input") -> InputVerdict:
    """Build the InputVerdict for a rule that fired, with its canned reply.

    Args:
        action: "block" or "escalate".
        text: The masked message.
        rule: The rule name that fired.
        events: Events collected so far; a new one for this rule is added to it.
        escalation_reason: Why a human is needed (only for "escalate").
        layer: Which guardrail layer to record on the event.

    Returns:
        The verdict, carrying the refusal or hand-off reply.
    """
    events.append(GuardEvent(layer=layer, rule=rule, action=action))
    first = REPLIES[rule if rule in REPLIES else "prompt_injection"]
    return InputVerdict(
        action=action,
        message=text,
        rule=rule,
        # "I can't ..." keeps its capital: the pronoun "I" is always upper case.
        reply=f"Hi {FIRST_NAME}, {first if re.match(r'I\b', first) else first[0].lower() + first[1:]}",
        escalation_reason=escalation_reason,
        events=events,
    )


# Rules that describe "text trying to steer the AI". Only these apply to untrusted data such as
# order notes; rules like off_topic would wrongly fire on normal notes.
INJECTION_RULES = {
    "prompt_injection", "role_play", "prompt_extraction", "fake_system_message", "authority_claim",
}  # fmt: skip


def untrusted_text_rule(text: str) -> str | None:
    """Check text from a data source (such as an order note) for instructions aimed at the AI.

    Args:
        text: The untrusted text.

    Returns:
        The name of the rule that matched, or None if the text looks harmless.
    """
    for rule, pattern in ATTACK_RULES:
        if rule in INJECTION_RULES and pattern.search(text):
            return rule
    return "encoded_instruction" if _decoded_instruction(text) else None


def check_input(message: str, hub=None) -> InputVerdict:
    """Mask PII, then apply the attack, off-topic and escalation rules.

    Args:
        message: The raw customer message.
        hub: Optional Guardrails AI validators (see hub.py), asked only when no rule fired.

    Returns:
        An InputVerdict: allow, block or escalate. Its `message` is always the masked text.
    """
    # --- Step 1: mask PII first, so every later rule and log only sees safe text ---
    text, found = mask_pii(message)
    events: list[GuardEvent] = []
    for kind, count in found.items():
        events.append(
            GuardEvent(layer="input", rule=f"pii_{kind.lower()}", action="mask", score=count)
        )

    # --- Step 2: attack rules (first match wins) and hidden base64 instructions ---
    for rule, pattern in ATTACK_RULES:
        if pattern.search(text):
            return _verdict("block", text, rule, events)
    if _decoded_instruction(text):
        return _verdict("block", text, "encoded_instruction", events)

    # --- Step 3: escalation rules (legal threats, abuse), skipped for safety reports ---
    # A safety hazard is always urgent (POL-10); the graph flags it, so do not pre-empt it here.
    if not SAFETY_WORDS.search(text):
        for rule, pattern in ESCALATION_RULES:
            if pattern.search(text):
                return _verdict("escalate", text, rule, events, escalation_reason=rule)
        if INSULTS.search(text) or _profanity_score(text) >= PROFANITY_THRESHOLD:
            return _verdict("escalate", text, "harassment", events, escalation_reason="harassment")
    # --- Step 4: optional Guardrails AI second opinion ---
    # Never let the optional model layer swallow a safety report (it once blocked "My battery is
    # smoking" as off-topic, GR-14): safety messages go straight to the graph's urgent path.
    if hub and not SAFETY_WORDS.search(text) and (hit := hub.screen_input(text)):
        action, rule, event = hit
        verdict = _verdict(action, text, rule, events)
        verdict.events[-1] = event  # report which Guardrails AI validator fired
        verdict.escalation_reason = rule if action == "escalate" else None
        return verdict
    # --- Step 5: nothing fired, so let the message through ---
    return InputVerdict(action="allow", message=text, events=events)

"""Pydantic models for the agent: what the LLM must return and what the app returns.

Pydantic classes describe the shape of data and check it. Where it sits: `Understanding` and
`Draft` are what the LLM fills in (structured output); `ResolutionResult` is the final answer
the API sends to the support agent.
"""

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.tools.eligibility import Recommendation

# What the customer is asking for; the LLM must pick exactly one.
Intent = Literal[
    "return",
    "damaged",
    "defective",
    "wrong_item",
    "late",
    "lost",
    "cancel",
    "change_address",
    "warranty",
    "price_adjustment",
    "refund_status",
    "policy_question",
    "other",
]
# Warning signs that make the case need a human.
Flag = Literal["safety_hazard", "legal_threat", "fraud", "harassment", "human_requested"]
# Outcome of the output checks: ok, fixed automatically, replaced by a safe text, or refused.
GuardrailStatus = Literal["passed", "repaired", "fallback", "blocked"]
# Where the human review of this case stands.
ReviewStatus = Literal["not_required", "pending", "approved", "edited", "escalated", "rejected"]


# ---- LLM-facing models (structured output, no defaults so strict mode accepts them) -------


class Understanding(BaseModel):
    """Step 4 (Understand): what the customer wants. The LLM never sees names or addresses."""

    intent: Intent
    order_id: str | None = Field(description="Order ID such as ORD-1234, or null if not given")
    item_sku: str | None = Field(description="SKU if the customer names one item, else null")
    error_origin: Literal["brightcart", "customer"] | None = Field(
        description="Who made a personalisation mistake, only if the customer said so"
    )
    flags: list[Flag] = Field(
        description="Safety hazard, legal threat, fraud, harassment, or a request for a human"
    )
    summary: str = Field(description="One neutral sentence, without personal data")


class Citation(BaseModel):
    """A pointer to the policy passage that supports the answer."""

    doc_id: str
    section: str
    chunk_id: str


class Draft(BaseModel):
    """Step 8 (Draft): the part of ResolutionResult the LLM writes."""

    recommendation: Recommendation
    proposed_action: str | None
    amount: float | None
    rationale: str
    policy_citations: list[Citation]
    clarifying_question: str | None
    customer_reply_draft: str = Field(description="Uses {{first_name}} as the name placeholder")
    confidence: float = Field(description="0.0 to 1.0")
    covered_by_policy: bool = Field(
        description="False if the policy text provided does not answer the customer's question"
    )


# ---- Application result ------------------------------------------------------------------


class EscalationInfo(BaseModel):
    """Summary of the escalation record created for this case."""

    escalation_id: str
    reason_code: str
    urgency: str


class ExecutionInfo(BaseModel):
    """What the agent carried out (mock) after the rules approved it (D50)."""

    resolution_id: str
    action: str
    amount: float | None = None


class HandoffCustomer(BaseModel):
    """Who the customer is: ids and a first name only, never an email, address or phone."""

    customer_id: str
    first_name: str | None = None


class HandoffRequest(BaseModel):
    """What the customer wanted, in neutral words."""

    intent: str
    order_id: str | None = None
    item_sku: str | None = None
    summary: str = ""


class HandoffReason(BaseModel):
    """Why the case goes to a person."""

    reason_code: str
    # policy: a POL-10 rule or another policy says a person decides. policy_gap: no policy covers
    # the case. system_fallback: a technical failure, not a policy reason.
    reason_type: Literal["policy", "policy_gap", "system_fallback"]
    explanation: str


class HandoffPayload(BaseModel):
    """Structured hand-off to staff: identity, request and reason, so nobody has to re-ask."""

    handoff_id: str
    customer: HandoffCustomer
    request: HandoffRequest
    reason: HandoffReason
    policies: list[str] = []
    urgency: str = "normal"
    facts_found: list[str] = []
    executed: bool = False  # always False: an escalated case is never executed
    actions_taken: list[str] = []
    ts: str


class ResolutionResult(BaseModel):
    """The final answer: the decision, what was executed or handed over, and the reply."""

    recommendation: Recommendation
    proposed_action: str | None = None
    amount: float | None = None
    approval_level: Literal["agent", "manager"] = "agent"
    rationale: str
    policy_citations: list[Citation] = []
    order_facts_used: list[str] = []
    clarifying_question: str | None = None
    customer_reply_draft: str
    confidence: float = 0.0
    escalation: EscalationInfo | None = None
    execution: ExecutionInfo | None = None  # set when the agent carried out the resolution
    handoff: HandoffPayload | None = None  # set for every escalation
    guardrail_status: GuardrailStatus = "passed"
    guardrail_events: list[dict] = []  # layer, rule, action, score; never the customer's text
    intent: str = "other"
    tool_trace: list[dict] = []
    prompt_version: str = ""
    case_id: str | None = None
    review_status: ReviewStatus = "not_required"
    notices: list[str] = []  # visible warnings, e.g. "could not verify the order"
    # True only for a semantic-cache hit (a stored answer to a similar question).
    served_from_cache: bool = False

    @computed_field
    @property
    def requires_human_approval(self) -> bool:
        """True when a person must take the case: a policy rule or a policy gap sent it to a human.

        The rules engine turns every case that policy keeps for a person (safety, fraud, legal,
        chargebacks, amounts above the agent limit, a request for a human...) into the
        recommendation "escalate"; a question that no policy covers is escalated too. Any other
        result stays inside policy and agent authority: the agent carries it out itself (see
        `execution`) or answers it, and no human decision is needed.
        """
        return self.recommendation == "escalate"

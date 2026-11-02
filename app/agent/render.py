"""Fill in the customer's first name after generation (the LLM never sees names).

Where it sits: the last step before the reply is returned to the customer. It also makes the
sign-off say "ResolveRight Customer Support" when the model closed the draft with the store name.
"""

import re

from app.tools.orders import get_customer

SIGN_OFF = "ResolveRight Customer Support"

# Only a closing signature at the very end of the text, e.g. "Brightcart Support",
# "Brightcart Customer Care Team", with optional trailing punctuation. A mention of the store in
# the body ("Under Brightcart policy ...") never matches.
_STORE_SIGN_OFF = re.compile(
    r"\bBrightcart(?:\s+Customer)?\s+(?:Support|Care)(?:\s+Team)?[\s.!]*$", re.IGNORECASE
)


def render_reply(text: str, customer_id: str) -> str:
    """Replace the {{first_name}} placeholder and normalise a store-name sign-off.

    Args:
        text: Draft reply that may contain the {{first_name}} placeholder.
        customer_id: Customer whose first name to use.

    Returns:
        The reply text; "there" is used if no name is on file ("Hi there"). A closing line such
        as "Brightcart Support" becomes "ResolveRight Customer Support".
    """
    first = (get_customer(customer_id) or {}).get("first_name") or "there"
    return _STORE_SIGN_OFF.sub(SIGN_OFF, text.replace("{{first_name}}", first))

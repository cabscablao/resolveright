"""render_reply: first name filled in, and the store-name sign-off becomes ResolveRight."""

import pytest

from app.agent.render import SIGN_OFF, render_reply


@pytest.mark.parametrize(
    "closing",
    [
        "Brightcart Support",
        "Brightcart Customer Care Team",
        "Brightcart Support.",
        "brightcart support",
    ],
)
def test_store_name_sign_off_is_replaced(closing):
    """A closing signature of the store name is replaced, whatever its exact form."""
    out = render_reply(f"Hi {{{{first_name}}}}, ok.\n\nBest regards,\n{closing}", "C-001")
    assert out.endswith(SIGN_OFF) and "Brightcart Support" not in out
    assert out.startswith("Hi Nora")


def test_body_mentions_of_the_store_are_left_alone():
    """Only a final signature changes; "Brightcart policy" in the body stays."""
    text = "Hi {{first_name}}, under Brightcart policy POL-01 you can return it."
    assert render_reply(text, "C-001") == (
        "Hi Nora, under Brightcart policy POL-01 you can return it."
    )


def test_reply_without_a_sign_off_only_gets_the_name():
    """No signature: nothing is added."""
    assert render_reply("Hi {{first_name}}, done.", "C-001") == "Hi Nora, done."

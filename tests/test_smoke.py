"""Smoke tests: the most basic checks that config and logging work at all."""

import json
from datetime import date

from app import config
from app.observability.logging import log_event


def test_config_constants():
    """App name, store name and the fixed "today" date are as expected.

    The fixed date matters: return windows are counted from it, so tests stay stable.
    """
    assert config.APP_NAME == "ResolveRight"
    assert config.STORE_NAME == "Brightcart"
    assert config.AS_OF_DATE == date(2026, 11, 2)


def test_log_line_is_json_and_drops_unknown_fields(capsys):
    """Logs are valid JSON and never print fields that are not on the allow-list.

    The raw customer message could hold personal data, so it must not be logged.
    """
    log_event("smoke", request_id="r1", raw_message="secret@example.test")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["event"] == "smoke"
    assert line["request_id"] == "r1"
    assert "raw_message" not in line

# Copyright 2026. Tests for session hash generation and ticket key extraction.

import re
from unittest.mock import patch

from forge.core.session_hash import generate_session_hash, extract_ticket_key


class TestGenerateSessionHash:
    def test_no_ticket_format(self):
        h = generate_session_hash()
        assert re.match(r"^session-\d{8}-[0-9a-f]{4}$", h)

    def test_with_ticket_key(self):
        h = generate_session_hash("PROJ-123")
        assert re.match(r"^PROJ-123-\d{8}-[0-9a-f]{4}$", h)

    def test_ticket_key_sanitized(self):
        h = generate_session_hash("PROJ@123!foo")
        # Non-alphanumeric except hyphens are stripped
        assert re.match(r"^PROJ123foo-\d{8}-[0-9a-f]{4}$", h)

    def test_uniqueness(self):
        hashes = {generate_session_hash("T-1") for _ in range(20)}
        assert len(hashes) == 20

    def test_date_portion_is_today(self):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        h = generate_session_hash()
        assert today in h

    def test_empty_string_ticket(self):
        h = generate_session_hash("")
        assert h.startswith("session-")


class TestExtractTicketKey:
    def test_standard_ticket(self):
        assert extract_ticket_key("fix PROJ-456 bug") == "PROJ-456"

    def test_no_match(self):
        assert extract_ticket_key("no ticket here") == ""

    def test_first_match_returned(self):
        assert extract_ticket_key("PROJ-1 and PROJ-2") == "PROJ-1"

    def test_ticket_at_start(self):
        assert extract_ticket_key("HZ-99 is the issue") == "HZ-99"

    def test_ticket_at_end(self):
        assert extract_ticket_key("working on PROJ-1234") == "PROJ-1234"

    @patch.dict("os.environ", {"FORGE_TICKET_PATTERN": r"CUSTOM-\d{3}"})
    def test_custom_pattern_via_env(self):
        assert extract_ticket_key("see CUSTOM-007") == "CUSTOM-007"

    @patch.dict("os.environ", {"FORGE_TICKET_PATTERN": r"CUSTOM-\d{3}"})
    def test_custom_pattern_no_match(self):
        assert extract_ticket_key("PROJ-123") == ""

    def test_empty_input(self):
        assert extract_ticket_key("") == ""

    def test_lowercase_not_matched(self):
        assert extract_ticket_key("proj-123") == ""

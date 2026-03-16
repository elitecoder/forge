# Copyright 2026. Session hash generation — links planner sessions to executor runs.

import os
import re
import secrets
from datetime import datetime, timezone


def generate_session_hash(ticket_key: str = "") -> str:
    """Generate a unique session identifier.

    Format: {ticket}-{date}-{random4}
    Example: PROJ-123-20260307-a1b2
    """
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(2)  # 4 hex chars

    if ticket_key:
        safe_key = re.sub(r"[^A-Za-z0-9-]", "", ticket_key)
        return f"{safe_key}-{date}-{suffix}"

    return f"session-{date}-{suffix}"


def extract_ticket_key(text: str) -> str:
    """Extract a ticket key (e.g. PROJ-123) from text.

    Respects FORGE_TICKET_PATTERN env var for custom ticket formats.
    """
    pattern = os.environ.get("FORGE_TICKET_PATTERN", r"[A-Z]+-\d+")
    match = re.search(pattern, text)
    return match.group(0) if match else ""

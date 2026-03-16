# Copyright 2026. Session store protocol — pluggable storage for session artifacts.

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Pluggable storage for session artifacts and comments."""

    def upload_session(self, ticket_key: str, zip_path: Path, session_hash: str) -> bool:
        """Upload a session zip. Returns True on success."""
        ...

    def download_latest_session(self, ticket_key: str, dest_dir: Path, prefix: str) -> Path | None:
        """Download the most recent session zip matching prefix. Returns extracted dir or None."""
        ...

    def post_comment(self, ticket_key: str, comment: str) -> bool:
        """Post a comment (success summaries, failure triage, etc). Returns True on success."""
        ...

    def get_comments(self, ticket_key: str) -> list[dict]:
        """Read Forge-posted comments. Returns list of {session_hash, body, created}."""
        ...

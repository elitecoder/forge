# Copyright 2026. Jira session store — attachments and comments via REST API.

import io
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_FORGE_COMMENT_MARKER = "[forge-session:"
_EXECUTOR_COMMENT_MARKER = "[forge-executor:"
_TIMEOUT = 30


def _markdown_to_jira_markup(md: str) -> str:
    """Convert common Markdown formatting to Jira wiki markup."""
    lines = md.split("\n")
    result: list[str] = []
    in_code_block = False

    for line in lines:
        # Code block fences
        if line.strip().startswith("```"):
            if in_code_block:
                result.append("{code}")
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                result.append(f"{{code:{lang}}}" if lang else "{code}")
                in_code_block = True
            continue

        if in_code_block:
            result.append(line)
            continue

        # Headings
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            line = f"h{level}. {m.group(2)}"
            line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
            line = re.sub(r"`([^`]+)`", r"{{\1}}", line)
            result.append(line)
            continue

        # Unordered list items (- or *)
        m = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if m:
            depth = len(m.group(1)) // 2 + 1
            line = "*" * depth + " " + m.group(2)

        # Numbered list items
        if not m:
            m2 = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
            if m2:
                depth = len(m2.group(1)) // 2 + 1
                line = "#" * depth + " " + m2.group(2)

        # Inline: **bold** → *bold*, `code` → {{code}}
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"`([^`]+)`", r"{{\1}}", line)

        result.append(line)

    return "\n".join(result)


class JiraSessionStore:
    """Session store backed by Jira REST API (attachments + comments)."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        self.token = os.environ.get("JIRA_TOKEN", "")

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        h: dict[str, str] = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def upload_session(self, ticket_key: str, zip_path: Path, session_hash: str) -> bool:
        if not (self.token and self.base_url):
            return False

        url = f"{self.base_url}/rest/api/2/issue/{ticket_key}/attachments"
        boundary = "----ForgeUploadBoundary"

        try:
            file_data = zip_path.read_bytes()
        except OSError:
            return False

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{zip_path.name}"\r\n'
            f"Content-Type: application/zip\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

        req = Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("X-Atlassian-Token", "no-check")

        try:
            urlopen(req, timeout=_TIMEOUT)
            return True
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def download_latest_session(self, ticket_key: str, dest_dir: Path, prefix: str) -> Path | None:
        if not (self.token and self.base_url):
            return None

        url = f"{self.base_url}/rest/api/2/issue/{ticket_key}?fields=attachment"
        req = Request(url, headers=self._headers())

        try:
            data = json.loads(urlopen(req, timeout=_TIMEOUT).read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None

        attachments = data.get("fields", {}).get("attachment", [])
        matching = [
            a for a in attachments
            if a.get("filename", "").startswith(prefix) and a.get("filename", "").endswith(".zip")
        ]

        if not matching:
            return None

        latest = max(matching, key=lambda a: a.get("created", ""))
        content_url = latest.get("content", "")
        if not content_url:
            return None

        dl_req = Request(content_url, headers=self._headers(""))
        try:
            zip_data = urlopen(dl_req, timeout=60).read()
        except (HTTPError, URLError, TimeoutError, OSError):
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                zf.extractall(dest_dir)
        except (zipfile.BadZipFile, OSError):
            return None

        return dest_dir

    def post_comment(self, ticket_key: str, comment: str) -> bool:
        if not (self.token and self.base_url):
            return False

        url = f"{self.base_url}/rest/api/2/issue/{ticket_key}/comment"
        body = json.dumps({"body": _markdown_to_jira_markup(comment)}).encode()
        req = Request(url, data=body, method="POST", headers=self._headers())

        try:
            urlopen(req, timeout=_TIMEOUT)
            return True
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def update_comment(self, ticket_key: str, comment_id: str, comment: str) -> bool:
        if not (self.token and self.base_url):
            return False

        url = f"{self.base_url}/rest/api/2/issue/{ticket_key}/comment/{comment_id}"
        body = json.dumps({"body": _markdown_to_jira_markup(comment)}).encode()
        req = Request(url, data=body, method="PUT", headers=self._headers())

        try:
            urlopen(req, timeout=_TIMEOUT)
            return True
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def get_comments(self, ticket_key: str) -> list[dict]:
        if not (self.token and self.base_url):
            return []

        url = f"{self.base_url}/rest/api/2/issue/{ticket_key}/comment"
        req = Request(url, headers=self._headers())

        try:
            data = json.loads(urlopen(req, timeout=_TIMEOUT).read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return []

        result = []
        for c in data.get("comments", []):
            body = c.get("body", "")
            if _FORGE_COMMENT_MARKER not in body and _EXECUTOR_COMMENT_MARKER not in body:
                continue

            session_hash = ""
            m = re.search(r"\[forge-(?:session|executor):([^\]]+)\]", body)
            if m:
                session_hash = m.group(1)

            result.append({
                "session_hash": session_hash,
                "body": body,
                "created": c.get("created", ""),
            })

        return result

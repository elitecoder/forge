# Copyright 2026. Tests for Jira session store — markdown conversion and REST API mocking.

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from forge.integrations.jira_store import JiraSessionStore, _markdown_to_jira_markup


class TestMarkdownToJiraMarkup:
    def test_heading_h1(self):
        assert _markdown_to_jira_markup("# Title") == "h1. Title"

    def test_heading_h3(self):
        assert _markdown_to_jira_markup("### Sub") == "h3. Sub"

    def test_bold(self):
        assert _markdown_to_jira_markup("**bold text**") == "*bold text*"

    def test_inline_code(self):
        assert _markdown_to_jira_markup("`code`") == "{{code}}"

    def test_code_block(self):
        md = "```python\nprint('hi')\n```"
        result = _markdown_to_jira_markup(md)
        assert "{code:python}" in result
        assert "print('hi')" in result
        assert result.endswith("{code}")

    def test_code_block_no_lang(self):
        md = "```\nsome code\n```"
        result = _markdown_to_jira_markup(md)
        assert result.startswith("{code}")

    def test_unordered_list(self):
        result = _markdown_to_jira_markup("- item one\n- item two")
        assert "* item one" in result
        assert "* item two" in result

    def test_nested_list(self):
        result = _markdown_to_jira_markup("- top\n  - nested")
        assert "* top" in result
        assert "** nested" in result

    def test_numbered_list(self):
        result = _markdown_to_jira_markup("1. first\n2. second")
        assert "# first" in result
        assert "# second" in result

    def test_heading_with_bold(self):
        result = _markdown_to_jira_markup("## **Important** heading")
        assert "h2. *Important* heading" in result

    def test_heading_with_code(self):
        result = _markdown_to_jira_markup("## The `main` function")
        assert "h2. The {{main}} function" in result

    def test_mixed_content(self):
        md = "# Title\n\nSome **bold** and `code` here.\n\n- list item"
        result = _markdown_to_jira_markup(md)
        assert "h1. Title" in result
        assert "*bold*" in result
        assert "{{code}}" in result
        assert "* list item" in result


class TestJiraSessionStoreInit:
    @patch.dict("os.environ", {"JIRA_BASE_URL": "https://jira.example.com/", "JIRA_TOKEN": "tok"})
    def test_reads_env(self):
        store = JiraSessionStore()
        assert store.base_url == "https://jira.example.com"  # trailing slash stripped
        assert store.token == "tok"

    @patch.dict("os.environ", {}, clear=True)
    def test_defaults(self):
        store = JiraSessionStore()
        assert "jira.example.com" in store.base_url
        assert store.token == ""


class TestUploadSession:
    def test_no_token_returns_false(self):
        store = JiraSessionStore()
        store.token = ""
        assert store.upload_session("PROJ-1", Path("/tmp/f.zip"), "h") is False

    @patch("forge.integrations.jira_store.urlopen")
    def test_success(self, mock_urlopen, tmp_path):
        store = JiraSessionStore()
        store.token = "tok"
        zip_path = tmp_path / "session.zip"
        zip_path.write_bytes(b"PK\x03\x04fake")
        mock_urlopen.return_value = MagicMock()
        assert store.upload_session("PROJ-1", zip_path, "h") is True
        mock_urlopen.assert_called_once()

    @patch("forge.integrations.jira_store.urlopen")
    def test_http_error_returns_false(self, mock_urlopen, tmp_path):
        from urllib.error import HTTPError
        store = JiraSessionStore()
        store.token = "tok"
        zip_path = tmp_path / "session.zip"
        zip_path.write_bytes(b"data")
        mock_urlopen.side_effect = HTTPError(None, 500, "err", {}, None)
        assert store.upload_session("PROJ-1", zip_path, "h") is False

    def test_missing_file_returns_false(self, tmp_path):
        store = JiraSessionStore()
        store.token = "tok"
        assert store.upload_session("PROJ-1", tmp_path / "nonexistent.zip", "h") is False


class TestDownloadLatestSession:
    def test_no_token_returns_none(self):
        store = JiraSessionStore()
        store.token = ""
        assert store.download_latest_session("PROJ-1", Path("/tmp"), "session-") is None

    @patch("forge.integrations.jira_store.urlopen")
    def test_no_matching_attachments(self, mock_urlopen):
        store = JiraSessionStore()
        store.token = "tok"
        response = MagicMock()
        response.read.return_value = json.dumps({
            "fields": {"attachment": [{"filename": "other.txt", "created": "2026-01-01"}]}
        }).encode()
        mock_urlopen.return_value = response
        assert store.download_latest_session("PROJ-1", Path("/tmp/d"), "session-") is None


class TestPostComment:
    def test_no_token_returns_false(self):
        store = JiraSessionStore()
        store.token = ""
        assert store.post_comment("PROJ-1", "hello") is False

    @patch("forge.integrations.jira_store.urlopen")
    def test_success(self, mock_urlopen):
        store = JiraSessionStore()
        store.token = "tok"
        mock_urlopen.return_value = MagicMock()
        assert store.post_comment("PROJ-1", "**bold**") is True


class TestUpdateComment:
    def test_no_token_returns_false(self):
        store = JiraSessionStore()
        store.token = ""
        assert store.update_comment("PROJ-1", "123", "text") is False

    @patch("forge.integrations.jira_store.urlopen")
    def test_success(self, mock_urlopen):
        store = JiraSessionStore()
        store.token = "tok"
        mock_urlopen.return_value = MagicMock()
        assert store.update_comment("PROJ-1", "456", "updated") is True


class TestGetComments:
    def test_no_token_returns_empty(self):
        store = JiraSessionStore()
        store.token = ""
        assert store.get_comments("PROJ-1") == []

    @patch("forge.integrations.jira_store.urlopen")
    def test_filters_forge_comments(self, mock_urlopen):
        store = JiraSessionStore()
        store.token = "tok"
        response_data = {
            "comments": [
                {"body": "[forge-session:abc] some content", "created": "2026-01-01"},
                {"body": "normal comment"},
                {"body": "[forge-executor:def] executor comment", "created": "2026-01-02"},
            ]
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(response_data).encode()
        mock_urlopen.return_value = mock_response
        result = store.get_comments("PROJ-1")
        assert len(result) == 2
        assert result[0]["session_hash"] == "abc"
        assert result[1]["session_hash"] == "def"

# Copyright 2026. Tests for session lifecycle — create, list, cleanup, find.

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from forge.core.session import (
    create_session, list_sessions, cleanup_sessions,
    find_running_session, find_active_session,
)


class TestCreateSession:
    def test_creates_directory(self, tmp_path):
        with patch("forge.core.session.SESSIONS_BASE", tmp_path):
            path = create_session("executor", slug="my-test")
        assert path.is_dir()
        assert "executor" in str(path)
        assert "my-test" in path.name

    def test_no_slug(self, tmp_path):
        with patch("forge.core.session.SESSIONS_BASE", tmp_path):
            path = create_session("planner")
        assert path.is_dir()
        # Name is just the timestamp
        assert "_" not in path.name or path.name.count("_") == 0 or "Z" in path.name

    def test_nested_base(self, tmp_path):
        with patch("forge.core.session.SESSIONS_BASE", tmp_path):
            path = create_session("planner", slug="deep")
        assert (tmp_path / "planner").is_dir()


class TestListSessions:
    def test_empty_dir(self, tmp_path):
        assert list_sessions(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert list_sessions(tmp_path / "nope") == []

    def test_lists_sessions(self, tmp_path):
        (tmp_path / "session-a").mkdir()
        (tmp_path / "session-b").mkdir()
        sessions = list_sessions(tmp_path)
        assert len(sessions) == 2
        names = {s["name"] for s in sessions}
        assert names == {"session-a", "session-b"}

    def test_includes_age(self, tmp_path):
        (tmp_path / "session-a").mkdir()
        sessions = list_sessions(tmp_path)
        assert "age_days" in sessions[0]
        assert sessions[0]["age_days"] >= 0

    def test_state_filename_active_flag(self, tmp_path):
        (tmp_path / "has-state").mkdir()
        (tmp_path / "has-state" / "state.json").write_text("{}")
        (tmp_path / "no-state").mkdir()
        sessions = list_sessions(tmp_path, state_filename="state.json")
        by_name = {s["name"]: s for s in sessions}
        assert by_name["has-state"]["active"] is True
        assert by_name["no-state"]["active"] is False

    def test_skips_files(self, tmp_path):
        (tmp_path / "session-a").mkdir()
        (tmp_path / "not-a-dir.txt").write_text("file")
        sessions = list_sessions(tmp_path)
        assert len(sessions) == 1


class TestCleanupSessions:
    def test_removes_old_sessions(self, tmp_path):
        old = tmp_path / "old-session"
        old.mkdir()
        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(old, (old_time, old_time))

        recent = tmp_path / "recent-session"
        recent.mkdir()

        with patch("forge.core.session.SESSIONS_BASE", tmp_path):
            removed = cleanup_sessions(tmp_path, older_than_days=30)

        assert len(removed) == 1
        assert "old-session" in removed[0]
        assert not old.exists()
        assert recent.exists()

    def test_nonexistent_dir(self, tmp_path):
        assert cleanup_sessions(tmp_path / "nope") == []

    def test_nothing_old(self, tmp_path):
        (tmp_path / "recent").mkdir()
        with patch("forge.core.session.SESSIONS_BASE", tmp_path):
            removed = cleanup_sessions(tmp_path, older_than_days=30)
        assert removed == []


class TestFindRunningSession:
    def test_finds_running(self, tmp_path):
        session = tmp_path / "running"
        session.mkdir()
        state = {"driver_pid": os.getpid()}  # current process is alive
        (session / "state.json").write_text(json.dumps(state))
        result = find_running_session(tmp_path, "state.json")
        assert result == session

    def test_skips_dead_pid(self, tmp_path):
        session = tmp_path / "dead"
        session.mkdir()
        state = {"driver_pid": 999999}  # unlikely to exist
        (session / "state.json").write_text(json.dumps(state))
        result = find_running_session(tmp_path, "state.json")
        assert result is None

    def test_skips_zero_pid(self, tmp_path):
        session = tmp_path / "no-pid"
        session.mkdir()
        state = {"driver_pid": 0}
        (session / "state.json").write_text(json.dumps(state))
        assert find_running_session(tmp_path, "state.json") is None

    def test_nonexistent_base(self, tmp_path):
        assert find_running_session(tmp_path / "nope", "state.json") is None

    def test_bad_json(self, tmp_path):
        session = tmp_path / "bad"
        session.mkdir()
        (session / "state.json").write_text("not json")
        assert find_running_session(tmp_path, "state.json") is None


class TestFindActiveSession:
    def test_finds_latest(self, tmp_path):
        a = tmp_path / "session-a"
        a.mkdir()
        (a / "state.json").write_text("{}")

        b = tmp_path / "session-b"
        b.mkdir()
        (b / "state.json").write_text("{}")
        # Touch b to make it newer
        os.utime(b, (time.time() + 1, time.time() + 1))

        result = find_active_session(tmp_path, "state.json")
        assert result == b

    def test_prefix_filter(self, tmp_path):
        matching = tmp_path / "PROJ-123_session"
        matching.mkdir()
        (matching / "state.json").write_text("{}")

        other = tmp_path / "OTHER_session"
        other.mkdir()
        (other / "state.json").write_text("{}")

        result = find_active_session(tmp_path, "state.json", prefix="PROJ-123")
        assert result == matching

    def test_no_state_file(self, tmp_path):
        (tmp_path / "session").mkdir()
        assert find_active_session(tmp_path, "state.json") is None

    def test_nonexistent_dir(self, tmp_path):
        assert find_active_session(tmp_path / "nope", "state.json") is None

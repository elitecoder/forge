# Integration tests for the dashboard HTTP API.
# Spins up a real server on a random port and hits endpoints.

import json
import threading
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from forge.dashboard.server import EnhancedSessionHandler


@pytest.fixture()
def dashboard(tmp_path):
    """Start a dashboard server on a random port with temp session dirs."""
    planner_dir = tmp_path / "planner"
    executor_dir = tmp_path / "executor"
    planner_dir.mkdir()
    executor_dir.mkdir()

    patches = {
        "PLANNER_DIR": planner_dir,
        "EXECUTOR_DIR": executor_dir,
        "ARCHITECT_DIR": tmp_path,
    }

    with patch.multiple("forge.dashboard.server", **patches):
        server = HTTPServer(("127.0.0.1", 0), EnhancedSessionHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        class Ctx:
            base_url = f"http://127.0.0.1:{port}"
            path = tmp_path
            planner = planner_dir
            executor = executor_dir

        yield Ctx()
        server.shutdown()


def _get(url):
    with urlopen(url) as resp:
        return resp.status, json.loads(resp.read())


def _get_html(url):
    with urlopen(url) as resp:
        return resp.status, resp.read().decode()


def _delete(url):
    req = Request(url, method="DELETE")
    with urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def _make_planner(base, name, problem="test", phases=None):
    sd = base / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / ".planner-state.json").write_text(json.dumps({
        "problem_statement": problem,
        "preset": "npm-ts",
        "repo_dir": "/tmp/repo",
        "phases": phases or {},
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-01T10:00:00Z",
        "driver_pid": 0,
    }))
    return sd


def _make_executor(base, name, steps=None, plan_file=""):
    sd = base / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "agent-state.json").write_text(json.dumps({
        "plan_file": plan_file,
        "preset": "npm-ts",
        "repo_dir": "/tmp/repo",
        "current_step": None,
        "steps": steps or {},
        "created_at": "2026-03-01T11:00:00Z",
        "updated_at": "2026-03-01T11:00:00Z",
        "driver_pid": 0,
    }))
    return sd


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestRootEndpoint:
    def test_serves_html(self, dashboard):
        status, body = _get_html(f"{dashboard.base_url}/")
        assert status == 200
        assert "Forge Dashboard" in body
        assert "<script>" in body

    def test_dashboard_alias(self, dashboard):
        status, body = _get_html(f"{dashboard.base_url}/dashboard")
        assert status == 200
        assert "Forge Dashboard" in body


# ---------------------------------------------------------------------------
# GET /sessions
# ---------------------------------------------------------------------------

class TestSessionsEndpoint:
    def test_empty(self, dashboard):
        status, data = _get(f"{dashboard.base_url}/sessions")
        assert status == 200
        assert data["projects"] == []
        assert data["total_sessions"] == 0

    def test_returns_planner(self, dashboard):
        _make_planner(dashboard.planner, "plan-1", problem="add login")
        status, data = _get(f"{dashboard.base_url}/sessions")
        assert status == 200
        assert data["total_sessions"] == 1
        assert data["projects"][0]["problem_statement"] == "add login"

    def test_cors_header(self, dashboard):
        with urlopen(f"{dashboard.base_url}/sessions") as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    def test_linked_planner_executor(self, dashboard):
        plan_sd = _make_planner(dashboard.planner, "plan-1")
        plan_file = str(plan_sd / "final-plan.md")
        Path(plan_file).write_text("# Plan")
        _make_executor(dashboard.executor, "exec-1", steps={
            "code": {"status": "complete"},
        }, plan_file=plan_file)

        status, data = _get(f"{dashboard.base_url}/sessions")
        assert len(data["projects"]) == 1
        assert len(data["projects"][0]["executors"]) == 1


# ---------------------------------------------------------------------------
# GET /artifacts
# ---------------------------------------------------------------------------

class TestArtifactsEndpoint:
    def test_missing_param(self, dashboard):
        with pytest.raises(HTTPError) as exc_info:
            _get(f"{dashboard.base_url}/artifacts")
        assert exc_info.value.code == 400

    def test_nonexistent_dir(self, dashboard):
        with pytest.raises(HTTPError) as exc_info:
            _get(f"{dashboard.base_url}/artifacts?session_dir=/nonexistent")
        assert exc_info.value.code == 404

    def test_empty_session(self, dashboard):
        sd = dashboard.path / "empty-session"
        sd.mkdir()
        status, data = _get(
            f"{dashboard.base_url}/artifacts?session_dir={sd}")
        assert status == 200
        assert data == {}

    def test_collects_artifacts(self, dashboard):
        sd = dashboard.path / "rich-session"
        sd.mkdir()
        (sd / "pr-url.txt").write_text("https://github.com/x/y/pull/99\n")
        (sd / "code-actions.json").write_text(json.dumps({
            "step": "code", "outcome": "pass",
            "files_read": ["a"], "files_written": ["b"],
            "commands": ["c"], "errors": [],
        }))

        status, data = _get(
            f"{dashboard.base_url}/artifacts?session_dir={sd}")
        assert status == 200
        assert data["pr_url"] == "https://github.com/x/y/pull/99"
        assert "code" in data["step_actions"]


# ---------------------------------------------------------------------------
# DELETE /session/<id>
# ---------------------------------------------------------------------------

class TestDeleteEndpoint:
    def test_deletes_planner(self, dashboard):
        _make_planner(dashboard.planner, "doomed")
        assert (dashboard.planner / "doomed").exists()

        status, data = _delete(f"{dashboard.base_url}/session/doomed")
        assert status == 200
        assert data["success"] is True
        assert not (dashboard.planner / "doomed").exists()

    def test_not_found(self, dashboard):
        status, data = _delete(f"{dashboard.base_url}/session/ghost")
        assert status == 200
        assert data["success"] is False


# ---------------------------------------------------------------------------
# 404 for unknown routes
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_path(self, dashboard):
        with pytest.raises(HTTPError) as exc_info:
            _get(f"{dashboard.base_url}/nope")
        assert exc_info.value.code == 404

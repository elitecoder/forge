# Tests for forge.dashboard.server — unit tests for session parsing and artifacts.

import json
import os
from pathlib import Path
from unittest.mock import patch


from forge.dashboard.server import EnhancedSessionHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_planner(base, name, *, phases=None, problem="test task",
                  pid=0, created_at="2026-03-01T10:00:00Z"):
    sd = base / "planner" / name
    sd.mkdir(parents=True)
    state = {
        "problem_statement": problem,
        "preset": "npm-ts",
        "repo_dir": "/tmp/repo",
        "phases": phases or {},
        "created_at": created_at,
        "updated_at": created_at,
        "driver_pid": pid,
    }
    (sd / ".planner-state.json").write_text(json.dumps(state))
    return sd


def _make_executor(base, name, *, steps=None, plan_file="",
                   pid=0, created_at="2026-03-01T11:00:00Z"):
    sd = base / "executor" / name
    sd.mkdir(parents=True)
    state = {
        "plan_file": plan_file,
        "preset": "npm-ts",
        "repo_dir": "/tmp/repo",
        "current_step": None,
        "steps": steps or {},
        "created_at": created_at,
        "updated_at": created_at,
        "driver_pid": pid,
    }
    (sd / "agent-state.json").write_text(json.dumps(state))
    return sd


def _patch_dirs(tmp_path):
    """Return a dict of patches pointing PLANNER_DIR / EXECUTOR_DIR at tmp_path."""
    return {
        "forge.dashboard.server.PLANNER_DIR": tmp_path / "planner",
        "forge.dashboard.server.EXECUTOR_DIR": tmp_path / "executor",
        "forge.dashboard.server.ARCHITECT_DIR": tmp_path,
    }


# ---------------------------------------------------------------------------
# get_planner_info
# ---------------------------------------------------------------------------

class TestGetPlannerInfo:
    def test_returns_none_for_missing_state(self, tmp_path):
        sd = tmp_path / "planner" / "empty"
        sd.mkdir(parents=True)
        inst = object.__new__(EnhancedSessionHandler)
        assert inst.get_planner_info(sd) is None

    def test_parses_completed_planner(self, tmp_path):
        sd = _make_planner(tmp_path, "plan-1", phases={
            "recon": {"status": "complete"},
            "architects": {"status": "complete"},
        })
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_planner_info(sd)
        assert info is not None
        assert info["session_id"] == "plan-1"
        assert info["status"] == "completed"
        assert info["problem_statement"] == "test task"
        assert info["type"] == "planner"

    def test_detects_active_phase(self, tmp_path):
        sd = _make_planner(tmp_path, "plan-active", phases={
            "recon": {"status": "complete"},
            "architects": {"status": "in_progress"},
        }, pid=os.getpid())  # Use own PID so kill(pid, 0) succeeds
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_planner_info(sd)
        assert info["status"] == "active"
        assert info["current_phase"] == "architects"

    def test_stale_pid_marks_completed(self, tmp_path):
        sd = _make_planner(tmp_path, "plan-stale", phases={
            "recon": {"status": "in_progress"},
        }, pid=99999999)  # Non-existent PID
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_planner_info(sd)
        assert info["status"] == "completed"


# ---------------------------------------------------------------------------
# get_executor_info
# ---------------------------------------------------------------------------

class TestGetExecutorInfo:
    def test_returns_none_for_missing_state(self, tmp_path):
        sd = tmp_path / "executor" / "empty"
        sd.mkdir(parents=True)
        inst = object.__new__(EnhancedSessionHandler)
        assert inst.get_executor_info(sd) is None

    def test_parses_completed_executor(self, tmp_path):
        sd = _make_executor(tmp_path, "exec-1", steps={
            "code": {"status": "complete"},
            "test": {"status": "complete"},
        })
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_executor_info(sd)
        assert info["session_id"] == "exec-1"
        assert info["status"] == "completed"
        assert info["type"] == "executor"

    def test_detects_active_via_pid(self, tmp_path):
        """Active detection should use PID check, not just step status."""
        sd = _make_executor(tmp_path, "exec-active", steps={
            "code": {"status": "complete"},
            "test": {"status": "pending"},
        }, pid=os.getpid())
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_executor_info(sd)
        assert info["status"] == "active"

    def test_detects_active_via_step_status(self, tmp_path):
        """Fallback: step in_progress without PID."""
        sd = _make_executor(tmp_path, "exec-step", steps={
            "code": {"status": "in_progress"},
        })
        # Patch agent-state to set current_step
        state_file = sd / "agent-state.json"
        state = json.loads(state_file.read_text())
        state["current_step"] = "code"
        state_file.write_text(json.dumps(state))

        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_executor_info(sd)
        assert info["status"] == "active"

    def test_stale_pid_marks_completed(self, tmp_path):
        sd = _make_executor(tmp_path, "exec-stale", steps={
            "code": {"status": "complete"},
        }, pid=99999999)
        inst = object.__new__(EnhancedSessionHandler)
        info = inst.get_executor_info(sd)
        assert info["status"] == "completed"


# ---------------------------------------------------------------------------
# get_all_sessions — grouping, linking, status rollup
# ---------------------------------------------------------------------------

class TestGetAllSessions:
    def _get_sessions(self, tmp_path):
        inst = object.__new__(EnhancedSessionHandler)
        patches = _patch_dirs(tmp_path)
        with patch.multiple("forge.dashboard.server", **{
            k.split(".")[-1]: v for k, v in patches.items()
        }):
            return inst.get_all_sessions()

    def test_empty(self, tmp_path):
        result = self._get_sessions(tmp_path)
        assert result["projects"] == []
        assert result["total_sessions"] == 0

    def test_planner_only(self, tmp_path):
        _make_planner(tmp_path, "plan-1", problem="build feature X")
        result = self._get_sessions(tmp_path)
        assert result["total_sessions"] == 1
        assert len(result["projects"]) == 1
        p = result["projects"][0]
        assert p["problem_statement"] == "build feature X"
        assert p["overall_status"] == "inactive"
        assert p["executors"] == []

    def test_linked_executor_sets_completed(self, tmp_path):
        plan_sd = _make_planner(tmp_path, "plan-1")
        plan_file = str(plan_sd / "final-plan.md")
        Path(plan_file).write_text("# Plan")
        _make_executor(tmp_path, "exec-1", steps={
            "code": {"status": "complete"},
        }, plan_file=plan_file)

        result = self._get_sessions(tmp_path)
        assert len(result["projects"]) == 1
        p = result["projects"][0]
        assert len(p["executors"]) == 1
        assert p["overall_status"] == "completed"

    def test_linked_executor_failed_status(self, tmp_path):
        plan_sd = _make_planner(tmp_path, "plan-1")
        plan_file = str(plan_sd / "final-plan.md")
        Path(plan_file).write_text("# Plan")
        _make_executor(tmp_path, "exec-1", steps={
            "code": {"status": "complete"},
            "test": {"status": "failed"},
        }, plan_file=plan_file)

        result = self._get_sessions(tmp_path)
        p = result["projects"][0]
        assert p["overall_status"] == "failed"

    def test_active_executor_sets_executing(self, tmp_path):
        plan_sd = _make_planner(tmp_path, "plan-1")
        plan_file = str(plan_sd / "final-plan.md")
        Path(plan_file).write_text("# Plan")
        _make_executor(tmp_path, "exec-1", steps={
            "code": {"status": "complete"},
        }, plan_file=plan_file, pid=os.getpid())

        result = self._get_sessions(tmp_path)
        p = result["projects"][0]
        assert p["overall_status"] == "executing"
        assert result["total_active"] == 1

    def test_orphaned_executor(self, tmp_path):
        _make_executor(tmp_path, "orphan-1", steps={
            "code": {"status": "complete"},
        }, plan_file="/nonexistent/plan.md")

        result = self._get_sessions(tmp_path)
        assert len(result["projects"]) == 1
        p = result["projects"][0]
        assert p["overall_status"] == "orphaned"

    def test_multiple_projects_sorted_by_date(self, tmp_path):
        _make_planner(tmp_path, "old", problem="old task",
                      created_at="2026-01-01T00:00:00Z")
        _make_planner(tmp_path, "new", problem="new task",
                      created_at="2026-03-01T00:00:00Z")

        result = self._get_sessions(tmp_path)
        assert len(result["projects"]) == 2
        assert result["projects"][0]["problem_statement"] == "new task"
        assert result["projects"][1]["problem_statement"] == "old task"


# ---------------------------------------------------------------------------
# Artifacts collection
# ---------------------------------------------------------------------------

class TestArtifacts:
    """Test the artifact collection logic by calling handle_artifacts internals."""

    def _make_session_with_artifacts(self, tmp_path):
        sd = tmp_path / "session"
        sd.mkdir()

        # code-checklist.json
        (sd / "code-checklist.json").write_text(json.dumps({
            "checklist": [
                {"id": "1", "status": "done"},
                {"id": "2", "status": "done"},
                {"id": "3", "status": "pending"},
            ],
            "files_created": ["a.ts", "b.ts"],
            "files_modified": ["c.ts"],
        }))

        # Various step actions
        (sd / "code-actions.json").write_text(json.dumps({
            "step": "code", "outcome": "pass",
            "files_read": ["x"], "files_written": ["y"],
            "commands": ["a", "b"], "errors": [],
        }))
        (sd / "e2e-actions.json").write_text(json.dumps({
            "step": "e2e", "outcome": "pass",
            "files_read": [], "files_written": [],
            "commands": ["npm test"], "errors": [],
        }))
        (sd / "smoke-actions.json").write_text(json.dumps({
            "step": "smoke", "outcome": "pass",
            "files_read": [], "files_written": [],
            "commands": ["pw:smoke"], "errors": [],
        }))
        (sd / "lint-actions.json").write_text(json.dumps({
            "step": "lint", "outcome": "pass",
            "commands": ["eslint ."], "errors": [],
        }))

        # e2e-checklist.json
        (sd / "e2e-checklist.json").write_text(json.dumps({
            "step": "e2e",
            "checklist": [{"id": "e2e-tests", "criteria": "All pass",
                           "status": "done", "evidence": "50 passed"}],
        }))

        # code-review-verdict.json
        (sd / "code-review-verdict.json").write_text(json.dumps({
            "verdict": "CLEAN", "issues": [],
        }))

        # Retries
        retries = sd / "_retries" / "code_review_attempt_1"
        retries.mkdir(parents=True)
        (retries / "code-review-verdict.json").write_text(json.dumps({
            "verdict": "HAS_ISSUES", "issue_count": 2,
            "issues": [{"file": "a.ts", "message": "bug"}],
        }))

        # PR URL
        (sd / "pr-url.txt").write_text("https://github.com/org/repo/pull/42\n")

        # agent-state.json with plan_file
        plan = tmp_path / "plan.md"
        plan.write_text("# My Plan\nDo stuff\nMore stuff\n")
        (sd / "agent-state.json").write_text(json.dumps({
            "plan_file": str(plan),
        }))

        # Checkpoints
        cp = sd / "checkpoints"
        cp.mkdir()
        (cp / "code.passed").write_text("step=code\npassed_at=2026-03-01T10:00:00Z\n")
        (cp / "test.passed").write_text("step=test\npassed_at=2026-03-01T10:01:00Z\n")

        # events.jsonl
        (sd / "events.jsonl").write_text(
            json.dumps({"event": "step_started", "step": "code"}) + "\n"
            + json.dumps({"event": "step_complete", "step": "code"}) + "\n"
        )

        return sd

    def test_collects_all_artifacts(self, tmp_path):
        """Verify the artifact endpoint collects data from all file types."""
        sd = self._make_session_with_artifacts(tmp_path)

        # Simulate what handle_artifacts does by importing and calling
        # We'll use a simplified approach — import the server module
        # and create a mock request
        import io
        from unittest.mock import MagicMock

        inst = object.__new__(EnhancedSessionHandler)
        inst.path = f"/artifacts?session_dir={sd}"
        inst.wfile = io.BytesIO()
        inst.send_response = MagicMock()
        inst.send_header = MagicMock()
        inst.end_headers = MagicMock()

        inst.handle_artifacts()

        result = json.loads(inst.wfile.getvalue().decode())

        # Code checklist
        assert result["code_checklist"]["total"] == 3
        assert result["code_checklist"]["done"] == 2
        assert result["code_checklist"]["files_created"] == 2

        # Step actions (generic collection)
        assert "code" in result["step_actions"]
        assert "e2e" in result["step_actions"]
        assert "smoke" in result["step_actions"]
        assert result["step_actions"]["smoke"]["outcome"] == "pass"

        # Step checklists
        assert "e2e" in result["step_checklists"]

        # Legacy keys
        assert result["e2e_actions"]["outcome"] == "pass"
        assert result["lint_actions"]["outcome"] == "pass"

        # Code review
        assert result["code_review"]["verdict"] == "CLEAN"
        assert len(result["code_review_retries"]) == 1
        assert result["code_review_retries"][0]["verdict"] == "HAS_ISSUES"

        # PR
        assert result["pr_url"] == "https://github.com/org/repo/pull/42"

        # Plan
        assert result["plan_file"].endswith("plan.md")
        assert "My Plan" in result["plan_summary"]
        assert "More stuff" in result["plan_summary"]  # Not truncated

        # Checkpoints
        assert "code" in result["checkpoints"]
        assert result["checkpoints"]["code"]["step"] == "code"

        # Events
        assert len(result["events"]) == 2

    def test_empty_session_dir(self, tmp_path):
        sd = tmp_path / "empty"
        sd.mkdir()

        inst = object.__new__(EnhancedSessionHandler)
        inst.path = f"/artifacts?session_dir={sd}"

        import io
        from unittest.mock import MagicMock
        inst.wfile = io.BytesIO()
        inst.send_response = MagicMock()
        inst.send_header = MagicMock()
        inst.end_headers = MagicMock()

        inst.handle_artifacts()
        result = json.loads(inst.wfile.getvalue().decode())
        assert result == {}

    def test_corrupt_json_skipped(self, tmp_path):
        sd = tmp_path / "corrupt"
        sd.mkdir()
        (sd / "code-actions.json").write_text("not json {{{")
        (sd / "e2e-checklist.json").write_text("also broken")

        inst = object.__new__(EnhancedSessionHandler)
        inst.path = f"/artifacts?session_dir={sd}"

        import io
        from unittest.mock import MagicMock
        inst.wfile = io.BytesIO()
        inst.send_response = MagicMock()
        inst.send_header = MagicMock()
        inst.end_headers = MagicMock()

        inst.handle_artifacts()
        result = json.loads(inst.wfile.getvalue().decode())
        # Should not crash, corrupt files are silently skipped
        assert "step_actions" not in result or "code" not in result.get("step_actions", {})


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

class TestDeleteSession:
    def test_deletes_planner(self, tmp_path):
        sd = _make_planner(tmp_path, "to-delete")
        assert sd.exists()

        inst = object.__new__(EnhancedSessionHandler)
        with patch("forge.dashboard.server.PLANNER_DIR", tmp_path / "planner"), \
             patch("forge.dashboard.server.EXECUTOR_DIR", tmp_path / "executor"):
            result = inst.delete_session("to-delete")

        assert result["success"] is True
        assert not sd.exists()

    def test_deletes_executor(self, tmp_path):
        sd = _make_executor(tmp_path, "to-delete")
        assert sd.exists()

        inst = object.__new__(EnhancedSessionHandler)
        with patch("forge.dashboard.server.PLANNER_DIR", tmp_path / "planner"), \
             patch("forge.dashboard.server.EXECUTOR_DIR", tmp_path / "executor"):
            result = inst.delete_session("to-delete")

        assert result["success"] is True
        assert not sd.exists()

    def test_not_found(self, tmp_path):
        inst = object.__new__(EnhancedSessionHandler)
        with patch("forge.dashboard.server.PLANNER_DIR", tmp_path / "planner"), \
             patch("forge.dashboard.server.EXECUTOR_DIR", tmp_path / "executor"):
            result = inst.delete_session("nonexistent")

        assert result["success"] is False

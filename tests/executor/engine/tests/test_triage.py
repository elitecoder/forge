# Copyright 2026. Tests for triage agent dispatch — prompt building and failure detection.

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from forge.executor.engine.state import PipelineState, StepState, StepStatus, StateManager
from forge.executor.engine.triage import (
    build_triage_prompt, find_failed_step, _ticket_from_session,
    _planner_session_dir, _build_visual_test_section, run_triage,
)


class TestTicketFromSession:
    def test_extracts_ticket(self):
        assert _ticket_from_session("/sessions/PROJ-123_2026-01-01") == "PROJ-123"

    def test_no_ticket(self):
        assert _ticket_from_session("/sessions/no-ticket") == ""

    def test_trailing_slash(self):
        assert _ticket_from_session("/sessions/HZ-99_session/") == "HZ-99"


class TestPlannerSessionDir:
    def test_valid_planner(self, tmp_path):
        planner = tmp_path / "planner-session"
        planner.mkdir()
        (planner / ".planner-state.json").write_text("{}")
        plan = planner / "final-plan.md"
        plan.write_text("plan")
        state = MagicMock(plan_file=str(plan))
        result = _planner_session_dir(state)
        assert result == str(planner)

    def test_no_plan_file(self):
        state = MagicMock(plan_file="")
        assert _planner_session_dir(state) == ""

    def test_not_a_planner_dir(self, tmp_path):
        plan = tmp_path / "final-plan.md"
        plan.write_text("plan")
        state = MagicMock(plan_file=str(plan))
        # No .planner-state.json
        assert _planner_session_dir(state) == ""


class TestBuildTriagePrompt:
    def test_includes_session_info(self):
        prompt = build_triage_prompt(
            session_dir="/sessions/test",
            step_name="test",
            session_hash="h-123",
            ticket_key="PROJ-1",
            last_error="assertion failed",
        )
        assert "h-123" in prompt
        assert "PROJ-1" in prompt
        assert "test" in prompt
        assert "assertion failed" in prompt

    def test_includes_planner_section(self, tmp_path):
        planner = str(tmp_path / "planner")
        Path(planner).mkdir()
        prompt = build_triage_prompt(
            session_dir="/sessions/test",
            step_name="code",
            session_hash="h",
            ticket_key="",
            last_error="err",
            planner_session_dir=planner,
        )
        assert "final-plan.md" in prompt
        assert "Planner session" in prompt

    def test_includes_ca_section(self, tmp_path):
        sd = tmp_path / "session"
        sd.mkdir()
        (sd / "prompts" / "ca-logs").mkdir(parents=True)
        prompt = build_triage_prompt(
            session_dir=str(sd),
            step_name="code",
            session_hash="h",
            ticket_key="",
            last_error="err",
        )
        assert "Context Assembler" in prompt

    def test_no_ticket(self):
        prompt = build_triage_prompt("/s", "step", "h", "", "err")
        assert "(none)" in prompt


class TestBuildVisualTestSection:
    def test_includes_figma_reference(self, tmp_path):
        (tmp_path / "figma-reference.png").write_bytes(b"img")
        section = _build_visual_test_section(str(tmp_path))
        assert "figma-reference.png" in section

    def test_includes_judge_verdict(self, tmp_path):
        vt = tmp_path / "visual-test" / "attempt-1"
        vt.mkdir(parents=True)
        (vt / "judge-verdict.json").write_text("{}")
        section = _build_visual_test_section(str(tmp_path))
        assert "judge-verdict.json" in section

    def test_includes_screenshots(self, tmp_path):
        (tmp_path / "verify-before.png").write_bytes(b"img")
        (tmp_path / "verify-after.png").write_bytes(b"img")
        section = _build_visual_test_section(str(tmp_path))
        assert "verify-before.png" in section


class TestFindFailedStep:
    def test_finds_permanently_failed(self, tmp_path):
        state = PipelineState(
            steps={
                "code": StepState(status=StepStatus.COMPLETE),
                "test": StepState(status=StepStatus.FAILED, retries=3, last_error="timeout"),
            },
            step_order=["code", "test"],
        )
        StateManager(tmp_path / "agent-state.json").save(state)
        result = find_failed_step(str(tmp_path))
        assert result == ("test", "timeout")

    def test_falls_back_to_any_failed(self, tmp_path):
        state = PipelineState(
            steps={
                "code": StepState(status=StepStatus.COMPLETE),
                "test": StepState(status=StepStatus.FAILED, retries=1, last_error="err"),
            },
            step_order=["code", "test"],
        )
        StateManager(tmp_path / "agent-state.json").save(state)
        result = find_failed_step(str(tmp_path))
        assert result == ("test", "err")

    def test_no_failures(self, tmp_path):
        state = PipelineState(
            steps={"code": StepState(status=StepStatus.COMPLETE)},
            step_order=["code"],
        )
        StateManager(tmp_path / "agent-state.json").save(state)
        assert find_failed_step(str(tmp_path)) is None

    def test_no_state_file(self, tmp_path):
        assert find_failed_step(str(tmp_path)) is None


class TestRunTriage:
    def test_returns_none_when_no_state(self, tmp_path):
        result = run_triage(str(tmp_path), "test", "err")
        assert result is None

    def test_dispatches_agent(self, tmp_path):
        state = PipelineState(
            steps={"test": StepState(status=StepStatus.FAILED, last_error="err")},
            step_order=["test"],
            session_dir=str(tmp_path),
        )
        StateManager(tmp_path / "agent-state.json").save(state)
        # Create the triage file that the agent would produce
        (tmp_path / "triage-test.md").write_text("# Triage\n")

        with patch("forge.executor.engine.triage.AgentRunner") as MockRunner:
            mock_instance = MagicMock()
            MockRunner.return_value = mock_instance
            result = run_triage(str(tmp_path), "test", "err")

        assert result == str(tmp_path / "triage-test.md")
        mock_instance.run.assert_called_once()

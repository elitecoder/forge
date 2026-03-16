# Copyright 2026. Tests for pipeline summary generation.

from pathlib import Path

from forge.executor.engine.state import PipelineState, StepState, StepStatus
from forge.executor.engine.pipeline_summary import (
    generate_pipeline_summary, write_pipeline_summary,
    format_success_comment, format_failure_comment,
    _format_duration,
)


def _make_state(steps_status: dict[str, StepStatus], session_dir: str = "") -> PipelineState:
    steps = {}
    for name, status in steps_status.items():
        ss = StepState(status=status)
        if status in (StepStatus.COMPLETE, StepStatus.FAILED):
            ss.started_at = "2026-01-01T10:00:00Z"
            ss.completed_at = "2026-01-01T10:05:00Z"
        if status == StepStatus.FAILED:
            ss.last_error = f"{name} failed: some error"
        steps[name] = ss
    return PipelineState(
        steps=steps,
        step_order=list(steps_status.keys()),
        session_dir=session_dir,
    )


class TestGeneratePipelineSummary:
    def test_all_passed(self):
        state = _make_state({"code": StepStatus.COMPLETE, "test": StepStatus.COMPLETE})
        summary = generate_pipeline_summary(state, "hash-1", "feat/x")
        assert "hash-1" in summary
        assert "feat/x" in summary
        assert "2/2 passed" in summary
        assert "COMPLETE" in summary

    def test_with_failure(self):
        state = _make_state({"code": StepStatus.COMPLETE, "test": StepStatus.FAILED})
        summary = generate_pipeline_summary(state, "h", "main")
        assert "1/2 passed" in summary
        assert "FAILED" in summary
        assert "test failed" in summary

    def test_no_hash(self):
        state = _make_state({"code": StepStatus.COMPLETE})
        summary = generate_pipeline_summary(state)
        assert "(no hash)" in summary

    def test_triage_detection(self, tmp_path):
        state = _make_state({"code": StepStatus.FAILED}, session_dir=str(tmp_path))
        (tmp_path / "triage-code.md").write_text("**Classification**: **code_fault**")
        summary = generate_pipeline_summary(state, "h")
        assert "code_fault" in summary

    def test_triage_comment_excluded(self, tmp_path):
        state = _make_state({"code": StepStatus.FAILED}, session_dir=str(tmp_path))
        (tmp_path / "triage-comment.md").write_text("code_fault stuff")
        summary = generate_pipeline_summary(state, "h")
        # triage-comment.md is explicitly excluded
        assert "code_fault" not in summary or "Triage" not in summary


class TestWritePipelineSummary:
    def test_writes_file(self, tmp_path):
        state = _make_state({"code": StepStatus.COMPLETE})
        path = write_pipeline_summary(str(tmp_path), state, "h", "main")
        assert path.is_file()
        assert "code" in path.read_text()


class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration("2026-01-01T10:00:00Z", "2026-01-01T10:00:30Z") == "30s"

    def test_minutes(self):
        assert _format_duration("2026-01-01T10:00:00Z", "2026-01-01T10:05:30Z") == "5m 30s"

    def test_no_start(self):
        assert _format_duration("", "2026-01-01T10:00:00Z") == ""

    def test_no_end_uses_now(self):
        result = _format_duration("2026-01-01T10:00:00Z", "")
        assert result != ""  # Should use current time

    def test_bad_format(self):
        assert _format_duration("not-a-date", "also-not") == ""


class TestFormatSuccessComment:
    def test_format(self):
        state = _make_state({"code": StepStatus.COMPLETE, "test": StepStatus.COMPLETE})
        comment = format_success_comment("hash-1", state, "feat/x")
        assert "[forge-session:hash-1]" in comment
        assert "SUCCESS" in comment
        assert "2/2" in comment
        assert "feat/x" in comment


class TestFormatFailureComment:
    def test_format(self):
        state = _make_state({"code": StepStatus.COMPLETE, "test": StepStatus.FAILED})
        comment = format_failure_comment("hash-1", state, "test", "feat/x")
        assert "FAILED" in comment
        assert "1/2" in comment
        assert "test" in comment
        assert "test failed" in comment

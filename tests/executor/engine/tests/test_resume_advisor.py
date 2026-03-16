# Copyright 2026. Tests for resume advisor — cross-run skip logic.

import json
from pathlib import Path

from forge.executor.engine.resume_advisor import advise, _find_executor_dir, _extract_triage_blame


class TestFindExecutorDir:
    def test_no_prior_dir(self, tmp_path):
        assert _find_executor_dir(str(tmp_path)) is None

    def test_finds_executor_subdir(self, tmp_path):
        prior = tmp_path / "prior-executor" / "executor-abc"
        prior.mkdir(parents=True)
        result = _find_executor_dir(str(tmp_path))
        assert result == prior

    def test_falls_back_to_prior_dir_with_state(self, tmp_path):
        prior = tmp_path / "prior-executor"
        prior.mkdir()
        (prior / "agent-state.json").write_text("{}")
        result = _find_executor_dir(str(tmp_path))
        assert result == prior


class TestExtractTriageBlame:
    def test_code_fault_blames_code(self, tmp_path):
        prior_steps = {"test": {"status": "failed"}}
        (tmp_path / "triage-test.md").write_text("**Classification**: **code_fault**")
        blamed = _extract_triage_blame(tmp_path, prior_steps)
        assert "code" in blamed

    def test_plan_fault_blames_code(self, tmp_path):
        prior_steps = {"test": {"status": "failed"}}
        (tmp_path / "triage-test.md").write_text("**Classification**: **plan_fault**")
        blamed = _extract_triage_blame(tmp_path, prior_steps)
        assert "code" in blamed

    def test_review_fault_blames_code_review(self, tmp_path):
        prior_steps = {"visual_test": {"status": "failed"}}
        (tmp_path / "triage-visual_test.md").write_text("**Classification**: **review_fault**")
        blamed = _extract_triage_blame(tmp_path, prior_steps)
        assert "code_review" in blamed

    def test_ignores_completed_steps(self, tmp_path):
        prior_steps = {"test": {"status": "complete"}}
        (tmp_path / "triage-test.md").write_text("**Classification**: **code_fault**")
        blamed = _extract_triage_blame(tmp_path, prior_steps)
        assert len(blamed) == 0

    def test_environment_fault_blames_nobody(self, tmp_path):
        prior_steps = {"test": {"status": "failed"}}
        (tmp_path / "triage-test.md").write_text("**Classification**: **environment_fault**")
        blamed = _extract_triage_blame(tmp_path, prior_steps)
        assert len(blamed) == 0


class TestAdvise:
    def _setup_prior(self, session_dir: Path, steps: dict, triage_files: dict | None = None):
        prior = session_dir / "prior-executor" / "executor-prev"
        prior.mkdir(parents=True)
        (prior / "agent-state.json").write_text(json.dumps({"steps": steps}))
        if triage_files:
            for name, content in triage_files.items():
                (prior / name).write_text(content)

    def test_no_prior_session(self, tmp_path):
        assert advise(str(tmp_path), ["code", "test"]) == []

    def test_skips_completed_code(self, tmp_path):
        self._setup_prior(tmp_path, {"code": {"status": "complete"}, "test": {"status": "complete"}})
        result = advise(str(tmp_path), ["code", "test"])
        assert "code" in result
        assert "test" not in result  # test is not in _SKIPPABLE_STEPS

    def test_does_not_skip_failed_code(self, tmp_path):
        self._setup_prior(tmp_path, {"code": {"status": "failed"}})
        result = advise(str(tmp_path), ["code", "test"])
        assert "code" not in result

    def test_does_not_skip_if_blamed(self, tmp_path):
        self._setup_prior(
            tmp_path,
            {"code": {"status": "complete"}, "test": {"status": "failed"}},
            {"triage-test.md": "**Classification**: **code_fault**"},
        )
        result = advise(str(tmp_path), ["code", "test"])
        assert "code" not in result  # blamed by triage

    def test_skips_code_when_not_blamed(self, tmp_path):
        self._setup_prior(
            tmp_path,
            {"code": {"status": "complete"}, "test": {"status": "failed"}},
            {"triage-test.md": "**Classification**: **environment_fault**"},
        )
        result = advise(str(tmp_path), ["code", "test"])
        assert "code" in result

    def test_empty_prior_steps(self, tmp_path):
        prior = tmp_path / "prior-executor" / "executor-prev"
        prior.mkdir(parents=True)
        (prior / "agent-state.json").write_text("{}")
        assert advise(str(tmp_path), ["code"]) == []

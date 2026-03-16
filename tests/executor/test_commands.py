# Copyright 2026. Tests for executor CLI commands — state mutations, dispatch, evidence.

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from forge.executor.engine.state import PipelineState, StepState, StepStatus, StateManager
from forge.executor.engine.registry import StepDefinition, Preset, PipelineDefinition, EvidenceRule
from forge.executor.commands import (
    cmd_status, cmd_next, cmd_run, cmd_pass, cmd_fail, cmd_reset,
    cmd_execute, cmd_dispatch, cmd_skip, cmd_resume_from, cmd_kill,
    cmd_add_packages, cmd_verify, cmd_model, cmd_summary,
    _resolve_timeout, _resolve_max_turns, _is_permanently_failed,
    _dep_is_satisfied, _check_dependencies, _archive_output_section,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

STEPS = ["code", "build", "lint", "test", "code_review"]


@pytest.fixture
def session_dir(tmp_path):
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


@pytest.fixture
def state_file(session_dir):
    return session_dir / "agent-state.json"


@pytest.fixture
def mgr(state_file):
    return StateManager(state_file)


@pytest.fixture
def saved_state(session_dir, mgr):
    state = PipelineState(
        phase="execution",
        pipeline="full",
        preset="test-preset",
        preset_path=str(session_dir / "presets"),
        current_step="code",
        steps={name: StepState() for name in STEPS},
        step_order=list(STEPS),
        session_dir=str(session_dir),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    mgr.save(state)
    return state


@pytest.fixture
def preset(session_dir):
    steps = {
        "code": StepDefinition(name="code", step_type="ai"),
        "build": StepDefinition(name="build", step_type="command", run_command="npm run build"),
        "lint": StepDefinition(name="lint", step_type="command", run_command="npm run lint"),
        "test": StepDefinition(name="test", step_type="command", run_command="npm test"),
        "code_review": StepDefinition(name="code_review", step_type="ai"),
    }
    return Preset(
        name="test-preset", version=2, description="Test",
        pipelines={"full": PipelineDefinition(steps=STEPS, dependencies={})},
        steps=steps, models={"fix": "sonnet"}, preset_dir=session_dir / "presets",
    )


def _make_args(**kwargs):
    return argparse.Namespace(**kwargs)


def _patch_state(session_dir):
    """Patch _active_session_dir to return our test session."""
    return patch("forge.executor.commands._active_session_dir", return_value=Path(session_dir))


def _patch_preset(preset):
    return patch("forge.executor.commands._load_preset_for_state", return_value=preset)


# ── Helper function tests ────────────────────────────────────────────────────

class TestResolveTimeout:
    def test_known_step(self):
        assert _resolve_timeout("code", "sonnet") == 3600000

    def test_unknown_step_uses_fix_default(self):
        assert _resolve_timeout("unknown_step", "sonnet") == 3600000

    def test_fix_phase(self):
        assert _resolve_timeout("build", "sonnet", is_fix=True) == 3600000


class TestResolveMaxTurns:
    def test_known_step(self):
        assert _resolve_max_turns("code") == 0

    def test_fix_phase(self):
        assert _resolve_max_turns("build", is_fix=True) == 0


class TestDependencyHelpers:
    def test_dep_is_satisfied_complete(self):
        s = StepState(status=StepStatus.COMPLETE)
        assert _dep_is_satisfied(s)

    def test_dep_is_satisfied_pending(self):
        s = StepState(status=StepStatus.PENDING)
        assert not _dep_is_satisfied(s)

    def test_is_permanently_failed(self):
        s = StepState(status=StepStatus.FAILED, retries=3)
        assert _is_permanently_failed(s)

    def test_not_permanently_failed_low_retries(self):
        s = StepState(status=StepStatus.FAILED, retries=1)
        assert not _is_permanently_failed(s)

    def test_check_dependencies_linear(self):
        state = PipelineState(
            steps={
                "a": StepState(status=StepStatus.COMPLETE),
                "b": StepState(status=StepStatus.PENDING),
                "c": StepState(status=StepStatus.PENDING),
            },
            step_order=["a", "b", "c"],
        )
        assert _check_dependencies(state, "b") == []
        incomplete = _check_dependencies(state, "c")
        assert len(incomplete) == 1
        assert "b" in incomplete[0]

    def test_check_dependencies_dag(self):
        state = PipelineState(
            steps={
                "a": StepState(status=StepStatus.COMPLETE),
                "b": StepState(status=StepStatus.PENDING),
                "c": StepState(status=StepStatus.PENDING),
            },
            step_order=["a", "b", "c"],
            dependency_graph={"a": [], "b": ["a"], "c": ["a", "b"]},
        )
        assert _check_dependencies(state, "b") == []
        incomplete = _check_dependencies(state, "c")
        assert len(incomplete) == 1
        assert "b" in incomplete[0]


# ── cmd_status ────────────────────────────────────────────────────────────────

class TestCmdStatus:
    def test_prints_json(self, session_dir, saved_state, capsys):
        with _patch_state(session_dir):
            cmd_status(_make_args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["pipeline"] == "full"


# ── cmd_next ──────────────────────────────────────────────────────────────────

class TestCmdNext:
    def test_returns_first_step(self, session_dir, mgr, saved_state, preset, capsys):
        with _patch_state(session_dir), _patch_preset(preset):
            cmd_next(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["runnable"][0]["step"] == "code"

    def test_all_complete(self, session_dir, mgr, saved_state, preset, capsys):
        def complete_all(s):
            for name in STEPS:
                s.steps[name].status = StepStatus.COMPLETE
        mgr.update(complete_all)
        with _patch_state(session_dir), _patch_preset(preset):
            cmd_next(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["step"] is None


# ── cmd_run ───────────────────────────────────────────────────────────────────

class TestCmdRun:
    def test_marks_in_progress(self, session_dir, mgr, saved_state, preset, capsys):
        with _patch_state(session_dir), _patch_preset(preset):
            cmd_run(_make_args(step="code", skip_deps=False))
        loaded = mgr.load()
        assert loaded.steps["code"].status == StepStatus.IN_PROGRESS
        assert loaded.steps["code"].started_at != ""
        assert loaded.current_step == "code"

    def test_unknown_step_exits(self, session_dir, saved_state):
        with _patch_state(session_dir), pytest.raises(SystemExit):
            cmd_run(_make_args(step="bogus", skip_deps=False))


# ── cmd_fail ──────────────────────────────────────────────────────────────────

class TestCmdFail:
    def test_increments_retries(self, session_dir, mgr, saved_state, capsys):
        with _patch_state(session_dir):
            cmd_fail(_make_args(step="code", error="build failed"))
        loaded = mgr.load()
        assert loaded.steps["code"].status == StepStatus.FAILED
        assert loaded.steps["code"].retries == 1
        assert loaded.steps["code"].last_error == "build failed"
        data = json.loads(capsys.readouterr().out)
        assert data["retries"] == 1

    def test_pipeline_exhausted(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: setattr(s.steps["code"], "retries", 2))
        with _patch_state(session_dir):
            cmd_fail(_make_args(step="code", error="again"))
        data = json.loads(capsys.readouterr().out)
        assert data.get("pipeline_exhausted") is True


# ── cmd_reset ─────────────────────────────────────────────────────────────────

class TestCmdReset:
    def test_resets_to_pending(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: setattr(s.steps["code"], "status", StepStatus.COMPLETE))
        with _patch_state(session_dir):
            cmd_reset(_make_args(step="code", no_retry_inc=False))
        loaded = mgr.load()
        assert loaded.steps["code"].status == StepStatus.PENDING
        assert loaded.steps["code"].retries == 1  # incremented

    def test_no_retry_inc(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: setattr(s.steps["code"], "status", StepStatus.FAILED))
        with _patch_state(session_dir):
            cmd_reset(_make_args(step="code", no_retry_inc=True))
        loaded = mgr.load()
        assert loaded.steps["code"].retries == 0

    def test_removes_checkpoint(self, session_dir, mgr, saved_state, capsys):
        cp_dir = Path(str(session_dir)) / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "code.passed").write_text("checkpoint")
        with _patch_state(session_dir):
            cmd_reset(_make_args(step="code", no_retry_inc=False))
        assert not (cp_dir / "code.passed").exists()


# ── cmd_pass ──────────────────────────────────────────────────────────────────

class TestCmdPass:
    def test_marks_complete_no_evidence(self, session_dir, mgr, saved_state, preset, capsys):
        # Step with no evidence rules
        with _patch_state(session_dir), _patch_preset(preset):
            cmd_pass(_make_args(step="code"))
        loaded = mgr.load()
        assert loaded.steps["code"].status == StepStatus.COMPLETE

    def test_evidence_failure_exits(self, session_dir, mgr, saved_state, capsys):
        step_with_evidence = StepDefinition(
            name="build", step_type="command", run_command="echo",
            evidence=[EvidenceRule(rule="file_exists", file_glob="build-output.txt")],
        )
        preset = Preset(
            name="test", version=2, description="",
            pipelines={"full": PipelineDefinition(steps=STEPS, dependencies={})},
            steps={"build": step_with_evidence}, models={}, preset_dir=Path("."),
        )
        # Mark code as complete so build dep is met
        mgr.update(lambda s: setattr(s.steps["code"], "status", StepStatus.COMPLETE))
        with _patch_state(session_dir), _patch_preset(preset), pytest.raises(SystemExit):
            cmd_pass(_make_args(step="build"))


# ── cmd_dispatch ──────────────────────────────────────────────────────────────

class TestCmdDispatch:
    def test_ai_step_dispatch(self, session_dir, mgr, saved_state, preset, capsys):
        with _patch_state(session_dir), _patch_preset(preset), \
             patch("forge.executor.commands.generate_ai_prompt", return_value="prompt text"):
            cmd_dispatch(_make_args(step="code", phase="run", failed_packages=""))
        data = json.loads(capsys.readouterr().out)
        assert data["step"] == "code"
        assert data["phase"] == "run"
        assert "prompt" in data

    def test_command_step_fix_dispatch(self, session_dir, mgr, saved_state, preset, capsys):
        with _patch_state(session_dir), _patch_preset(preset), \
             patch("forge.executor.commands.generate_fix_prompt", return_value="fix it"):
            cmd_dispatch(_make_args(step="build", phase="fix", failed_packages=""))
        data = json.loads(capsys.readouterr().out)
        assert data["phase"] == "fix"

    def test_unknown_step_exits(self, session_dir, saved_state, preset):
        with _patch_state(session_dir), _patch_preset(preset), pytest.raises(SystemExit):
            cmd_dispatch(_make_args(step="bogus", phase="run", failed_packages=""))


# ── cmd_add_packages ──────────────────────────────────────────────────────────

class TestCmdAddPackages:
    def test_adds_packages(self, session_dir, mgr, saved_state, capsys):
        with _patch_state(session_dir):
            cmd_add_packages(_make_args(packages=["pkg-a", "pkg-b"]))
        loaded = mgr.load()
        assert "pkg-a" in loaded.affected_packages
        assert "pkg-b" in loaded.affected_packages

    def test_deduplicates(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: setattr(s, "affected_packages", ["pkg-a"]))
        with _patch_state(session_dir):
            cmd_add_packages(_make_args(packages=["pkg-a", "pkg-b"]))
        loaded = mgr.load()
        assert loaded.affected_packages.count("pkg-a") == 1


# ── cmd_verify ────────────────────────────────────────────────────────────────

class TestCmdVerify:
    def test_all_valid(self, session_dir, mgr, saved_state, capsys):
        # Create checkpoints for all steps
        cp_dir = session_dir / "checkpoints"
        cp_dir.mkdir()
        for s in STEPS:
            (cp_dir / f"{s}.passed").write_text(f"step: {s}\npassed_at: 2026-01-01\n")
        with _patch_state(session_dir):
            cmd_verify(_make_args())
        assert "COMPLETE" in capsys.readouterr().out

    def test_missing_checkpoint_fails(self, session_dir, mgr, saved_state, capsys):
        cp_dir = session_dir / "checkpoints"
        cp_dir.mkdir()
        # Only create checkpoint for 'code'
        (cp_dir / "code.passed").write_text("step: code\n")
        with _patch_state(session_dir), pytest.raises(SystemExit):
            cmd_verify(_make_args())


# ── _archive_output_section ───────────────────────────────────────────────────

class TestArchiveOutputSection:
    def test_extracts_and_removes_section(self, session_dir):
        output = session_dir / "pipeline-output.md"
        output.write_text(
            "# Pipeline\n\n## code\ncode output here\n\n## build\nbuild output\n"
        )
        archive_dir = str(session_dir / "archive")
        _archive_output_section(str(session_dir), "code", archive_dir)

        archived = (session_dir / "archive" / "output-section.md").read_text()
        assert "code output here" in archived

        remaining = output.read_text()
        assert "code output here" not in remaining
        assert "build output" in remaining

    def test_no_output_file(self, session_dir):
        # Should not raise
        _archive_output_section(str(session_dir), "code", str(session_dir / "archive"))

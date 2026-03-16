# Copyright 2026. Tests for planner CLI commands — state transitions, dispatch, evidence.

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from forge.planner.engine.state import (
    PlannerState, PhaseState, PhaseStatus, StateManager,
    PHASE_ORDER, state_to_dict, now_iso,
)
from forge.planner.commands import (
    cmd_init, cmd_set_context, cmd_status, cmd_next,
    cmd_run, cmd_pass, cmd_fail, cmd_skip, cmd_reset,
    cmd_complete, cmd_kill, cmd_approve, cmd_dispatch,
    resolve_skill_dir, _load_planner_conventions, _load_enrichment_plugins,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def session_dir(tmp_path):
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


@pytest.fixture
def state_file(session_dir):
    return session_dir / ".planner-state.json"


@pytest.fixture
def mgr(state_file):
    return StateManager(state_file)


@pytest.fixture
def saved_state(session_dir, mgr):
    """Create and save a default planner state with all phases pending."""
    phases = {name: PhaseState() for name in PHASE_ORDER}
    state = PlannerState(
        slug="test-slug",
        session_dir=str(session_dir),
        phases=phases,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    mgr.save(state)
    return state


def _make_args(**kwargs):
    return argparse.Namespace(**kwargs)


def _patch_session(session_dir):
    """Patch _find_active_session to return our test session dir."""
    return patch("forge.planner.commands._find_active_session", return_value=Path(session_dir))


# ── resolve_skill_dir ─────────────────────────────────────────────────────────

class TestResolveSkillDir:
    def test_user_override(self, tmp_path):
        user_dir = tmp_path / ".claude" / "skills" / "my-skill"
        user_dir.mkdir(parents=True)
        (user_dir / "SKILL.md").write_text("# Skill")
        with patch("forge.planner.commands.Path.home", return_value=tmp_path):
            result = resolve_skill_dir("my-skill", "")
        assert result == user_dir

    def test_bundled_in_preset(self, tmp_path):
        preset_path = tmp_path / "preset"
        bundled = preset_path / "bundled-skills" / "my-skill"
        bundled.mkdir(parents=True)
        (bundled / "SKILL.md").write_text("# Skill")
        # No user override
        with patch("forge.planner.commands.Path.home", return_value=tmp_path / "empty"):
            result = resolve_skill_dir("my-skill", str(preset_path))
        assert result == bundled

    def test_env_fallback(self, tmp_path):
        env_dir = tmp_path / "skills" / "my-skill"
        env_dir.mkdir(parents=True)
        (env_dir / "SKILL.md").write_text("# Skill")
        with patch("forge.planner.commands.Path.home", return_value=tmp_path / "empty"), \
             patch.dict(os.environ, {"FORGE_SKILLS_BASE": str(tmp_path / "skills")}):
            result = resolve_skill_dir("my-skill", "")
        assert result == env_dir

    def test_returns_none_when_not_found(self, tmp_path):
        with patch("forge.planner.commands.Path.home", return_value=tmp_path):
            assert resolve_skill_dir("nonexistent", "") is None


# ── _load_planner_conventions ─────────────────────────────────────────────────

class TestLoadPlannerConventions:
    def test_loads_conventions(self, tmp_path):
        refs = tmp_path / "references"
        refs.mkdir()
        (refs / "planner.md").write_text("Do this and that")
        result = _load_planner_conventions(str(tmp_path))
        assert "Do this and that" in result
        assert "Planning Conventions" in result

    def test_empty_when_no_preset(self):
        assert _load_planner_conventions("") == ""

    def test_empty_when_no_file(self, tmp_path):
        assert _load_planner_conventions(str(tmp_path)) == ""


# ── _load_enrichment_plugins ──────────────────────────────────────────────────

class TestLoadEnrichmentPlugins:
    def test_loads_plugins(self, tmp_path):
        manifest = {"enrichment": [{"skill": "figma", "model": "sonnet"}]}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        result = _load_enrichment_plugins(str(tmp_path))
        assert len(result) == 1
        assert result[0]["skill"] == "figma"

    def test_empty_when_no_preset(self):
        assert _load_enrichment_plugins("") == []

    def test_empty_when_no_manifest(self, tmp_path):
        assert _load_enrichment_plugins(str(tmp_path)) == []

    def test_empty_on_bad_json(self, tmp_path):
        (tmp_path / "manifest.json").write_text("not json")
        assert _load_enrichment_plugins(str(tmp_path)) == []


# ── cmd_run ───────────────────────────────────────────────────────────────────

class TestCmdRun:
    def test_marks_phase_in_progress(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_run(_make_args(phase="gatekeeper"))
        loaded = mgr.load()
        assert loaded.phases["gatekeeper"].status == PhaseStatus.IN_PROGRESS
        assert loaded.phases["gatekeeper"].started_at != ""
        assert "in_progress" in capsys.readouterr().out

    def test_unknown_phase_exits(self, session_dir, saved_state):
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_run(_make_args(phase="bogus"))


# ── cmd_pass ──────────────────────────────────────────────────────────────────

class TestCmdPass:
    def test_marks_phase_complete(self, session_dir, mgr, saved_state, capsys):
        # Set gatekeeper to in_progress first, then create minimal evidence
        mgr.update(lambda s: setattr(s.phases["gatekeeper"], "status", PhaseStatus.IN_PROGRESS))
        # validate_phase for gatekeeper checks for spec-verdict file
        (session_dir / "spec-verdict").write_text("APPROVED\nall good\n" * 10)

        with _patch_session(session_dir), \
             patch("forge.planner.commands.validate_phase") as mock_val:
            mock_val.return_value = MagicMock(passed=True, message="ok")
            cmd_pass(_make_args(phase="gatekeeper"))

        loaded = mgr.load()
        assert loaded.phases["gatekeeper"].status == PhaseStatus.COMPLETE
        assert loaded.phases["gatekeeper"].completed_at != ""

    def test_evidence_failure_exits(self, session_dir, mgr, saved_state):
        with _patch_session(session_dir), \
             patch("forge.planner.commands.validate_phase") as mock_val:
            mock_val.return_value = MagicMock(passed=False, message="missing file")
            with pytest.raises(SystemExit):
                cmd_pass(_make_args(phase="gatekeeper"))

    def test_unknown_phase_exits(self, session_dir, saved_state):
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_pass(_make_args(phase="bogus"))


# ── cmd_fail ──────────────────────────────────────────────────────────────────

class TestCmdFail:
    def test_increments_retries(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_fail(_make_args(phase="gatekeeper", error="timeout"))
        loaded = mgr.load()
        assert loaded.phases["gatekeeper"].status == PhaseStatus.FAILED
        assert loaded.phases["gatekeeper"].retries == 1
        assert loaded.phases["gatekeeper"].last_error == "timeout"

    def test_max_retries_warning(self, session_dir, mgr, saved_state, capsys):
        # Set retries to 1 so next fail makes it 2 (MAX_RETRIES)
        mgr.update(lambda s: setattr(s.phases["gatekeeper"], "retries", 1))
        with _patch_session(session_dir):
            cmd_fail(_make_args(phase="gatekeeper", error="again"))
        out = capsys.readouterr().out
        assert "max retries" in out.lower() or "WARNING" in out


# ── cmd_skip ──────────────────────────────────────────────────────────────────

class TestCmdSkip:
    def test_marks_skipped(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_skip(_make_args(phase="router"))
        loaded = mgr.load()
        assert loaded.phases["router"].status == PhaseStatus.SKIPPED
        assert loaded.phases["router"].completed_at != ""


# ── cmd_reset ─────────────────────────────────────────────────────────────────

class TestCmdReset:
    def test_resets_to_pending(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: setattr(s.phases["gatekeeper"], "status", PhaseStatus.COMPLETE))
        with _patch_session(session_dir):
            cmd_reset(_make_args(phase="gatekeeper"))
        loaded = mgr.load()
        assert loaded.phases["gatekeeper"].status == PhaseStatus.PENDING


# ── cmd_status ────────────────────────────────────────────────────────────────

class TestCmdStatus:
    def test_prints_json(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_status(_make_args())
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["slug"] == "test-slug"


# ── cmd_next ──────────────────────────────────────────────────────────────────

class TestCmdNext:
    def test_returns_first_phase(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_next(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["phase"] == "gatekeeper"

    def test_all_done(self, session_dir, mgr, saved_state, capsys):
        def complete_all(s):
            for p in PHASE_ORDER:
                s.phases[p].status = PhaseStatus.COMPLETE
        mgr.update(complete_all)
        with _patch_session(session_dir):
            cmd_next(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["phase"] is None


# ── cmd_dispatch ──────────────────────────────────────────────────────────────

class TestCmdDispatch:
    def test_gatekeeper_dispatch(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir):
            cmd_dispatch(_make_args(phase="gatekeeper", model=None))
        data = json.loads(capsys.readouterr().out)
        assert data["phase"] == "gatekeeper"
        assert isinstance(data["agents"], list)

    def test_unknown_phase_exits(self, session_dir, saved_state):
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_dispatch(_make_args(phase="bogus", model=None))


# ── cmd_complete ──────────────────────────────────────────────────────────────

class TestCmdComplete:
    def test_incomplete_phases(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_complete(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["complete"] is False
        assert "missing_phases" in data

    def test_missing_final_plan(self, session_dir, mgr, saved_state, capsys):
        required = ["gatekeeper", "architects", "critics", "refiners", "judge"]
        def complete_required(s):
            for p in required:
                s.phases[p].status = PhaseStatus.COMPLETE
        mgr.update(complete_required)
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_complete(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["reason"] == "missing_final_plan"

    def test_final_plan_too_small(self, session_dir, mgr, saved_state, capsys):
        required = ["gatekeeper", "architects", "critics", "refiners", "judge"]
        def complete_required(s):
            for p in required:
                s.phases[p].status = PhaseStatus.COMPLETE
        mgr.update(complete_required)
        (session_dir / "final-plan.md").write_text("short")
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_complete(_make_args())
        data = json.loads(capsys.readouterr().out)
        assert data["reason"] == "final_plan_too_small"

    def test_success(self, session_dir, mgr, saved_state, capsys):
        required = ["gatekeeper", "architects", "critics", "refiners", "judge"]
        def complete_required(s):
            for p in required:
                s.phases[p].status = PhaseStatus.COMPLETE
        mgr.update(complete_required)
        (session_dir / "final-plan.md").write_text("x" * 2000)
        with _patch_session(session_dir), pytest.raises(SystemExit) as exc:
            cmd_complete(_make_args())
        assert exc.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["complete"] is True


# ── cmd_kill ──────────────────────────────────────────────────────────────────

class TestCmdKill:
    def test_sets_killed_flag(self, session_dir, mgr, saved_state, capsys):
        with _patch_session(session_dir), \
             patch("forge.planner.commands.emit"):
            cmd_kill(_make_args(reason="user request"))
        loaded = mgr.load()
        assert loaded.killed is True
        assert loaded.kill_reason == "user request"


# ── cmd_approve ───────────────────────────────────────────────────────────────

class TestCmdApprove:
    def test_unblocks_pipeline(self, session_dir, mgr, saved_state, capsys):
        mgr.update(lambda s: (setattr(s, "killed", True), setattr(s, "kill_reason", "spec_review_required")))
        with _patch_session(session_dir):
            cmd_approve(_make_args(session=str(session_dir), answers=None))
        loaded = mgr.load()
        assert loaded.killed is False
        assert loaded.kill_reason == ""

    def test_not_blocked_exits(self, session_dir, mgr, saved_state):
        with _patch_session(session_dir), pytest.raises(SystemExit):
            cmd_approve(_make_args(session=str(session_dir), answers=None))

    def test_appends_answers_file(self, session_dir, mgr, saved_state, tmp_path, capsys):
        mgr.update(lambda s: (setattr(s, "killed", True), setattr(s, "kill_reason", "spec_review_required")))
        answers = tmp_path / "answers.md"
        answers.write_text("The answer is 42")
        (session_dir / "spec-gaps.md").write_text("# Gaps\n")
        with _patch_session(session_dir):
            cmd_approve(_make_args(session=str(session_dir), answers=str(answers)))
        content = (session_dir / "spec-gaps.md").read_text()
        assert "The answer is 42" in content
        assert "Human-Provided Answers" in content


# ── cmd_init ──────────────────────────────────────────────────────────────────

class TestCmdInit:
    def test_creates_session(self, tmp_path, capsys):
        with patch("forge.planner.commands.create_session", return_value=tmp_path / "new-session") as mock_create, \
             patch("forge.planner.commands._core_log"):
            (tmp_path / "new-session").mkdir()
            cmd_init(_make_args(slug="my-slug", preset=""))
        mock_create.assert_called_once_with("planner", slug="my-slug")
        state_file = tmp_path / "new-session" / ".planner-state.json"
        assert state_file.is_file()
        data = json.loads(state_file.read_text())
        assert data["slug"] == "my-slug"

    def test_with_preset(self, tmp_path, capsys):
        preset_dir = tmp_path / "presets" / "my-preset"
        preset_dir.mkdir(parents=True)
        with patch("forge.planner.commands.create_session", return_value=tmp_path / "sess"), \
             patch("forge.planner.commands.resolve_preset_dir", return_value=preset_dir), \
             patch("forge.planner.commands._core_log"):
            (tmp_path / "sess").mkdir()
            cmd_init(_make_args(slug="s", preset="my-preset"))
        data = json.loads((tmp_path / "sess" / ".planner-state.json").read_text())
        assert data["preset"] == "my-preset"
        assert str(preset_dir) in data["preset_path"]

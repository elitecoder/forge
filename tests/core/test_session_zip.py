# Copyright 2026. Tests for session zip creation.

import json
import zipfile
from pathlib import Path

from forge.core.session_zip import create_planner_zip, create_executor_zip


class TestCreatePlannerZip:
    def test_creates_zip(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        (session / "final-plan.md").write_text("# Plan\n" * 100)
        (session / "design-a.md").write_text("Design A")

        result = create_planner_zip(session, "hash-123", ticket_key="PROJ-1")
        assert result.is_file()
        assert result.suffix == ".zip"

    def test_contains_meta_json(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        result = create_planner_zip(session, "hash-123", ticket_key="PROJ-1", preset="my-preset")

        with zipfile.ZipFile(result) as zf:
            meta_path = [n for n in zf.namelist() if n.endswith("meta.json")]
            assert len(meta_path) == 1
            meta = json.loads(zf.read(meta_path[0]))
            assert meta["session_hash"] == "hash-123"
            assert meta["ticket_key"] == "PROJ-1"
            assert meta["preset"] == "my-preset"
            assert meta["subsystem"] == "planner"

    def test_includes_phase_outputs(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        (session / "final-plan.md").write_text("plan content")
        (session / "design-a.md").write_text("design a")

        result = create_planner_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert any("final-plan.md" in n for n in names)
            assert any("design-a.md" in n for n in names)

    def test_includes_prompts(self, tmp_path):
        session = tmp_path / "session"
        prompts = session / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "recon.md").write_text("prompt")

        result = create_planner_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            assert any("prompts/recon.md" in n for n in zf.namelist())

    def test_skips_missing_files(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        result = create_planner_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            # Only meta.json should be present
            assert len(zf.namelist()) == 1


class TestCreateExecutorZip:
    def test_creates_zip(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        (session / "pipeline-output.md").write_text("output")

        result = create_executor_zip(session, "exe-123")
        assert result.is_file()
        assert "executor" in result.name

    def test_contains_meta_json(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        outcomes = {"code": "pass", "test": "fail"}
        result = create_executor_zip(session, "h", step_outcomes=outcomes, branch_name="feat/x")

        with zipfile.ZipFile(result) as zf:
            meta_path = [n for n in zf.namelist() if n.endswith("meta.json")]
            meta = json.loads(zf.read(meta_path[0]))
            assert meta["step_outcomes"] == outcomes
            assert meta["branch_name"] == "feat/x"

    def test_excludes_state_files(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        (session / ".pipeline-state.json").write_text("{}")
        (session / ".pipeline-state.json.lock").write_text("")
        (session / "events.jsonl").write_text("")
        (session / "pipeline-output.md").write_text("keep")

        result = create_executor_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert not any(".pipeline-state.json" in n for n in names if "meta" not in n)
            assert not any("events.jsonl" in n for n in names)
            assert any("pipeline-output.md" in n for n in names)

    def test_excludes_zip_files(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        (session / "old.zip").write_bytes(b"PK")

        result = create_executor_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            assert not any("old.zip" in n for n in zf.namelist())

    def test_excludes_large_files(self, tmp_path):
        session = tmp_path / "session"
        session.mkdir()
        big = session / "huge-screenshot.png"
        big.write_bytes(b"x" * (6 * 1024 * 1024))  # 6MB
        (session / "small.txt").write_text("keep")

        result = create_executor_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert not any("huge-screenshot" in n for n in names)
            assert any("small.txt" in n for n in names)

    def test_excludes_prior_dirs(self, tmp_path):
        session = tmp_path / "session"
        prior = session / "prior" / "old-data.json"
        prior.parent.mkdir(parents=True)
        prior.write_text("{}")
        prior_exec = session / "prior-executor" / "state.json"
        prior_exec.parent.mkdir(parents=True)
        prior_exec.write_text("{}")

        result = create_executor_zip(session, "h")
        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()
            assert not any("prior/" in n for n in names)
            assert not any("prior-executor/" in n for n in names)

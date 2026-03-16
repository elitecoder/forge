# Copyright 2026. Tests for step summary extraction from activity logs.

import json
import os
from pathlib import Path

from forge.executor.engine.step_summary import extract_summary, write_step_summary


class TestExtractSummary:
    def test_empty_log(self, tmp_path):
        log = tmp_path / "activity.log"
        result = extract_summary(str(log), "code", True)
        assert result["step"] == "code"
        assert result["outcome"] == "pass"
        assert result["files_read"] == []

    def test_missing_log(self, tmp_path):
        result = extract_summary(str(tmp_path / "nonexistent.log"), "code", False)
        assert result["outcome"] == "fail"

    def test_parses_reads(self, tmp_path):
        log = tmp_path / "activity.log"
        log.write_text(
            "[12:00:00] code  read: src/main.ts\n"
            "[12:00:01] code  read: src/utils.ts\n"
            "[12:00:02] code  read: src/main.ts\n"  # duplicate
        )
        result = extract_summary(str(log), "code", True)
        assert result["files_read"] == ["src/main.ts", "src/utils.ts"]

    def test_parses_writes(self, tmp_path):
        log = tmp_path / "activity.log"
        log.write_text("[12:00:00] code  write: src/new-file.ts\n")
        result = extract_summary(str(log), "code", True)
        assert result["files_written"] == ["src/new-file.ts"]

    def test_parses_commands(self, tmp_path):
        log = tmp_path / "activity.log"
        log.write_text("[12:00:00] code  bash: npm run build\n")
        result = extract_summary(str(log), "code", True)
        assert result["commands"] == ["npm run build"]

    def test_parses_errors(self, tmp_path):
        log = tmp_path / "activity.log"
        log.write_text("[12:00:00] code  error: Type mismatch\n")
        result = extract_summary(str(log), "code", False)
        assert result["errors"] == ["Type mismatch"]

    def test_filters_by_step_name(self, tmp_path):
        log = tmp_path / "activity.log"
        log.write_text(
            "[12:00:00] code  read: src/a.ts\n"
            "[12:00:01] build  bash: npm run build\n"
            "[12:00:02] code  write: src/b.ts\n"
        )
        result = extract_summary(str(log), "code", True)
        assert result["files_read"] == ["src/a.ts"]
        assert result["files_written"] == ["src/b.ts"]
        assert result["commands"] == []

    def test_outcome_pass(self, tmp_path):
        result = extract_summary(str(tmp_path / "x.log"), "s", True)
        assert result["outcome"] == "pass"

    def test_outcome_fail(self, tmp_path):
        result = extract_summary(str(tmp_path / "x.log"), "s", False)
        assert result["outcome"] == "fail"


class TestWriteStepSummary:
    def test_writes_json_file(self, tmp_path):
        session_dir = str(tmp_path)
        log = tmp_path / "pipeline-activity.log"
        log.write_text("[12:00:00] code  read: src/main.ts\n")
        path = write_step_summary(session_dir, "code", True)
        assert path is not None
        data = json.loads(Path(path).read_text())
        assert data["step"] == "code"

    def test_returns_none_when_empty(self, tmp_path):
        session_dir = str(tmp_path)
        # No activity log
        result = write_step_summary(session_dir, "code", True)
        assert result is None

    def test_appends_to_output(self, tmp_path):
        session_dir = str(tmp_path)
        log = tmp_path / "pipeline-activity.log"
        log.write_text("[12:00:00] code  read: src/a.ts\n[12:00:01] code  bash: npm test\n")
        write_step_summary(session_dir, "code", True)
        output = (tmp_path / "pipeline-output.md").read_text()
        assert "code — Actions" in output
        assert "src/a.ts" in output

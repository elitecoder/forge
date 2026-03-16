# Copyright 2026. Tests for Claude provider.

import json
from unittest.mock import MagicMock, patch

from forge.providers.claude import (
    ClaudeProvider,
    _child_env,
    _extract_content_text,
)
from forge.providers.protocol import AgentResult


class TestChildEnv:
    def test_strips_claudecode(self):
        with patch.dict("os.environ", {"CLAUDECODE": "1", "HOME": "/test"}):
            env = _child_env()
        assert "CLAUDECODE" not in env
        assert env["HOME"] == "/test"

    def test_preserves_other_vars(self):
        with patch.dict("os.environ", {"PATH": "/usr/bin", "MY_VAR": "val"}, clear=True):
            env = _child_env()
        assert env["PATH"] == "/usr/bin"
        assert env["MY_VAR"] == "val"


class TestExtractContentText:
    def test_string_content(self):
        assert _extract_content_text("hello\nworld") == ["hello", "world"]

    def test_empty_string(self):
        assert _extract_content_text("") == []

    def test_list_of_strings(self):
        assert _extract_content_text(["hello", "world"]) == ["hello", "world"]

    def test_list_of_dicts(self):
        content = [{"text": "hello"}, {"text": "world"}]
        assert _extract_content_text(content) == ["hello", "world"]

    def test_none(self):
        assert _extract_content_text(None) == []

    def test_int(self):
        assert _extract_content_text(42) == []


class TestParseStreamJson:
    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_assistant_text(self):
        msg = json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Hello world"}]},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("Hello world", True)]

    def test_assistant_tool_use_bash(self):
        msg = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls -la"},
            }]},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("$ ls -la", False)]

    def test_assistant_tool_use_read(self):
        msg = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            }]},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("[Read] /tmp/test.py", False)]

    def test_tool_result(self):
        msg = json.dumps({
            "type": "tool_result",
            "tool_result": {"content": "output text"},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("output text", False)]

    def test_result_type(self):
        msg = json.dumps({"type": "result", "result": "Final answer"})
        result = self.provider._parse_stream_json(msg)
        assert result == [("Final answer", True)]

    def test_error_type(self):
        msg = json.dumps({"type": "error", "error": "something failed"})
        result = self.provider._parse_stream_json(msg)
        assert result == [("[error] something failed", True)]

    def test_system_type(self):
        msg = json.dumps({"type": "system", "message": "init done", "subtype": "init"})
        result = self.provider._parse_stream_json(msg)
        assert result == [("[system:init] init done", False)]

    def test_invalid_json(self):
        result = self.provider._parse_stream_json("not json")
        assert result == [("not json", False)]

    def test_empty_string(self):
        result = self.provider._parse_stream_json("")
        assert result == []


class TestFormatSubagentType:
    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_no_namespace(self):
        assert self.provider.format_subagent_type("general-purpose") == "general-purpose"

    def test_with_namespace(self):
        assert self.provider.format_subagent_type("architect", "planner") == "planner:architect"

    def test_general_purpose_ignores_namespace(self):
        assert self.provider.format_subagent_type("general-purpose", "planner") == "general-purpose"


class TestRunAgentNotFound:
    def test_command_not_found(self, tmp_path):
        provider = ClaudeProvider(agent_command="/nonexistent/binary")
        result = provider.run_agent(
            prompt="test",
            model="sonnet",
            max_turns=5,
            cwd=str(tmp_path),
            timeout_s=5,
            step_name="test",
            session_dir=str(tmp_path),
            activity_log_path=str(tmp_path / "activity.log"),
        )
        assert result.exit_code == 127
        assert result.timed_out is False


class TestRunJudge:
    def test_returns_stdout(self):
        provider = ClaudeProvider()
        with patch("forge.providers.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="  PASS  \n")
            result = provider.run_judge("evaluate this", model="haiku", max_turns=5)
        assert result == "PASS"
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "haiku" in cmd

    def test_timeout_returns_empty(self):
        import subprocess
        provider = ClaudeProvider()
        with patch("forge.providers.claude.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            result = provider.run_judge("test", timeout_s=10)
        assert result == ""


class TestRunAgentCommand:
    def test_builds_correct_command(self, tmp_path):
        """Verify the command includes expected flags."""
        provider = ClaudeProvider(agent_command="test-claude")
        with patch("forge.providers.claude.subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.stdout = iter([])
            proc.stdin = MagicMock()
            proc.wait.return_value = None
            proc.returncode = 0
            mock_popen.return_value = proc

            provider.run_agent(
                prompt="do something",
                model="opus",
                max_turns=10,
                cwd=str(tmp_path),
                timeout_s=30,
                step_name="code",
                session_dir=str(tmp_path),
            )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "test-claude"
        assert "-p" in cmd
        assert "--model" in cmd
        assert "opus" in cmd
        assert "--max-turns" in cmd
        assert "10" in cmd

    def test_extra_env_merged(self, tmp_path):
        provider = ClaudeProvider()
        with patch("forge.providers.claude.subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.stdout = iter([])
            proc.stdin = MagicMock()
            proc.wait.return_value = None
            proc.returncode = 0
            mock_popen.return_value = proc

            provider.run_agent(
                prompt="test", model="sonnet", max_turns=1, cwd=str(tmp_path),
                extra_env={"MY_VAR": "value"},
            )

        env = mock_popen.call_args[1]["env"]
        assert env["MY_VAR"] == "value"

    def test_zero_max_turns_omitted(self, tmp_path):
        provider = ClaudeProvider()
        with patch("forge.providers.claude.subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.stdout = iter([])
            proc.stdin = MagicMock()
            proc.wait.return_value = None
            proc.returncode = 0
            mock_popen.return_value = proc

            provider.run_agent(
                prompt="test", model="sonnet", max_turns=0, cwd=str(tmp_path),
            )

        cmd = mock_popen.call_args[0][0]
        assert "--max-turns" not in cmd


class TestCheckKeyEvent:
    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_detects_read(self, tmp_path):
        log = str(tmp_path / "log")
        self.provider._check_key_event(log, "code", "Reading file: src/main.ts")
        from pathlib import Path
        assert "read" in Path(log).read_text()

    def test_detects_write(self, tmp_path):
        log = str(tmp_path / "log")
        self.provider._check_key_event(log, "code", "Writing src/new-file.ts")
        from pathlib import Path
        assert "write" in Path(log).read_text()

    def test_detects_bash(self, tmp_path):
        log = str(tmp_path / "log")
        self.provider._check_key_event(log, "code", "$ npm run build")
        from pathlib import Path
        assert "bash" in Path(log).read_text()

    def test_detects_error(self, tmp_path):
        log = str(tmp_path / "log")
        self.provider._check_key_event(log, "code", "Error: Type mismatch")
        from pathlib import Path
        assert "error" in Path(log).read_text()

    def test_no_match(self, tmp_path):
        log = str(tmp_path / "log")
        self.provider._check_key_event(log, "code", "just some text")
        from pathlib import Path
        assert not Path(log).exists()


class TestParseStreamJsonExtended:
    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_system_no_subtype(self):
        msg = json.dumps({"type": "system", "message": "ready"})
        result = self.provider._parse_stream_json(msg)
        assert result == [("[system] ready", False)]

    def test_error_dict(self):
        msg = json.dumps({"type": "error", "error": {"message": "rate limited"}})
        result = self.provider._parse_stream_json(msg)
        assert result == [("[error] rate limited", True)]

    def test_user_type(self):
        msg = json.dumps({
            "type": "user",
            "message": {"content": [{"content": "user input"}]},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("user input", False)]

    def test_result_empty(self):
        msg = json.dumps({"type": "result", "result": ""})
        result = self.provider._parse_stream_json(msg)
        assert result == []

    def test_tool_result_fallback_output(self):
        msg = json.dumps({
            "type": "tool_result",
            "tool_result": {"output": "fallback text"},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("fallback text", False)]

    def test_tool_use_pattern(self):
        msg = json.dumps({
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Grep",
                "input": {"pattern": "TODO", "path": "/src"},
            }]},
        })
        result = self.provider._parse_stream_json(msg)
        assert result == [("[Grep] TODO", False)]


class TestProviderModels:
    def test_provider_models_structure(self):
        from forge.providers.models import PROVIDER_MODELS
        assert "claude" in PROVIDER_MODELS
        assert "codex" in PROVIDER_MODELS
        assert PROVIDER_MODELS["claude"]["reasoning"] == "opus"
        assert PROVIDER_MODELS["claude"]["balanced"] == "sonnet"
        assert PROVIDER_MODELS["claude"]["fast"] == "haiku"

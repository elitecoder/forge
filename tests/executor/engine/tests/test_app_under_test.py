# Copyright 2026. Tests for app_under_test — dev server lifecycle and health checks.

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from forge.executor.engine.app_under_test import (
    _find_playwright_runner, _kill_existing_dev_server, _cleanup,
    _poll_with_curl, _poll_with_playwright, execute,
)


# ── _find_playwright_runner ───────────────────────────────────────────────────

class TestFindPlaywrightRunner:
    def test_no_config(self):
        preset = MagicMock(visual_test_config={})
        assert _find_playwright_runner(preset) is None

    def test_empty_runner_dir(self):
        preset = MagicMock(visual_test_config={"playwright_runner_dir": ""})
        assert _find_playwright_runner(preset) is None

    def test_resolves_preset_dir_variable(self, tmp_path):
        runner = tmp_path / "runner"
        runner.mkdir()
        (runner / "run.js").write_text("// runner")
        preset = MagicMock(
            visual_test_config={"playwright_runner_dir": "${PRESET_DIR}/runner"},
            preset_dir=tmp_path,
        )
        result = _find_playwright_runner(preset)
        assert result is not None
        assert "runner" in result

    def test_returns_none_when_dir_missing(self, tmp_path):
        preset = MagicMock(
            visual_test_config={"playwright_runner_dir": str(tmp_path / "nonexistent")},
            preset_dir=tmp_path,
        )
        assert _find_playwright_runner(preset) is None

    def test_returns_none_when_run_js_missing(self, tmp_path):
        runner = tmp_path / "runner"
        runner.mkdir()
        # No run.js
        preset = MagicMock(
            visual_test_config={"playwright_runner_dir": str(runner)},
            preset_dir=tmp_path,
        )
        assert _find_playwright_runner(preset) is None

    def test_valid_absolute_path(self, tmp_path):
        runner = tmp_path / "pw-runner"
        runner.mkdir()
        (runner / "run.js").write_text("module.exports = {}")
        preset = MagicMock(
            visual_test_config={"playwright_runner_dir": str(runner)},
            preset_dir=tmp_path,
        )
        assert _find_playwright_runner(preset) == str(runner)


# ── _kill_existing_dev_server ─────────────────────────────────────────────────

class TestKillExistingDevServer:
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.os.kill")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    def test_kills_running_process(self, mock_sleep, mock_kill, mock_ops):
        state = MagicMock(dev_server_pid=12345)
        _kill_existing_dev_server(state)
        mock_kill.assert_any_call(12345, 15)  # SIGTERM
        mock_kill.assert_any_call(12345, 9)   # SIGKILL
        mock_ops.clear_dev_server_state.assert_called_once()

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.os.kill")
    def test_skips_zero_pid(self, mock_kill, mock_ops):
        state = MagicMock(dev_server_pid=0)
        _kill_existing_dev_server(state)
        mock_kill.assert_not_called()
        mock_ops.clear_dev_server_state.assert_called_once()

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.os.kill", side_effect=ProcessLookupError)
    def test_handles_dead_process(self, mock_kill, mock_ops):
        state = MagicMock(dev_server_pid=99999)
        _kill_existing_dev_server(state)
        mock_ops.clear_dev_server_state.assert_called_once()


# ── _cleanup ──────────────────────────────────────────────────────────────────

class TestCleanup:
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_kills_process(self, mock_ops):
        proc = MagicMock()
        _cleanup(proc)
        proc.kill.assert_called_once()
        proc.wait.assert_called_once_with(timeout=5)
        mock_ops.clear_dev_server_state.assert_called_once()

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_handles_kill_exception(self, mock_ops):
        proc = MagicMock()
        proc.kill.side_effect = OSError("already dead")
        _cleanup(proc)  # Should not raise
        mock_ops.clear_dev_server_state.assert_called_once()

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_handles_clear_state_exception(self, mock_ops):
        proc = MagicMock()
        mock_ops.clear_dev_server_state.side_effect = RuntimeError("no state")
        _cleanup(proc)  # Should not raise


# ── _poll_with_curl ───────────────────────────────────────────────────────────

class TestPollWithCurl:
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    @patch("forge.executor.engine.app_under_test.subprocess.run")
    def test_success_on_first_try(self, mock_run, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        mock_run.return_value = MagicMock(returncode=0)
        log_msgs = []

        result = _poll_with_curl(proc, "https://localhost:8080/", 30, 8080, lambda m: log_msgs.append(m))
        assert result is True
        assert any("responding" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    @patch("forge.executor.engine.app_under_test.subprocess.run")
    def test_timeout(self, mock_run, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=7)  # curl: connection refused
        log_msgs = []

        result = _poll_with_curl(proc, "https://localhost:8080/", 10, 8080, lambda m: log_msgs.append(m))
        assert result is False
        assert any("failed to start" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    def test_server_crashes(self, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = 1  # process exited
        proc.returncode = 1
        proc.stdout.read.return_value = b"Error: EADDRINUSE"
        log_msgs = []

        result = _poll_with_curl(proc, "https://localhost:8080/", 30, 8080, lambda m: log_msgs.append(m))
        assert result is False
        assert any("crashed" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    @patch("forge.executor.engine.app_under_test.subprocess.run")
    def test_curl_timeout_retries(self, mock_run, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = None
        call_count = [0]

        def curl_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise subprocess.TimeoutExpired("curl", 5)
            return MagicMock(returncode=0)

        mock_run.side_effect = curl_side_effect
        result = _poll_with_curl(proc, "https://localhost:8080/", 30, 8080, lambda m: None)
        assert result is True


# ── _poll_with_playwright ─────────────────────────────────────────────────────

class TestPollWithPlaywright:
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    @patch("forge.executor.engine.app_under_test.subprocess.run")
    def test_success(self, mock_run, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stdout="HEALTHY\n")
        log_msgs = []

        result = _poll_with_playwright(
            proc, "https://localhost:8080/", 60, 8080, "/pw-runner",
            lambda m: log_msgs.append(m),
        )
        assert result is True
        assert any("healthy" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    @patch("forge.executor.engine.app_under_test.subprocess.run")
    def test_timeout(self, mock_run, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=1, stdout="UNHEALTHY")

        result = _poll_with_playwright(
            proc, "https://localhost:8080/", 15, 8080, "/pw",
            lambda m: None,
        )
        assert result is False

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.time.sleep")
    def test_server_crash(self, mock_sleep, mock_ops):
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        proc.stdout.read.return_value = b"Segfault"

        result = _poll_with_playwright(
            proc, "https://localhost:8080/", 60, 8080, "/pw",
            lambda m: None,
        )
        assert result is False


# ── execute ───────────────────────────────────────────────────────────────────

class TestExecute:
    def _make_preset(self, dev_server=None, visual_test_config=None):
        preset = MagicMock()
        preset.dev_server = dev_server
        preset.visual_test_config = visual_test_config or {}
        return preset

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_no_dev_config_returns_false(self, mock_ops):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(dev_server=None)
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        log_msgs = []
        assert execute("/repo", lambda src, m: log_msgs.append(m)) is False
        assert any("no dev_server" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_no_command_returns_false(self, mock_ops):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(dev_server={"command": ""})
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 8080}
        assert execute("/repo", lambda src, m: None) is False

    @patch("forge.executor.engine.app_under_test._poll_with_curl", return_value=True)
    @patch("forge.executor.engine.app_under_test.subprocess.Popen")
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_success_with_curl_fallback(self, mock_ops, mock_popen, mock_poll):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(
            dev_server={"command": "npm start -- --port ${PORT}"},
            visual_test_config={},
        )
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 3000}
        mock_ops.state_mgr.return_value = MagicMock()

        proc = MagicMock(pid=42)
        mock_popen.return_value = proc

        result = execute("/repo", lambda src, m: None)
        assert result is True

        # Verify the command had the port substituted
        cmd = mock_popen.call_args[0][0]
        assert "3000" in cmd

    @patch("forge.executor.engine.app_under_test._poll_with_playwright", return_value=True)
    @patch("forge.executor.engine.app_under_test._find_playwright_runner", return_value="/pw-runner")
    @patch("forge.executor.engine.app_under_test.subprocess.Popen")
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_success_with_playwright(self, mock_ops, mock_popen, mock_find_pw, mock_poll):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(dev_server={"command": "npm start"})
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 3000}
        mock_ops.state_mgr.return_value = MagicMock()
        mock_popen.return_value = MagicMock(pid=42)

        result = execute("/repo", lambda src, m: None)
        assert result is True
        mock_poll.assert_called_once()

    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_vpn_check_fails(self, mock_ops):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(
            dev_server={"command": "npm start", "vpn_check_host": "unreachable.internal"},
        )
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        log_msgs = []

        with patch("socket.create_connection", side_effect=OSError("timeout")):
            result = execute("/repo", lambda src, m: log_msgs.append(m))

        assert result is False
        assert any("VPN" in m for m in log_msgs)

    @patch("forge.executor.engine.app_under_test._poll_with_curl", return_value=True)
    @patch("forge.executor.engine.app_under_test.subprocess.Popen")
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_saves_pid_to_state(self, mock_ops, mock_popen, mock_poll):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(dev_server={"command": "npm start"})
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 3000}

        mgr = MagicMock()
        mock_ops.state_mgr.return_value = mgr
        mock_popen.return_value = MagicMock(pid=777)

        execute("/repo")

        # Verify state_mgr().update was called to save the PID
        mgr.update.assert_called_once()

    @patch("forge.executor.engine.app_under_test._poll_with_curl", return_value=True)
    @patch("forge.executor.engine.app_under_test.subprocess.Popen")
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    @patch("forge.executor.engine.app_under_test.is_bazel_repo", return_value=True)
    def test_uses_bazel_command(self, mock_bazel, mock_ops, mock_popen, mock_poll):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(
            dev_server={"command": "npm start", "bazel_command": "ibazel run //app:dev"},
        )
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 3000}
        mock_ops.state_mgr.return_value = MagicMock()
        mock_popen.return_value = MagicMock(pid=42)

        execute("/repo")

        cmd = mock_popen.call_args[0][0]
        assert "ibazel" in cmd

    @patch("forge.executor.engine.app_under_test._poll_with_curl", return_value=True)
    @patch("forge.executor.engine.app_under_test.subprocess.Popen")
    @patch("forge.executor.engine.app_under_test.pipeline_ops")
    def test_custom_health_url(self, mock_ops, mock_popen, mock_poll):
        state = MagicMock(dev_server_pid=0)
        preset = self._make_preset(
            dev_server={
                "command": "npm start",
                "health_url": "http://localhost:${PORT}/api/health",
            },
        )
        mock_ops.require_state.return_value = state
        mock_ops.load_preset_for_state.return_value = preset
        mock_ops.allocate_dev_server.return_value = {"port": 4000}
        mock_ops.state_mgr.return_value = MagicMock()
        mock_popen.return_value = MagicMock(pid=42)

        execute("/repo")

        # The health_url passed to _poll_with_curl should have port substituted
        poll_args = mock_poll.call_args
        health_url = poll_args[0][1]
        assert "4000" in health_url
        assert "api/health" in health_url

# Copyright 2026. Tests for dashboard CLI commands.

from unittest.mock import patch, MagicMock
import argparse

from forge.dashboard.commands import cmd_dashboard, _find_running_server


class TestFindRunningServer:
    @patch("forge.dashboard.commands.subprocess.run")
    def test_finds_pid(self, mock_run):
        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        assert _find_running_server() == 12345

    @patch("forge.dashboard.commands.subprocess.run")
    def test_no_process(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        assert _find_running_server() is None

    @patch("forge.dashboard.commands.subprocess.run", side_effect=OSError)
    def test_os_error(self, mock_run):
        assert _find_running_server() is None


class TestCmdDashboard:
    @patch("forge.dashboard.commands._find_running_server", return_value=None)
    @patch("forge.dashboard.commands.subprocess.Popen")
    @patch("forge.dashboard.commands.webbrowser.open")
    def test_starts_server(self, mock_browser, mock_popen, mock_find, capsys):
        mock_popen.return_value = MagicMock(pid=999)
        args = argparse.Namespace(port=8765, no_browser=False)
        cmd_dashboard(args)
        mock_popen.assert_called_once()
        mock_browser.assert_called_once()

    @patch("forge.dashboard.commands._find_running_server", return_value=999)
    def test_already_running(self, mock_find, capsys):
        args = argparse.Namespace(port=8765, no_browser=False)
        cmd_dashboard(args)
        out = capsys.readouterr().out
        assert "already running" in out.lower() or "999" in out

    @patch("forge.dashboard.commands._find_running_server", return_value=None)
    @patch("forge.dashboard.commands.subprocess.Popen")
    @patch("forge.dashboard.commands.webbrowser.open")
    def test_no_browser_flag(self, mock_browser, mock_popen, mock_find):
        mock_popen.return_value = MagicMock(pid=999)
        args = argparse.Namespace(port=9999, no_browser=True)
        cmd_dashboard(args)
        mock_browser.assert_not_called()

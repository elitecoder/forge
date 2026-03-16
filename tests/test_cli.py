# Copyright 2026. Tests for unified CLI entry point.

from unittest.mock import patch, MagicMock

from forge.cli import main


class TestCLIRouting:
    @patch("sys.argv", ["forge"])
    def test_no_command_prints_help(self):
        result = main()
        assert result == 1

    @patch("sys.argv", ["forge", "plan", "--help"])
    def test_plan_routes_to_planner(self):
        with patch("forge.planner.commands.main") as mock_main:
            mock_main.side_effect = SystemExit(0)
            try:
                main()
            except SystemExit:
                pass
            mock_main.assert_called_once()

    @patch("sys.argv", ["forge", "execute", "--help"])
    def test_execute_routes_to_executor(self):
        with patch("forge.executor.commands.main") as mock_main:
            mock_main.side_effect = SystemExit(0)
            try:
                main()
            except SystemExit:
                pass
            mock_main.assert_called_once()

    @patch("sys.argv", ["forge", "status"])
    def test_status_routes_to_events(self):
        with patch("forge.core.events.cmd_status", return_value=0) as mock_status:
            result = main()
            mock_status.assert_called_once()
            assert result == 0

    @patch("sys.argv", ["forge", "setup"])
    def test_setup_routes_to_setup(self):
        with patch("forge.setup.run_setup") as mock_setup:
            result = main()
            mock_setup.assert_called_once_with(force=False)
            assert result == 0

    @patch("sys.argv", ["forge", "setup", "--force"])
    def test_setup_force(self):
        with patch("forge.setup.run_setup") as mock_setup:
            main()
            mock_setup.assert_called_once_with(force=True)

    @patch("sys.argv", ["forge", "setup"])
    def test_setup_failure(self):
        with patch("forge.setup.run_setup", side_effect=RuntimeError("fail")):
            result = main()
            assert result == 1

    @patch("sys.argv", ["forge", "dashboard"])
    def test_dashboard_routes(self):
        with patch("forge.dashboard.commands.cmd_dashboard", return_value=0) as mock_dash:
            result = main()
            mock_dash.assert_called_once()
            assert result == 0

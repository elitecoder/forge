# Copyright 2026. Tests for AgentRunner — thin wrapper over ClaudeProvider.

from unittest.mock import patch, MagicMock

from forge.core.runner import AgentRunner
from forge.providers.protocol import AgentResult


class TestAgentRunner:
    def test_delegates_to_provider(self):
        with patch("forge.core.runner.ClaudeProvider") as MockProvider:
            mock_provider = MagicMock()
            mock_provider.run_agent.return_value = AgentResult(
                exit_code=0, stdout="done", transcript_path="/t.log", timed_out=False,
            )
            MockProvider.return_value = mock_provider

            runner = AgentRunner("/session", "code", "/activity.log")
            result = runner.run(
                prompt="do work", model="sonnet", max_turns=10,
                cwd="/repo", timeout_s=300, agent_name="my-agent",
                extra_env={"FOO": "bar"},
            )

        assert result.exit_code == 0
        assert result.stdout == "done"
        mock_provider.run_agent.assert_called_once_with(
            prompt="do work", model="sonnet", max_turns=10, cwd="/repo",
            timeout_s=300, step_name="code", session_dir="/session",
            activity_log_path="/activity.log", agent_name="my-agent",
            extra_env={"FOO": "bar"},
        )

    def test_custom_agent_command(self):
        with patch("forge.core.runner.ClaudeProvider") as MockProvider:
            AgentRunner("/s", "step", "/log", agent_command="/usr/local/bin/claude-custom")
            MockProvider.assert_called_once_with(agent_command="/usr/local/bin/claude-custom")

    def test_default_agent_command(self):
        with patch("forge.core.runner.ClaudeProvider") as MockProvider:
            AgentRunner("/s", "step", "/log")
            MockProvider.assert_called_once_with(agent_command="claude")

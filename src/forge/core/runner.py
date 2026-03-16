# Copyright 2026. AgentRunner — thin wrapper over Provider protocol.

from forge.providers.protocol import AgentResult
from forge.providers.claude import ClaudeProvider


class AgentRunner:
    """Spawn and stream a claude -p agent with observability.

    Thin wrapper that delegates to ClaudeProvider, preserving the original
    interface where session_dir/step_name are bound at construction time.
    """

    def __init__(self, session_dir: str, step_name: str,
                 activity_log_path: str, agent_command: str | None = None):
        self.session_dir = session_dir
        self.step_name = step_name
        self.activity_log_path = activity_log_path
        self._provider = ClaudeProvider(agent_command=agent_command or "claude")

    def run(self, prompt: str, model: str, max_turns: int, cwd: str,
            timeout_s: int = 3600, agent_name: str = "",
            extra_env: dict[str, str] | None = None) -> AgentResult:
        return self._provider.run_agent(
            prompt=prompt, model=model, max_turns=max_turns, cwd=cwd,
            timeout_s=timeout_s, step_name=self.step_name,
            session_dir=self.session_dir,
            activity_log_path=self.activity_log_path,
            agent_name=agent_name, extra_env=extra_env,
        )


__all__ = ["AgentRunner", "AgentResult"]

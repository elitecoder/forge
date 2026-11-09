# Copyright 2026. Every agent Forge asks for must exist in the shipped plugin.

from pathlib import Path

from forge.executor.driver import _EXECUTOR_AGENTS
from forge.planner.driver import _AGENT_NAMES
from tests.fake_claude import available_agents

SKIN_DIR = Path(__file__).resolve().parents[1] / "skins" / "claude-code"


def test_every_requested_agent_is_in_the_plugin():
    shipped = set(available_agents([str(SKIN_DIR)]))
    requested = {f"forge:{n}" for n in [*_AGENT_NAMES.values(), *_EXECUTOR_AGENTS.values()]}
    assert requested - shipped == set()

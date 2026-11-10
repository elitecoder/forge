# Copyright 2026. Tests for planner driver — deterministic multi-phase orchestrator.

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

from forge.planner.driver import (
    PlannerDriver,
    PHASE_AGENTS,
    _load_enrichment_plugins,
)
from forge.planner.engine.state import (
    PHASE_ORDER,
    PhaseStatus,
    StateManager,
)
from forge.providers.protocol import AgentResult
from forge.providers.claude import ClaudeProvider
from tests.fake_claude import install


def _make_provider() -> MagicMock:
    provider = MagicMock()
    provider.run_agent.return_value = AgentResult(
        exit_code=0, stdout="ok", transcript_path="", timed_out=False,
    )
    provider.format_subagent_type.return_value = "general-purpose"
    return provider


def _write_evidence(session_dir: str, phase: str) -> None:
    """Write minimal valid evidence files for a given phase."""
    files = {
        "gatekeeper": [("spec-verdict", 1, None), ("spec-gaps.md", 5, None), ("complexity-verdict", 1, None)],
        "router": [("complexity-verdict", 1, None)],
        "architects": [("design-a.md", 25, None), ("design-b.md", 25, None)],
        "critics": [("critique-a.md", 20, None), ("critique-b.md", 20, None)],
        "refiners": [("refined-a.md", 25, None), ("refined-b.md", 25, None)],
        "judge": [("final-plan.md", 35, ["Decision", "Implementation Plan"])],
        "approval": [("approval-verdict", 1, None)],
    }
    for filename, line_count, headings in files.get(phase, []):
        lines = []
        if headings:
            lines.extend(f"# {h}" for h in headings)
        lines.extend(f"content line {i}" for i in range(line_count))
        path = Path(session_dir) / filename
        path.write_text("\n".join(lines))


def _setup_full_evidence(session_dir: str) -> MagicMock:
    """Create a provider that writes evidence files when run_agent is called."""
    provider = _make_provider()
    phase_for_agent = {}
    for phase, agents in PHASE_AGENTS.items():
        for agent_def in agents:
            phase_for_agent[agent_def["name"]] = phase

    def write_on_run(**kwargs):
        step = kwargs.get("step_name", "")
        phase = phase_for_agent.get(step, "")
        if phase:
            _write_evidence(session_dir, phase)
        return AgentResult(exit_code=0, stdout="ok", transcript_path="", timed_out=False)

    provider.run_agent.side_effect = lambda **kwargs: write_on_run(**kwargs)
    return provider


def test_init_session_creates_state(tmp_path):
    session_dir = str(tmp_path / "test-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("Add auth")

    state_file = Path(session_dir) / ".planner-state.json"
    assert state_file.is_file()

    mgr = StateManager(state_file)
    state = mgr.load()
    assert state.slug == "test-session"
    assert state.problem_statement == "Add auth"
    assert len(state.phases) == len(PHASE_ORDER)
    for phase in PHASE_ORDER:
        assert phase in state.phases


def test_init_session_lite_sets_pipeline_mode(tmp_path):
    session_dir = str(tmp_path / "lite-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("Add auth", lite=True)

    mgr = StateManager(Path(session_dir) / ".planner-state.json")
    state = mgr.load()
    assert state.pipeline_mode == "lite"


def test_dispatch_phase_calls_correct_agent_count(tmp_path):
    session_dir = str(tmp_path / "dispatch-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("problem")

    # Write evidence so gatekeeper passes
    _write_evidence(session_dir, "gatekeeper")
    state = driver.state_manager.load()
    driver._dispatch_phase("gatekeeper", state)
    assert provider.run_agent.call_count == 1

    # Mark gatekeeper complete so router can run
    def mark_gatekeeper(s):
        s.phases["gatekeeper"].status = PhaseStatus.COMPLETE
    driver.state_manager.update(mark_gatekeeper)

    provider.run_agent.reset_mock()
    _write_evidence(session_dir, "router")
    state = driver.state_manager.load()
    driver._dispatch_phase("router", state)
    # Router may skip agent if complexity-verdict already exists (written by gatekeeper evidence)
    # Since we wrote complexity-verdict in gatekeeper evidence, router skips the agent call
    assert provider.run_agent.call_count == 0

    # Mark router complete so architects can run
    def mark_router(s):
        s.phases["router"].status = PhaseStatus.COMPLETE
    driver.state_manager.update(mark_router)

    provider.run_agent.reset_mock()
    _write_evidence(session_dir, "architects")
    state = driver.state_manager.load()
    driver._dispatch_phase("architects", state)
    assert provider.run_agent.call_count == 2

    provider.run_agent.reset_mock()
    _write_evidence(session_dir, "critics")
    state = driver.state_manager.load()
    driver._dispatch_phase("critics", state)
    assert provider.run_agent.call_count == 2

    provider.run_agent.reset_mock()
    _write_evidence(session_dir, "refiners")
    state = driver.state_manager.load()
    driver._dispatch_phase("refiners", state)
    assert provider.run_agent.call_count == 2

    provider.run_agent.reset_mock()
    _write_evidence(session_dir, "judge")
    state = driver.state_manager.load()
    driver._dispatch_phase("judge", state)
    assert provider.run_agent.call_count == 1


def test_full_pipeline_dispatch(tmp_path):
    session_dir = str(tmp_path / "full-session")
    provider = _setup_full_evidence(session_dir)

    driver = PlannerDriver(provider, session_dir, auto_approve=True)
    result = driver.run("Add authentication")

    assert result == Path(session_dir) / "final-plan.md"

    state = driver.state_manager.load()
    for phase in PHASE_ORDER:
        if phase == "enrichment":
            assert state.phases[phase].status in (
                PhaseStatus.COMPLETE, PhaseStatus.SKIPPED,
            )
        else:
            assert state.phases[phase].status == PhaseStatus.COMPLETE, (
                f"{phase} should be COMPLETE but is {state.phases[phase].status}"
            )


def test_retry_on_evidence_failure(tmp_path):
    session_dir = str(tmp_path / "retry-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir, max_retries=2)
    driver._init_session("problem")

    # Mark prior phases complete so architects can run
    def complete_prior(s):
        for phase in ["gatekeeper", "router"]:
            s.phases[phase].status = PhaseStatus.COMPLETE
    driver.state_manager.update(complete_prior)

    # First dispatch: no evidence files -> fails (architects needs design-a.md etc.)
    state = driver.state_manager.load()
    driver._dispatch_phase("architects", state)

    state = driver.state_manager.load()
    assert state.phases["architects"].status == PhaseStatus.FAILED
    assert state.phases["architects"].retries == 1

    # Second dispatch: still no evidence -> fails again
    state = driver.state_manager.load()
    driver._dispatch_phase("architects", state)

    state = driver.state_manager.load()
    assert state.phases["architects"].status == PhaseStatus.FAILED
    assert state.phases["architects"].retries == 2


def test_state_transitions_throughout(tmp_path):
    session_dir = str(tmp_path / "transitions")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("problem")

    # All phases start as PENDING
    state = driver.state_manager.load()
    for phase in PHASE_ORDER:
        assert state.phases[phase].status == PhaseStatus.PENDING

    # After dispatching gatekeeper (with evidence), it goes to COMPLETE
    _write_evidence(session_dir, "gatekeeper")
    state = driver.state_manager.load()
    driver._dispatch_phase("gatekeeper", state)

    state = driver.state_manager.load()
    assert state.phases["gatekeeper"].status == PhaseStatus.COMPLETE
    assert state.phases["gatekeeper"].completed_at != ""
    assert state.phases["router"].status == PhaseStatus.PENDING


def test_build_phase_prompt_includes_context(tmp_path):
    session_dir = str(tmp_path / "prompt-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("Add caching layer")

    state = driver.state_manager.load()

    # Gatekeeper prompt
    prompt = driver._build_phase_prompt("gatekeeper", PHASE_AGENTS["gatekeeper"][0], state)
    assert "Add caching layer" in prompt
    assert "spec-verdict" in prompt

    # Architect A prompt
    prompt = driver._build_phase_prompt(
        "architects", PHASE_AGENTS["architects"][0], state,
    )
    assert "design-a.md" in prompt

    # Architect B prompt
    prompt = driver._build_phase_prompt(
        "architects", PHASE_AGENTS["architects"][1], state,
    )
    assert "design-b.md" in prompt

    # Judge prompt
    prompt = driver._build_phase_prompt("judge", PHASE_AGENTS["judge"][0], state)
    assert "final-plan.md" in prompt
    assert "refined-a.md" in prompt
    assert "refined-b.md" in prompt


def test_run_agent_passes_correct_kwargs(tmp_path):
    session_dir = str(tmp_path / "kwargs-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("problem")

    _write_evidence(session_dir, "gatekeeper")
    state = driver.state_manager.load()
    agent_def = PHASE_AGENTS["gatekeeper"][0]
    driver._run_agent("gatekeeper", agent_def, state)

    call_kwargs = provider.run_agent.call_args.kwargs
    assert call_kwargs["model"] == "opus"
    assert call_kwargs["max_turns"] == 50
    assert call_kwargs["cwd"] == session_dir
    assert call_kwargs["step_name"] == "gatekeeper"
    assert call_kwargs["session_dir"] == session_dir


# ── Enrichment from preset manifest ──────────────────────────────────────


def _create_preset_manifest(preset_dir: Path, enrichment: list[dict]) -> None:
    """Write a minimal preset manifest with enrichment config."""
    preset_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "preset": "test-preset",
        "version": 3,
        "description": "test",
        "enrichment": enrichment,
        "pipelines": {},
        "steps": {},
        "models": {},
    }
    (preset_dir / "manifest.json").write_text(json.dumps(manifest))


def test_load_enrichment_plugins_from_preset(tmp_path):
    preset_dir = tmp_path / "presets" / "web-app"
    plugins = [
        {"skill": "sq-unit-test-planner", "output_suffix": "unit-test-plan"},
        {"skill": "sq-visual-test-planner", "output_suffix": "visual-test-plan"},
    ]
    _create_preset_manifest(preset_dir, plugins)

    loaded = _load_enrichment_plugins(str(preset_dir))
    assert len(loaded) == 2
    assert loaded[0]["skill"] == "sq-unit-test-planner"
    assert loaded[1]["output_suffix"] == "visual-test-plan"


def test_load_enrichment_plugins_empty_preset_path():
    assert _load_enrichment_plugins("") == []


def test_load_enrichment_plugins_no_manifest(tmp_path):
    assert _load_enrichment_plugins(str(tmp_path / "nonexistent")) == []


def test_load_enrichment_plugins_no_enrichment_key(tmp_path):
    preset_dir = tmp_path / "preset"
    preset_dir.mkdir()
    (preset_dir / "manifest.json").write_text('{"preset": "x"}')
    assert _load_enrichment_plugins(str(preset_dir)) == []


def test_init_session_stores_preset(tmp_path):
    session_dir = str(tmp_path / "preset-session")
    provider = _make_provider()
    driver = PlannerDriver(
        provider, session_dir,
        preset="web-app", preset_path="/presets/web-app",
    )
    driver._init_session("problem")

    state = driver.state_manager.load()
    assert state.preset == "web-app"
    assert state.preset_path == "/presets/web-app"


def test_dispatch_enrichment_with_preset_plugins(tmp_path):
    preset_dir = tmp_path / "presets" / "web-app"
    plugins = [
        {"skill": "sq-unit-test-planner", "output_suffix": "unit-test-plan"},
        {"skill": "sq-visual-test-planner", "output_suffix": "visual-test-plan"},
    ]
    _create_preset_manifest(preset_dir, plugins)

    session_dir = str(tmp_path / "enrich-session")
    provider = _make_provider()
    driver = PlannerDriver(
        provider, session_dir,
        preset="web-app", preset_path=str(preset_dir),
    )
    driver._init_session("problem")

    # Complete all phases up to enrichment
    def complete_prior(s):
        for phase in PHASE_ORDER:
            if phase == "enrichment":
                break
            s.phases[phase].status = PhaseStatus.COMPLETE
    driver.state_manager.update(complete_prior)

    state = driver.state_manager.load()
    driver._dispatch_enrichment(state)

    assert provider.run_agent.call_count == 2
    call_names = [c.kwargs["step_name"] for c in provider.run_agent.call_args_list]
    assert "enrich-sq-unit-test-planner" in call_names
    assert "enrich-sq-visual-test-planner" in call_names


def test_dispatch_enrichment_without_preset(tmp_path):
    session_dir = str(tmp_path / "no-preset-session")
    provider = _make_provider()
    driver = PlannerDriver(provider, session_dir)
    driver._init_session("problem")

    state = driver.state_manager.load()
    driver._dispatch_enrichment(state)

    provider.run_agent.assert_not_called()


SKIN_DIR = Path(__file__).resolve().parents[2] / "skins" / "claude-code"


def test_architects_start_with_shipped_plugin(tmp_path, monkeypatch):
    log = install(
        tmp_path, monkeypatch,
        script='f=$(grep -o "[^ ]*/design-[ab]\\.md" "$FAKE_CLAUDE_PROMPT" | head -1); '
               'seq 1 30 | sed "s/^/line /" > "$f"',
    )
    monkeypatch.setenv("FORGE_PLUGIN_DIR", str(SKIN_DIR))
    session_dir = str(tmp_path / "session")
    driver = PlannerDriver(ClaudeProvider(), session_dir, repo_dir=str(tmp_path), workers=1)
    driver._init_session("Add undo")

    def complete_prior(s):
        for phase in ["gatekeeper", "router"]:
            s.phases[phase].status = PhaseStatus.COMPLETE
    driver.state_manager.update(complete_prior)

    driver._dispatch_phase("architects", driver.state_manager.load())

    agents = [json.loads(line)["argv"] for line in log.read_text().splitlines()]
    assert [a[a.index("--agent") + 1] for a in agents] == ["forge:planner-architect"] * 2
    assert driver.state_manager.load().phases["architects"].status == PhaseStatus.COMPLETE


def test_run_stops_when_phase_exhausts_retries(tmp_path):
    session_dir = str(tmp_path / "exhaust")
    calls = []

    def run_agent(**kwargs):
        calls.append(kwargs["step_name"])
        if len(calls) > 20:
            raise RuntimeError("retry cap not enforced")
        if kwargs["step_name"] == "gatekeeper":
            _write_evidence(session_dir, "gatekeeper")
        return AgentResult(exit_code=0, stdout="", transcript_path="", timed_out=False)

    provider = _make_provider()
    provider.run_agent.side_effect = run_agent
    driver = PlannerDriver(provider, session_dir, max_retries=2, workers=1, auto_approve=True)

    assert driver.run("problem") is None

    architects = driver.state_manager.load().phases["architects"]
    assert architects.status == PhaseStatus.FAILED
    assert architects.retries == 2
    assert calls.count("architect-a") == 2
    assert "Stopped: phase architects failed 2 times" in (
        Path(session_dir) / "planner-activity.log").read_text()

# Copyright 2026. Planner driver — deterministic multi-phase orchestrator.

import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from forge.core.events import emit, EventType
from forge.core.logging import log_activity, StatusWriter
from forge.planner.engine.evidence import validate_phase
from forge.planner.engine.state import (
    PHASE_ORDER,
    PhaseState,
    PhaseStatus,
    PlannerState,
    StateManager,
    now_iso,
)
from forge.planner.commands import resolve_skill_dir, _load_planner_conventions
from forge.providers.protocol import AgentResult, Provider


MAX_RETRIES = 2

# Plugin prefix for agent names (from skins/claude-code/.claude-plugin/plugin.json)
_PLUGIN_PREFIX = "forge"

# Agent names per phase (resolved as {_PLUGIN_PREFIX}:{name} by --agent flag)
_AGENT_NAMES: dict[str, str] = {
    "planner-gatekeeper": "planner-gatekeeper",
    "planner-router":     "planner-router",
    "planner-architect":  "planner-architect",
    "planner-critic":     "planner-critic",
    "planner-refiner":    "planner-refiner",
    "planner-judge":      "planner-judge",
    "planner-approval":   "planner-approval",
}

PHASE_MODELS = {
    "gatekeeper":  {"default": "opus"},
    "router":      {"default": "haiku"},
    "architects":  {"default": "opus"},
    "critics":     {"default": "opus"},
    "refiners":    {"default": "sonnet"},
    "judge":       {"default": "opus"},
    "enrichment":  {"default": "sonnet"},
    "approval":    {"default": "sonnet"},
}

PHASE_MAX_TURNS = {
    "gatekeeper": 50,
    "router": 20,
    "architects": 200,
    "critics": 200,
    "refiners": 200,
    "judge": 200,
    "enrichment": 200,
    "approval": 50,
}

PHASE_TIMEOUT_S = {
    "gatekeeper": 600,
    "router": 120,
    "architects": 900,
    "critics": 600,
    "refiners": 600,
    "judge": 600,
    "enrichment": 600,
    "approval": 300,
}

# Agent definitions per phase
PHASE_AGENTS: dict[str, list[dict]] = {
    "gatekeeper": [
        {"name": "gatekeeper", "agent_type": "planner-gatekeeper", "output": "spec-verdict"},
    ],
    "router": [
        {"name": "router", "agent_type": "planner-router", "output": "complexity-verdict"},
    ],
    "architects": [
        {"name": "architect-a", "agent_type": "planner-architect",
         "output": "design-a.md", "id": "a", "reasoning": "top-down"},
        {"name": "architect-b", "agent_type": "planner-architect",
         "output": "design-b.md", "id": "b", "reasoning": "bottom-up"},
    ],
    "critics": [
        {"name": "critic-a", "agent_type": "planner-critic",
         "output": "critique-a.md", "id": "a"},
        {"name": "critic-b", "agent_type": "planner-critic",
         "output": "critique-b.md", "id": "b"},
    ],
    "refiners": [
        {"name": "refiner-a", "agent_type": "planner-refiner",
         "output": "refined-a.md", "id": "a"},
        {"name": "refiner-b", "agent_type": "planner-refiner",
         "output": "refined-b.md", "id": "b"},
    ],
    "judge": [
        {"name": "judge", "agent_type": "planner-judge", "output": "final-plan.md"},
    ],
    "approval": [
        {"name": "approval", "agent_type": "planner-approval", "output": "approval-verdict"},
    ],
}


def _kill_child_processes():
    my_pid = os.getpid()
    try:
        subprocess.run(["pkill", "-TERM", "-P", str(my_pid)], capture_output=True, timeout=3)
    except Exception:
        pass
    time.sleep(0.5)
    try:
        subprocess.run(["pkill", "-KILL", "-P", str(my_pid)], capture_output=True, timeout=3)
    except Exception:
        pass


def _resolve_model(phase: str) -> str:
    return PHASE_MODELS.get(phase, {}).get("default", "sonnet")


def _activity_log(session_dir: str) -> str:
    return os.path.join(session_dir, "planner-activity.log")


def _log_activity(session_dir: str, message: str) -> None:
    log_activity(_activity_log(session_dir), "", message)


def _load_enrichment_plugins(preset_path: str) -> list[dict]:
    if not preset_path:
        return []
    manifest = Path(preset_path) / "manifest.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text())
        return data.get("enrichment", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_system_prompt(prompts_dir: str, template_file: str) -> str:
    path = Path(prompts_dir) / template_file
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].lstrip("\n")
    return text


class PlannerDriver:
    """Deterministic multi-phase planner orchestrator.

    Runs: gatekeeper -> router -> architects -> critics -> refiners -> judge -> enrichment -> approval
    Each phase: dispatch agents -> wait -> validate evidence -> pass/fail
    """

    def __init__(self, provider: Provider, session_dir: str, prompts_dir: str = "",
                 max_retries: int = MAX_RETRIES, workers: int = 2,
                 repo_dir: str = "", preset: str = "", preset_path: str = "",
                 auto_approve: bool = False):
        self.provider = provider
        self.session_dir = session_dir
        self.repo_dir = repo_dir or session_dir
        self.prompts_dir = prompts_dir or str(Path(__file__).parent.parent / "prompts")
        self.preset = preset
        self.preset_path = preset_path
        self.auto_approve = auto_approve
        self.state_manager = StateManager(Path(session_dir) / ".planner-state.json")
        self.max_retries = max_retries
        self.workers = workers

    def run(self, problem: str, force: bool = False, clean: bool = False,
            lite: bool = False, **kwargs) -> Path | None:
        """Run all phases, return path to final-plan.md or None on failure."""
        self._init_session(problem, lite=lite)
        self._install_signal_handlers()
        self._set_driver_pid()

        try:
            killed = self.drive_phases()
        finally:
            self._clear_driver_pid()

        if killed:
            state = self.state_manager.load()
            emit(self.session_dir, EventType.PLANNER_KILLED, "planner",
                 preset=self.preset, error=state.kill_reason or "")

        plan_path = Path(self.session_dir) / "final-plan.md"
        if killed or not plan_path.is_file():
            if not killed:
                emit(self.session_dir, EventType.PLANNER_FAILED, "planner",
                     preset=self.preset)
            return None
        emit(self.session_dir, EventType.PLANNER_COMPLETED, "planner",
             preset=self.preset)
        return plan_path

    def drive_phases(self) -> bool:
        """Run phases until done, killed, or a phase uses up its retries.

        Returns True when the session was killed.
        """
        while True:
            state = self.state_manager.load()
            if state.killed:
                _log_activity(self.session_dir,
                              f"Stopped: killed ({state.kill_reason or 'no reason'})")
                return True
            phase = state.next_runnable()
            if phase is None:
                return False
            self._dispatch_phase(phase, state)

            state = self.state_manager.load()
            ps = state.phases[phase]
            if ps.status == PhaseStatus.FAILED and ps.retries >= self.max_retries:
                _log_activity(self.session_dir,
                              f"Stopped: phase {phase} failed {ps.retries} times")
                return False

            if phase == "gatekeeper" and ps.status == PhaseStatus.COMPLETE:
                if not self.auto_approve and self._verdict_blocks():
                    continue
                self._apply_complexity_routing()

            if (phase == "refiners" and ps.status == PhaseStatus.COMPLETE
                    and state.pipeline_mode == "lite"):
                self._promote_refined_to_final()

    def _verdict_blocks(self) -> bool:
        """Check if spec-verdict is BLOCKED. If so, kill pipeline for human review."""
        verdict_path = Path(self.session_dir) / "spec-verdict"
        if not verdict_path.is_file():
            return False
        verdict = verdict_path.read_text().strip().upper()
        if verdict == "BLOCKED":
            _log_activity(self.session_dir, "Spec verdict: BLOCKED — pausing for human review")
            def mutate(s: PlannerState) -> None:
                s.killed = True
                s.kill_reason = "spec_review_required"
            self.state_manager.update(mutate)
            return True
        return False

    def _apply_complexity_routing(self) -> None:
        """Read complexity-verdict and set pipeline_mode (FULL or LITE)."""
        verdict_path = Path(self.session_dir) / "complexity-verdict"
        mode = "full"
        if verdict_path.is_file():
            raw = verdict_path.read_text().strip().upper()
            if raw == "LITE":
                mode = "lite"

        def mutate(s: PlannerState) -> None:
            s.pipeline_mode = mode
        self.state_manager.update(mutate)
        _log_activity(self.session_dir, f"Pipeline mode: {mode}")

        if mode == "lite":
            # Skip "b" agents — mark critics/refiners as needing only "a" variants
            # The router phase handles this; the evidence validator uses pipeline_mode
            _log_activity(self.session_dir, "Lite mode: single architect/critic/refiner track")

    def _promote_refined_to_final(self) -> None:
        """In lite mode, copy refined-a.md → final-plan.md and skip judge."""
        refined = Path(self.session_dir) / "refined-a.md"
        final = Path(self.session_dir) / "final-plan.md"
        if refined.is_file() and not final.is_file():
            final.write_text(refined.read_text())
            _log_activity(self.session_dir, "Lite mode: promoted refined-a → final-plan")

            def skip_judge(s: PlannerState) -> None:
                s.phases["judge"].status = PhaseStatus.SKIPPED
                s.phases["judge"].completed_at = now_iso()
            self.state_manager.update(skip_judge)

    def _install_signal_handlers(self) -> None:
        def _handle(sig, _frame):
            reason = "SIGTERM" if sig == signal.SIGTERM else "SIGINT"
            _log_activity(self.session_dir, f"Signal received: {reason}")
            _kill_child_processes()
            try:
                def mutate(s: PlannerState) -> None:
                    s.killed = True
                    s.kill_reason = reason
                    s.driver_pid = 0
                self.state_manager.update(mutate)
            except Exception:
                pass

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)

    def _set_driver_pid(self) -> None:
        try:
            def mutate(s: PlannerState) -> None:
                s.driver_pid = os.getpid()
                s.killed = False
                s.kill_reason = ""
            self.state_manager.update(mutate)
        except Exception:
            pass

    def _clear_driver_pid(self) -> None:
        try:
            def mutate(s: PlannerState) -> None:
                s.driver_pid = 0
            self.state_manager.update(mutate)
        except Exception:
            pass

    def _init_session(self, problem: str, lite: bool = False) -> None:
        os.makedirs(self.session_dir, exist_ok=True)
        slug = Path(self.session_dir).name

        phases = {name: PhaseState() for name in PHASE_ORDER}

        state = PlannerState(
            slug=slug,
            session_dir=self.session_dir,
            repo_dir=self.repo_dir,
            preset=self.preset,
            preset_path=self.preset_path,
            problem_statement=problem,
            pipeline_mode="lite" if lite else "",
            phases=phases,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self.state_manager.save(state)
        emit(self.session_dir, EventType.PLANNER_STARTED, "planner",
             preset=self.preset)
        _log_activity(self.session_dir, f"Session initialized: {slug}")

    def _dispatch_phase(self, phase: str, state: PlannerState) -> None:
        _log_activity(self.session_dir, f"Phase {phase} -> in_progress")
        phase_start = time.monotonic()

        def mark_running(s: PlannerState) -> None:
            s.phases[phase].status = PhaseStatus.IN_PROGRESS
            s.phases[phase].started_at = now_iso()

        self.state_manager.update(mark_running)
        emit(self.session_dir, EventType.PHASE_STARTED, "planner",
             preset=self.preset, phase=phase)

        if phase == "enrichment":
            self._dispatch_enrichment(state)
        elif phase == "router":
            # Router is handled by gatekeeper output + _apply_complexity_routing
            # If we have a complexity-verdict file, skip the agent
            verdict_path = Path(self.session_dir) / "complexity-verdict"
            if not verdict_path.is_file():
                agents = PHASE_AGENTS.get(phase, [])
                for agent_def in agents:
                    self._run_agent(phase, agent_def, state)
        else:
            agents = PHASE_AGENTS.get(phase, [])
            # In lite mode, only run "a" variant agents
            if state.pipeline_mode == "lite":
                agents = [a for a in agents if a.get("id", "") != "b"]
            # Only run agents whose output files are missing
            agents_to_run = [
                ad for ad in agents
                if not os.path.isfile(os.path.join(self.session_dir, ad.get("output", "")))
            ]
            if not agents_to_run:
                agents_to_run = agents

            if len(agents_to_run) > 1 and self.workers > 1:
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    futures = {
                        pool.submit(self._run_agent, phase, ad, state): ad
                        for ad in agents_to_run
                    }
                    for f in as_completed(futures):
                        f.result()
            else:
                for agent_def in agents_to_run:
                    self._run_agent(phase, agent_def, state)

        elapsed = time.monotonic() - phase_start
        elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

        evidence = validate_phase(self.session_dir, phase, state.pipeline_mode)
        if evidence.passed:
            def mark_passed(s: PlannerState) -> None:
                s.phases[phase].status = PhaseStatus.COMPLETE
                s.phases[phase].completed_at = now_iso()

            self.state_manager.update(mark_passed)
            emit(self.session_dir, EventType.PHASE_COMPLETED, "planner",
                 preset=self.preset, phase=phase)
            _log_activity(self.session_dir,
                          f"Phase {phase} -> complete in {elapsed_str} ({evidence.message})")
        else:
            current = self.state_manager.load()
            retries = current.phases[phase].retries

            def mark_failed(s: PlannerState) -> None:
                s.phases[phase].status = PhaseStatus.FAILED
                s.phases[phase].retries += 1
                s.phases[phase].last_error = evidence.message

            self.state_manager.update(mark_failed)
            emit(self.session_dir, EventType.PHASE_FAILED, "planner",
                 preset=self.preset, phase=phase, error=evidence.message,
                 retries=retries + 1)
            _log_activity(self.session_dir,
                          f"Phase {phase} -> failed in {elapsed_str} "
                          f"(retry {retries + 1}/{self.max_retries}): {evidence.message}")

    def _run_agent(self, phase: str, agent_def: dict, state: PlannerState) -> AgentResult:
        model = _resolve_model(phase)
        max_turns = PHASE_MAX_TURNS.get(phase, 200)
        timeout_s = PHASE_TIMEOUT_S.get(phase, 900)
        prompt = self._build_phase_prompt(phase, agent_def, state)
        step_name = agent_def["name"]
        cwd = state.repo_dir or self.repo_dir

        # Resolve agent name for --agent flag
        agent_type = agent_def.get("agent_type", "")
        agent_name = ""
        if agent_type and agent_type in _AGENT_NAMES:
            agent_name = f"{_PLUGIN_PREFIX}:{_AGENT_NAMES[agent_type]}"

        result = self.provider.run_agent(
            prompt=prompt,
            model=model,
            max_turns=max_turns,
            cwd=cwd,
            timeout_s=timeout_s,
            step_name=step_name,
            session_dir=self.session_dir,
            activity_log_path=_activity_log(self.session_dir),
            agent_name=agent_name,
            extra_env={"FORGE_MODE": "planner", "FORGE_AGENT": step_name},
        )
        if result.timed_out:
            _log_activity(self.session_dir, f"WARNING: agent {step_name} timed out after {timeout_s}s")
        return result

    def _dispatch_enrichment(self, state: PlannerState) -> None:
        plugins = _load_enrichment_plugins(state.preset_path)
        if not plugins:
            return

        model = _resolve_model("enrichment")
        max_turns = PHASE_MAX_TURNS.get("enrichment", 200)

        for plugin in plugins:
            skill_name = plugin.get("skill", "")
            skill_dir = resolve_skill_dir(skill_name, state.preset_path)
            if skill_dir is None:
                continue
            output_suffix = plugin.get("output_suffix", skill_name)
            prompt = (
                f"Enrichment task for skill '{skill_name}'.\n"
                f"Skill directory: {skill_dir}\n"
                f"Read the skill at: {skill_dir}/SKILL.md\n"
                f"Session: {self.session_dir}\n"
                f"Problem: {state.problem_statement}\n"
                f"Write output to: {self.session_dir}/{output_suffix}.md"
            )
            self.provider.run_agent(
                prompt=prompt,
                model=model,
                max_turns=max_turns,
                cwd=state.repo_dir or self.repo_dir,
                step_name=f"enrich-{skill_name}",
                session_dir=self.session_dir,
                activity_log_path=_activity_log(self.session_dir),
            )

    def _build_phase_prompt(self, phase: str, agent_def: dict,
                            state: PlannerState) -> str:
        design_id = agent_def.get("id", "")
        sd = self.session_dir
        conventions = _load_planner_conventions(state.preset_path)

        if phase == "gatekeeper":
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Problem statement**: {state.problem_statement}\n"
                f"- **Write verdict to**: {sd}/spec-verdict (exactly one word: PASS or BLOCKED)\n"
                f"- **Write gaps to**: {sd}/spec-gaps.md (full analysis for humans)\n"
                f"- **Write complexity verdict to**: {sd}/complexity-verdict (exactly one word: FULL or LITE)\n\n"
                f"Evaluate whether the problem statement provides enough information for architects."
            )

        if phase == "router":
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Problem statement**: {state.problem_statement}\n"
                f"- **Write complexity verdict to**: {sd}/complexity-verdict (exactly one word: FULL or LITE)\n\n"
                f"Assess architectural ambiguity to route to FULL (adversarial dual-track) or LITE (single-track) pipeline."
            )

        if phase == "architects":
            reasoning = agent_def.get("reasoning", "top-down")
            design_file = f"design-{design_id}.md"
            gaps_path = Path(sd) / "spec-gaps.md"
            gaps_hint = ""
            if gaps_path.is_file():
                gaps_hint = f"\n- **Spec analysis**: {gaps_path} (read this — it contains identified gaps and any answers from the product owner)"
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Problem statement**: {state.problem_statement}\n"
                f"- **Reasoning direction**: {reasoning}\n"
                f"- **Write design to**: {sd}/{design_file}{gaps_hint}{conventions}"
            )

        if phase == "critics":
            design_file = f"design-{design_id}.md"
            critique_file = f"critique-{design_id}.md"
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Design file**: {sd}/{design_file}\n"
                f"- **Codebase brief**: {sd}/codebase-brief.md\n"
                f"- **Write critique to**: {sd}/{critique_file}"
            )

        if phase == "refiners":
            design_file = f"design-{design_id}.md"
            critique_file = f"critique-{design_id}.md"
            refined_file = f"refined-{design_id}.md"
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Design file**: {sd}/{design_file}\n"
                f"- **Critique file**: {sd}/{critique_file}\n"
                f"- **Codebase brief**: {sd}/codebase-brief.md\n"
                f"- **Write refined design to**: {sd}/{refined_file}"
            )

        if phase == "judge":
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Problem statement**: {state.problem_statement}\n"
                f"- **Original design A**: {sd}/design-a.md\n"
                f"- **Original design B**: {sd}/design-b.md\n"
                f"- **Refined design A (revisions)**: {sd}/refined-a.md\n"
                f"- **Refined design B (revisions)**: {sd}/refined-b.md\n"
                f"- **Codebase brief**: {sd}/codebase-brief.md\n"
                f"- **Write final plan to**: {sd}/final-plan.md{conventions}"
            )

        if phase == "approval":
            return (
                f"## Context\n\n"
                f"- **Session directory**: {sd}\n"
                f"- **Problem statement**: {state.problem_statement}\n"
                f"- **Final plan**: {sd}/final-plan.md\n"
                f"- **Write verdict to**: {sd}/approval-verdict\n\n"
                f"Compare the final plan against the source ticket requirements and post an APPROVED or FAILED verdict."
            )

        return f"Phase: {phase}\nSession: {sd}\nProblem: {state.problem_statement}"

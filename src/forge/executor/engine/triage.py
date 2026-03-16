# Copyright 2026. Triage agent dispatch — failure classification after fix retries exhaust.

import json
import os
import re
from pathlib import Path

from forge.core.runner import AgentRunner


def _ticket_from_session(session_dir: str) -> str:
    """Extract ticket key (e.g. PROJ-123) from session directory name."""
    from forge.core.session_hash import extract_ticket_key
    name = os.path.basename(session_dir.rstrip("/"))
    return extract_ticket_key(name)


def _planner_session_dir(state) -> str:
    """Infer planner session directory from the plan_file path in state."""
    if not state.plan_file:
        return ""
    plan_dir = str(Path(state.plan_file).expanduser().resolve().parent)
    if Path(plan_dir).is_dir() and (Path(plan_dir) / ".planner-state.json").is_file():
        return plan_dir
    return ""


def run_triage(
    session_dir: str,
    step_name: str,
    last_error: str,
    cwd: str = "",
    model: str = "sonnet",
    agent_name: str = "",
    planner_session: str = "",
) -> str | None:
    """Dispatch the triage agent for a failed step. Returns triage file path, or None on failure."""
    from forge.executor.engine.state import StateManager

    state_path = Path(session_dir) / "agent-state.json"
    if not state_path.is_file():
        return None

    state = StateManager(state_path).load()

    ticket_key = _ticket_from_session(session_dir)
    session_hash = os.path.basename(session_dir.rstrip("/"))
    planner_dir = planner_session if planner_session else _planner_session_dir(state)
    activity_log = os.path.join(session_dir, "pipeline-activity.log")

    prompt = build_triage_prompt(
        session_dir=session_dir,
        step_name=step_name,
        session_hash=session_hash,
        ticket_key=ticket_key,
        last_error=last_error,
        planner_session_dir=planner_dir,
    )

    triage_env = {
        "FORGE_MODE": "executor",
        "FORGE_OUTPUT_FILE": f"triage-{step_name}.md,triage-comment.md",
    }
    runner = AgentRunner(session_dir, f"triage-{step_name}", activity_log)
    runner.run(
        prompt=prompt,
        model=model,
        max_turns=0,
        cwd=cwd or os.getcwd(),
        timeout_s=600,
        agent_name=agent_name,
        extra_env=triage_env,
    )

    triage_file = os.path.join(session_dir, f"triage-{step_name}.md")
    return triage_file if os.path.isfile(triage_file) else None


def find_failed_step(session_dir: str) -> tuple[str, str] | None:
    """Find the first permanently failed step in a session. Returns (step_name, last_error) or None."""
    from forge.executor.engine.state import StateManager, StepStatus

    state_path = Path(session_dir) / "agent-state.json"
    if not state_path.is_file():
        return None

    state = StateManager(state_path).load()
    for name in state.step_names_ordered():
        ss = state.steps[name]
        if ss.status == StepStatus.FAILED and ss.retries >= 3:
            return name, ss.last_error
    # Fall back to any failed step
    for name in state.step_names_ordered():
        ss = state.steps[name]
        if ss.status == StepStatus.FAILED:
            return name, ss.last_error
    return None


def _build_visual_test_section(session_dir: str) -> str:
    """Build triage context for visual_test failures — screenshots, judge verdict, Figma reference."""
    sd = Path(session_dir)
    lines = ["\nVisual test evidence (YOU MUST review these to classify correctly):"]

    # Figma reference — the design source of truth
    figma_ref = sd / "figma-reference.png"
    if figma_ref.is_file():
        lines.append(f"- Figma reference (design intent): {figma_ref}")

    figma_spec = sd / "figma-spec.json"
    if figma_spec.is_file():
        lines.append(f"- Figma design specs: {figma_spec}")

    # Judge verdict — what the judge decided and why
    # Check latest attempt dir first, then session root
    verdict_found = False
    for attempt_dir in sorted(sd.glob("visual-test/attempt-*"), reverse=True):
        verdict = attempt_dir / "judge-verdict.json"
        if verdict.is_file():
            lines.append(f"- Judge verdict: {verdict}")
            verdict_found = True
            # Screenshots from this attempt
            screenshots_dir = attempt_dir / "screenshots"
            if screenshots_dir.is_dir():
                for img in sorted(screenshots_dir.glob("*.png")):
                    lines.append(f"- Screenshot: {img}")
            break
    if not verdict_found:
        verdict = sd / "judge-verdict.json"
        if verdict.is_file():
            lines.append(f"- Judge verdict: {verdict}")

    # Before/after screenshots in session root
    screenshots = sorted(sd.glob("verify-*.png"))
    if screenshots:
        lines.append("")
        lines.append("Before/after screenshots (VIEW ALL of these — they are ground truth):")
        for img in screenshots:
            lines.append(f"- {img}")

    # Test results
    results = sd / "feature-test-results.json"
    if results.is_file():
        lines.append(f"- Test results: {results}")

    # Judge transcript
    judge_log = sd / "visual_test_judge-transcript.log"
    if judge_log.is_file():
        lines.append(f"- Judge reasoning transcript: {judge_log}")

    lines.append("")
    lines.append(
        "**CRITICAL**: Compare screenshots against the Figma reference. "
        "If the Figma shows UI elements (icons, buttons, controls) that are NOT visible "
        "in the implementation screenshots, classify as code_fault — the implementation "
        "does not match the design, regardless of what DOM assertions or the judge claimed."
    )
    lines.append("")

    return "\n".join(lines)


def build_triage_prompt(
    session_dir: str,
    step_name: str,
    session_hash: str,
    ticket_key: str,
    last_error: str,
    planner_session_dir: str = "",
) -> str:
    """Build the prompt for the triage agent."""
    sd = session_dir
    triage_file = f"{sd}/triage-{step_name}.md"
    comment_file = f"{sd}/triage-comment.md"

    planner_section = ""
    if planner_session_dir and Path(planner_session_dir).is_dir():
        assumptions_line = ""
        assumptions_path = Path(planner_session_dir) / "assumptions.md"
        if assumptions_path.is_file():
            brief_path = Path(planner_session_dir) / "codebase-brief.md"
            has_human_specs = (
                brief_path.is_file()
                and "## Human-Verified Specifications" in brief_path.read_text(errors="replace")
            )
            if has_human_specs:
                assumptions_line = f"\n- Assumptions file: {assumptions_path} (assumptions were reviewed and answered by human)"
            else:
                assumptions_line = f"\n- Assumptions file: {assumptions_path} (assumptions were auto-approved — human did NOT review)"

        planner_section = f"""
Planner session artifacts (read these to trace root cause):
- Recon brief: {planner_session_dir}/codebase-brief.md
- Design A: {planner_session_dir}/design-a.md
- Design B: {planner_session_dir}/design-b.md
- Critique A: {planner_session_dir}/critique-a.md
- Critique B: {planner_session_dir}/critique-b.md
- Final plan: {planner_session_dir}/final-plan.md
- Planner prompts: {planner_session_dir}/prompts/{assumptions_line}
"""

    ca_section = ""
    ca_logs = Path(sd) / "prompts" / "ca-logs"
    base_prompts = Path(sd) / "prompts" / "base"
    if ca_logs.is_dir() or base_prompts.is_dir():
        ca_section = f"""
Context Assembler artifacts (this session used the Context Assembler subsystem):
- Context Assembler logs (reasoning): {ca_logs}/
- Base prompts (before Context Assembler): {base_prompts}/
- Final prompts (after Context Assembler): {sd}/prompts/
Compare base vs final to see what Context Assembler changed.
"""

    visual_section = _build_visual_test_section(sd) if step_name == "visual_test" else ""

    return f"""## Failure Triage

Session: {session_hash}
Failed step: {step_name}
Ticket: {ticket_key or '(none)'}
Last error: {last_error}

Session directory: {sd}
{planner_section}{ca_section}
Executor artifacts:
- Step prompts: {sd}/prompts/
- Pipeline output: {sd}/pipeline-output.md
- Fix attempt outputs: check for {step_name}-related files in {sd}/
{visual_section}
## Task

**READ-ONLY: Do NOT modify any source code, test files, or repository files. Only read artifacts and write the triage/comment files below.**

1. Read the artifacts above in order (planner → executor → error) to trace the failure to its root cause.
2. Classify the failure using ONLY the labels from the system prompt.
3. Write detailed analysis to: {triage_file}
   Include: root cause chain, evidence quotes, classification with confidence.
4. Write formatted comment to: {comment_file}
   Use this format:

[forge-session:{session_hash}]

{{panel:title=Forge Session {session_hash} — FAILED}}
*Steps*: (count passed/total from pipeline state)
*Failed at*: {step_name}
*Root cause*: (your classification) (confidence: high|medium|low)
*Trace*: (which phase/artifact → what was missing/wrong)
*Evidence*: (quoted sections from artifacts)
*Summary*: (what went wrong and why)
*What to change*: (specific guidance for next run)
{{panel}}

No comment skill configured — skip posting, just write the files.
"""

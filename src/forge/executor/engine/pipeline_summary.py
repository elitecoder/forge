# Copyright 2026. Pipeline summary — mechanical generation of step-by-step outcome summary.

from datetime import datetime, timezone
from pathlib import Path

from forge.executor.engine.state import PipelineState, StepStatus


def generate_pipeline_summary(state: PipelineState, session_hash: str = "",
                               branch_name: str = "") -> str:
    """Generate a markdown summary of pipeline execution."""
    steps = state.step_names_ordered()
    passed = sum(1 for n in steps if state.steps[n].status == StepStatus.COMPLETE)
    total = len(steps)

    lines = [
        f"# Executor Session {session_hash or '(no hash)'}",
        "",
        f"Branch: `{branch_name or '(unknown)'}`",
        f"Steps: {passed}/{total} passed",
        "",
        "| Step | Status | Duration | Error |",
        "|------|--------|----------|-------|",
    ]

    for name in steps:
        ss = state.steps[name]
        status = ss.status.value.upper()
        duration = _format_duration(ss.started_at, ss.completed_at)
        error = ss.last_error[:80] if ss.last_error else ""
        lines.append(f"| {name} | {status} | {duration} | {error} |")

    # Append triage classification if triage files exist
    session_dir = Path(state.session_dir) if state.session_dir else None
    if session_dir:
        for triage_file in session_dir.glob("triage-*.md"):
            if triage_file.name == "triage-comment.md":
                continue
            content = triage_file.read_text()
            for fault_type in ("code_fault", "plan_fault", "recon_fault",
                               "context_assembler_fault", "input_fault",
                               "environment_fault"):
                if fault_type in content.lower() or fault_type.replace("_", " ") in content.lower():
                    step_name = triage_file.stem.replace("triage-", "")
                    lines.append(f"\nTriage: {fault_type} — see triage-{step_name}.md for details")
                    break

    return "\n".join(lines) + "\n"


def write_pipeline_summary(session_dir: str, state: PipelineState,
                            session_hash: str = "", branch_name: str = "") -> Path:
    """Write pipeline summary to session dir. Returns the file path."""
    summary = generate_pipeline_summary(state, session_hash, branch_name)
    path = Path(session_dir) / "pipeline-summary.md"
    path.write_text(summary)
    return path


def _format_duration(started_at: str, completed_at: str) -> str:
    if not started_at:
        return ""
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed_at
            else datetime.now(timezone.utc)
        )
        seconds = int((end - start).total_seconds())
        if seconds >= 60:
            return f"{seconds // 60}m {seconds % 60}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return ""


def format_success_comment(session_hash: str, state: PipelineState,
                            branch_name: str = "") -> str:
    """Format a mechanical success comment for posting to the session store."""
    steps = state.step_names_ordered()
    total = len(steps)
    return (
        f"[forge-session:{session_hash}]\n\n"
        f"{{panel:title=Forge Session {session_hash} — SUCCESS}}\n"
        f"*Steps*: {total}/{total}\n"
        f"*Branch*: `{branch_name}`\n"
        f"{{panel}}"
    )


def format_failure_comment(session_hash: str, state: PipelineState,
                            failed_step: str, branch_name: str = "") -> str:
    """Format a failure comment (without triage — triage agent handles its own comment)."""
    steps = state.step_names_ordered()
    passed = sum(1 for n in steps if state.steps[n].status == StepStatus.COMPLETE)
    total = len(steps)
    error = state.steps.get(failed_step, None)
    error_msg = error.last_error if error else "unknown"

    return (
        f"[forge-session:{session_hash}]\n\n"
        f"{{panel:title=Forge Session {session_hash} — FAILED}}\n"
        f"*Steps*: {passed}/{total}\n"
        f"*Failed at*: {failed_step}\n"
        f"*Error*: {error_msg}\n"
        f"*Branch*: `{branch_name}`\n"
        f"{{panel}}"
    )

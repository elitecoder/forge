# Copyright 2026. Resume advisor — deterministic skip logic for cross-run continuity.

import json
import re
from pathlib import Path


# Steps that produce durable artifacts ON THE BRANCH (git commits) and can be skipped.
# Everything else must always run: ephemeral steps (app_under_test), session-specific
# outputs (report, create_pr), and validation steps that must verify current state.
_SKIPPABLE_STEPS = {"code"}

# Classification → which step (if any) it blames.
# environment_fault blames nobody. code_fault/plan_fault blame code. review_fault blames code_review.
_CLASSIFICATION_BLAME: dict[str, str] = {
    "code_fault": "code",
    "plan_fault": "code",
    "review_fault": "code_review",
}


def _find_executor_dir(session_dir: str) -> Path | None:
    """Locate the prior executor session directory."""
    prior_dir = Path(session_dir) / "prior-executor"
    if not prior_dir.is_dir():
        return None
    subdirs = [d for d in prior_dir.iterdir() if d.is_dir() and d.name.startswith("executor-")]
    return subdirs[0] if subdirs else (prior_dir if (prior_dir / "agent-state.json").is_file() else None)


def _load_prior_steps(executor_dir: Path) -> dict:
    """Load step statuses from the prior session's agent-state.json."""
    state_path = executor_dir / "agent-state.json"
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text())
        return state.get("steps", {})
    except (json.JSONDecodeError, KeyError):
        return {}


def _extract_triage_blame(executor_dir: Path, prior_steps: dict) -> set[str]:
    """Read triage reports from *failed* steps and return the set of blamed step names."""
    blamed: set[str] = set()
    pattern = re.compile(r"\*\*Classification\*\*:\s*\*\*(\w+)\*\*", re.IGNORECASE)

    for f in executor_dir.glob("triage-*.md"):
        # triage-visual_test.md → visual_test
        triaged_step = f.stem.removeprefix("triage-")

        # Only consider triage from steps whose final status is "failed".
        # Triage from steps that ultimately completed is noise.
        prior = prior_steps.get(triaged_step, {})
        if prior.get("status") != "failed":
            continue

        text = f.read_text(errors="replace")[:3000]
        match = pattern.search(text)
        if match:
            classification = match.group(1).lower()
            blamed_step = _CLASSIFICATION_BLAME.get(classification)
            if blamed_step:
                blamed.add(blamed_step)

    return blamed


def advise(session_dir: str, step_names: list[str]) -> list[str]:
    """Determine which steps to skip based on prior session. Returns step names to skip."""
    executor_dir = _find_executor_dir(session_dir)
    if not executor_dir:
        return []

    prior_steps = _load_prior_steps(executor_dir)
    if not prior_steps:
        return []

    blamed = _extract_triage_blame(executor_dir, prior_steps)

    skip = []
    for name in step_names:
        prior = prior_steps.get(name, {})
        prior_status = prior.get("status", "")

        # Only skip steps that produce durable branch artifacts
        if name not in _SKIPPABLE_STEPS:
            continue

        # Only skip steps that completed in the prior session
        if prior_status != "complete":
            continue

        # Don't skip if triage from a failed step blames this one
        if name in blamed:
            continue

        skip.append(name)

    return skip

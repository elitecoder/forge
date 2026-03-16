# Copyright 2026. Session zip — create and extract session archives.

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Files to include from planner session
_PLANNER_OUTPUTS = [
    "codebase-brief.md",
    "spec-verdict", "complexity-verdict",
    "design-a.md", "design-b.md",
    "critique-a.md", "critique-b.md",
    "refined-a.md", "refined-b.md",
    "final-plan.md",
    "figma-context.md",
]

# Files to exclude from zip (internal state, not useful for re-runs)
_EXCLUDED_PATTERNS = {
    ".planner-state.json", ".planner-state.json.lock",
    ".pipeline-state.json", ".pipeline-state.json.lock",
    "planner-activity.log", "executor-activity.log",
    "events.jsonl",
}


def create_planner_zip(session_dir: Path, session_hash: str, ticket_key: str = "",
                        preset: str = "") -> Path:
    """Zip planner session outputs for upload. Returns path to created zip."""
    zip_name = f"session-{session_hash}.zip"
    zip_path = session_dir / zip_name
    archive_prefix = f"session-{session_hash}"

    meta = {
        "session_hash": session_hash,
        "ticket_key": ticket_key,
        "preset": preset,
        "subsystem": "planner",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{archive_prefix}/meta.json", json.dumps(meta, indent=2))

        # Include prompt files
        prompts_dir = session_dir / "prompts"
        if prompts_dir.is_dir():
            for p in prompts_dir.rglob("*"):
                if p.is_file():
                    arcname = f"{archive_prefix}/prompts/{p.relative_to(prompts_dir)}"
                    zf.write(p, arcname)

        # Include phase output files
        for filename in _PLANNER_OUTPUTS:
            filepath = session_dir / filename
            if filepath.is_file():
                zf.write(filepath, f"{archive_prefix}/{filename}")

    return zip_path


_EXECUTOR_ZIP_EXCLUDED = {
    ".pipeline-state.json", ".pipeline-state.json.lock",
    "events.jsonl",
}

_EXECUTOR_ZIP_EXCLUDED_SUFFIXES = (".zip",)

_EXECUTOR_ZIP_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB per file (skip large screenshots)


def create_executor_zip(session_dir: Path, session_hash: str, ticket_key: str = "",
                         preset: str = "", branch_name: str = "",
                         step_outcomes: dict | None = None) -> Path:
    """Zip entire executor session directory for upload. Returns path to created zip."""
    zip_name = f"executor-{session_hash}.zip"
    zip_path = session_dir / zip_name
    archive_prefix = f"executor-{session_hash}"

    meta = {
        "session_hash": session_hash,
        "ticket_key": ticket_key,
        "preset": preset,
        "subsystem": "executor",
        "branch_name": branch_name,
        "step_outcomes": step_outcomes or {},
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{archive_prefix}/meta.json", json.dumps(meta, indent=2))

        for p in session_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.name in _EXECUTOR_ZIP_EXCLUDED:
                continue
            if p.suffix in _EXECUTOR_ZIP_EXCLUDED_SUFFIXES:
                continue
            if p.stat().st_size > _EXECUTOR_ZIP_MAX_FILE_SIZE:
                continue
            # Skip prior session directories (don't re-zip downloaded prior sessions)
            rel = p.relative_to(session_dir)
            if rel.parts and rel.parts[0] in ("prior", "prior-executor"):
                continue
            zf.write(p, f"{archive_prefix}/{rel}")

    return zip_path

# Copyright 2026. Unified session management for planner and executor.

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


SESSIONS_BASE = Path(os.environ.get(
    "FORGE_SESSIONS",
    str(Path.home() / ".forge" / "sessions"),
))


def create_session(subsystem: str, slug: str = "") -> Path:
    base = SESSIONS_BASE / subsystem
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%SZ")
    name = f"{slug}_{ts}" if slug else ts
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_sessions(base: Path, state_filename: str = "") -> list[dict]:
    if not base.is_dir():
        return []
    sessions = []
    now = time.time()
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        age_days = (now - entry.stat().st_mtime) / 86400
        info: dict = {
            "name": entry.name,
            "path": str(entry),
            "age_days": round(age_days, 1),
        }
        if state_filename:
            info["active"] = (entry / state_filename).is_file()
        sessions.append(info)
    return sessions


def cleanup_sessions(base: Path, older_than_days: int = 30) -> list[str]:
    removed: list[str] = []
    if not base.is_dir():
        return removed
    cutoff = time.time() - (older_than_days * 86400)
    for entry in base.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry)
            removed.append(str(entry))
    if removed:
        _compact_registry(SESSIONS_BASE)
    return removed


def _compact_registry(sessions_base: Path) -> None:
    registry = sessions_base / "registry.jsonl"
    if not registry.is_file():
        return
    try:
        lines = registry.read_text().splitlines()
        kept = []
        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            sd = ev.get("session", "")
            if sd and os.path.isdir(sd):
                kept.append(line)
        registry.write_text("\n".join(kept) + "\n" if kept else "")
    except OSError:
        pass


def _sanitize_slug(raw: str) -> str:
    slug = raw.strip().split("\n")[0].strip()
    slug = slug.strip("`\"'")
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug[:50]


def generate_slug(text: str, fallback: str = "session",
                  agent_command: str = "claude") -> str:
    """Generate a short descriptive kebab-case slug from text using an LLM."""
    prompt = (
        "Summarize the following text in 2-4 words as a kebab-case slug. "
        "Return ONLY the slug, nothing else. "
        "Examples: health-endpoint-api, fix-login-bug, add-dark-mode\n\n"
        f"{text[:500]}"
    )
    try:
        result = subprocess.run(
            [agent_command, "-p", "--model", "haiku", "--max-turns", "1"],
            input=prompt, capture_output=True, text=True, timeout=30,
        )
        slug = _sanitize_slug(result.stdout)
        if slug:
            return slug
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return fallback


def find_running_session(base: Path, state_filename: str) -> Path | None:
    """Find a session with a live driver process (driver_pid > 0 and process exists)."""
    if not base.is_dir():
        return None
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        state_file = d / state_filename
        if not state_file.is_file():
            continue
        try:
            data = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        pid = data.get("driver_pid", 0)
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
            return d
        except OSError:
            continue
    return None


def find_active_session(base: Path, state_filename: str, prefix: str = "") -> Path | None:
    if not base.is_dir():
        return None
    candidates = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        if prefix and not d.name.startswith(prefix + "_"):
            continue
        if (d / state_filename).is_file():
            candidates.append(d)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

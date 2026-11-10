# Copyright 2026. A stand-in `claude` CLI for tests that run real Forge steps.
#
# It resolves `--agent plugin:name` the way Claude Code does: the plugin must be
# passed with `--plugin-dir`, and `name` must match the `name:` frontmatter of a
# file in that plugin's agents/ directory. An unknown agent exits 1 with Claude
# Code's error text. A known agent runs $FAKE_CLAUDE_SCRIPT in the working
# directory and prints $FAKE_CLAUDE_STDOUT.

import json
import os
import subprocess
import sys
from pathlib import Path


def _frontmatter_name(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            return ""
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""


def available_agents(plugin_dirs: list[str]) -> list[str]:
    agents = []
    for d in plugin_dirs:
        manifest = Path(d) / ".claude-plugin" / "plugin.json"
        plugin = json.loads(manifest.read_text())["name"]
        for f in sorted((Path(d) / "agents").glob("*.md")):
            name = _frontmatter_name(f)
            if name:
                agents.append(f"{plugin}:{name}")
    return agents


def main(argv: list[str]) -> int:
    plugin_dirs: list[str] = []
    agent = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--plugin-dir":
            plugin_dirs.append(argv[i + 1])
            i += 2
        elif argv[i] == "--agent":
            agent = argv[i + 1]
            i += 2
        else:
            i += 1

    prompt = sys.stdin.read()
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")

    if agent and agent not in available_agents(plugin_dirs):
        names = ", ".join(["general-purpose", *available_agents(plugin_dirs)])
        print(f"--agent '{agent}' not found. Available agents: {names}", file=sys.stderr)
        return 1

    script = os.environ.get("FAKE_CLAUDE_SCRIPT", "")
    if script:
        prompt_file = Path(os.environ["FAKE_CLAUDE_PROMPT"])
        prompt_file.write_text(prompt)
        subprocess.run(["sh", "-c", script], check=True)

    sys.stdout.write(os.environ.get("FAKE_CLAUDE_STDOUT", ""))
    return 0


def install(tmp_path: Path, monkeypatch, script: str = "", stdout: str = "") -> Path:
    """Put a fake `claude` first on PATH. Returns the invocation log path.

    `script` is a shell command run for each known agent. The prompt is in the
    file named by $FAKE_CLAUDE_PROMPT.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "claude"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" "$@"\n')
    exe.chmod(0o755)
    log = tmp_path / "fake-claude.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", script)
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", stdout)
    monkeypatch.setenv("FAKE_CLAUDE_PROMPT", str(tmp_path / "fake-claude-prompt.txt"))
    return log


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

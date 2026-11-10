# Copyright 2026. Built-in presets find their skill files from any project directory.

import subprocess
from pathlib import Path

import pytest

from forge.executor.engine.registry import load_preset
from forge.executor.engine.runner import generate_ai_fix_prompt, generate_ai_prompt
from tests.executor.helpers import make_state

PRESETS = Path(__file__).resolve().parents[2] / "src" / "forge" / "presets"


@pytest.fixture
def project(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("preset_name", ["npm-ts", "python-uv"])
@pytest.mark.parametrize("step_name", ["code_review", "create_pr"])
def test_skill_is_inlined_into_prompt(project, preset_name, step_name):
    preset = load_preset(PRESETS / preset_name)
    step = preset.steps[step_name]
    state = make_state(steps=[step_name], session_dir=str(project / "session"))
    skill = (PRESETS / preset_name / step.skill).read_text()

    assert skill in generate_ai_prompt(step, state, preset)


@pytest.mark.parametrize("preset_name", ["npm-ts", "python-uv"])
def test_skill_is_inlined_into_fix_prompt(project, preset_name):
    preset = load_preset(PRESETS / preset_name)
    step = preset.steps["code_review"]
    state = make_state(steps=["code_review"], session_dir=str(project / "session"))
    skill = (PRESETS / preset_name / step.skill).read_text()

    assert skill in generate_ai_fix_prompt(step, state, preset)


def test_absolute_and_preset_dir_skill_paths_still_resolve(project, tmp_path):
    preset = load_preset(PRESETS / "python-uv")
    step = preset.steps["code_review"]
    state = make_state(steps=["code_review"], session_dir=str(project / "session"))
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("absolute skill body")

    step.skill = str(elsewhere)
    assert "absolute skill body" in generate_ai_prompt(step, state, preset)

    step.skill = "${PRESET_DIR}/skills/code-review.md"
    assert (PRESETS / "python-uv/skills/code-review.md").read_text() in generate_ai_prompt(
        step, state, preset)

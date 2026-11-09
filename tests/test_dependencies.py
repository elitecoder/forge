# Copyright 2026. The locked dependencies install from prebuilt wheels on every supported Python.

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("python", ["3.12", "3.15"])
def test_locked_dependencies_install_without_building(python, tmp_path, monkeypatch):
    if not shutil.which("uv"):
        pytest.skip("uv is not installed")
    if subprocess.run(["uv", "python", "find", python], capture_output=True).returncode:
        pytest.skip(f"Python {python} is not installed")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "venv"))

    result = subprocess.run(
        ["uv", "sync", "--frozen", "--extra", "dev", "--no-install-project",
         "--no-build", "--python", python],
        cwd=ROOT, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr

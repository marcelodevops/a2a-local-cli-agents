import os
import subprocess
import sys

import pytest


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("A2A_LOCAL_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "a2a_local", *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )


def test_demo_uses_real_protocol_without_installed_agent_clis() -> None:
    result = cli("run")
    assert result.returncode == 0, result.stderr
    assert "SCRIPTED demo" in result.stdout
    assert "antigravity | A2A task" in result.stdout
    assert "qoder | A2A task" in result.stdout
    assert "copilot | A2A task" in result.stdout
    assert "All local A2A servers stopped" in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("run", "--topic", " "),
        ("run", "--timeout", "0"),
        ("run", "--timeout", "nan"),
        ("serve", "--agent", "qoder"),
    ],
)
def test_invalid_arguments_are_reported(arguments: tuple[str, ...]) -> None:
    result = cli(*arguments)
    assert result.returncode == 2
    assert "error:" in result.stderr

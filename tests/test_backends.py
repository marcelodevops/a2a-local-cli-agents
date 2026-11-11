import asyncio
import os
import sys
from pathlib import Path

import pytest

from a2a_local.backends import MAX_OUTPUT, BackendError, execute


def test_subprocess_receives_prompt_as_stdin() -> None:
    result = asyncio.run(
        execute(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            "hello; $(not a shell)",
            timeout=5,
        )
    )
    assert result == "HELLO; $(NOT A SHELL)"


def test_nonzero_exit_is_not_a_success() -> None:
    with pytest.raises(BackendError, match="exit code 7"):
        asyncio.run(
            execute(
                [sys.executable, "-c", "import sys; sys.exit(7)"], "task", timeout=5
            )
        )


def test_empty_response_is_an_error() -> None:
    with pytest.raises(BackendError, match="empty"):
        asyncio.run(execute([sys.executable, "-c", "pass"], "task", timeout=5))


def test_timeout_stops_process() -> None:
    with pytest.raises(BackendError, match="timed out"):
        asyncio.run(
            execute(
                [sys.executable, "-c", "import time; time.sleep(20)"],
                "task",
                timeout=0.1,
            )
        )


def test_output_limit_is_enforced() -> None:
    with pytest.raises(BackendError, match="output exceeded"):
        asyncio.run(
            execute(
                [sys.executable, "-c", f"print('x' * {MAX_OUTPUT + 1})"],
                "",
                timeout=5,
            )
        )


def test_cancellation_reaps_the_actual_process(tmp_path: Path) -> None:
    async def scenario() -> None:
        pid_file = tmp_path / "child.pid"
        command = (
            "import os, sys, time; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(20)"
        )
        task = asyncio.create_task(
            execute([sys.executable, "-c", command, str(pid_file)], "", timeout=10)
        )
        try:
            async with asyncio.timeout(3):
                while not pid_file.exists():
                    await asyncio.sleep(0.01)
            pid = int(pid_file.read_text())
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(scenario())

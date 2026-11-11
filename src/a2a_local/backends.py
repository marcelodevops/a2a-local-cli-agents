import asyncio
import os
import shutil
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Protocol

AGENTS = ("antigravity", "qoder", "copilot")
EXECUTABLES = {"antigravity": "agy", "qoder": "qodercli", "copilot": "copilot"}
MAX_PROMPT = 48_000
MAX_OUTPUT = 128_000


class BackendError(RuntimeError):
    """A CLI failed, exceeded a limit, or returned no usable text."""


class Backend(Protocol):
    async def run(self, prompt: str) -> str: ...


async def _read_limited(stream: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(4096):
        size += len(chunk)
        if size > MAX_OUTPUT:
            raise BackendError(f"CLI output exceeded {MAX_OUTPUT} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _communicate(
    process: asyncio.subprocess.Process, prompt: str
) -> tuple[bytes, bytes]:
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    readers = [
        asyncio.create_task(_read_limited(process.stdout)),
        asyncio.create_task(_read_limited(process.stderr)),
    ]
    try:
        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # Like subprocess.communicate(), tolerate a child closing stdin early.
            pass
        finally:
            process.stdin.close()
        stdout, stderr = await asyncio.gather(*readers)
        await process.wait()
        return stdout, stderr
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def _stop(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def execute(argv: Sequence[str], prompt: str, *, timeout: float) -> str:
    if os.name != "posix":
        raise BackendError("This example requires macOS or Linux process groups")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if len(prompt.encode()) > MAX_PROMPT:
        raise BackendError(f"Prompt exceeded {MAX_PROMPT} bytes")
    with TemporaryDirectory(prefix="a2a-cli-") as directory:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise BackendError(f"Could not start {argv[0]}: {error}") from error
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await _communicate(process, prompt)
        except TimeoutError as error:
            raise BackendError(f"{argv[0]} timed out after {timeout:g}s") from error
        finally:
            await _stop(process)
        if process.returncode:
            # Do not expose raw CLI stderr: it can contain account/config details.
            raise BackendError(
                f"{argv[0]} exited with exit code {process.returncode} "
                f"({len(stderr)} stderr bytes). Check its login/configuration directly."
            )
        text = stdout.decode(errors="replace").strip()
        if not text:
            raise BackendError(f"{argv[0]} returned an empty response")
        return text


@dataclass(frozen=True)
class CLIBackend:
    agent: str
    timeout: float = 120

    async def run(self, prompt: str) -> str:
        if len(prompt.encode()) > MAX_PROMPT:
            raise BackendError(f"Prompt exceeded {MAX_PROMPT} bytes")
        executable = shutil.which(EXECUTABLES[self.agent])
        if executable is None:
            raise BackendError(f"Missing executable: {EXECUTABLES[self.agent]}")
        if self.agent == "antigravity":
            argv = [
                executable,
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--output-format",
                "text",
                "--print",
                prompt,
            ]
        elif self.agent == "qoder":
            argv = [
                executable,
                "--tools",
                "",
                "--permission-mode",
                "dont_ask",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--no-session-persistence",
                "--output-format",
                "text",
                "--print",
                prompt,
            ]
        else:
            argv = [
                executable,
                "--silent",
                "--stream",
                "off",
                "--no-ask-user",
                "--no-custom-instructions",
                "--disable-builtin-mcps",
                "--deny-tool=shell",
                "--deny-tool=write",
                "--deny-tool=read",
                "--deny-tool=memory",
                "--prompt",
                prompt,
                "--available-tools",
            ]
        return await execute(argv, "", timeout=self.timeout)


@dataclass(frozen=True)
class ScriptedBackend:
    agent: str

    async def run(self, prompt: str) -> str:
        responses = {
            "antigravity": (
                "SCRIPTED proposal: hold a 30-minute team learning session; "
                "share a short agenda and ask everyone for one question."
            ),
            "qoder": (
                "SCRIPTED critique: the proposal needs an owner and an accessible "
                "way for absent teammates to contribute asynchronously."
            ),
            "copilot": (
                "SCRIPTED synthesis: assign a facilitator, publish the agenda, "
                "hold the 30-minute session, and collect asynchronous questions "
                "and feedback in a shared document."
            ),
        }
        return responses[self.agent]

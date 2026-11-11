# A2A local CLI agents

Three local agents exchange tasks and text artifacts over the official
[Agent2Agent protocol](https://github.com/a2aproject/A2A):
**Antigravity proposes, Qoder critiques, and GitHub Copilot synthesizes.**
The default mode is a labeled scripted demonstration. Live mode invokes the
actual installed CLIs, not three calls to a substitute model API.

```text
                         Local A2A coordinator
                         (Python, no fourth LLM)
                                   |
                 Agent Card discovery + A2A JSON-RPC
                                   |
           +-----------------------+-----------------------+
           |                       |                       |
   Antigravity endpoint      Qoder endpoint         Copilot endpoint
      127.0.0.1:port          127.0.0.1:port         127.0.0.1:port
           |                       |                       |
       agy --print           qodercli --print       copilot --prompt
           |                       |                       |
        proposal -------------> critique -------------> synthesis
```

Three HTTP servers run in one Python process on separate ephemeral loopback
ports. Live mode launches a new CLI process for each agent turn. The coordinator
passes earlier text artifacts to the next agent; it does not share the CLIs'
private memories or resume existing conversations. The Copilot participant is
a new Copilot CLI process, not the terminal conversation that created this repo.

## Quick start: no models or API keys

Requires **Python 3.11+**, [uv](https://docs.astral.sh/uv/), and macOS or Linux.

```bash
git clone https://github.com/marcelodevops/a2a-local-cli-agents.git
cd a2a-local-cli-agents
uv sync --frozen
uv run a2a-local run
```

The demo still performs real localhost HTTP discovery and A2A task/artifact
exchange. Only its three backend answers are scripted; it does not generate
answers for arbitrary topics. Dependency installation initially needs network
access. No agent CLI is required for the scripted demo.

## Run the actual three agents

Install and authenticate these CLIs first:

| Agent | Executable on PATH | Role |
| --- | --- | --- |
| Antigravity | `agy` | Propose an answer |
| Qoder | `qodercli` | Critique the proposal |
| GitHub Copilot | `copilot` | Synthesize both contributions |

Use each CLI's normal sign-in flow (`qodercli login`, `copilot login`, and
Antigravity's interactive sign-in). The example reuses their existing
authentication and default model selection; it does not store API keys.
Recent versions supporting the flags in `backends.py` are required.

```bash
# Checks executable availability only; does not test authentication or call models.
uv run a2a-local doctor

# Starts three servers, performs the discussion, then stops the servers.
uv run a2a-local run --live \
  --topic "Suggest a 30-minute team session about clear technical writing." \
  --timeout 120
```

Each contribution prints its originating agent and real A2A task ID. A failure
stops the workflow with a nonzero exit status; it never falls back to scripted
answers. `--timeout` bounds **each CLI invocation**, not the entire discussion.
Authentication and model availability are checked by the actual CLI invocation.

**Live calls send your topic and earlier contributions to the providers used
by each CLI and may consume subscription quotas or incur charges. Use only
non-sensitive topics.** Their normal account configuration and session/cache
behavior still apply.

If a stale `GITHUB_TOKEN` or `GH_TOKEN` overrides valid stored Copilot login,
clear that override for the command, rather than committing a token:

```bash
env -u GITHUB_TOKEN -u GH_TOKEN uv run a2a-local run --live
```

## Protocol and lifecycle

The application uses `a2a-sdk` **1.3.x** and **A2A 1.0 JSON-RPC over HTTP**.
No custom "agent communication" JSON format replaces the protocol.

1. The coordinator fetches each authenticated
   `/.well-known/agent-card.json` and checks its identity and advertised URL.
2. The SDK sends a text message to the selected agent's endpoint.
3. An SDK `AgentExecutor` creates a task and publishes the working state.
4. The backend invokes its CLI in a disposable working directory.
5. The executor publishes the answer as a text artifact and completes the task.
6. The coordinator includes that artifact in the next agent's prompt.

The agents are **sequential**, not concurrent speakers. Servers can accept A2A
task-cancellation requests; cancellation and timeouts terminate the owned CLI
process group, including child processes. In-memory task history lasts only
for that server's lifetime. This is not a durable queue or production service.

Default CLI timeout: 120 seconds. Input limit: 48,000 UTF-8 bytes per task.
Output limit: 128,000 bytes for each captured stdout/stderr stream. Oversized
artifacts or accumulated prompts fail explicitly, rather than being silently
truncated. The console reports an A2A failed task for known backend failures.
Raw CLI stderr is not sent to other agents because it may contain account or
configuration details; check the affected CLI directly when diagnosing login
errors.

### Local access and permissions

- Servers bind only to `127.0.0.1`; there is no remote exposure option.
- All routes, including Agent Cards, require a bearer token. `run` generates a
  random per-run token in memory and does not print it.
- The coordinator refuses non-loopback URLs and unexpected advertised endpoints.
  HTTP proxy environment settings are disabled for its localhost traffic.
- CLIs run with argument arrays, never interpolated shell commands, and use
  disposable working directories rather than this repository.
- No permission-bypass flags are used. Qoder has built-in tools disabled and
  an empty strict MCP configuration. Copilot uses an empty tool allowlist and
  explicit shell/read/write/memory deny rules. Antigravity uses its native
  plan and sandbox modes, with slash-command expansion disabled.

**This is not OS-level security isolation.** Native CLI settings, extensions,
hooks, authentication, and access controls still matter. Temporary working
directories and prompt instructions do not guarantee isolation from your home
directory or malicious agent output. Use trusted discussion topics; a coding
workflow with shared repository edits is deliberately out of scope.

## Inspect an individual A2A agent

`serve` runs one endpoint until interrupted. It is scripted unless `--live`
is supplied. Set your own token of at least 16 characters, and do not commit it.

```bash
export A2A_LOCAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run a2a-local serve --agent antigravity --port 9101 --live
```

In another terminal, with the same token exported:

```bash
curl --fail --silent \
  -H "Authorization: Bearer $A2A_LOCAL_TOKEN" \
  http://127.0.0.1:9101/.well-known/agent-card.json
```

Send a task through the SDK-backed client:

```bash
uv run python - <<'PY'
import asyncio
import os
from a2a_local.coordinator import ask_agent

turn = asyncio.run(ask_agent(
    "antigravity",
    "http://127.0.0.1:9101",
    "Suggest one short team learning exercise. Do not use tools.",
    token=os.environ["A2A_LOCAL_TOKEN"],
    timeout=120,
))
print(turn.task_id, turn.text)
PY
```

Use `--agent qoder --port 9102` or `--agent copilot --port 9103` to inspect
the other agents. CLI authentication is separate from the local A2A bearer token.

## Development

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

Tests and CI never invoke paid models. Tests cover actual loopback HTTP
discovery and artifact exchange, shared discussion context, failed tasks,
bearer authentication, Agent Card identity checks, timeouts, output limits,
process cancellation/reaping, A2A cancellation, shutdown, and CLI validation.

A real live smoke test has also completed successfully on the development Mac:
Antigravity proposed a technical-writing workshop, Qoder identified gaps, and
Copilot incorporated both into the final agenda. Authentication and provider
availability can change; CI intentionally does not repeat that paid test.

| File | Responsibility |
| --- | --- |
| `backends.py` | Scripted responses and bounded CLI subprocess adapters |
| `server.py` | Authenticated Agent Cards, A2A executor and task lifecycle |
| `coordinator.py` | SDK discovery, sequential discussion and artifact extraction |
| `runtime.py` | Three-server startup and shutdown |
| `__main__.py` | `run`, `serve`, and `doctor` commands |

This repo uses the **graphify skill**. With the existing Python Graphify CLI:

```bash
graphify update .
graphify query "How does a task reach the CLI backend?"
```

Generated graph artifacts stay local and are ignored by Git.

## Upstream references

- [A2A protocol](https://github.com/a2aproject/A2A)
- [Official Python SDK](https://github.com/a2aproject/a2a-python)
- [Official Hello World example](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/helloworld)

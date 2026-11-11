# Contributor guidance

Use the graphify skill when working in this repository. When the local
`graphify-out/graph.json` exists, start codebase questions with
`graphify query "<question>"`. Run `graphify update .` after code changes.
Use the existing Python Graphify installation; never replace or reinstall it.

Keep the example focused on a three-agent text discussion over the official
A2A SDK. Do not replace CLI participants with direct model API calls.
Default mode and automated tests must be scripted and must not invoke models.
Live tests require working CLI authentication and may incur provider charges.

Bind only to literal loopback, preserve bearer authentication and bounded
subprocess lifetimes, and never add permission-bypass flags. Do not claim that
temporary directories are OS sandboxes. Never commit tokens, CLI account data,
or real user conversations.

Before committing:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

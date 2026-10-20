# Contributing

Thanks for helping. Bug reports, new agent adapters and fixes are all welcome.

## Setting up

```bash
git clone https://github.com/Keerthirajan58/bugbout
cd bugbout
uv venv && uv pip install -e ".[dev]"
```

## Checks

Every change must pass the same checks CI runs:

```bash
ruff check .
ruff format --check .
mypy
pytest
```

The tests build small git repositories in temporary directories and drive scripted
agents through them, so they need no network, API keys or agent CLIs.

## Layout

| Package | What it does |
|---|---|
| `bugbout.workspace` | Git history, history-free checkouts, patches and test environments. |
| `bugbout.tasks` | Mining tasks, issue text, and the ground truth that scores patches. |
| `bugbout.sandbox` | Seatbelt and bubblewrap sandboxes. |
| `bugbout.agents` | One adapter per agent CLI, and the registry that names them. |
| `bugbout.arena` | Prompts, the race, cross-examination and the judge. |
| `bugbout.report` | Turning results into numbers, and rendering them. |
| `bugbout.bout` | The pipeline that runs every stage; `bugbout.cli` wraps it. |

## Adding an agent

An adapter is a subclass of `CodingAgent` with an `invocation` method; see
[Agents](docs/agents.md#writing-an-adapter-in-python). A built-in adapter should start
its agent from a clean configuration, keep web tools off, and come with tests of the
command line it builds - see `tests/agents/test_adapters.py`.

## Pull requests

Keep each pull request to one change, with tests for what it adds or fixes, and describe
what it changes and why.

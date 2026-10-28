[![PyPI version](https://shields.io)](https://pypi.org/project/bugbout/)

# bugbout

**Which coding agent actually fixes bugs in *your* codebase?**

Public benchmarks tell you how coding agents do on someone else's code. bugbout tells
you how they do on yours. It replays real bugs from your project's own history, lets
agents race to fix them, and scores every patch with the tests your maintainers wrote
alongside the real fix - tests no agent ever gets to see.

## Why

- **Your code, not a leaderboard's.** An agent that tops a benchmark can still stumble
  on your conventions, your framework and your test suite. Measure it where you will
  use it.
- **Scored by your own tests.** Every task is a fix your team already shipped with a
  new test, and that test decides whether an agent's patch really fixes the bug.
- **Bring any agent.** Codex, Claude Code, Gemini CLI and mini-swe-agent work out of
  the box, and any other agent with a command line takes a few lines of config - so
  you can also compare models, including local ones.
- **Cross-examination.** Agents' tests are run against each other's patches, so you
  also learn whether an "agent jury" could pick, or gate, a patch on its own before you
  trust one in your workflow.
- **Safe to run.** Agents work in history-free checkouts, inside an OS sandbox, with
  web access switched off.

## Quickstart

You need macOS or Linux (on Windows, use WSL2), Python 3.12+, git,
[uv](https://docs.astral.sh/uv/), and at least one coding agent CLI installed and logged in.

```bash
pip install bugbout   # or: uv tool install bugbout

cd your-project
bugbout init      # writes bugbout.toml from your layout and the agents you have
bugbout doctor    # checks everything before any agent spends quota
bugbout run       # mines tasks, races the agents, judges and scores
bugbout report --format markdown
```

A bout is resumable: stop it at any point and run it again to carry on. Add an agent
later and only that agent runs.

## How it works

1. **Mine.** Fixes in your git history that name an issue and add a test become tasks.
2. **Ground truth.** The maintainers' tests run before and after their fix; the tests
   that flip from failing to passing define what "fixed" means.
3. **Race.** Each agent gets the issue and a checkout of the code before the fix - no
   git history, none of the fix's tests - and works inside a sandbox.
4. **Cross-examine.** Every test the agents wrote runs against every patch.
5. **Judge and score.** A policy picks one patch per bug from the executed evidence,
   then the hidden tests score every patch and every pick.

More in [How it works](docs/how-it-works.md).

## Configuration

`bugbout init` writes a commented `bugbout.toml`. A minimal one looks like this:

```toml
[project]
source = "src/mypkg"
tests = "tests"

[[contestants]]
agent = "codex"

[[contestants]]
agent = "claude-code"
model = "sonnet"
```

Every setting is described in [Configuration](docs/configuration.md); the built-in
agents, sandboxing and adding your own agent are covered in [Agents](docs/agents.md).

## From Python

```python
from bugbout import Bout, render_markdown

report = Bout.load(".").run()
print(render_markdown(report))
```

## License

[MIT](LICENSE)

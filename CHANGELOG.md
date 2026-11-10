# Changelog

## 0.1.1 - 2026-11-09

- An Agent Skill (`skills/bugbout`) teaches Claude Code, Codex, Cursor, Copilot, Gemini CLI
  and other agents to set up a bout, run it and explain the report. In Claude Code, install
  it with `/plugin install bugbout --marketplace Keerthirajan58/bugbout`.
- Package metadata now names the author in full, with a new contact email.
- The README shows badges, the supported platforms (macOS and Linux, WSL2 on Windows) and
  installs from PyPI.

## 0.1.0 - 2026-10-20

First release.

- Mine tasks from a project's own fixed bugs, with ground truth from the maintainers'
  tests.
- Race Codex, Claude Code, Gemini CLI, mini-swe-agent, or any agent with a command
  line, in sandboxed, history-free checkouts.
- Cross-examine every patch with every agent's tests, and pick one per bug under a
  judge policy.
- Report in the terminal, Markdown and JSON.
- `bugbout init`, `doctor`, `tasks`, `run` and `report`; and the same pipeline from
  Python through `Bout`.

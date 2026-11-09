---
name: bugbout
description: Runs bugbout, an open source CLI that tests coding agents (Codex, Claude Code, Gemini CLI, mini-swe-agent or any command line agent) on bugs a Python repository has already fixed, and scores every patch with the tests the maintainers wrote for the real fix. Use when the user wants to compare or benchmark coding agents or models on their own codebase, find out which agent fixes bugs best in a repo, set up or configure bugbout or bugbout.toml, run a bout, read or explain a bugbout report, or fix a bugbout error.
license: MIT
compatibility: Needs Python 3.12+, git, uv, macOS or Linux (Windows through WSL2), the bugbout CLI from PyPI, and at least one coding agent CLI installed and logged in. Uses the network for GitHub issue text and for the agents' own model APIs.
metadata:
  author: Keerthirajan Senthilkumar
  version: "0.1.1"
  repository: "https://github.com/Keerthirajan58/bugbout"
---

# bugbout

bugbout replays bugs a project has already fixed. It mines the git history for fixes that
came with a new test, gives each coding agent the issue text and the code from just before
the fix (no git history, no web access, inside a sandbox), and scores every patch with the
maintainers' tests, which the agents never see. The result is a report of which agent
actually fixed which bug in this codebase.

Work through the steps below in order. Run every command from the repository root unless
the user says otherwise.

## 1. Check bugbout is installed

```bash
bugbout version
```

If the command is missing, ask the user before installing anything, then install it with
`uv tool install bugbout` (or `pip install bugbout`). bugbout needs `git` and `uv` on the
PATH, and works on Python projects tested with pytest whose issues are on GitHub.

## 2. Set up the project

```bash
bugbout init
```

This writes `bugbout.toml`. It detects the package folder, the test folder and test
dependencies, and adds every installed agent (codex, claude-code, gemini-cli) as a contestant.

- If it says it could not find the package, rerun with the paths:
  `bugbout init --source src/yourpkg --tests tests`.
- If `bugbout.toml` already exists, it refuses. Never pass `--force` unless the user asks,
  because that overwrites their configuration.

Show the user the resulting `[project]`, `[tasks]` and `[[contestants]]` sections and
confirm them. Fewer tasks (`[tasks] limit`) means a cheaper first run; 3 to 5 is a good start.

Then check the setup:

```bash
bugbout doctor
```

Each line is a check. `✓` passed, `!` is a warning the run can continue with, `✗` must be
fixed first. The command exits with status 1 if any required check fails.

## 3. Find the tasks (free: no agent runs)

```bash
bugbout tasks
```

This mines the history and runs the maintainers' tests to prove each bug. The table lists
each task with its issue number and title. The Proof column shows how many tests the real
fix turned from failing to passing; `none` means the task cannot be scored and is skipped.
Results are cached in `.bugbout/`; `bugbout tasks --refresh` mines again.

If no tasks are found, the history may have no fixes that reference an issue (`#123`) and
add a test. Try a wider window or broader rules in `bugbout.toml`, for example removing
`[tasks] since`, raising `limit`, or raising `max-source-files`, and check that `source` and
`tests` point at the right folders.

## 4. Run the bout (spends the user's agent quota)

Before running, tell the user plainly: every contestant will attempt every task, each run
can take several minutes, and each run uses that agent's subscription or API quota. Get an
explicit yes, and confirm which agents and how many tasks.

```bash
bugbout run
```

- It can take a long time. Run it with a long timeout or in the background, and check on it.
- It is resumable. Finished attempts are saved as they complete, so if it stops for any
  reason, running `bugbout run` again carries on without repeating finished work. Adding a
  contestant later runs only the new one.
- An attempt that hit a usage limit is marked `quota` and is left out of the scores. Once the
  limit resets, `bugbout run --retry-failed` runs those attempts again.
- When it finishes it writes `.bugbout/report.md` and `.bugbout/report.json` and prints a
  summary table.

## 5. Read the results

```bash
bugbout report                     # tables in the terminal
bugbout report --format markdown   # the full report
bugbout report --format json       # machine-readable
```

`bugbout report` never runs anything; it reads the saved results. How to explain them:

- **Resolved:** the patch passed every test the real fix turned green, and broke nothing that
  passed before. This is the headline score.
- **Broke a test:** it fixed the bug but broke something else.
- **Wrong fix:** it changed the code but the bug is still there.
- **No fix:** it left no change to the source, or a change that did not apply.
- **Judge:** a set of rules (not an AI) that runs every agent's tests against every other
  agent's patch and picks one patch per bug, or abstains. "Best single agent" and "any agent"
  give the comparison points.
- **Merge gate:** each patch judged alone, as if one agent had just said it was done.

Be honest about sample size. A handful of tasks is an anecdote, not a benchmark; say so
when the user draws conclusions, and suggest more tasks for a firmer answer.

## Configuration essentials

`bugbout.toml` keys use dashes. The ones users change most:

| Key | Meaning |
|---|---|
| `[project] source`, `tests` | Package folder and test folder |
| `[project] github` | `owner/name` for issue text when `origin` is not on GitHub |
| `[project] test-dependencies` | Extra packages the tests need, such as `hypothesis` |
| `[project] pytest-args` | Arguments every test run needs; the project's own `addopts` are ignored |
| `[project] interpreter` | An existing Python to run tests with, instead of building one with uv |
| `[tasks] limit`, `since`, `max-source-files` | How many past fixes, from when, how broad |
| `[run] timeout` | Seconds per agent per task (default 1800) |
| `[run] quotas` | Runs at once per quota, such as `{ codex = 2 }` (default 1) |
| `[run] sandbox` | `auto`, `seatbelt`, `bubblewrap` or `none` |
| `[judge] policy` | `majority`, `unanimous`, `strict` or `cross-vendor` |

A contestant needs `agent` (`codex`, `claude-code`, `gemini-cli`, `mini-swe-agent` or
`command`) and optionally `model` and `name`. `mini-swe-agent` needs a LiteLLM model name
such as `ollama/qwen3-coder:30b`. A `command` agent needs `name` and `command`, a list in which
`{prompt_file}`, `{prompt}`, `{workdir}`, `{logdir}` and `{model}` are filled in. An optional
`[[examiners]]` entry takes the same keys plus `assignment` (`reproduce` or `specify`).
The full reference: https://github.com/Keerthirajan58/bugbout/blob/main/docs/configuration.md

## Gotchas

- **No sandbox is available.** On Linux, install bubblewrap (`apt install bubblewrap`). On
  Ubuntu 24.04 and later, AppArmor may block it; the error says so. Only set
  `sandbox = "none"` if the user explicitly agrees to run agents' commands unconfined.
- **"cannot tell which GitHub repository holds the issues".** Set `github = "owner/name"`
  under `[project]`.
- **GitHub rate limit.** Without a token bugbout is limited to 60 requests an hour. Ask the
  user to set `GITHUB_TOKEN` or run `gh auth login`.
- **Test environment fails to build.** Check `uv` is installed, add missing packages to
  `test-dependencies`, or point `interpreter` at a Python that already has them.
- **An agent is reported as not installed.** Install and log in to that agent's CLI, or
  remove it from `bugbout.toml`.
- **Running inside Claude Code.** A `claude-code` contestant is safe to run from this
  session; bugbout starts it as a separate session.

## Rules

- Never run `bugbout run` without the user's explicit agreement; it spends their quota.
- Never edit files in `.bugbout/` or in the cache (`~/.cache/bugbout`, or `BUGBOUT_HOME`) by
  hand. To start over, ask the user before deleting `.bugbout/`.
- Do not commit `.bugbout/`; it ignores itself in git, and only the user decides otherwise.
- Do not change contestants, models or the judge policy without telling the user.

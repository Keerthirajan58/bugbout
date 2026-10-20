# Configuration

A bout is configured by `bugbout.toml`, usually at the root of your repository.
`bugbout init` writes one for you; every key below is optional except
`project.source`. Keys use dashes, as in `pyproject.toml`.

## `[project]` - the code under test

| Key | Default | Meaning |
|---|---|---|
| `source` | *required* | The package directory, relative to the repository root, e.g. `src/mypkg`. |
| `tests` | `"tests"` | The test directory. |
| `repository` | `"."` | The repository: a path relative to `bugbout.toml`, or a git URL to clone. |
| `github` | from `origin` | `owner/name` to read issues from, when `origin` is not on GitHub. |
| `python` | `"3.12"` | The Python version of the test environment. |
| `test-dependencies` | `["pytest"]` | What the tests need besides the project's own dependencies. |
| `pytest-args` | `[]` | Extra arguments for every test run. |
| `env` | `{}` | Extra environment variables for the tests, such as a database URL. |
| `install-project` | `true` | Install the project once to pull in its runtime dependencies. |
| `interpreter` | none | An existing Python to run tests with, instead of building an environment. |

bugbout builds one environment per project with uv. It holds the test dependencies and
the project's runtime dependencies but not the project itself: every checkout is put on
`PYTHONPATH`, so the code under test is always the tree being tested.

The project's own `addopts` are ignored, because they often need plugins the test
environment does not have (coverage, xdist). Put anything your tests really need in
`pytest-args`.

## `[tasks]` - which fixes become tasks

| Key | Default | Meaning |
|---|---|---|
| `since` | all history | Only consider changes after this date, e.g. `"2023-01-01"`. |
| `limit` | `20` | The most tasks to keep, newest first. |
| `max-source-files` | `2` | A fix that touches more source files than this is too broad to be one bug. |

## `[run]` - how agents run

| Key | Default | Meaning |
|---|---|---|
| `sandbox` | `"auto"` | `auto`, `seatbelt` (macOS), `bubblewrap` (Linux) or `none`. |
| `timeout` | `1800` | Seconds one agent may spend on one task. |
| `quotas` | `{}` | How many runs may share a quota at once, e.g. `{ codex = 2 }`. One by default. |

## `[judge]` - how a patch is picked

| Key | Default | Meaning |
|---|---|---|
| `policy` | `"majority"` | `majority`, `unanimous`, `strict` or `cross-vendor`; see [How it works](how-it-works.md#the-judge). |
| `run-suite` | `true` | Also run the repository's own tests against every patch, so breakage shows. Turn it off for very slow suites. |

## `[[contestants]]` - the agents that fix bugs

Each entry is one agent with one model.

| Key | Meaning |
|---|---|
| `agent` | The agent type: `codex`, `claude-code`, `gemini-cli`, `mini-swe-agent` or `command`. |
| `model` | The model to use. Leave it out to use the agent's own default (not allowed for `mini-swe-agent`). |
| `name` | How the agent appears in reports. Defaults to `agent/model`, or just `agent`. |
| `vendor` | Who made the model. Guessed from the model name; set it when the guess is wrong. |
| `quota` | The rate limit it shares with other agents. Defaults to the agent type. |

Any other key is passed to the agent's adapter; see [Agents](agents.md) for each one's
options. For example:

```toml
[[contestants]]
agent = "codex"
model = "gpt-5.1-codex"
reasoning-effort = "high"
```

## `[[examiners]]` - the agents that write tests

Examiners never fix anything. From the issue alone, they write tests that a correct fix
must pass, and those tests cross-examine every contestant's patch. An examiner takes
the same keys as a contestant, plus:

| Key | Default | Meaning |
|---|---|---|
| `assignment` | `"reproduce"` | `reproduce`: tests for exactly what the issue reports. `specify`: also the general rule behind it, and nearby behaviour a fix must keep. |

Examiners are optional. Without them, the contestants' own tests still cross-check each
other's patches.

## Environment variables

| Variable | Meaning |
|---|---|
| `BUGBOUT_HOME` | Where environments and checkouts are cached; `~/.cache/bugbout` by default. |
| `GITHUB_TOKEN` | Read issues with a token. Without one, bugbout uses the GitHub CLI's login, or anonymous access at 60 requests an hour. |

Agents read their own credentials as usual - for example `GEMINI_API_KEY` for Gemini
CLI, or whatever a LiteLLM model needs for mini-swe-agent.

## Where things are kept

Results live with your project, in `.bugbout/` beside `bugbout.toml`. That directory
ignores itself in git; delete its `.gitignore` if you want to commit results.

| Path | Holds |
|---|---|
| `tasks.json` | The mined tasks, and why other fixes were left out. |
| `oracles.json` | Each task's ground truth. |
| `attempts/` | One file per agent per task: how the run went, and its patch. |
| `matrices/` | Every test's outcome on every patch, per task. |
| `verdicts.json`, `resolutions.json` | The judge's picks, and every patch's score. |
| `report.md`, `report.json` | The report from the last `bugbout run`. |

Heavy, disposable state - test environments, checkouts and the agents' working copies
with their logs - lives in `BUGBOUT_HOME`, never in your repository.

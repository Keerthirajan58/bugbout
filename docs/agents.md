# Agents

bugbout reimplements no agent. Each adapter runs the same command line a developer
would, headlessly, and judges the agent only by what it leaves on disk. Two rules hold
for every adapter:

- **Nothing personal leaks in.** Your own agent configuration - plugins, memories, MCP
  servers, house rules - is left out wherever the agent allows it, so the comparison is
  between agents, not set-ups.
- **Nothing from the future leaks in.** The tasks are real bugs with public fixes, so web
  tools are off, commands run in a sandbox, and the checkout has no git history.

## Built-in agents

### `codex` - OpenAI's Codex CLI

Runs `codex exec` with your user config and rules ignored, an ephemeral session, web
search and every feature that reaches beyond the checkout switched off. Shell commands
run in Codex's own `workspace-write` sandbox: no network, writes only in the checkout.

| Option | Meaning |
|---|---|
| `reasoning-effort` | Passed through as `model_reasoning_effort`, e.g. `"high"`. |
| `local-provider` | `ollama` or `lmstudio`, to run an open model locally. |

### `claude-code` - Anthropic's Claude Code

Runs `claude --print` in safe mode, which leaves out CLAUDE.md, skills, plugins, hooks
and MCP servers. Edits are accepted only inside the checkout, shell commands run in
Claude Code's own sandbox, web tools are disallowed, and anything that would need a
person's approval is refused. `model` takes a full name or an alias such as `sonnet`.

| Option | Meaning |
|---|---|
| `effort` | Claude Code's effort level, e.g. `"high"`. |

### `gemini-cli` - Google's Gemini CLI

Runs `gemini --prompt` in auto-approve mode, with web tools excluded through workspace
settings. The whole process runs inside bugbout's sandbox: it can reach the Gemini API,
but write only in the checkout, its logs and `~/.gemini`.

### `mini-swe-agent` - any LiteLLM model

[mini-swe-agent](https://mini-swe-agent.com) is the SWE-bench team's minimal agent, and
the easiest way to race models rather than products - hosted or local. `model` is a
LiteLLM name such as `ollama/qwen3-coder:30b` and is required. Every shell command it
runs goes through bugbout's sandbox, with no network.

| Option | Meaning |
|---|---|
| `text-actions` | `true` for models whose server cannot parse tool calls reliably. |
| `step-limit` | The most steps per task; 75 by default. |
| `model-kwargs` | Extra model arguments, e.g. `{ temperature = "0" }`. |

For local models served by Ollama, give the server enough context for an agent, e.g.
`OLLAMA_CONTEXT_LENGTH=65536 ollama serve`.

### `command` - anything else with a command line

Any agent that can run unattended can join through configuration alone:

```toml
[[contestants]]
agent = "command"
name = "aider"
model = "gpt-5"
command = ["aider", "--yes-always", "--no-git", "--model", "{model}", "--message-file", "{prompt_file}"]
state-dirs = ["~/.aider"]
```

In `command`, these placeholders are filled in for each run:

| Placeholder | Becomes |
|---|---|
| `{prompt}` | The task prompt itself. |
| `{prompt_file}` | A file holding the prompt. |
| `{workdir}` | The checkout the agent works in (also its working directory). |
| `{logdir}` | A directory for the agent's own logs. |
| `{model}` | The configured model. |

Without `{prompt}` or `{prompt_file}`, the prompt arrives on standard input.

| Option | Meaning |
|---|---|
| `command` | The command line, as a list. Required. |
| `name` | Required, since there is no agent type to name it after. |
| `env` | Extra environment variables for the agent. |
| `state-dirs` | Directories outside the checkout the agent must write to, such as its own config. |
| `sandboxed` | `false` to run without bugbout's sandbox - only for agents that sandbox themselves. |

## Sandboxing

On macOS, bugbout uses seatbelt (`sandbox-exec`); on Linux, bubblewrap (`bwrap`, e.g.
`apt install bubblewrap`). Either way a sandboxed process can read anything, but write
only in the directories it was given and the system's temporary directories.

- An agent's **shell commands** get no network at all.
- An agent's **whole process**, where bugbout confines it (Gemini CLI, `command`
  agents), keeps the network so it can reach its model.
- The **tests** bugbout runs - including tests an agent wrote - get no network and may
  write only in their own checkout.

`sandbox = "none"` runs everything unconfined. `bugbout doctor` reports which sandbox
is in use, or why none can start. On Ubuntu 24.04 and later, AppArmor restricts the
unprivileged user namespaces bubblewrap needs; allow them for `bwrap` with an AppArmor
profile, or system-wide with `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`.

## Quotas and lanes

Agents are rate-limited by their plans, not by your machine, so a bout queues runs per
**quota**: runs sharing a quota take turns, and different quotas run in parallel. By
default an agent's quota is its type (all `codex` agents share a ChatGPT plan, say),
and one run per quota runs at a time. Raise that with `[run] quotas`, or move an agent
to its own lane with `quota = "..."`.

When an agent stops on a usage limit without producing anything, the attempt is marked
`quota`. `bugbout run --retry-failed` runs such attempts again once the limit resets;
finished attempts are never re-run.

## Writing an adapter in Python

An adapter says how to start the agent; bugbout does the rest - checkouts, timeouts,
logs, patches and scoring.

```python
from pathlib import Path

from bugbout import CodingAgent, Invocation, Session


class MyAgent(CodingAgent):
    harness = "my-agent"  # the default name prefix
    executable = "my-agent"  # what `bugbout doctor` looks for

    def invocation(self, session: Session) -> Invocation:
        argv = [self.command(), "--model", self.model, "--yes"]
        return Invocation(argv, stdin=session.prompt)

    def parse(self, logdir: Path) -> tuple[str, dict[str, int]]:
        # Optional: the agent's closing summary and token usage, from its logs.
        return (logdir / "stdout.log").read_text()[-2000:], {}
```

`Session` carries the prompt, the checkout (`workdir`), a log directory and the
sandbox; if the agent does not confine itself, wrap its command with
`session.sandbox.wrap(argv, Confinement(...))` (from `bugbout.sandbox`). Pass agents to a bout directly:

```python
from bugbout import Bout, Project

report = Bout(Project.load("."), contestants=[MyAgent("my-model")]).run()
```

To make an adapter available to `bugbout.toml`, publish it with an entry point:

```toml
[project.entry-points."bugbout.agents"]
my-agent = "my_package.agent:MyAgent"
```

and use it as `agent = "my-agent"`.

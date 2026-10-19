# Verdict

Verdict runs several AI coding agents on the same bug, has other agents write the
tests a correct fix must pass, and decides which patch is safe to merge by running
those tests - not by asking a model for its opinion.

## Why

Coding agents are fast, and they report success whether or not the fix is right.
Reviewing every agent-written patch by hand doesn't scale, and asking another model
to "review" it just adds a second opinion. Verdict replaces opinions with evidence:

- **Pick the best patch** when several agents attempt the same issue.
- **Gate a patch** before it merges: approve it, reject it, or flag that nothing
  independent has verified it.
- **See why** - every decision traces back to concrete tests and how they ran on
  each patch.

## How it works

1. **Race** - coding agents (Codex CLI, mini-swe-agent, Gemini CLI) each fix the
   issue in their own sandboxed checkout.
2. **Examine** - examiner agents write the tests a fix must pass, from the issue
   alone, without ever seeing a patch.
3. **Cross-examine** - every test runs against every patch and the unfixed code.
4. **Judge** - simple, explicit rules over the executed results approve, reject or
   pick a patch.

Agents run headlessly with clean configs, no web access and no git history to peek
at; runs are journaled, so an interrupted race resumes without redoing work.

## Getting started

Requires Python 3.12 and at least one coding-agent CLI.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,llm]"
npm install -g @openai/codex && codex login

verdict arena --help
```

A typical run over a suite of tasks:

```bash
verdict arena oracles --suite test
verdict arena race -e codex -e codex-mini --suite test
verdict arena examine -x codex-reproducer -x codex-specifier --suite test
verdict arena cross --suite test
verdict arena judge --suite test --policy cross-executed
```

The `dashboard/` folder is a static Next.js site for browsing every task, patch and
test.

## License

MIT

# Verdict

**Several coding agents fix the same real bug. Independent agents write the tests
that judge them. Executed tests - never a model's opinion - decide what may merge.**

Verdict is an orchestration harness and a measurement. It drives off-the-shelf
coding-agent CLIs (Codex, mini-swe-agent, Gemini CLI) headlessly against bugs
reconstructed from real repositories, has examiner agents capture each issue as
tests without ever seeing a patch, cross-examines every patch with every test, and
judges from the executed results alone. Every number below is scored against the
maintainers' own hidden tests, and the headline numbers come from a suite of bugs
the system was never tuned on.

---

## Results

<!-- results:begin -->
<!-- results:end -->

---

## What it found

- **Agents report success either way.** Every agent finished every task saying it
  was done. A large share of those patches were wrong by the maintainers' tests.
- **Most wrong patches are partial fixes.** They fix the example in the issue and
  miss the general rule the maintainer's tests check, so a test written from the
  issue passes them.
- **Tests written from the issue barely separate right from wrong.** Five examiner
  designs were measured, including agents that execute their tests and agents told
  to think like the reviewing maintainer. Most land near a coin flip.
- **The best evidence comes from whoever actually solved the bug.** The strongest
  contestant's own regression tests - validated against a working fix - were the
  most informative source. A blind test encodes its writer's guess at the
  maintainer's intent, and that guess is wrong about as often as the patches are.
  Verification is bounded by the verifier's ability to solve the problem.
- **With one dominant agent, picking among agents can match it, not beat it.**
  Selection pays only when agents are complementary; the gate is where independent
  evidence earns its place.

## How it works

```
real merged fix ──▶ task: repo at the parent commit (no .git), issue text only
                    oracle: FAIL_TO_PASS + PASS_TO_PASS from the maintainer's tests (hidden)

  1. race      coding agents fix the issue, each in its own sealed checkout
  2. examine   examiner agents write the tests a fix must pass - seeing no patch
  3. cross     every test on every fix and on the unfixed code, plus the full suite
  4. judge     rules over executed results: approve, reject, or pick a patch
  5. score     every decision against the hidden oracle
```

| Stage | What makes it trustworthy |
|---|---|
| Tasks | `git archive` checkouts with no history; oracles must fail before the fix and pass after it, SWE-bench style; flaky tests excluded |
| Race | the same binaries developers run, from clean configs, web tools off, shell network off; patches measured from disk, never from an agent's account |
| Examine | examiners never see a patch; agentic ones must run their tests against the unfixed code first |
| Cross | only a patch's source changes are applied; every exhibit installed under its own module; a collection error cannot abort the run |
| Judge | a reproduction counts only if it fails on the unfixed code; no author vouches for its own patch; rules are data (`JudgePolicy`) |

### The orchestration

- **Lanes, not workers.** Work is queued per quota - a ChatGPT plan, a free API
  key, a local model - lanes run in parallel and each caps its own concurrency, so
  a slow lane never stalls a fast one.
- **Journaled and resumable.** Every attempt writes its result atomically; an
  interrupted race resumes and never spends an agent's quota twice. A run that
  hit a quota is recorded as such, not as the agent's answer.
- **Sandboxed per harness.** Codex runs in its own workspace-write sandbox;
  mini-swe-agent's commands go through a seatbelt profile (no network, writes only
  in the checkout) while its model calls stay outside it; local models get a
  private Ollama server with a context window an agent fits in.
- **One race, two roles.** Fixing an issue and capturing it as tests differ only
  in the brief (`Fix`, `Reproduce`, `Specify`), so contestants and examiners share
  the same isolation, journal and lanes.

## How the numbers were kept honest

1. **Ground truth from the maintainers.** A patch resolves a task only if every
   FAIL_TO_PASS test passes and every PASS_TO_PASS test still passes. Adopting this
   definition flipped six of the earlier study's verdicts.
2. **Develop on one suite, report on another.** The design was built on bugs from
   marshmallow, attrs and arrow. The held-out suite - sqlparse and tomlkit - was
   mined afterwards.
3. **Declare before measuring.** Each round of candidate judge policies, and the
   procedure for freezing one, was committed before its evidence was judged. The
   freeze itself is a commit and the tag `frozen-policy`, made before any held-out
   number existed. The first design's development score is kept as the
   pre-registered result.
4. **No hand-typed numbers.** Every table in this README is written by
   `verdict arena results --into README.md` from content-hashed artifacts.

## Run it

```bash
python3.12 -m venv RagAgent && ./RagAgent/bin/pip install -e ".[dev,llm]"
npm install -g @openai/codex && codex login          # ChatGPT sign-in
uv tool install mini-swe-agent --with mcp            # optional third harness

verdict arena oracles --suite test                   # FAIL_TO_PASS / PASS_TO_PASS per task
verdict arena race -e codex -e codex-mini --suite test
verdict arena examine -x codex-reproducer -x codex-specifier -x codestral-reproducer --suite test
verdict arena cross --suite test                     # every test x every fix x the base
verdict arena score --suite test                     # hidden-oracle ground truth
verdict arena analyze --suite test --policy cross-executed
verdict arena evidence --suite test                  # raw signal of each evidence source
```

The dashboard (`dashboard/`) is a static site over the exported bundles:
`verdict arena export --suite test --policy cross-executed`, then `npm run build`.

## Repository layout

| Path | |
|---|---|
| [`src/verdict/arena/`](src/verdict/arena/) | agents, assignments, race, examiners, matrix, judge, analysis, workflow |
| [`src/verdict/cli/arena.py`](src/verdict/cli/arena.py) | the `verdict arena` commands - options in, tables out |
| [`data/artifacts/arena/`](data/artifacts/arena/) | the evidence: tasks, oracles, every patch, exhibit, matrix and verdict, per suite |
| [`dashboard/`](dashboard/) | the static results site |
| [`src/verdict/mutation/`](src/verdict/mutation/), [`retrieval/`](src/verdict/retrieval/) | the earlier mutation-testing study, kept with its reports |

## Limitations

- Python only, and three plus two repositories: free and subscription tiers bounded
  how many tasks every agent could attempt.
- The development suite's Claude Code patches were recorded by an earlier study and
  not re-run; the held-out field is two OpenAI models.
- Tests written from an issue can only check what the issue implies; when every
  agent makes the same partial fix, no examiner that shares their reading can
  catch it.
- macOS seatbelt sandboxes contain accidents, not adversaries.

## License

MIT

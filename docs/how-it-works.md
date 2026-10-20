# How it works

A bout replays bugs your project has already fixed. Because the fix and its tests are
known, every agent's patch can be scored by the maintainers' own judgement - without
asking a model whether it looks right.

## 1. Mining tasks

bugbout walks the first-parent history of your default branch, so each change that
landed is seen once: a squashed commit, a rebased one, or a pull request's merge commit.
A change becomes a task when:

- its title reads like a fix (*fix*, *bug*, *crash*, *regression*, ...);
- it changes at most `max-source-files` Python files in the package;
- it adds at least one test function to a test module;
- it names an issue or pull request, such as `(#123)` or `Fixes #45`.

The task's problem statement is the text of that issue. When a pull request links the
issue it closes, the issue's own text is used instead, because it describes the problem
without knowing the answer. Sections in which an author describes their fix
(*Solution*, *Changes*, ...) and diff blocks are removed. If several changes point at
the same issue, only the newest one is kept.

## 2. Ground truth

For each task, the test modules the fix touched are run twice: on the code just before
the fix, with the fix's versions of those tests installed, and on the fix itself. The
same definitions as SWE-bench follow:

- **fail-to-pass** tests fail before the fix and pass after it: a correct patch must
  make them all pass;
- **pass-to-pass** tests pass on both sides: a correct patch must keep them passing.

The fixed tree is run twice, and any test whose outcome differs between the two runs is
set aside as unstable. A task where the fix made no test go from failing to passing
proves nothing and is not scored.

## 3. The race

Every contestant gets the same prompt: the issue's text, a request to fix it and add a
regression test, and a command that runs the project's tests. Its working copy is the
code just before the fix, with:

- no `.git` directory, so neither the fix nor any later commit can be read;
- none of the fix's test files;
- web tools off and commands sandboxed (see [Agents](agents.md#sandboxing)).

Each attempt runs in a fresh copy, and what changed on disk *is* the patch - whatever
the agent says it did. Attempts are saved as they finish, so a bout can stop and resume
at any time without spending an agent's quota twice.

Examiners, if any are configured, get the same checkout and the issue, but are told to
fix nothing: only to write tests that capture the bug - checked to fail on the unfixed
code for the reason the issue describes.

## 4. Scoring

A patch is scored by applying its source changes - never its edits to the tests - to the
code before the fix, installing the maintainers' test files from the fix, and running
them. It **resolves** the task when every fail-to-pass test passes and no pass-to-pass
test fails. The report also separates patches that fixed the bug but broke something
else, patches that left the bug in place, and attempts that produced no fix.

## 5. Cross-examination

Before anyone looks at the hidden tests, every test the agents wrote is run against
every patch:

- each contestant's regression test, against everyone else's fix;
- each examiner's tests, which never saw a patch;
- and the repository's existing suite, so breakage shows without anyone predicting it.

Each author's tests are installed under a module name of their own, so none can shadow
another's. The result is a matrix of executed outcomes - no model is asked anything.

## The judge

The judge reads the matrix and picks one patch per task, or refuses to. Every rule is
about executed tests, so every verdict can be traced to cells in the matrix:

1. **A reproduction must reproduce.** A test counts as evidence about the bug only if it
   fails on the unfixed code.
2. **Nobody vouches for themselves.** A patch is supported only by other authors' tests.
3. **Breaking something needs a quorum.** A repository test the patch breaks counts
   against it only if most other patches keep it passing; when nearly every patch
   changes it, the issue probably asked for that change.
4. **Abstain rather than guess.** Without enough independent support, there is no pick.

| Policy | Approves a patch that passes... |
|---|---|
| `majority` | at least half of the independent reproductions, on average across authors. |
| `unanimous` | every independent reproduction. |
| `strict` | every independent reproduction, and breaks none of the examiners' tests for behaviour the issue does not ask to change. |
| `cross-vendor` | as `majority`, but no vendor's tests may support its own models. |

## The report

- **Agents** - each contestant's patches against the ground truth: resolved, fixed but
  broke a test, wrong fix, no fix.
- **Picking one patch per bug** - the judge's picks against the best single agent, an
  agent chosen at random, and the best any choice could do.
- **Every patch through a merge gate** - each patch judged alone, as if one agent had
  just said it was done: how often an approval was right, and how many wrong patches
  were stopped.
- **Which tests tell a right fix from a wrong one** - for each author of tests, and for
  your own suite, how well their outcomes separate correct patches from wrong ones.
- **How the tasks were found** - how much history was examined, and why fixes were left
  out.

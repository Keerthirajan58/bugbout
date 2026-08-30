# Precedent

**An adversarial, retrieval-grounded code verification agent.**

Most AI coding-agent demos show an agent completing a task. Precedent does the
opposite and rarer thing: it tries to prove another agent's code change is
unsafe. It takes a diff, retrieves what has actually gone wrong in *this*
codebase before, writes a regression test targeting that specific failure mode,
and then verifies the test by executing it against pre- and post-diff code in a
sandbox. A test only counts once execution proves a real behavioural difference.

> **Headline result:** mutation kill-rate without retrieval vs. with retrieval.
> _Measured in phase 3 — this section is filled in from the frozen artifacts in
> `data/artifacts/`, never by hand._

---

## How it works

```
diff -> risk retrieval (macro)  -> where is this repo historically fragile?
     -> context retrieval (micro) -> hybrid BM25 + embedding search, AST-chunked
     -> Prosecutor  -> hypothesises a break, writes a test, cites its source
     -> Skeptic     -> runs it against both trees; only execution counts
     -> verdict + citation
```

## Targets under audit

Both are pinned to immutable commit SHAs, because the "with retrieval" and
"without retrieval" arms must measure the same tree.

| Repo | Pin | Statements | Coverage | Suite | License |
|---|---|---|---|---|---|
| [marshmallow](https://github.com/marshmallow-code/marshmallow) | `4.3.1` | 1,819 | 98% | 1.4s | MIT |
| [arrow](https://github.com/arrow-py/arrow) | `1.4.0` | 2,315 | 99.9% | 9.8s | Apache-2.0 |

## Ground truth (phase 1)

Mutation testing supplies the objective baseline. Small, plausible bugs are
injected into each target and the repo's own suite is run against every one. A
mutant the suite catches is *killed*; one that slips through **survived** — a
change that passes the entire test suite and is still wrong. Survivors are the
corpus Precedent is evaluated on.

| Target | Line coverage | Mutants | Kill-rate | Survivors |
|---|---|---|---|---|
| marshmallow 4.3.1 | 98% | 920 | 87.5% | 115 |
| arrow 1.4.0 | 99.9% | 1,690 | 79.9% | 339 |
| **combined** | | **2,610** | | **454** |

Note the second row against the first. **Arrow has higher line coverage and a
lower kill-rate.** Coverage asks whether a line executed; mutation asks whether
anyone would notice if that line were wrong. Arrow's suite runs essentially
every line and still fails to detect one change in five. That gap is the reason
this project's ground truth is mutation-based rather than coverage-based, and
those 454 survivors are the evaluation corpus.

30 of cosmic-ray's 213 operators are used, chosen to match mistakes people and
coding agents actually make — boundary slips, inverted conditions, flipped
defaults, off-by-one constants. Every inclusion and exclusion is justified in
[`operators.py`](src/precedent/mutation/operators.py), and the same set is used
in both arms of the ablation so operator choice cannot move the headline number.

```bash
precedent mutants generate marshmallow   # enumerate, run nothing
precedent mutants run marshmallow -w 6   # score every mutant
precedent mutants report marshmallow     # the saved summary
precedent mutants survivors marshmallow  # the corpus
```

## The knowledge base (phase 3)

Mined entirely from each repository's own local clone — no GitHub API quota, no
rate limits, works offline.

| Document kind | marshmallow | arrow | What it contributes |
|---|---|---|---|
| incidents + reverts | 337 | 138 | what has actually broken here before |
| AST code chunks | 295 | 267 | complete functions, never fixed-size windows |
| call sites | 85 | 73 | how a symbol is actually reached |
| **total** | **717** | **462** | |

Search fuses BM25 and embedding *ranks* rather than averaging their scores —
BM25 is unbounded and corpus-dependent, cosine is bounded, and any common scale
drifts as documents are added. Scope (same symbol, same file, past defect) is an
explicit boost, because relevance to *this* symbol is a structural fact rather
than a textual similarity.

```bash
precedent kb build marshmallow          # mine, chunk, embed, index
precedent kb risk marshmallow           # most historically fragile symbols
precedent kb context marshmallow <id>   # exactly what the Prosecutor is handed
```

## Running on free tiers

Every provider limit here was measured, not assumed:

| Provider | Measured free-tier limit | Used for |
|---|---|---|
| Gemini | **20 requests/day per model** (8 models work) | single-diff `precedent verify` |
| Groq | **8,000 tokens/min + 1,000 requests/day per model**, 4 models | batch evaluation |

A Prosecutor call costs ~1,400 tokens, so one Groq model sustains ~5.7 calls/min
and four sustain ~22 — the difference between an hour and a day and a half for
the full evaluation.

Spreading work across models would normally wreck the comparison, so each mutant
is assigned a model by **hashing its id**: the same mutant draws the same model
in both arms and is compared against itself. That makes the result stronger, not
weaker — an improvement holding across four independently-trained models is
harder to dismiss than one model's quirk.

## Quickstart

```bash
git clone https://github.com/Keerthirajan58/precedent.git && cd precedent
python3.12 -m venv RagAgent && ./RagAgent/bin/pip install -e ".[dev]"

./RagAgent/bin/precedent doctor              # what's installed, what's missing
./RagAgent/bin/precedent targets list        # the pinned repos and why
./RagAgent/bin/precedent targets prepare marshmallow
./RagAgent/bin/precedent targets verify marshmallow   # gate: suite must be green
```

No API keys are needed for the scaffold or the mutation harness. Copy
`.env.example` to `.env` when you reach the agent and retrieval phases; every
key it asks for has a free tier and needs no credit card.

## Project status

| Phase | State |
|---|---|
| 0 — scaffold, pinned targets, CI | done |
| 1 — mutation harness + ground truth | done |
| 2 — Prosecutor + Skeptic, no retrieval (frozen baseline) | running |
| 3 — RAG layer + ablation delta | retrieval built, measurement pending |
| 4 — dashboard | pending |
| 5 — packaging, GitHub Action, cached demos | pending |

Phase-by-phase build reports live in [`Reports/`](Reports/).

## License

MIT

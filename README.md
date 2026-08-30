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
| 1 — mutation harness + ground truth | in progress |
| 2 — Prosecutor + Skeptic, no retrieval (frozen baseline) | pending |
| 3 — RAG layer + ablation delta | pending |
| 4 — dashboard | pending |
| 5 — packaging, GitHub Action, cached demos | pending |

Phase-by-phase build reports live in [`Reports/`](Reports/).

## License

MIT

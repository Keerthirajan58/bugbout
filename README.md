# Precedent

**An adversarial code-verification agent — and a measurement of whether
retrieval actually helps it.**

Most AI coding-agent projects show an agent completing a task. This one does the
opposite and rarer thing: it tries to prove another change is unsafe, and its
verdict comes from **executing code**, never from a model's opinion.

---

## The headline

> **The agent proves a regression in 53% of code changes that defeat
> marshmallow's entire 1,188-test suite.**
>
> **Retrieval did not improve that. Measured: 52.4% → 48.8% (−3.7 points) on 82
> mutants, with the model held constant per mutant and the control arm frozen
> under a SHA-256 hash before the retrieval arm was ever run.**

The second number is the one worth reading. The retrieval layer works — 98% of
its citations point at the file or symbol the diff touches — and it still did
not make the agent better. That result is reported as measured, not tuned away.

---

## What it does

```
diff ──▶ risk retrieval (macro)    where is this repo historically fragile?
     ──▶ context retrieval (micro) hybrid BM25 + embeddings over AST chunks
     ──▶ Prosecutor                one hypothesis, one test, cite the evidence
     ──▶ Skeptic                   run it on BOTH trees; execution decides
     ──▶ verdict
```

Two agents, named for the courtroom. The **Prosecutor** accuses: it forms one
specific hypothesis about how behaviour changed and writes a pytest test to
expose it. The **Skeptic** refuses to take its word — it executes that test
against the original and the changed code, and counts it only if a real
behavioural difference is proven.

---

## Ground truth

Objectivity comes from mutation testing, not from asking a model to grade
itself. Small, plausible bugs are injected; a mutant the suite catches is
*killed*, one that slips through **survived** — a change that passes every test
and is still wrong.

| Repo | Coverage | Mutants | Suite kill-rate | Survivors |
|---|---|---|---|---|
| marshmallow 4.3.1 | 98% | 920 | 87.5% | 115 |
| arrow 1.4.0 | **99.9%** | 1,690 | 79.9% | 339 |
| attrs 26.1.0 | — | 1,130 | 83.4% | 181 |
| **total** | | **3,740** | | **635** |

Note the second row against the first. **Arrow has higher line coverage and a
lower kill-rate.** Coverage asks whether a line ran; mutation asks whether
anyone would notice if it were wrong. Arrow's suite executes essentially every
line and still misses one change in five — which is why ground truth here is
mutation-based.

---

## The experiment

Both arms run the identical corpus, agents, and models. They differ in exactly
one respect: whether the repository's own history is appended to the
Prosecutor's prompt. A unit test asserts that.

| Arm | Caught | Rate |
|---|---|---|
| without retrieval | 43/82 | 52.4% |
| with retrieval | 40/82 | 48.8% |

Three invariants make the comparison mean something:

- **Same code** — targets pinned to immutable commit SHAs, enforced by a test.
- **Same mutants** — one operator set, hashed and recorded in both artifacts.
- **Same model per mutant** — assignment seeded from the control arm, and the
  model that actually served is recorded, so pairing is *verified* rather than
  assumed.

The control arm was **frozen** the moment it was produced: a `.sha256` sidecar,
a refusal to overwrite, and verification on read. It could not be quietly
re-run once the retrieval number was known.

### Why retrieval didn't help

The diagnostic (`precedent eval diagnose`) locates the failure precisely:

| Measurement | Value |
|---|---|
| Citation precision (cited item concerns the diff's file or symbol) | **98%** |
| Cases where the agent engaged with the evidence | 45% |
| `rejected_invalid_on_original`, control → retrieval | **7 → 16** |

The layer finds the right documents. The agent reads them. It then writes tests
that are *more* often wrong about the library — extra context led to more
elaborate tests that missed the API. This is a conversion failure, not a
retrieval failure, which is why improving ranking would have been wasted effort.

---

## Quickstart

```bash
git clone https://github.com/Keerthirajan58/precedent.git && cd precedent
python3.12 -m venv RagAgent && ./RagAgent/bin/pip install -e ".[dev,llm,rag]"

precedent doctor                      # what's installed, what's missing
precedent demo list                   # curated examples, no API key needed
precedent demo show marshmallow 1     # walk one end to end
```

The demos read committed artifacts — no network, no key, no pipeline run. A
demo that can be rate-limited is not a demo.

To reproduce from scratch:

```bash
precedent targets prepare marshmallow      # pinned checkout + isolated venv
precedent targets verify  marshmallow      # gate: the suite must be green
precedent mutants run     marshmallow -w 6 # ground truth
precedent kb build        marshmallow      # mine history, chunk, embed, index
precedent eval run        marshmallow --arm no_retrieval --freeze
precedent eval run        marshmallow --arm with_retrieval
precedent eval ablation   marshmallow
```

Phases 0–1 need **no credentials at all**. Copy `.env.example` to `.env` for the
agent phases; every key has a free tier and none requires a credit card.

---

## Running on free tiers

Every limit here was measured, not assumed — and it shaped the design more than
anything else.

| Provider | Measured free-tier limit | Role |
|---|---|---|
| Gemini flash | **20 requests/day per model**, ~106 s/call | single-diff `precedent verify` |
| Groq | **8,000 tokens/min + ~1,000 req/day per model**, 4 models | batch evaluation |

**The binding constraint was never the quota.** Groq debits `max_tokens` against
the tokens-per-minute allowance when a request is *admitted*, and never refunds
the remainder — measured directly: an identical 74-token prompt at
`max_tokens=4096` consumed 126 tokens and removed ~4,170 of headroom. At that
cap one call books ~5,300 tokens and a model sustains **1.5 calls a minute**,
whatever it writes. The symptom is a lane in cooldown, indistinguishable from an
exhausted daily quota — checked mid-run, every model still had 950+ of its 1,000
daily requests while lanes sat in 120-second waits.

On a token-per-minute free tier, `max_tokens` is a throughput setting, not a
safety limit — but it is bounded below by what the model actually emits. Every
free Groq model is a reasoning model, and sampled on eight real prompts,
**60–87% of every completion was hidden reasoning** (990–3,292 tokens total).
Both the cap and the pacing are now arithmetic from those measurements rather
than tuned by trial; two earlier values, set from estimates, each produced a
permanent 429 while the API reported the budget almost untouched.

Five behaviours in the LLM router were each learned from a failure:

1. **Cache per lane, in preference order.** Scanning every lane first let cache
   residue choose the model — 111 of 115 cases went to one model regardless of
   routing, silently destroying the paired comparison.
2. **Cap cooldowns at 120 s.** Providers quote the widest window they hit: a
   2,169 s hint while the per-minute bucket refilled in seconds; a 7-hour reset
   while 708 of 1,000 requests remained. Obeying them parked healthy lanes for
   hours.
3. **Pace to the arm's real token cost.** Retrieval enlarges the prompt, so it
   gets a longer interval — rather than a trimmed context, which would
   understate the very thing being measured.
4. **Journal every case.** An interrupted run resumes instead of restarting.
5. **Retry a truncated call, once, with more room.** Groq reports a JSON
   response that ran out of budget as a *400*, so four cases were silently
   recorded as "the Prosecutor produced no test" — a harness failure wearing an
   agent's decision as a disguise.

---

## What's here

| Path | |
|---|---|
| [`src/precedent/`](src/precedent/) | CLI, mutation harness, agents, retrieval, sandbox, eval |
| [`architecture/`](architecture/architecture.md) | system-design reference with diagrams |
| [`Reports/`](Reports/) | phase-by-phase build reports, written for a non-specialist |
| [`dashboard/`](dashboard/) | static Next.js site over the committed artifacts |
| [`data/artifacts/`](data/artifacts/) | the evidence: mutation runs, frozen eval arms, the real-diff study |

---

## Honest limitations

- **The local sandbox is isolation, not a security boundary.** It contains
  accidents, not adversaries. CI gets Docker; the local path does not.
- **Python only** — the mutation operators and AST chunker are Python-specific.
- **Call-site resolution is name-based.** Exact resolution is undecidable in a
  dynamic language; same-named methods on different classes are grouped.
- **Free-tier throughput bounds the corpus, not the design.** The ablation is
  reported on 82 paired mutants because that is what the quota allowed, not
  because the harness cannot do more.

## License

MIT

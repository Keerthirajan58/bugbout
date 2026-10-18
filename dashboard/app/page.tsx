import Link from "next/link";
import { AblationChart } from "@/components/AblationChart";
import { RealDiffStudy } from "@/components/RealDiffStudy";
import { StatTile } from "@/components/StatTile";
import {
  caseKey,
  getAblations,
  getCases,
  getRealDiffs,
  getTargets,
} from "@/lib/data";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export default function Home() {
  const targets = getTargets();
  const ablations = getAblations();
  const cases = getCases();
  const realDiffs = getRealDiffs();

  const corpus = targets.reduce((n, t) => n + (t.mutation?.survivors ?? 0), 0);
  const mutants = targets.reduce((n, t) => n + (t.mutation?.total ?? 0), 0);

  return (
    <div className="space-y-12">
      <section className="space-y-4">
        <h1 className="text-3xl font-semibold tracking-tight">
          Does retrieval actually make an adversarial agent better at catching
          regressions?
        </h1>
        <p className="max-w-3xl" style={{ color: "var(--text-secondary)" }}>
          Verdict audits a code change by writing a regression test against
          it, then proving that test by executing it on both versions of the
          code. This page reports the measurement that matters: the same agent,
          the same corpus, the same models — run once with the repository&apos;s
          own history available and once without.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="mutants scored"
          value={mutants.toLocaleString()}
          detail="objective ground truth"
        />
        <StatTile
          label="surviving mutants"
          value={corpus.toLocaleString()}
          detail="pass the full suite and are still wrong"
        />
        <StatTile
          label="repositories"
          value={String(targets.length)}
          detail="pinned to commit SHAs"
        />
        <StatTile
          label="cases recorded"
          value={cases.length.toLocaleString()}
          detail="every debate reconstructable"
        />
      </section>

      <section className="space-y-5">
        <h2 className="text-xl font-semibold">The ablation</h2>
        <AblationChart rows={ablations} />
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold">Ground truth</h2>
        <div className="scroll-x">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="py-2 text-left font-medium">repository</th>
                <th className="py-2 text-right font-medium">mutants</th>
                <th className="py-2 text-right font-medium">suite kill-rate</th>
                <th className="py-2 text-right font-medium">survivors</th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr
                  key={t.slug}
                  className="border-t"
                  style={{ borderColor: "var(--border)" }}
                >
                  <td className="py-2">
                    <span className="font-medium">{t.fullName}</span>{" "}
                    <span style={{ color: "var(--text-muted)" }}>
                      {t.ref} · {t.license}
                    </span>
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {t.mutation?.total.toLocaleString() ?? "—"}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {t.mutation ? pct(t.mutation.killRate) : "—"}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {t.mutation?.survivors.toLocaleString() ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p
          className="max-w-3xl text-sm"
          style={{ color: "var(--text-secondary)" }}
        >
          A mutant is a small, plausible bug injected into the source. One the
          test suite catches is killed; one that slips through <em>survived</em>{" "}
          — a change that passes every test and is still wrong. Survivors are
          the corpus the agent is scored on.
        </p>
      </section>

      {realDiffs && realDiffs.tasks.length > 0 && (
        <section className="space-y-5">
          <h2 className="text-xl font-semibold">
            Diffs an agent actually wrote
          </h2>
          <RealDiffStudy tasks={realDiffs.tasks} />
        </section>
      )}

      {cases.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">Cases</h2>
          <ul className="space-y-2">
            {cases.slice(0, 60).map((c) => (
              <li key={caseKey(c)}>
                <Link
                  href={`/case/${caseKey(c)}`}
                  className="flex flex-wrap items-baseline gap-3 rounded border px-3 py-2 text-sm transition-colors hover:brightness-105"
                  style={{
                    borderColor: "var(--border)",
                    background: "var(--surface-1)",
                  }}
                >
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-2.5 rounded-full"
                    style={{
                      background: c.caught
                        ? "var(--good)"
                        : "var(--text-muted)",
                    }}
                  />
                  <code className="text-xs">{c.mutantId}</code>
                  <span className="font-medium">
                    {c.modulePath.split("/").pop()}:{c.function ?? "—"}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {c.operator}
                  </span>
                  <span
                    className="ml-auto"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {c.verdict.replace(/_/g, " ")}
                  </span>
                  <span
                    className="rounded px-1.5 py-0.5 text-xs"
                    style={{
                      background: "var(--surface-2)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {c.arm === "with_retrieval" ? "with retrieval" : "control"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {cases.length > 60 && (
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Showing 60 of {cases.length}.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

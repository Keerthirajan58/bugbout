import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge } from "@/components/Badge";
import { DiffBlock } from "@/components/DiffBlock";
import { EvidenceGrid } from "@/components/EvidenceGrid";
import { SUITE_LABEL, SUITES, getBundle, getTask, isSuite, shortName } from "@/lib/data";
import type { Candidate } from "@/lib/types";

export function generateStaticParams() {
  return SUITES.flatMap((suite) => getBundle(suite).tasks.map((t) => ({ suite, task: t.task_id })));
}

function CandidateCard({ candidate, chosen }: { candidate: Candidate; chosen: boolean }) {
  const s = candidate.standing;
  return (
    <details
      className="rounded-lg border p-4"
      style={{ borderColor: chosen ? "var(--series-with)" : "var(--border)", background: "var(--surface-1)" }}
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2">
        <span className="font-medium">{shortName(candidate.entrant)}</span>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {candidate.vendor}
          {candidate.seconds ? ` · ${Math.round(candidate.seconds)}s` : ""}
        </span>
        {chosen && <Badge tone="neutral">Verdict&apos;s pick</Badge>}
        {s && <Badge tone={s.approved ? "good" : "bad"}>{s.approved ? "approved" : "rejected"}</Badge>}
        <Badge tone={candidate.resolved ? "good" : "bad"}>
          {candidate.resolved ? "hidden tests: resolved" : "hidden tests: not resolved"}
        </Badge>
      </summary>
      <div className="mt-3 space-y-3 text-sm">
        {s && (
          <p style={{ color: "var(--text-secondary)" }}>
            Passed {s.reproductions_passed}/{s.reproductions_total} independent reproductions from{" "}
            {s.judges.length} author(s)
            {s.breaks_total ? ` · breaks ${s.breaks_total} repository test(s) the others keep` : ""}
            {s.invariant_failures ? ` · fails ${s.invariant_failures} examiner invariant(s)` : ""}.
          </p>
        )}
        <DiffBlock diff={candidate.diff} />
      </div>
    </details>
  );
}

export default async function TaskPage({ params }: { params: Promise<{ suite: string; task: string }> }) {
  const { suite, task: taskId } = await params;
  if (!isSuite(suite)) notFound();
  const task = getTask(suite, taskId);
  if (!task) notFound();
  const { chosen, reason, chosen_resolved } = task.verdict;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <Link href={`/suite/${suite}`} className="text-sm underline underline-offset-4" style={{ color: "var(--text-secondary)" }}>
          ← {SUITE_LABEL[suite]}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          {task.repo} #{task.reference}: {task.title}
        </h1>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          <a href={task.url} className="underline underline-offset-4">
            the original issue
          </a>{" "}
          · hidden maintainer tests: {task.oracle.fail_to_pass} must start passing, {task.oracle.pass_to_pass} must keep
          passing
        </p>
      </header>

      <section
        className="rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <h2 className="text-sm font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
          Verdict
        </h2>
        <p className="mt-1 text-lg">
          {chosen ? (
            <>
              Ship <strong>{shortName(chosen)}</strong>{" "}
              <Badge tone={chosen_resolved ? "good" : "bad"}>{chosen_resolved ? "correct" : "wrong"}</Badge>
            </>
          ) : (
            <>No patch is safe to ship.</>
          )}
        </p>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {reason}
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">The issue, as the agents saw it</h2>
        <pre
          className="scroll-x max-h-72 whitespace-pre-wrap rounded-md p-3 text-sm"
          style={{ background: "var(--code-bg)" }}
        >
          {task.statement}
        </pre>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">The patches</h2>
        <div className="space-y-3">
          {task.candidates.map((c) => (
            <CandidateCard key={c.entrant} candidate={c} chosen={c.entrant === chosen} />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">The cross-examination</h2>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Each row is a test; each column the code it ran on. A reproduction has to fail on the unfixed code - only
          then can it say anything about a fix.
        </p>
        <EvidenceGrid evidence={task.evidence} candidates={task.candidates} />
      </section>
    </div>
  );
}

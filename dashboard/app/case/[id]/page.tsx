import Link from "next/link";
import { notFound } from "next/navigation";
import { DebateCard, DiffCard, RetrievedCard, VerdictCard } from "@/components/CaseCards";
import { caseKey, getCase, getCases } from "@/lib/data";

export function generateStaticParams() {
  return getCases().map((c) => ({ id: caseKey(c) }));
}

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const c = getCase(id);
  if (!c) notFound();

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm underline underline-offset-4" style={{ color: "var(--text-secondary)" }}>
          ← all cases
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {c.modulePath}
          {c.function ? `:${c.function}` : ""}
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>
          mutant <code>{c.mutantId}</code> · {c.operator} · {c.family} ·{" "}
          {c.arm === "with_retrieval" ? "with retrieval" : "control (no retrieval)"} ·
          model {c.model}
        </p>
      </div>

      <DiffCard diff={c.diff} />
      <RetrievedCard items={c.retrieved} citation={c.citation as string | null} />
      <DebateCard c={c} />
      <VerdictCard c={c} />
    </div>
  );
}

import { shortName } from "@/lib/data";
import type { Candidate, Evidence, Outcome } from "@/lib/types";

const CELL: Record<Outcome, { glyph: string; color: string; label: string }> = {
  passed: { glyph: "●", color: "var(--good)", label: "passed" },
  failed: { glyph: "●", color: "var(--bad)", label: "failed" },
  error: { glyph: "◆", color: "var(--text-muted)", label: "error" },
  skipped: { glyph: "○", color: "var(--text-muted)", label: "skipped" },
  absent: { glyph: "·", color: "var(--text-muted)", label: "not run" },
};

const KIND: Record<Evidence["kind"], string> = {
  agentic: "agentic examiner - ran its test before submitting",
  prompted: "prompted examiner - one model call",
  self: "contestant's own test",
};

function Dot({ outcome }: { outcome: Outcome }) {
  const c = CELL[outcome];
  return (
    <span title={c.label} style={{ color: c.color }}>
      {c.glyph}
    </span>
  );
}

/**
 * The cross-examination for one task: every exhibit's tests (rows) on the
 * unfixed code and on every candidate fix (columns). A reproduction must fail
 * on the base; a correct fix should turn it green.
 */
export function EvidenceGrid({ evidence, candidates }: { evidence: Evidence[]; candidates: Candidate[] }) {
  const trees = candidates.map((c) => c.entrant);
  if (evidence.length === 0) {
    return <p className="text-sm" style={{ color: "var(--text-muted)" }}>No exhibits for this task.</p>;
  }
  return (
    <div className="scroll-x rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <table className="w-full text-xs">
        <thead style={{ color: "var(--text-muted)" }}>
          <tr className="text-left uppercase tracking-wide">
            <th className="px-3 py-2">Test</th>
            <th className="px-3 py-2 text-center">unfixed</th>
            {trees.map((t) => (
              <th key={t} className="px-3 py-2 text-center whitespace-nowrap">
                {shortName(t)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {evidence.map((ex) => (
            <ExhibitRows key={ex.author} exhibit={ex} trees={trees} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExhibitRows({ exhibit, trees }: { exhibit: Evidence; trees: string[] }) {
  return (
    <>
      <tr className="border-t" style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}>
        <td className="px-3 py-1.5" colSpan={trees.length + 2}>
          <span className="font-medium">{shortName(exhibit.author)}</span>{" "}
          <span style={{ color: "var(--text-muted)" }}>
            · {KIND[exhibit.kind]} · {exhibit.vendor}
            {exhibit.reproduces ? "" : " · reproduced nothing, so not counted"}
          </span>
        </td>
      </tr>
      {exhibit.tests.map((test) => (
        <tr key={test.id} className="border-t" style={{ borderColor: "var(--border)" }}>
          <td className="px-3 py-1 font-mono">{test.id}</td>
          <td className="px-3 py-1 text-center">
            <Dot outcome={test.base} />
          </td>
          {trees.map((t) => (
            <td key={t} className="px-3 py-1 text-center">
              <Dot outcome={test.trees[t] ?? "absent"} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

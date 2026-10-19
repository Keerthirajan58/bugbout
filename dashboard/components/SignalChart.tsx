import { shortName } from "@/lib/data";
import type { Signal } from "@/lib/types";

/** What each source of evidence is, in words a reader can place. */
function describe(source: string): string {
  if (source.startsWith("the repository")) return "the existing test suite";
  if (source.startsWith("examiner/specify/")) return "examiner, asked for the maintainer's rule";
  if (source.startsWith("examiner/reproduce/")) return "examiner, reproduced the issue";
  if (source.startsWith("examiner/")) return "examiner, one model call";
  return "a contestant's own tests, after fixing the bug";
}

const DOMAIN: [number, number] = [0.3, 0.9];
const TICKS = [0.3, 0.5, 0.7, 0.9];

/**
 * How well each source of evidence separates correct patches from wrong ones,
 * drawn as distance from a coin flip (AUC 0.5). One series, one hue; every
 * value is labelled at its tip, and the same numbers are in the table view.
 */
export function SignalChart({ signals }: { signals: Signal[] }) {
  const rows = [...signals].sort((a, b) => b.auc - a.auc);
  const width = 720;
  const labelWidth = 300;
  const rowHeight = 38;
  const top = 28;
  const plot = width - labelWidth - 56;
  const x = (v: number) => labelWidth + ((v - DOMAIN[0]) / (DOMAIN[1] - DOMAIN[0])) * plot;
  const height = top + rows.length * rowHeight + 8;

  return (
    <figure className="space-y-2">
      <div className="scroll-x">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="AUC per evidence source" className="min-w-[560px] w-full">
          {TICKS.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={top - 6} y2={height - 6} stroke="var(--border)" strokeWidth={1} />
              <text x={x(t)} y={top - 12} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
                {t === 0.5 ? "0.5 coin flip" : t.toFixed(1)}
              </text>
            </g>
          ))}
          {rows.map((row, i) => {
            const y = top + i * rowHeight + rowHeight / 2;
            const end = x(row.auc);
            return (
              <g key={row.source}>
                <title>
                  {`${shortName(row.source)}: AUC ${row.auc.toFixed(2)} over ${row.correct} correct and ${row.wrong} wrong patches`}
                </title>
                <text x={0} y={y - 3} fontSize="12.5" fill="var(--text-primary)">
                  {shortName(row.source)}
                </text>
                <text x={0} y={y + 12} fontSize="11" fill="var(--text-muted)">
                  {describe(row.source)}
                </text>
                <line x1={x(0.5)} x2={end} y1={y} y2={y} stroke="var(--series-with)" strokeWidth={2} strokeLinecap="round" />
                <circle cx={end} cy={y} r={5} fill="var(--series-with)" stroke="var(--surface-1)" strokeWidth={2} />
                <text
                  x={row.auc >= 0.5 ? end + 10 : end - 10}
                  y={y + 4}
                  fontSize="12"
                  textAnchor={row.auc >= 0.5 ? "start" : "end"}
                  fill="var(--text-secondary)"
                >
                  {row.auc.toFixed(2)}
                </text>
                <rect x={0} y={y - rowHeight / 2} width={width} height={rowHeight} fill="transparent" />
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="text-xs" style={{ color: "var(--text-muted)" }}>
        AUC: the chance that a correct patch passes more of a source&apos;s tests than a wrong one does. No author is
        scored on its own patch.
      </figcaption>
      <details className="text-sm">
        <summary className="cursor-pointer" style={{ color: "var(--text-secondary)" }}>
          Show as a table
        </summary>
        <table className="mt-2 w-full text-left tabular-nums">
          <thead style={{ color: "var(--text-muted)" }}>
            <tr>
              <th className="py-1 pr-4 font-medium">Source</th>
              <th className="py-1 pr-4 font-medium">AUC</th>
              <th className="py-1 font-medium">Correct / wrong patches scored</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.source} className="border-t" style={{ borderColor: "var(--border)" }}>
                <td className="py-1 pr-4">{shortName(row.source)}</td>
                <td className="py-1 pr-4">{row.auc.toFixed(2)}</td>
                <td className="py-1">
                  {row.correct} / {row.wrong}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

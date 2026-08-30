import type { Ablation } from "@/lib/data";

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

/**
 * The project's headline result. Two bars per target, one per ablation arm.
 *
 * A grouped bar is the right form here: the job is comparing the magnitude of
 * the same measure under two conditions. Both bars are direct-labeled and a
 * legend is present, so identity never rests on colour alone, and the delta is
 * stated as a hero number because that single figure is the whole point.
 */
export function AblationChart({ rows }: { rows: Ablation[] }) {
  if (rows.length === 0) {
    return (
      <div
        className="rounded-lg border p-6 text-sm"
        style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
      >
        Both arms must have run before an ablation can be shown. Run{" "}
        <code>precedent eval run &lt;target&gt; --arm with_retrieval</code>.
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <Legend />
      {rows.map((row) => (
        <figure key={row.target} className="space-y-3">
          <figcaption className="flex flex-wrap items-baseline gap-3">
            <span className="font-medium">{row.fullName}</span>
            <span className="text-sm" style={{ color: "var(--text-muted)" }}>
              {row.corpus} surviving mutants
            </span>
          </figcaption>

          <Bar
            label="without retrieval"
            value={row.without}
            caught={row.caughtWithout}
            total={row.corpus}
            color="var(--series-without)"
          />
          <Bar
            label="with retrieval"
            value={row.with}
            caught={row.caughtWith}
            total={row.corpus}
            color="var(--series-with)"
          />

          <div className="flex flex-wrap items-baseline gap-4 pt-1">
            <span
              className="text-2xl font-semibold tabular-nums"
              style={{ color: row.delta >= 0 ? "var(--good)" : "var(--bad)" }}
            >
              {row.delta >= 0 ? "+" : ""}
              {(row.delta * 100).toFixed(1)} points
            </span>
            <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {row.flipped} regression{row.flipped === 1 ? "" : "s"} caught only when
              retrieval was available
            </span>
          </div>
        </figure>
      ))}
    </div>
  );
}

function Bar({
  label,
  value,
  caught,
  total,
  color,
}: {
  label: string;
  value: number;
  caught: number;
  total: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-4">
      <span
        className="w-40 shrink-0 text-right text-sm"
        style={{ color: "var(--text-secondary)" }}
      >
        {label}
      </span>
      <div
        className="relative h-7 flex-1 rounded"
        style={{ background: "var(--surface-2)" }}
      >
        <div
          className="h-full rounded-r"
          style={{
            width: `${Math.max(value * 100, 0.5)}%`,
            background: color,
            borderTopLeftRadius: 4,
            borderBottomLeftRadius: 4,
          }}
          title={`${caught} of ${total} caught`}
        />
      </div>
      <span className="w-32 shrink-0 tabular-nums text-sm">
        <strong>{pct(value)}</strong>{" "}
        <span style={{ color: "var(--text-muted)" }}>
          {caught}/{total}
        </span>
      </span>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex gap-6 text-sm" style={{ color: "var(--text-secondary)" }}>
      {[
        ["without retrieval", "var(--series-without)"],
        ["with retrieval", "var(--series-with)"],
      ].map(([label, color]) => (
        <span key={label} className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-3 w-3 rounded-sm"
            style={{ background: color }}
          />
          {label}
        </span>
      ))}
    </div>
  );
}

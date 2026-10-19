import { percent, shortName } from "@/lib/data";
import type { Gate, Scoreboard } from "@/lib/types";

const cell = "px-3 py-2 text-left";
const head = "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="scroll-x rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <table className="w-full text-sm tabular-nums">{children}</table>
    </div>
  );
}

/** Choosing a patch: one pick per task, scored by the hidden maintainer tests. */
export function SelectionTable({ board }: { board: Scoreboard }) {
  const agents = Object.entries(board.per_entrant).sort((a, b) => b[1].resolved - a[1].resolved);
  const rows: [React.ReactNode, number, string][] = [
    ...agents.map(([name, s]): [React.ReactNode, number, string] => [`${shortName(name)} alone`, s.resolved, ""]),
    ["random pick among the patches", board.random_pick, "expected value"],
    [<strong key="v">Verdict</strong>, board.verdict_resolved, `${board.verdict_picks} picks, ${board.wrong_picks} wrong, ${board.abstained} abstained`],
    ["best possible (any agent solved it)", board.best_possible, "ceiling"],
  ];
  return (
    <Frame>
      <thead style={{ color: "var(--text-muted)" }}>
        <tr>
          <th className={head}>Selector</th>
          <th className={head}>Resolved</th>
          <th className={head}>Note</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, value, note], i) => (
          <tr key={i} className="border-t" style={{ borderColor: "var(--border)" }}>
            <td className={cell}>{label}</td>
            <td className={cell}>
              {Number.isInteger(value) ? value : value.toFixed(1)}/{board.tasks}
            </td>
            <td className={cell} style={{ color: "var(--text-muted)" }}>
              {note}
            </td>
          </tr>
        ))}
      </tbody>
    </Frame>
  );
}

/** Gating a patch: every agent patch, approved / rejected / unverified, against the truth. */
export function GateTable({ gate }: { gate: Gate }) {
  const rows: [string, number, number][] = [
    ["approved", gate.approved_correct, gate.approved_wrong],
    ["rejected", gate.rejected_correct, gate.rejected_wrong],
    ["unverified", gate.unverified_correct, gate.unverified_wrong],
  ];
  return (
    <Frame>
      <thead style={{ color: "var(--text-muted)" }}>
        <tr>
          <th className={head}>Verdict on the patch</th>
          <th className={head}>Actually correct</th>
          <th className={head}>Actually wrong</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, good, bad]) => (
          <tr key={label} className="border-t" style={{ borderColor: "var(--border)" }}>
            <td className={cell}>{label}</td>
            <td className={cell} style={{ color: "var(--good)" }}>
              {good}
            </td>
            <td className={cell} style={{ color: "var(--bad)" }}>
              {bad}
            </td>
          </tr>
        ))}
        <tr className="border-t" style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}>
          <td className={cell} colSpan={3}>
            Precision of an approval <strong>{percent(gate.precision)}</strong> against{" "}
            {percent(gate.base_rate)} for merging everything · wrong patches caught{" "}
            <strong>{percent(gate.catch_rate)}</strong> · correct patches rejected {percent(gate.false_alarm_rate)}
          </td>
        </tr>
      </tbody>
    </Frame>
  );
}

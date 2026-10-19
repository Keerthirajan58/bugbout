import Link from "next/link";
import { shortName } from "@/lib/data";
import type { SuiteName, Task } from "@/lib/types";

function Mark({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span title={label} style={{ color: ok ? "var(--good)" : "var(--bad)" }}>
      {ok ? "✓" : "✗"}
    </span>
  );
}

/** Every task in a suite: who actually solved it, and what Verdict decided. */
export function TaskTable({ suite, tasks }: { suite: SuiteName; tasks: Task[] }) {
  const agents = Array.from(new Set(tasks.flatMap((t) => t.candidates.map((c) => c.entrant)))).sort();
  return (
    <div className="scroll-x rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <table className="w-full text-sm">
        <thead style={{ color: "var(--text-muted)" }}>
          <tr className="text-left text-xs uppercase tracking-wide">
            <th className="px-3 py-2">Issue</th>
            {agents.map((a) => (
              <th key={a} className="px-3 py-2 whitespace-nowrap">
                {shortName(a)}
              </th>
            ))}
            <th className="px-3 py-2">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => {
            const byAgent = new Map(task.candidates.map((c) => [c.entrant, c]));
            const { chosen, chosen_resolved } = task.verdict;
            return (
              <tr key={task.task_id} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
                <td className="px-3 py-2">
                  <Link href={`/suite/${suite}/${task.task_id}`} className="underline underline-offset-4">
                    {task.repo.split("/")[1]} #{task.reference}
                  </Link>
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {task.title.slice(0, 80)}
                  </div>
                </td>
                {agents.map((a) => {
                  const c = byAgent.get(a);
                  return (
                    <td key={a} className="px-3 py-2 text-center">
                      {c ? <Mark ok={c.resolved} label={c.resolved ? "resolved" : "not resolved"} /> : "–"}
                    </td>
                  );
                })}
                <td className="px-3 py-2 whitespace-nowrap">
                  {chosen ? (
                    <>
                      <Mark ok={chosen_resolved} label={chosen_resolved ? "correct pick" : "wrong pick"} />{" "}
                      {shortName(chosen)}
                    </>
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>abstained</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

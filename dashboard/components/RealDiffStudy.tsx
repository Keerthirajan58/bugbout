import type { RealDiffTask } from "@/lib/types";

/**
 * The second experiment: diffs a coding agent actually wrote, against real
 * historical bugs, scored by the test the maintainer wrote at the time.
 *
 * Presented as a table rather than a chart on purpose. With 22 rows the
 * interesting question is per-task identity - which bug, what the agent did,
 * what broke - and a bar chart of four aggregate counts would hide exactly the
 * detail a reader needs to check the claim.
 */

function Status({
  state,
  label,
}: {
  state: "good" | "bad" | "none";
  label: string;
}) {
  const colour =
    state === "good"
      ? "var(--good)"
      : state === "bad"
        ? "var(--bad)"
        : "var(--text-muted)";
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span
        aria-hidden
        className="inline-block h-2 w-2 shrink-0 rounded-full"
        style={{ background: colour }}
      />
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
    </span>
  );
}

const outcome = (value: string | null): "good" | "bad" | "none" =>
  value === "passed"
    ? "good"
    : value === null || value === "absent"
      ? "none"
      : "bad";

export function RealDiffStudy({ tasks }: { tasks: RealDiffTask[] }) {
  const produced = tasks.filter((t) => t.changedFiles.length > 0);
  const broke = produced.filter(
    (t) => t.regression !== "passed" && t.regression !== "absent",
  );
  const clean = produced.filter((t) => t.regression === "passed");
  const audited = produced.some((t) => t.auditVerdict);
  const caught = broke.filter((t) => t.flagged).length;
  const alarms = clean.filter((t) => t.flagged).length;

  return (
    <div className="space-y-5">
      <p className="max-w-3xl" style={{ color: "var(--text-secondary)" }}>
        Mutants are objective but synthetic. These are changes a coding agent
        really wrote: for each row, the repository was rewound to the commit
        before a real merged fix, the agent was given only the bug report, and
        its diff was scored with the maintainer&apos;s own regression test —
        which it never saw. The checkout is produced with{" "}
        <code>git archive</code>, so it contains no git history at all and the
        fix is not on disk to be found.
      </p>

      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          [
            "tasks with a valid oracle",
            String(tasks.length),
            "the test fails at base, as required",
          ],
          [
            "agent diffs produced",
            String(produced.length),
            `${tasks.length - produced.length} left the code unchanged`,
          ],
          [
            "resolved the issue",
            String(produced.filter((t) => t.resolved).length),
            "oracle passes and the suite stays green",
          ],
          [
            "broke a pre-existing test",
            String(broke.length),
            audited ? `Verdict flagged ${caught}` : "not yet audited",
          ],
        ].map(([label, value, detail]) => (
          <div
            key={label}
            className="rounded-lg border p-4"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
            }}
          >
            <dt
              className="text-xs uppercase tracking-wide"
              style={{ color: "var(--text-muted)" }}
            >
              {label}
            </dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">
              {value}
            </dd>
            <dd
              className="mt-1 text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              {detail}
            </dd>
          </div>
        ))}
      </dl>

      {audited && (
        <p
          className="max-w-3xl text-sm"
          style={{ color: "var(--text-secondary)" }}
        >
          Verdict is scored on the question it is built to answer. It proves{" "}
          <em>regressions</em> — a test green before a diff and red after — so
          its recall is measured on the {broke.length} diffs that really did
          break something ({caught} flagged), and its false-alarm rate on the{" "}
          {clean.length} that did not ({alarms} flagged). Whether a diff fixed
          the reported bug is a different question, reported beside it rather
          than claimed as its result.
        </p>
      )}

      <div className="scroll-x">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ color: "var(--text-muted)" }}>
              <th className="py-2 text-left font-medium">bug</th>
              <th className="py-2 text-left font-medium">repository</th>
              <th className="py-2 text-right font-medium">files</th>
              <th className="py-2 text-left font-medium">fixed the bug</th>
              <th className="py-2 text-left font-medium">suite still green</th>
              {audited && (
                <th className="py-2 text-left font-medium">Verdict</th>
              )}
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr
                key={t.taskId}
                className="border-t"
                style={{ borderColor: "var(--border)" }}
              >
                <td className="py-2">
                  <span className="font-medium">#{t.reference}</span>{" "}
                  <span style={{ color: "var(--text-secondary)" }}>
                    {t.title}
                  </span>
                </td>
                <td className="py-2" style={{ color: "var(--text-muted)" }}>
                  {t.target}
                </td>
                <td className="py-2 text-right tabular-nums">
                  {t.changedFiles.length}
                </td>
                <td className="py-2">
                  <Status
                    state={outcome(t.oracle)}
                    label={
                      t.oracle === "passed"
                        ? "yes"
                        : t.oracle === "absent"
                          ? "no diff"
                          : "no"
                    }
                  />
                </td>
                <td className="py-2">
                  <Status
                    state={outcome(t.regression)}
                    label={
                      t.regression === "passed"
                        ? "yes"
                        : t.regression === "absent"
                          ? "—"
                          : "broke tests"
                    }
                  />
                </td>
                {audited && (
                  <td className="py-2">
                    {t.auditVerdict ? (
                      <Status
                        state={t.flagged ? "bad" : "none"}
                        label={
                          t.flagged
                            ? "regression proven"
                            : t.auditVerdict.replace(/_/g, " ")
                        }
                      />
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import type { Case, RetrievedItem } from "@/lib/types";

/** The change under review, rendered as a real unified diff. */
export function DiffCard({ diff }: { diff: string }) {
  return (
    <Card title="the change under review">
      <pre
        className="scroll-x rounded p-3 text-xs leading-relaxed"
        style={{ background: "var(--code-bg)" }}
      >
        {diff.split("\n").map((line, i) => {
          const added = line.startsWith("+") && !line.startsWith("+++");
          const removed = line.startsWith("-") && !line.startsWith("---");
          return (
            <div
              key={i}
              style={{
                color: added
                  ? "var(--good)"
                  : removed
                    ? "var(--bad)"
                    : "var(--text-secondary)",
              }}
            >
              {line || " "}
            </div>
          );
        })}
      </pre>
    </Card>
  );
}

/**
 * What retrieval supplied. Each item shows *why* it ranked where it did - BM25
 * rank, vector rank and any scope boost - because a claim about grounding that
 * cannot be inspected is just an assertion.
 */
export function RetrievedCard({
  items,
  citation,
}: {
  items: RetrievedItem[];
  citation: string | null;
}) {
  if (items.length === 0) {
    return (
      <Card title="retrieved context">
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          None — this is the control arm. The Prosecutor saw only the diff and
          its surrounding code, which is exactly what the ablation measures.
        </p>
      </Card>
    );
  }
  return (
    <Card title={`retrieved context (${items.length} items)`}>
      <ul className="space-y-3">
        {items.map((item) => {
          const cited = citation != null && citation.includes(item.doc_id);
          return (
            <li
              key={item.doc_id}
              className="rounded border p-3"
              style={{
                borderColor: cited ? "var(--series-with)" : "var(--border)",
                background: cited
                  ? "color-mix(in srgb, var(--series-with) 7%, transparent)"
                  : undefined,
              }}
            >
              <div className="flex flex-wrap items-baseline gap-2">
                <span
                  className="rounded px-1.5 py-0.5 text-xs"
                  style={{
                    background: "var(--surface-2)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {item.kind}
                </span>
                <span className="text-sm font-medium">{item.title}</span>
                {cited && (
                  <span
                    className="text-xs font-semibold"
                    style={{ color: "var(--series-with)" }}
                  >
                    cited by the Prosecutor
                  </span>
                )}
              </div>
              <div
                className="mt-1 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                score {item.score.toFixed(4)} · {item.why}
                {item.module_path ? ` · ${item.module_path}` : ""}
              </div>
              {item.url && (
                <a
                  href={item.url}
                  className="mt-1 inline-block text-xs underline underline-offset-2"
                  style={{ color: "var(--text-secondary)" }}
                >
                  view commit
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Prosecutor's argument, then the Skeptic's response — the debate thread. */
export function DebateCard({ c }: { c: Case }) {
  return (
    <Card title="the debate">
      <div className="space-y-4">
        <Turn who="Prosecutor" color="var(--series-without)">
          <p className="font-medium">
            {c.hypothesis || "(no hypothesis produced)"}
          </p>
          {c.reasoning && (
            <p
              className="mt-1 text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              {c.reasoning}
            </p>
          )}
          {(c.originalBehaviour || c.changedBehaviour) && (
            <dl className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
              <div>
                <dt
                  className="text-xs uppercase tracking-wide"
                  style={{ color: "var(--text-muted)" }}
                >
                  predicted before
                </dt>
                <dd style={{ color: "var(--text-secondary)" }}>
                  {c.originalBehaviour || "—"}
                </dd>
              </div>
              <div>
                <dt
                  className="text-xs uppercase tracking-wide"
                  style={{ color: "var(--text-muted)" }}
                >
                  predicted after
                </dt>
                <dd style={{ color: "var(--text-secondary)" }}>
                  {c.changedBehaviour || "—"}
                </dd>
              </div>
            </dl>
          )}
          {c.testCode && (
            <pre
              className="scroll-x mt-2 rounded p-3 text-xs"
              style={{ background: "var(--code-bg)" }}
            >
              {c.testCode}
            </pre>
          )}
          {!c.confident && c.testCode && (
            <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
              The Prosecutor flagged itself unsure. The test was executed anyway
              — execution arbitrates, not the model&apos;s self-assessment.
            </p>
          )}
          {(c.gateFailures ?? 0) > 0 && (
            <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {c.gateFailures === 1
                ? "One candidate was rejected at the grounding gate"
                : `${c.gateFailures} candidates were rejected at the grounding gate`}
              : the test failed against the unchanged code, so it was wrong
              about the library rather than about the change.
              {c.repairs > 0 && " One repair attempt followed."}
            </p>
          )}
          {c.repairs > 0 && (c.gateFailures ?? 0) === 0 && (
            <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {c.repairs} repair attempt used: the first test did not run
              correctly against the unchanged code.
            </p>
          )}
        </Turn>

        <Turn who="Skeptic" color="var(--series-with)">
          <p className="text-sm">{c.reason}</p>
          {c.reviewReason && (
            <p
              className="mt-1 text-sm"
              style={{ color: "var(--text-secondary)" }}
            >
              Review: {c.reviewReason}
            </p>
          )}
        </Turn>
      </div>
    </Card>
  );
}

export function VerdictCard({ c }: { c: Case }) {
  const good = c.caught;
  return (
    <div
      className="rounded-lg border-2 p-5"
      style={{
        borderColor: good ? "var(--good)" : "var(--border)",
        background: "var(--surface-1)",
      }}
    >
      <div className="flex flex-wrap items-baseline gap-3">
        <span
          className="text-lg font-semibold"
          style={{ color: good ? "var(--good)" : "var(--text-secondary)" }}
        >
          {good
            ? "REGRESSION CONFIRMED"
            : c.verdict.replace(/_/g, " ").toUpperCase()}
        </span>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          verdict established by execution, not argument
        </span>
      </div>
      <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
        {c.reason}
      </p>
    </div>
  );
}

function Turn({
  who,
  color,
  children,
}: {
  who: string;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-l-2 pl-4" style={{ borderColor: color }}>
      <div
        className="text-xs font-semibold uppercase tracking-wide"
        style={{ color }}
      >
        {who}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className="rounded-lg border p-5"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <h2
        className="mb-3 text-xs font-semibold uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        {title}
      </h2>
      {children}
    </section>
  );
}

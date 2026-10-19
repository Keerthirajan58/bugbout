/** A unified diff, coloured by line, scrolling inside its own box. */
export function DiffBlock({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <p className="text-sm" style={{ color: "var(--text-muted)" }}>No change.</p>;
  }
  return (
    <pre
      className="scroll-x max-h-[28rem] rounded-md p-3 text-xs leading-relaxed"
      style={{ background: "var(--code-bg)" }}
    >
      {diff.split("\n").map((line, i) => {
        const color = line.startsWith("+++") || line.startsWith("---")
          ? "var(--text-muted)"
          : line.startsWith("+")
            ? "var(--good)"
            : line.startsWith("-")
              ? "var(--bad)"
              : line.startsWith("@@")
                ? "var(--series-with)"
                : "var(--text-secondary)";
        return (
          <div key={i} style={{ color }}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

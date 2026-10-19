type Tone = "good" | "bad" | "neutral";

const COLOR: Record<Tone, string> = {
  good: "var(--good)",
  bad: "var(--bad)",
  neutral: "var(--text-muted)",
};

export function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ borderColor: COLOR[tone], color: COLOR[tone] }}
    >
      {children}
    </span>
  );
}

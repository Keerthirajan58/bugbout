/**
 * The arena's stages, left to right. Drawn as inline SVG so it inherits the
 * page's theme tokens and stays sharp at any width.
 */
const STAGES = [
  { title: "Race", detail: "coding agents fix the issue, each in a sealed checkout" },
  { title: "Examine", detail: "other agents write the tests a maintainer would demand - seeing no patch" },
  { title: "Cross-examine", detail: "every test runs on every fix and on the unfixed code" },
  { title: "Judge", detail: "executed tests approve, reject or pick a patch - no model votes" },
];

export function Pipeline() {
  const width = 960;
  const box = 210;
  const gap = (width - STAGES.length * box) / (STAGES.length - 1);
  return (
    <div className="scroll-x">
      <svg
        viewBox={`0 0 ${width} 132`}
        role="img"
        aria-label="Race, examine, cross-examine, judge"
        className="min-w-[640px] w-full"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)" />
          </marker>
        </defs>
        {STAGES.map((stage, i) => {
          const x = i * (box + gap);
          return (
            <g key={stage.title}>
              <rect x={x} y={8} width={box} height={116} rx={10} fill="var(--surface-1)" stroke="var(--border)" />
              <text x={x + 16} y={38} fontSize="15" fontWeight={600} fill="var(--text-primary)">
                {i + 1}. {stage.title}
              </text>
              <foreignObject x={x + 16} y={48} width={box - 32} height={72}>
                <div style={{ fontSize: 12.5, lineHeight: 1.35, color: "var(--text-secondary)" }}>{stage.detail}</div>
              </foreignObject>
              {i < STAGES.length - 1 && (
                <line
                  x1={x + box + 4}
                  y1={66}
                  x2={x + box + gap - 6}
                  y2={66}
                  stroke="var(--text-muted)"
                  strokeWidth={1.5}
                  markerEnd="url(#arrow)"
                />
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

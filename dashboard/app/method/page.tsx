const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "Real bugs, with the answer hidden",
    body: [
      "Each task is a merged bugfix from a repository's own history that shipped a new test. The repository is rewound to the commit before the fix and handed to agents with no git history at all - the fix, the maintainer's tests and every later commit are physically absent.",
      "A patch resolves the task the way SWE-bench defines it: every test the maintainer's fix turned from failing to passing must pass (FAIL_TO_PASS), and every test that passed both before and after the maintainer's fix must still pass (PASS_TO_PASS). No agent, examiner or judge ever sees these tests.",
    ],
  },
  {
    title: "Execution decides, not a model",
    body: [
      "Examiners are coding agents that write tests from the issue alone and must run them against the unfixed code before submitting. The judge reads only the executed results: a reproduction counts only if it fails on the unfixed code, and no author may vouch for its own patch.",
    ],
  },
  {
    title: "Tuned on one set of bugs, measured on another",
    body: [
      "The system was designed on a development suite (marshmallow, attrs, arrow). Every rule variant was declared in the repository before its evidence was judged, and one was frozen by a procedure also committed in advance. Only then were the held-out suite's results computed (sqlparse and tomlkit, mined afterwards).",
      "The first design's development score is kept on record as pre-registered, alongside the reasons it fell short.",
    ],
  },
  {
    title: "What this does not show",
    body: [
      "Tests written from an issue can only check what the issue implies. When every agent makes the same partial fix, no examiner that agrees with them can catch it.",
      "Selection can at best match the strongest agent when one agent dominates the field; the gate is where independent tests earn their keep.",
      "All agents ran on free or subscription tiers, which bounded the number of tasks.",
    ],
  },
];

export default function Method() {
  return (
    <div className="max-w-3xl space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight">How the numbers were kept honest</h1>
      {SECTIONS.map((section) => (
        <section key={section.title} className="space-y-2">
          <h2 className="text-lg font-semibold">{section.title}</h2>
          {section.body.map((p) => (
            <p key={p.slice(0, 32)} className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              {p}
            </p>
          ))}
        </section>
      ))}
    </div>
  );
}

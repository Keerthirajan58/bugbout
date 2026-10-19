import { notFound } from "next/navigation";
import { GateTable, SelectionTable } from "@/components/Scoreboards";
import { TaskTable } from "@/components/TaskTable";
import { SUITE_LABEL, SUITES, getBundle, isSuite } from "@/lib/data";

export function generateStaticParams() {
  return SUITES.map((suite) => ({ suite }));
}

const ABOUT = {
  test: "Bugs from sqlparse and tomlkit, mined after the design was frozen. No rule, threshold or prompt was chosen while looking at them.",
  dev: "Bugs from marshmallow, attrs and arrow, used to design and tune the system. Numbers here are in-sample.",
};

export default async function SuitePage({ params }: { params: Promise<{ suite: string }> }) {
  const { suite } = await params;
  if (!isSuite(suite)) notFound();
  const bundle = getBundle(suite);

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {SUITE_LABEL[suite]} · {bundle.scoreboard.tasks} real bugs
        </h1>
        <p className="max-w-3xl text-sm" style={{ color: "var(--text-secondary)" }}>
          {ABOUT[suite]} Judge policy <code>{bundle.policy}</code>.
        </p>
      </header>
      <section className="grid gap-8 lg:grid-cols-2">
        <div className="space-y-3">
          <h2 className="text-lg font-semibold">Gating each patch</h2>
          <GateTable gate={bundle.gate} />
        </div>
        <div className="space-y-3">
          <h2 className="text-lg font-semibold">Choosing a patch per bug</h2>
          <SelectionTable board={bundle.scoreboard} />
        </div>
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Every bug</h2>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          ✓ / ✗: whether the agent&apos;s patch passes the maintainers&apos; hidden tests. Open a bug to see every
          patch, every examiner&apos;s test and how each ran.
        </p>
        <TaskTable suite={suite} tasks={bundle.tasks} />
      </section>
    </div>
  );
}

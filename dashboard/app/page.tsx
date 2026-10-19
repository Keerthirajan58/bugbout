import Link from "next/link";
import { Pipeline } from "@/components/Pipeline";
import { GateTable, SelectionTable } from "@/components/Scoreboards";
import { SignalChart } from "@/components/SignalChart";
import { StatTile } from "@/components/StatTile";
import { getBundle, shortName } from "@/lib/data";

export default function Home() {
  const test = getBundle("test");
  const { scoreboard: board, blindness, signals } = test;
  const solver = signals.find((s) => !s.source.startsWith("examiner") && !s.source.startsWith("the repository"));
  const blind = signals.filter((s) => s.source.startsWith("examiner"));
  const blindRange = blind.length
    ? `${Math.min(...blind.map((s) => s.auc)).toFixed(2)}-${Math.max(...blind.map((s) => s.auc)).toFixed(2)}`
    : "-";
  const alone = Object.entries(board.per_entrant)
    .map(([name, s]) => `${shortName(name)} ${s.resolved}/${board.tasks}`)
    .join(" · ");

  return (
    <div className="space-y-12">
      <section className="space-y-4">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Can coding agents check each other&apos;s work?</h1>
        <p className="max-w-3xl text-base" style={{ color: "var(--text-secondary)" }}>
          Verdict races off-the-shelf coding agents on bugs reconstructed from real repositories, has independent
          agents write the tests a fix must pass - without ever seeing a patch - and judges from executed tests
          alone. Everything below is scored against the maintainers&apos; own hidden tests, on {board.tasks} bugs the
          system was never tuned on.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Agent patches that were wrong"
          value={`${blindness.wrong}/${blindness.patches}`}
          detail="every attempt ended with the agent reporting success"
        />
        <StatTile
          label="Wrong patches that pass the issue's tests"
          value={`${blindness.wrong_passing_every_reproduction}/${blindness.wrong_scored}`}
          detail="they fix the symptom described, not the bug"
        />
        <StatTile
          label="Best evidence, from whoever solved it"
          value={solver ? solver.auc.toFixed(2) : "-"}
          detail={`AUC of the solver's own tests; blind examiners ${blindRange}`}
        />
        <StatTile
          label="Verdict's pick resolved the bug"
          value={`${board.verdict_resolved}/${board.tasks}`}
          detail={`each agent alone: ${alone}`}
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">How much can each kind of test tell?</h2>
        <p className="max-w-3xl text-sm" style={{ color: "var(--text-secondary)" }}>
          Tests written from the issue alone sit close to a coin flip at telling a correct patch from a wrong one.
          The most informative tests came from the agent that had actually fixed the bug: verification is bounded by
          the verifier&apos;s ability to solve the problem.
        </p>
        <SignalChart signals={signals} />
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">How it works</h2>
        <Pipeline />
      </section>

      <section className="grid gap-8 lg:grid-cols-2">
        <div className="space-y-3">
          <h2 className="text-xl font-semibold">Gating a patch</h2>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            An agent says it is done. May its patch merge?
          </p>
          <GateTable gate={test.gate} />
        </div>
        <div className="space-y-3">
          <h2 className="text-xl font-semibold">Choosing a patch</h2>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Several agents fixed the same issue. Which one ships?
          </p>
          <SelectionTable board={board} />
        </div>
      </section>

      <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
        Every bug, patch and test is browsable:{" "}
        <Link href="/suite/test" className="underline underline-offset-4">
          held-out suite
        </Link>{" "}
        ·{" "}
        <Link href="/suite/dev" className="underline underline-offset-4">
          development suite
        </Link>{" "}
        ·{" "}
        <Link href="/method" className="underline underline-offset-4">
          how the numbers were kept honest
        </Link>
      </p>
    </div>
  );
}

import Link from "next/link";
import { Pipeline } from "@/components/Pipeline";
import { GateTable, SelectionTable } from "@/components/Scoreboards";
import { StatTile } from "@/components/StatTile";
import { getBundle, percent, shortName } from "@/lib/data";

export default function Home() {
  const test = getBundle("test");
  const { scoreboard: board, gate } = test;
  const wrongDone = gate.approved_wrong + gate.rejected_wrong + gate.unverified_wrong;
  const allDone = wrongDone + gate.approved_correct + gate.rejected_correct + gate.unverified_correct;

  return (
    <div className="space-y-12">
      <section className="space-y-4">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Several coding agents fix the same real bug.
          <br />
          Other agents write the tests that judge them.
        </h1>
        <p className="max-w-3xl text-base" style={{ color: "var(--text-secondary)" }}>
          Verdict orchestrates off-the-shelf coding agents on bugs reconstructed from real repositories, has
          independent examiner agents write the tests a maintainer would demand - without ever seeing a patch -
          and lets executed tests, not a model&apos;s opinion, decide which patch may merge. Every result below is
          scored against the maintainers&apos; own hidden tests, on {board.tasks} bugs the system was never tuned on.
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Agent patches that were wrong"
          value={`${wrongDone}/${allDone}`}
          detail="every one reported as done by its agent"
        />
        <StatTile
          label="Approved patches that were correct"
          value={percent(gate.precision)}
          detail={`vs ${percent(gate.base_rate)} if you merge everything`}
        />
        <StatTile
          label="Wrong patches caught"
          value={percent(gate.catch_rate)}
          detail={`${percent(gate.false_alarm_rate)} of correct patches rejected`}
        />
        <StatTile
          label="Verdict's pick resolved the bug"
          value={`${board.verdict_resolved}/${board.tasks}`}
          detail={`best single agent ${board.best_single.resolved}/${board.tasks} (${shortName(board.best_single.entrant)})`}
        />
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
          <GateTable gate={gate} />
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
        Every task, patch and test is browsable:{" "}
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

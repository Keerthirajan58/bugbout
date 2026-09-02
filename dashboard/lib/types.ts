/** Shapes emitted by `precedent eval export`. Mirrors src/precedent/eval/export.py. */

export type Arm = "no_retrieval" | "with_retrieval";

export interface RetrievedItem {
  doc_id: string;
  kind: "incident" | "revert" | "call_site" | "code_chunk";
  citation: string;
  title: string;
  module_path: string | null;
  symbols: string[];
  score: number;
  /** Why this ranked where it did: bm25 rank, vector rank, and any scope boosts. */
  why: string;
  url?: string | null;
}

export interface Case {
  mutantId: string;
  arm: Arm;
  target: string;
  modulePath: string;
  function: string | null;
  operator: string;
  family: string;
  diff: string;
  verdict: string;
  caught: boolean;
  reason: string;
  hypothesis: string;
  reasoning: string;
  testName: string;
  testCode: string;
  confident: boolean;
  repairs: number;
  /** Candidate tests rejected for failing against the unmutated code. */
  gateFailures?: number;
  /** The Prosecutor's stated prediction for each side of the diff. */
  originalBehaviour?: string;
  changedBehaviour?: string;
  model: string;
  reviewReason: string | null;
  citation: string | null;
  retrieved: RetrievedItem[];
}

export interface ArmStats {
  total: number;
  caught: number;
  catchRate: number;
  executionConfirmed?: number;
  executionRate?: number;
  byVerdict: Record<string, number>;
  model: string;
  corpusHash: string;
}

export interface MutationStats {
  total: number;
  killRate: number;
  survivors: number;
  operators: number;
}

export interface Target {
  slug: string;
  fullName: string;
  ref: string;
  commit: string;
  license: string;
  rationale: string;
  mutation?: MutationStats;
  arms: Partial<Record<Arm, ArmStats>>;
}

export interface Results {
  targets: Target[];
  cases: Case[];
  /** Present once the real-diff study has been run. */
  realDiffs?: RealDiffStudy;
}

/** One reconstructed historical bug, and what an agent did with it. */
export interface RealDiffTask {
  taskId: string;
  target: string;
  reference: number;
  referenceKind: string;
  title: string;
  problemStatement: string;
  baseCommit: string;
  oracleTests: string[];
  changedFiles: string[];
  patch: string;
  /** Did the maintainer's own test pass against the agent's diff? */
  oracle: string | null;
  /** Did the suite as it stood at the base commit still pass? */
  regression: string | null;
  resolved: boolean;
  auditVerdict: string | null;
  flagged: boolean;
  hypothesis: string;
  testCode: string;
  originalBehaviour: string;
  changedBehaviour: string;
  citation: string | null;
  retrieved: RetrievedItem[];
  auditReason: string;
}

export interface RealDiffStudy {
  tasks: RealDiffTask[];
  arm: string | null;
}

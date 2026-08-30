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
}

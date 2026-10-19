/**
 * The shape of a suite bundle, as written by `verdict arena export`.
 * Every value here was produced by a run; the dashboard only displays it.
 */

export type Outcome = "passed" | "failed" | "error" | "skipped" | "absent";
export type Kind = "agentic" | "prompted" | "self";
export type SuiteName = "dev" | "test";

export interface EntrantStats {
  resolved: number;
  fixed_bug: number;
  broke_tests: number;
}

export interface Scoreboard {
  tasks: number;
  per_entrant: Record<string, EntrantStats>;
  best_single: { entrant: string; resolved: number };
  random_pick: number;
  verdict_resolved: number;
  verdict_picks: number;
  wrong_picks: number;
  abstained: number;
  abstained_correctly: number;
  best_possible: number;
}

export interface Gate {
  approved_correct: number;
  approved_wrong: number;
  rejected_correct: number;
  rejected_wrong: number;
  unverified_correct: number;
  unverified_wrong: number;
  precision: number;
  base_rate: number;
  catch_rate: number;
  false_alarm_rate: number;
}

export interface Standing {
  support: number | null;
  judges: string[];
  reproductions_passed: number;
  reproductions_total: number;
  breaks: string[];
  breaks_total: number;
  lines_changed: number;
  invariant_failures: number;
  approved: boolean;
}

export interface Candidate {
  entrant: string;
  vendor: string;
  diff: string;
  status: string;
  seconds: number;
  summary: string;
  resolved: boolean;
  fixed_bug: boolean;
  broke: string[];
  standing: Standing | null;
}

export interface EvidenceTest {
  id: string;
  base: Outcome;
  trees: Record<string, Outcome>;
}

export interface Evidence {
  author: string;
  vendor: string;
  kind: Kind;
  code: string;
  reproduces: boolean;
  tests: EvidenceTest[];
}

export interface Task {
  task_id: string;
  repo: string;
  target: string;
  reference: number;
  url: string;
  title: string;
  statement: string;
  oracle: { fail_to_pass: number; pass_to_pass: number };
  candidates: Candidate[];
  evidence: Evidence[];
  verdict: {
    chosen: string | null;
    reason: string;
    chosen_resolved: boolean;
    resolved_by: string[];
  };
}

export interface Participant {
  name: string;
  vendor: string;
  [detail: string]: unknown;
}

export interface Bundle {
  suite: SuiteName;
  policy: string;
  generated_at: string;
  scoreboard: Scoreboard;
  gate: Gate;
  entrants: Participant[];
  examiners: Participant[];
  tasks: Task[];
}

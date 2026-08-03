import type { EvaluationPhase, ResearchPhase, ResearchRoute, ResearchRunStatus } from "@/types/deepResearch";

export const ROUTE_LABELS: Record<ResearchRoute, string> = {
  accept: "Accept",
  targeted_repair: "Targeted repair",
  partial_replan: "Partial replan",
  full_replan: "Full replan",
  stop_incomplete: "Stop incomplete",
};

export const STATUS_LABELS: Record<ResearchRunStatus, string> = {
  running: "Running",
  interrupted: "Interrupted",
  completed: "Accepted",
  incomplete: "Incomplete",
  failed: "Failed",
};

export const EVALUATION_LABELS: Record<EvaluationPhase, string> = {
  pre_synthesis: "Evidence readiness",
  post_synthesis: "Claim audit",
};

export const PHASE_LABELS: Record<ResearchPhase, string> = {
  validating: "Validating research contract",
  planning: "Planning investigation",
  executing: "Researching subquestions",
  pre_synthesis_evaluation: "Evaluating evidence readiness",
  routing: "Selecting deterministic route",
  targeted_repair: "Repairing targeted gaps",
  partial_replan: "Replanning affected scope",
  full_replan: "Rebuilding research plan",
  synthesizing: "Synthesizing candidate report",
  post_synthesis_evaluation: "Auditing report claims",
  report_revision: "Revising candidate report",
  finalizing: "Finalizing terminal decision",
};

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "Not recorded";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function shortId(value: string, visible = 8): string {
  return value.length > visible * 2 + 1
    ? `${value.slice(0, visible)}…${value.slice(-visible)}`
    : value;
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ").replace(/^\w/, (letter) => letter.toUpperCase());
}

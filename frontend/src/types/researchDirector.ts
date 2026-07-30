/**
 * Research Director is a planning and review surface. It deliberately stops at
 * handoff: code changes, builds, experiments, and result verification happen in
 * an external execution system and must never be inferred from these statuses.
 */

export type ResearchPlanStatus =
  | "draft"
  | "reviewed"
  | "approved"
  | "superseded"
  | "handed_off";

export type ExternalExecutionStatus = "awaiting_external_execution";

export type ResearchDirectorArtifact =
  | "brief"
  | "plan"
  | "hypotheses"
  | "implementation"
  | "review";

export type ResearchDirectorRequestStatus =
  | "idle"
  | "loading"
  | "generating"
  | "reviewing"
  | "revising"
  | "approving"
  | "preparing_handoff"
  | "confirming_handoff";

export type ReviewIssueSeverity = "blocker" | "major" | "minor";
export type ReviewIssueStatus = "open" | "addressed" | "accepted_risk";

export type ImplementationTaskStatus =
  | "planned"
  | "ready_for_handoff"
  | "blocked";

export interface ResearchSourcePolicy {
  use_workspace_sources: boolean;
  discover_external_sources: boolean;
  prefer_primary_sources: boolean;
  time_horizon: string;
  must_include: string[];
  must_exclude: string[];
}

export interface ResearchBrief {
  title: string;
  research_question: string;
  objective: string;
  problem_statement: string;
  intended_contribution: string;
  scope: string;
  success_criteria: string[];
  constraints: string[];
  desired_deliverables: string[];
  source_policy: ResearchSourcePolicy;
  notes: string;
}

export interface ResearchContract {
  title: string;
  research_question: string;
  objective: string;
  scope_inclusions: string[];
  scope_exclusions: string[];
  constraints: string[];
  assumptions: string[];
  unknowns: string[];
  success_criteria: string[];
  failure_criteria: string[];
  allowed_sources: string[];
  excluded_sources: string[];
  required_deliverables: string[];
  human_decisions_required: string[];
}

export interface EvidenceReference {
  id: string;
  source_id?: string | null;
  source_title: string;
  source_type?: string | null;
  authors?: string[];
  year?: number | null;
  passage?: string | null;
  locator?: string | null;
  url?: string | null;
  relationship: "supports" | "refutes" | "conflicts" | "unknown" | "context";
}

export interface EvidenceClaim {
  id: string;
  claim: string;
  evidence_status: "supported" | "conflicting" | "insufficient";
  evidence_refs: EvidenceReference[];
  uncertainty?: string | null;
}

export interface ResearchGap {
  id: string;
  title: string;
  description: string;
  evidence_refs: string[];
  impact: string;
  testability: "high" | "medium" | "low";
  novelty_confidence: "high" | "medium" | "low";
  unresolved_questions: string[];
}

export interface ResearchHypothesis {
  id: string;
  title: string;
  statement: string;
  rationale: string;
  falsifiable_predictions: string[];
  strongest_counterargument: string;
  minimum_validation: string[];
  differentiation_from_prior_work: string;
  risks: string[];
  evidence_refs: string[];
  dependencies: string[];
  status: "proposed" | "evidence_backed";
}

export interface MethodCandidate {
  id: string;
  title: string;
  summary: string;
  addresses_hypothesis_ids: string[];
  components: string[];
  procedure: string[];
  interfaces_or_boundaries: string[];
  assumptions: string[];
  alternatives_considered: Array<{
    title: string;
    description: string;
    rejection_reason: string;
    reconsider_when: string | null;
  }>;
  selection_rationale: string;
  risks: string[];
  execution_status: ExternalExecutionStatus;
}

export interface ExperimentDesign {
  id: string;
  title: string;
  purpose: string;
  hypothesis_ids: string[];
  method_id: string;
  datasets: Array<{
    name: string;
    purpose: string;
    split_or_sampling: string;
    access_or_license_notes: string | null;
    leakage_checks: string[];
  }>;
  baselines: string[];
  metrics: Array<{
    name: string;
    definition: string;
    direction: string;
    success_threshold: string | null;
  }>;
  controls: string[];
  ablations: string[];
  negative_tests: string[];
  statistical_plan: string;
  seeds_or_repetitions: string;
  stop_conditions: string[];
  expected_artifacts: string[];
  acceptance_criteria: string[];
  risks: string[];
  execution_status: ExternalExecutionStatus;
}

export interface PlanArtifactSection {
  id: string;
  title: string;
  summary?: string | null;
  content: string;
  evidence_refs: string[];
  status: "draft" | "evidence_backed" | "needs_attention";
}

export interface ImplementationTask {
  id: string;
  title: string;
  objective: string;
  tasks: string[];
  inputs: string[];
  outputs: string[];
  interface_contracts: Array<{
    name: string;
    inputs: string[];
    outputs: string[];
    invariants: string[];
  }>;
  deliverable: string;
  dependencies: string[];
  acceptance_criteria: string[];
  risks: string[];
  owner_role: string;
  effort_estimate: string | null;
  suggested_owner: "human" | "coding_agent" | "external_team";
  status: ImplementationTaskStatus;
}

export interface ImplementationPlan {
  objective: string;
  summary: string;
  tasks: ImplementationTask[];
  milestones: Array<{
    id: string;
    title: string;
    task_ids: string[];
    exit_criteria: string[];
  }>;
  resource_assumptions: string[];
  fallback_strategies: string[];
  handoff_instructions: string[];
  handoff: {
    target_roles: string[];
    prerequisites: string[];
    included_artifacts: string[];
    execution_instructions: string[];
    external_result_contract: string[];
    human_approval_required: boolean;
    status: "not_handed_off" | "handed_off";
  };
  unresolved_decisions: string[];
  execution_status: ExternalExecutionStatus;
}

export interface ReviewIssue {
  id: string;
  severity: ReviewIssueSeverity;
  artifact: string;
  problem: string;
  evidence?: string | null;
  impact: string;
  required_fix: string;
  status: ReviewIssueStatus;
}

export interface ResearchPlanReview {
  id: string;
  research_project_id: string;
  research_plan_version_id: string;
  review_round: number;
  status: "draft" | "reviewed";
  verdict: "approve" | "revise" | "blocked";
  summary: string;
  perspectives: string[];
  issues: ReviewIssue[];
  created_at: string;
  updated_at: string;
}

export interface ResearchProject {
  id: string;
  workspace_id: string;
  title: string;
  objective: string;
  brief_snapshot: ResearchBrief;
  brief_snapshot_source: "submitted_snapshot" | "legacy_contract_fallback";
  status: ResearchPlanStatus;
  created_at: string;
  updated_at: string;
}

export interface ResearchPlanBundle {
  id: string;
  research_project_id: string;
  workspace_id: string;
  version_number: number;
  status: ResearchPlanStatus;
  execution_status: ExternalExecutionStatus;
  research_brief: ResearchBrief;
  research_brief_source: "submitted_snapshot" | "legacy_contract_fallback";
  contract: ResearchContract;
  artifact_sections: PlanArtifactSection[];
  evidence_catalog: EvidenceReference[];
  evidence_claims: EvidenceClaim[];
  gaps: ResearchGap[];
  hypotheses: ResearchHypothesis[];
  methods: MethodCandidate[];
  experiments: ExperimentDesign[];
  implementation_plan: ImplementationPlan;
  generation_warnings: string[];
  review?: ResearchPlanReview | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchHandoffBundle {
  id: string;
  research_project_id: string;
  research_plan_version_id: string;
  version_number: number;
  status: "ready_for_handoff" | "handed_off";
  execution_status: ExternalExecutionStatus;
  title: string;
  summary: string;
  implementation_plan: ImplementationPlan;
  open_risks: string[];
  external_instructions: string[];
  /** Exact frozen content returned by the server; never reconstructed client-side. */
  server_content: Readonly<Record<string, unknown>>;
  created_at: string;
}

export interface GenerateResearchPlanRequest {
  workspace_id: string;
  research_brief: ResearchBrief;
  contract?: Partial<ResearchContract> | null;
  evidence: EvidenceReference[];
  evidence_warnings?: string[];
  constraints: string[];
  desired_deliverables: string[];
  notes?: string | null;
}

export interface ReviewResearchPlanRequest {
  workspace_id: string;
  plan: ResearchPlanBundle;
  evidence: EvidenceReference[];
  perspectives: string[];
  review_instructions?: string | null;
}

export interface ReviseResearchPlanRequest {
  workspace_id: string;
  plan: ResearchPlanBundle;
  review: ResearchPlanReview;
  evidence: EvidenceReference[];
  evidence_warnings?: string[];
  revision_instructions?: string | null;
}

export interface ResearchDirectorSnapshot {
  project: ResearchProject;
  plan: ResearchPlanBundle;
  review: ResearchPlanReview | null;
  handoff: ResearchHandoffBundle | null;
}

/**
 * The backend is authoritative for every lifecycle transition. The adapter
 * exposes UI view models while preserving persisted project and version IDs.
 */
export interface ResearchDirectorApi {
  loadLatestProject(workspaceId: string): Promise<ResearchDirectorSnapshot | null>;
  generatePlan(request: GenerateResearchPlanRequest): Promise<ResearchPlanBundle>;
  reviewPlan(request: ReviewResearchPlanRequest): Promise<ResearchPlanReview>;
  revisePlan(request: ReviseResearchPlanRequest): Promise<ResearchPlanBundle>;
  approvePlan(plan: ResearchPlanBundle): Promise<ResearchPlanBundle>;
  prepareHandoff(plan: ResearchPlanBundle): Promise<ResearchHandoffBundle>;
  confirmHandoff(plan: ResearchPlanBundle): Promise<ResearchHandoffBundle>;
}

export const DEFAULT_REVIEW_PERSPECTIVES = [
  "evidence",
  "novelty",
  "method",
  "experiment",
  "statistics",
  "implementation",
  "risk",
  "execution_boundary",
] as const;

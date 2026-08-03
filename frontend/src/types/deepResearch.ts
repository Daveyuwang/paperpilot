export const DEEP_RESEARCH_EVENT_SCHEMA = "deep-research-event.v1" as const;

export type ResearchRoute =
  | "accept"
  | "targeted_repair"
  | "partial_replan"
  | "full_replan"
  | "stop_incomplete";

export type EvaluationPhase = "pre_synthesis" | "post_synthesis";

export type ResearchPhase =
  | "validating"
  | "planning"
  | "executing"
  | "pre_synthesis_evaluation"
  | "routing"
  | "targeted_repair"
  | "partial_replan"
  | "full_replan"
  | "synthesizing"
  | "post_synthesis_evaluation"
  | "report_revision"
  | "finalizing";

export type ResearchRunStatus =
  | "running"
  | "interrupted"
  | "completed"
  | "incomplete"
  | "failed";

export type ResearchArtifactKind =
  | "plan"
  | "sub_report"
  | "pre_synthesis_evaluation"
  | "controller_transition"
  | "report_candidate"
  | "post_synthesis_evaluation"
  | "terminal_decision";

export type SubQuestionStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed"
  | "superseded";

export interface BudgetSnapshot {
  pre_evaluations_used: number;
  targeted_repairs_used: number;
  partial_replans_used: number;
  full_replans_used: number;
  total_recoveries_used: number;
  post_evaluations_used: number;
  synthesis_repairs_used: number;
  pre_evaluation_limit: number;
  targeted_repair_limit: number;
  partial_replan_limit: number;
  full_replan_limit: number;
  total_recovery_limit: number;
  post_evaluation_limit: number;
  synthesis_repair_limit: number;
}

export const EMPTY_BUDGET: BudgetSnapshot = {
  pre_evaluations_used: 0,
  targeted_repairs_used: 0,
  partial_replans_used: 0,
  full_replans_used: 0,
  total_recoveries_used: 0,
  post_evaluations_used: 0,
  synthesis_repairs_used: 0,
  pre_evaluation_limit: 5,
  targeted_repair_limit: 2,
  partial_replan_limit: 1,
  full_replan_limit: 1,
  total_recovery_limit: 4,
  post_evaluation_limit: 4,
  synthesis_repair_limit: 2,
};

export interface ResumeCapability {
  allowed: boolean;
  checkpoint_id: string | null;
  reason_code: string;
  reason: string;
}

export const RESUME_UNAVAILABLE: ResumeCapability = {
  allowed: false,
  checkpoint_id: null,
  reason_code: "not_available",
  reason: "No restorable server checkpoint is available for this run.",
};

export interface ArtifactVersionRef {
  id: string;
  run_id: string;
  artifact_kind: ResearchArtifactKind;
  logical_artifact_id: string;
  version_number: number;
  plan_version: number;
  controller_cycle: number;
  schema_version: number;
  parent_version_id: string | null;
  source_checkpoint_id: string | null;
  content_hash: string;
  created_at: string;
  payload?: Record<string, unknown>;
}

export interface CheckpointSummary {
  checkpoint_id: string;
  graph_version: string;
  restorable: boolean;
  saved_at: string;
  next_nodes: string[];
}

export interface ResearchSubQuestion {
  id: string;
  question: string;
  priority: number;
  order: number;
  plan_version: number;
  origin: "initial" | "targeted_repair" | "partial_replan" | "full_replan";
  status: SubQuestionStatus;
  attempt: number;
  confidence: number | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  sub_report_artifact_version_id: string | null;
}

export interface EvaluationIssueSummary {
  id: string;
  category: string;
  severity: "minor" | "major" | "blocker";
  suggested_repair_stage: string | null;
  affected_sub_question_ids: string[];
  claim_ids: string[];
  segment_ids: string[];
}

export interface EvaluationSubject {
  kind: "corpus" | "report";
  digest: string;
  version: number;
}

export interface EvaluationSummary {
  evaluation_id: string;
  round_id: string;
  phase: EvaluationPhase;
  status: "running" | "completed" | "failed";
  subject: EvaluationSubject;
  evaluator_model: string;
  attempts: number;
  duration_ms: number | null;
  scores: Record<string, number>;
  issues: EvaluationIssueSummary[];
  summary: string | null;
  error_code: string | null;
  artifact_version_id: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface RouteDecisionSummary {
  decision_id: string;
  round_id: string;
  evaluation_id: string;
  phase: EvaluationPhase;
  route: ResearchRoute;
  repair_stage: string | null;
  weighted_overall_score: number | null;
  reason_code: string;
  reason: string;
  target_sub_question_ids: string[];
  target_report_segment_ids: string[];
  artifact_version_id: string | null;
  selected_at: string;
}

export interface RepairSummary {
  phase: ResearchPhase;
  status: "running" | "completed" | "failed";
  label: string;
  target_sub_question_ids: string[];
  target_report_segment_ids: string[];
  output_artifact_version_ids: string[];
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface DecisionRound {
  id: string;
  cycle: number;
  phase: EvaluationPhase;
  plan_version: number;
  corpus_version: number;
  report_version: number | null;
  evaluation: EvaluationSummary | null;
  route: RouteDecisionSummary | null;
  repair: RepairSummary | null;
}

export interface ReportSegmentProgress {
  segment_id: string;
  title: string;
  status: "pending" | "writing" | "completed" | "failed";
  report_version: number;
  duration_ms: number | null;
  artifact_version_id: string | null;
}

export interface TerminalOutcome {
  status: Exclude<ResearchRunStatus, "running">;
  report_accepted: boolean;
  publishable: boolean;
  terminal_reason_code: string | null;
  terminal_reason: string | null;
  candidate_artifact_version_id: string | null;
  final_artifact_version_id: string | null;
  deliverable_id: string | null;
  result: Record<string, unknown> | null;
  finished_at: string;
}

export interface RunStartedPayload {
  workspace_id: string;
  topic: string;
  graph_version: string;
  status: "running";
  budget: BudgetSnapshot;
  resume: ResumeCapability;
}

export interface PhaseEventPayload {
  phase: ResearchPhase;
  node: string;
  label: string;
  round_id: string | null;
  evaluation_phase: EvaluationPhase | null;
  target_sub_question_ids: string[];
  target_report_segment_ids: string[];
  output_artifact_version_ids: string[];
  duration_ms: number | null;
  status?: "completed" | "failed";
}

export interface SubQuestionUpsertedPayload {
  sub_question: ResearchSubQuestion;
}

export interface SubQuestionProgressedPayload {
  sub_question_id: string;
  status: SubQuestionStatus;
  attempt: number;
  confidence: number | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  sub_report_artifact_version_id: string | null;
}

export interface EvaluationStartedPayload {
  evaluation_id: string;
  round_id: string;
  phase: EvaluationPhase;
  subject: EvaluationSubject;
  evaluator_model: string;
}

export interface EvaluationCompletedPayload {
  evaluation_id: string;
  round_id: string;
  phase: EvaluationPhase;
  status: "completed" | "failed";
  subject: EvaluationSubject;
  evaluator_model: string;
  attempts: number;
  duration_ms: number;
  scores: Record<string, number>;
  issues: EvaluationIssueSummary[];
  summary: string | null;
  error_code: string | null;
  artifact_version_id: string | null;
}

export interface RouteSelectedPayload {
  decision_id: string;
  round_id: string;
  evaluation_id: string;
  phase: EvaluationPhase;
  route: ResearchRoute;
  repair_stage: string | null;
  weighted_overall_score: number | null;
  reason_code: string;
  reason: string;
  target_sub_question_ids: string[];
  target_report_segment_ids: string[];
  budget: BudgetSnapshot;
  artifact_version_id: string | null;
}

export interface ArtifactVersionCreatedPayload {
  artifact: ArtifactVersionRef;
}

export interface CheckpointSavedPayload {
  checkpoint: CheckpointSummary;
  resume: ResumeCapability;
}

export interface BudgetUpdatedPayload {
  budget: BudgetSnapshot;
  cause: string;
}

export interface SynthesisSectionUpdatedPayload {
  segment: ReportSegmentProgress;
}

export interface RunFinishedPayload {
  status: Exclude<ResearchRunStatus, "running">;
  report_accepted: boolean;
  publishable: boolean;
  terminal_reason_code: string | null;
  terminal_reason: string | null;
  candidate_artifact_version_id: string | null;
  final_artifact_version_id: string | null;
  deliverable_id: string | null;
  result: Record<string, unknown> | null;
  resume: ResumeCapability;
}

export interface ProtocolErrorPayload {
  code: string;
  message: string;
  recoverable: boolean;
  last_good_seq: number;
}

export type DeepResearchEventType =
  | "run_started"
  | "phase_started"
  | "phase_completed"
  | "subquestion_upserted"
  | "subquestion_progressed"
  | "evaluation_started"
  | "evaluation_completed"
  | "route_selected"
  | "artifact_version_created"
  | "checkpoint_saved"
  | "budget_updated"
  | "synthesis_section_updated"
  | "run_finished"
  | "protocol_error";

export interface DeepResearchEventEnvelope<
  T extends DeepResearchEventType,
  P,
> {
  schema_version: typeof DEEP_RESEARCH_EVENT_SCHEMA;
  event_id: string;
  seq: number;
  type: T;
  run_id: string;
  emitted_at: string;
  cycle: number;
  plan_version: number;
  corpus_version: number;
  report_version: number | null;
  checkpoint_id: string | null;
  payload: P;
}

export type DeepResearchRunEvent =
  | DeepResearchEventEnvelope<"run_started", RunStartedPayload>
  | DeepResearchEventEnvelope<"phase_started", PhaseEventPayload>
  | DeepResearchEventEnvelope<"phase_completed", PhaseEventPayload>
  | DeepResearchEventEnvelope<"subquestion_upserted", SubQuestionUpsertedPayload>
  | DeepResearchEventEnvelope<"subquestion_progressed", SubQuestionProgressedPayload>
  | DeepResearchEventEnvelope<"evaluation_started", EvaluationStartedPayload>
  | DeepResearchEventEnvelope<"evaluation_completed", EvaluationCompletedPayload>
  | DeepResearchEventEnvelope<"route_selected", RouteSelectedPayload>
  | DeepResearchEventEnvelope<"artifact_version_created", ArtifactVersionCreatedPayload>
  | DeepResearchEventEnvelope<"checkpoint_saved", CheckpointSavedPayload>
  | DeepResearchEventEnvelope<"budget_updated", BudgetUpdatedPayload>
  | DeepResearchEventEnvelope<"synthesis_section_updated", SynthesisSectionUpdatedPayload>
  | DeepResearchEventEnvelope<"run_finished", RunFinishedPayload>
  | DeepResearchEventEnvelope<"protocol_error", ProtocolErrorPayload>;

export interface DeepResearchRunSnapshot {
  schema_version: "deep-research-run.v1";
  run_id: string;
  workspace_id: string;
  topic: string;
  status: ResearchRunStatus;
  graph_version: string;
  current_phase: ResearchPhase | null;
  snapshot_seq: number;
  last_event_id: string | null;
  plan_version: number;
  corpus_version: number;
  report_version: number | null;
  budget: BudgetSnapshot;
  sub_questions: ResearchSubQuestion[];
  decision_rounds: DecisionRound[];
  artifacts: ArtifactVersionRef[];
  report_segments: ReportSegmentProgress[];
  latest_checkpoint: CheckpointSummary | null;
  resume: ResumeCapability;
  terminal: TerminalOutcome | null;
  created_at: string;
  updated_at: string;
}

export class DeepResearchProtocolError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly raw?: unknown,
  ) {
    super(message);
    this.name = "DeepResearchProtocolError";
  }
}

type UnknownRecord = Record<string, unknown>;

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasString(value: UnknownRecord, key: string): boolean {
  return typeof value[key] === "string";
}

function hasNonEmptyString(value: UnknownRecord, key: string): boolean {
  return typeof value[key] === "string" && (value[key] as string).trim().length > 0;
}

function hasNullableString(value: UnknownRecord, key: string): boolean {
  return value[key] === null || typeof value[key] === "string";
}

function hasFiniteNumber(value: UnknownRecord, key: string): boolean {
  return typeof value[key] === "number" && Number.isFinite(value[key]);
}

function hasNonNegativeNumber(value: UnknownRecord, key: string): boolean {
  return hasFiniteNumber(value, key) && (value[key] as number) >= 0;
}

function hasNullableNonNegativeNumber(value: UnknownRecord, key: string): boolean {
  return value[key] === null || hasNonNegativeNumber(value, key);
}

function hasNonNegativeInteger(value: UnknownRecord, key: string): boolean {
  return hasNonNegativeNumber(value, key) && Number.isInteger(value[key]);
}

function hasPositiveInteger(value: UnknownRecord, key: string): boolean {
  return hasNonNegativeInteger(value, key) && (value[key] as number) >= 1;
}

function hasNullablePositiveInteger(value: UnknownRecord, key: string): boolean {
  return value[key] === null || hasPositiveInteger(value, key);
}

function hasStringArray(value: UnknownRecord, key: string): boolean {
  return Array.isArray(value[key]) && (value[key] as unknown[]).every((item) => typeof item === "string");
}

const EVENT_TYPES = new Set<DeepResearchEventType>([
  "run_started",
  "phase_started",
  "phase_completed",
  "subquestion_upserted",
  "subquestion_progressed",
  "evaluation_started",
  "evaluation_completed",
  "route_selected",
  "artifact_version_created",
  "checkpoint_saved",
  "budget_updated",
  "synthesis_section_updated",
  "run_finished",
  "protocol_error",
]);

const ROUTES = new Set<ResearchRoute>([
  "accept",
  "targeted_repair",
  "partial_replan",
  "full_replan",
  "stop_incomplete",
]);

const RESEARCH_PHASES = new Set<ResearchPhase>([
  "validating",
  "planning",
  "executing",
  "pre_synthesis_evaluation",
  "routing",
  "targeted_repair",
  "partial_replan",
  "full_replan",
  "synthesizing",
  "post_synthesis_evaluation",
  "report_revision",
  "finalizing",
]);

const EVALUATION_PHASES = new Set<EvaluationPhase>([
  "pre_synthesis",
  "post_synthesis",
]);

const ARTIFACT_KINDS = new Set<ResearchArtifactKind>([
  "plan",
  "sub_report",
  "pre_synthesis_evaluation",
  "controller_transition",
  "report_candidate",
  "post_synthesis_evaluation",
  "terminal_decision",
]);

const SUBQUESTION_STATUSES = new Set<SubQuestionStatus>([
  "pending",
  "in_progress",
  "completed",
  "failed",
  "superseded",
]);

const SUBQUESTION_ORIGINS = new Set<ResearchSubQuestion["origin"]>([
  "initial",
  "targeted_repair",
  "partial_replan",
  "full_replan",
]);

const SEGMENT_STATUSES = new Set<ReportSegmentProgress["status"]>([
  "pending",
  "writing",
  "completed",
  "failed",
]);

const RUN_STATUSES = new Set<ResearchRunStatus>([
  "running",
  "interrupted",
  "completed",
  "incomplete",
  "failed",
]);

function assertResume(value: unknown): asserts value is ResumeCapability {
  if (!isRecord(value) || typeof value.allowed !== "boolean" || !hasNullableString(value, "checkpoint_id") || !hasNonEmptyString(value, "reason_code") || !hasNonEmptyString(value, "reason")) {
    throw new DeepResearchProtocolError("Invalid resume capability", "invalid_resume", value);
  }
}

function assertBudget(value: unknown): asserts value is BudgetSnapshot {
  if (!isRecord(value)) {
    throw new DeepResearchProtocolError("Invalid budget snapshot", "invalid_budget", value);
  }
  for (const key of Object.keys(EMPTY_BUDGET)) {
    if (!hasNonNegativeInteger(value, key)) {
      throw new DeepResearchProtocolError(`Invalid budget field: ${key}`, "invalid_budget", value);
    }
  }
}

function assertSubject(value: unknown): asserts value is EvaluationSubject {
  if (!isRecord(value) || !["corpus", "report"].includes(String(value.kind)) || !hasNonEmptyString(value, "digest") || !hasNonNegativeInteger(value, "version")) {
    throw new DeepResearchProtocolError("Invalid evaluation subject", "invalid_subject", value);
  }
}

function assertIssue(value: unknown): asserts value is EvaluationIssueSummary {
  if (!isRecord(value) || !hasNonEmptyString(value, "id") || !hasNonEmptyString(value, "category") || !["minor", "major", "blocker"].includes(String(value.severity)) || !hasNullableString(value, "suggested_repair_stage") || !hasStringArray(value, "affected_sub_question_ids") || !hasStringArray(value, "claim_ids") || !hasStringArray(value, "segment_ids")) {
    throw new DeepResearchProtocolError("Invalid evaluation issue", "invalid_issue", value);
  }
}

function assertScores(value: unknown): asserts value is Record<string, number> {
  if (!isRecord(value) || Object.values(value).some((score) => typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > 100)) {
    throw new DeepResearchProtocolError("Invalid evaluation scores", "invalid_scores", value);
  }
}

function assertArtifact(value: unknown): asserts value is ArtifactVersionRef {
  if (!isRecord(value) || !hasNonEmptyString(value, "id") || !hasNonEmptyString(value, "run_id") || !ARTIFACT_KINDS.has(value.artifact_kind as ResearchArtifactKind) || !hasNonEmptyString(value, "logical_artifact_id") || !hasPositiveInteger(value, "version_number") || !hasNonNegativeInteger(value, "plan_version") || !hasNonNegativeInteger(value, "controller_cycle") || !hasPositiveInteger(value, "schema_version") || !hasNullableString(value, "parent_version_id") || !hasNullableString(value, "source_checkpoint_id") || typeof value.content_hash !== "string" || !/^[0-9a-f]{64}$/.test(value.content_hash) || !hasNonEmptyString(value, "created_at") || !(value.payload === undefined || isRecord(value.payload))) {
    throw new DeepResearchProtocolError("Invalid artifact version", "invalid_artifact", value);
  }
}

export function parseArtifactVersionRef(value: unknown): ArtifactVersionRef {
  assertArtifact(value);
  return value;
}

function assertCheckpoint(value: unknown): asserts value is CheckpointSummary {
  if (!isRecord(value) || !hasNonEmptyString(value, "checkpoint_id") || !hasNonEmptyString(value, "graph_version") || typeof value.restorable !== "boolean" || !hasNonEmptyString(value, "saved_at") || !hasStringArray(value, "next_nodes")) {
    throw new DeepResearchProtocolError("Invalid checkpoint summary", "invalid_checkpoint", value);
  }
}

function assertSubQuestion(value: unknown): asserts value is ResearchSubQuestion {
  if (!isRecord(value) || !hasNonEmptyString(value, "id") || !hasNonEmptyString(value, "question") || !hasNonNegativeInteger(value, "priority") || !hasNonNegativeInteger(value, "order") || !hasNonNegativeInteger(value, "plan_version") || !SUBQUESTION_ORIGINS.has(value.origin as ResearchSubQuestion["origin"]) || !SUBQUESTION_STATUSES.has(value.status as SubQuestionStatus) || !hasNonNegativeInteger(value, "attempt") || !hasNullableNonNegativeNumber(value, "confidence") || !hasNullableNonNegativeNumber(value, "duration_ms") || !hasNullableString(value, "error_code") || !hasNullableString(value, "error_message") || !hasNullableString(value, "sub_report_artifact_version_id")) {
    throw new DeepResearchProtocolError("Invalid subquestion", "invalid_subquestion", value);
  }
}

function assertSegment(value: unknown): asserts value is ReportSegmentProgress {
  if (!isRecord(value) || !hasNonEmptyString(value, "segment_id") || !hasNonEmptyString(value, "title") || !SEGMENT_STATUSES.has(value.status as ReportSegmentProgress["status"]) || !hasPositiveInteger(value, "report_version") || !hasNullableNonNegativeNumber(value, "duration_ms") || !hasNullableString(value, "artifact_version_id")) {
    throw new DeepResearchProtocolError("Invalid report segment", "invalid_segment", value);
  }
}

function assertPayload(type: DeepResearchEventType, payload: unknown): void {
  if (!isRecord(payload)) {
    throw new DeepResearchProtocolError("Event payload must be an object", "invalid_payload", payload);
  }
  switch (type) {
    case "run_started":
      if (!hasNonEmptyString(payload, "workspace_id") || !hasNonEmptyString(payload, "topic") || !hasNonEmptyString(payload, "graph_version") || payload.status !== "running") throw new DeepResearchProtocolError("Invalid run_started payload", "invalid_payload", payload);
      assertBudget(payload.budget);
      assertResume(payload.resume);
      return;
    case "phase_started":
    case "phase_completed":
      if (!RESEARCH_PHASES.has(payload.phase as ResearchPhase) || !hasNonEmptyString(payload, "node") || !hasNonEmptyString(payload, "label") || !hasNullableString(payload, "round_id") || !(payload.evaluation_phase === null || EVALUATION_PHASES.has(payload.evaluation_phase as EvaluationPhase)) || !hasStringArray(payload, "target_sub_question_ids") || !hasStringArray(payload, "target_report_segment_ids") || !hasStringArray(payload, "output_artifact_version_ids") || !hasNullableNonNegativeNumber(payload, "duration_ms") || !(payload.status === undefined || payload.status === "completed" || payload.status === "failed")) throw new DeepResearchProtocolError("Invalid phase payload", "invalid_payload", payload);
      return;
    case "subquestion_upserted":
      assertSubQuestion(payload.sub_question);
      return;
    case "subquestion_progressed":
      if (!hasNonEmptyString(payload, "sub_question_id") || !SUBQUESTION_STATUSES.has(payload.status as SubQuestionStatus) || !hasNonNegativeInteger(payload, "attempt") || !hasNullableNonNegativeNumber(payload, "confidence") || !hasNullableNonNegativeNumber(payload, "duration_ms") || !hasNullableString(payload, "error_code") || !hasNullableString(payload, "error_message") || !hasNullableString(payload, "sub_report_artifact_version_id")) throw new DeepResearchProtocolError("Invalid subquestion progress", "invalid_payload", payload);
      return;
    case "evaluation_started":
      if (!hasNonEmptyString(payload, "evaluation_id") || !hasNonEmptyString(payload, "round_id") || !EVALUATION_PHASES.has(payload.phase as EvaluationPhase) || !hasNonEmptyString(payload, "evaluator_model")) throw new DeepResearchProtocolError("Invalid evaluation_started payload", "invalid_payload", payload);
      assertSubject(payload.subject);
      return;
    case "evaluation_completed":
      if (!hasNonEmptyString(payload, "evaluation_id") || !hasNonEmptyString(payload, "round_id") || !EVALUATION_PHASES.has(payload.phase as EvaluationPhase) || !["completed", "failed"].includes(String(payload.status)) || !hasNonEmptyString(payload, "evaluator_model") || !hasNonNegativeInteger(payload, "attempts") || !hasNonNegativeNumber(payload, "duration_ms") || !Array.isArray(payload.issues) || !hasNullableString(payload, "summary") || !hasNullableString(payload, "error_code") || !hasNullableString(payload, "artifact_version_id")) throw new DeepResearchProtocolError("Invalid evaluation_completed payload", "invalid_payload", payload);
      assertSubject(payload.subject);
      assertScores(payload.scores);
      payload.issues.forEach(assertIssue);
      return;
    case "route_selected":
      if (!hasNonEmptyString(payload, "decision_id") || !hasNonEmptyString(payload, "round_id") || !hasNonEmptyString(payload, "evaluation_id") || !EVALUATION_PHASES.has(payload.phase as EvaluationPhase) || !ROUTES.has(payload.route as ResearchRoute) || !hasNullableString(payload, "repair_stage") || !hasNullableNonNegativeNumber(payload, "weighted_overall_score") || !hasNonEmptyString(payload, "reason_code") || !hasNonEmptyString(payload, "reason") || !hasStringArray(payload, "target_sub_question_ids") || !hasStringArray(payload, "target_report_segment_ids") || !hasNullableString(payload, "artifact_version_id")) throw new DeepResearchProtocolError("Invalid route_selected payload", "invalid_payload", payload);
      assertBudget(payload.budget);
      return;
    case "artifact_version_created":
      assertArtifact(payload.artifact);
      return;
    case "checkpoint_saved":
      assertCheckpoint(payload.checkpoint);
      assertResume(payload.resume);
      return;
    case "budget_updated":
      if (!hasNonEmptyString(payload, "cause")) throw new DeepResearchProtocolError("Invalid budget update", "invalid_payload", payload);
      assertBudget(payload.budget);
      return;
    case "synthesis_section_updated":
      assertSegment(payload.segment);
      return;
    case "run_finished":
      if (!hasString(payload, "status") || !RUN_STATUSES.has(payload.status as ResearchRunStatus) || payload.status === "running" || typeof payload.report_accepted !== "boolean" || typeof payload.publishable !== "boolean" || !hasNullableString(payload, "terminal_reason_code") || !hasNullableString(payload, "terminal_reason") || !hasNullableString(payload, "candidate_artifact_version_id") || !hasNullableString(payload, "final_artifact_version_id") || !hasNullableString(payload, "deliverable_id") || !(payload.result === null || isRecord(payload.result))) throw new DeepResearchProtocolError("Invalid run_finished payload", "invalid_payload", payload);
      assertResume(payload.resume);
      return;
    case "protocol_error":
      if (!hasNonEmptyString(payload, "code") || !hasNonEmptyString(payload, "message") || typeof payload.recoverable !== "boolean" || !hasNonNegativeInteger(payload, "last_good_seq")) throw new DeepResearchProtocolError("Invalid protocol_error payload", "invalid_payload", payload);
  }
}

export function parseDeepResearchEvent(value: unknown): DeepResearchRunEvent {
  if (!isRecord(value)) {
    throw new DeepResearchProtocolError("Event must be an object", "invalid_envelope", value);
  }
  if (value.schema_version !== DEEP_RESEARCH_EVENT_SCHEMA) {
    throw new DeepResearchProtocolError("Unsupported Deep Research event schema", "unsupported_schema", value);
  }
  if (typeof value.event_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value.event_id) || !hasPositiveInteger(value, "seq") || !hasString(value, "type") || !EVENT_TYPES.has(value.type as DeepResearchEventType) || !hasNonEmptyString(value, "run_id") || !hasNonEmptyString(value, "emitted_at") || !hasNonNegativeInteger(value, "cycle") || !hasNonNegativeInteger(value, "plan_version") || !hasNonNegativeInteger(value, "corpus_version") || !hasNullablePositiveInteger(value, "report_version") || !hasNullableString(value, "checkpoint_id")) {
    throw new DeepResearchProtocolError("Invalid Deep Research event envelope", "invalid_envelope", value);
  }
  assertPayload(value.type as DeepResearchEventType, value.payload);
  return value as unknown as DeepResearchRunEvent;
}

export function parseDeepResearchSnapshot(value: unknown): DeepResearchRunSnapshot {
  if (!isRecord(value) || value.schema_version !== "deep-research-run.v1" || !hasString(value, "run_id") || !hasString(value, "workspace_id") || !hasString(value, "topic") || !hasString(value, "status") || !RUN_STATUSES.has(value.status as ResearchRunStatus) || !hasString(value, "graph_version") || !hasNullableString(value, "current_phase") || !hasNonNegativeInteger(value, "snapshot_seq") || !hasNullableString(value, "last_event_id") || !hasNonNegativeInteger(value, "plan_version") || !hasNonNegativeInteger(value, "corpus_version") || !hasNullablePositiveInteger(value, "report_version") || !Array.isArray(value.sub_questions) || !Array.isArray(value.decision_rounds) || !Array.isArray(value.artifacts) || !Array.isArray(value.report_segments) || !hasString(value, "created_at") || !hasString(value, "updated_at")) {
    throw new DeepResearchProtocolError("Invalid Deep Research run snapshot", "invalid_snapshot", value);
  }
  assertBudget(value.budget);
  assertResume(value.resume);
  value.sub_questions.forEach(assertSubQuestion);
  value.artifacts.forEach(assertArtifact);
  value.report_segments.forEach(assertSegment);
  if (value.latest_checkpoint !== null) assertCheckpoint(value.latest_checkpoint);
  return value as unknown as DeepResearchRunSnapshot;
}

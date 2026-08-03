import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ClarificationQuestion } from "@/types";
import {
  EMPTY_BUDGET,
  RESUME_UNAVAILABLE,
  type ArtifactVersionRef,
  type BudgetSnapshot,
  type CheckpointSummary,
  type DecisionRound,
  type DeepResearchRunEvent,
  type DeepResearchRunSnapshot,
  type EvaluationPhase,
  type ReportSegmentProgress,
  type ResearchPhase,
  type ResearchRunStatus,
  type ResearchSubQuestion,
  type ResumeCapability,
  type TerminalOutcome,
} from "@/types/deepResearch";

// Shared by Proposal Plan's legacy progress display. Deep Research no longer
// uses these linear-stage types for its server-authoritative decision loop.
export type MacroStageKey = "plan" | "research" | "evaluate" | "write" | "context" | "draft" | "finalize";
export type MacroStageStatus = "pending" | "in_progress" | "completed" | "failed";

export interface MacroStage {
  key: MacroStageKey;
  label: string;
  status: MacroStageStatus;
  startedAt?: number;
  completedAt?: number;
  durationMs?: number;
}

export interface SubQuestionProgress {
  id: string;
  question: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  startedAt?: number;
  durationMs?: number;
  confidence?: number;
  retryCount: number;
  isSupplementary?: boolean;
  failReason?: string;
}

export interface SectionProgressV2 {
  title: string;
  status: "pending" | "drafting" | "done" | "failed";
  durationMs?: number;
  preview?: string;
}

export interface DynamicStage {
  key: string;
  label: string;
  status: "completed" | "active" | "pending";
  startedAt?: number;
  completedAt?: number;
}

export interface ActivityEvent {
  id: string;
  timestamp: number;
  type: "thinking" | "searching" | "reading" | "deciding" | "writing" | "tool_call" | "done" | "error";
  label: string;
  detail?: string;
  status: "active" | "done";
}

export type DeepResearchStatus =
  | "idle"
  | "generating_plan"
  | "plan_ready"
  | "validating"
  | "needs_clarification"
  | "running"
  | "loading_run"
  | "resuming"
  | "interrupted"
  | "completed"
  | "incomplete"
  | "blocked"
  | "failed";

export interface DeepResearchInput {
  topic: string;
  focus: string;
  timeHorizon: "recent_2y" | "recent_5y" | "broad";
  outputLength: "short" | "medium";
  useWorkspaceSources: boolean;
  discoverNewSources: boolean;
  mustInclude: string;
  mustExclude: string;
  notes: string;
  targetDeliverableId: string | null;
}

export interface GeneratedDRPlan {
  subQuestions: Array<{
    id: string;
    question: string;
    rationale: string;
    searchQueries: string[];
    priority: number;
  }>;
  overallApproach: string;
  recommendedDepth: string;
  sourcesStrategy: string;
  focusNote: string | null;
}

export interface ResearchProtocolState {
  code: string;
  message: string;
  recoverable: boolean;
  lastGoodSeq: number;
}

export type ResearchConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "resync_required"
  | "offline"
  | "closed";

export interface ResearchRunView {
  runId: string;
  workspaceId: string;
  topic: string;
  status: ResearchRunStatus;
  graphVersion: string;
  currentPhase: ResearchPhase | null;
  planVersion: number;
  corpusVersion: number;
  reportVersion: number | null;
  lastSeq: number;
  lastEventId: string | null;
  connection: ResearchConnectionState;
  protocolError: ResearchProtocolState | null;
  budget: BudgetSnapshot;
  questionsById: Record<string, ResearchSubQuestion>;
  questionOrder: string[];
  roundsById: Record<string, DecisionRound>;
  roundOrder: string[];
  artifactsById: Record<string, ArtifactVersionRef>;
  artifactOrder: string[];
  segmentsById: Record<string, ReportSegmentProgress>;
  segmentOrder: string[];
  latestCheckpoint: CheckpointSummary | null;
  resume: ResumeCapability;
  terminal: TerminalOutcome | null;
  createdAt: string;
  updatedAt: string;
}

export type EventApplyCode =
  | "applied"
  | "duplicate"
  | "sequence_conflict"
  | "sequence_gap"
  | "unknown_run";

export interface EventApplyResult {
  code: EventApplyCode;
  run: ResearchRunView | null;
}

const DEFAULT_INPUT: DeepResearchInput = {
  topic: "",
  focus: "",
  timeHorizon: "broad",
  outputLength: "medium",
  useWorkspaceSources: true,
  discoverNewSources: true,
  mustInclude: "",
  mustExclude: "",
  notes: "",
  targetDeliverableId: null,
};

function mapById<T extends { id: string }>(values: T[]): Record<string, T> {
  return Object.fromEntries(values.map((value) => [value.id, value]));
}

function mapSegments(values: ReportSegmentProgress[]): Record<string, ReportSegmentProgress> {
  return Object.fromEntries(values.map((value) => [value.segment_id, value]));
}

function roundOrder(rounds: Record<string, DecisionRound>): string[] {
  return Object.values(rounds)
    .sort((left, right) => left.cycle - right.cycle || left.phase.localeCompare(right.phase))
    .map((round) => round.id);
}

function questionOrder(questions: Record<string, ResearchSubQuestion>): string[] {
  return Object.values(questions)
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id))
    .map((question) => question.id);
}

function artifactOrder(artifacts: Record<string, ArtifactVersionRef>): string[] {
  return Object.values(artifacts)
    .sort((left, right) => left.controller_cycle - right.controller_cycle || left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id))
    .map((artifact) => artifact.id);
}

function createRound(
  event: DeepResearchRunEvent,
  id: string,
  phase: EvaluationPhase,
): DecisionRound {
  return {
    id,
    cycle: event.cycle,
    phase,
    plan_version: event.plan_version,
    corpus_version: event.corpus_version,
    report_version: event.report_version,
    evaluation: null,
    route: null,
    repair: null,
  };
}

function runFromStarted(event: Extract<DeepResearchRunEvent, { type: "run_started" }>): ResearchRunView {
  const { payload } = event;
  return {
    runId: event.run_id,
    workspaceId: payload.workspace_id,
    topic: payload.topic,
    status: "running",
    graphVersion: payload.graph_version,
    currentPhase: null,
    planVersion: event.plan_version,
    corpusVersion: event.corpus_version,
    reportVersion: event.report_version,
    lastSeq: 0,
    lastEventId: null,
    connection: "live",
    protocolError: null,
    budget: payload.budget,
    questionsById: {},
    questionOrder: [],
    roundsById: {},
    roundOrder: [],
    artifactsById: {},
    artifactOrder: [],
    segmentsById: {},
    segmentOrder: [],
    latestCheckpoint: null,
    resume: payload.resume,
    terminal: null,
    createdAt: event.emitted_at,
    updatedAt: event.emitted_at,
  };
}

function failClosedTerminal(
  event: Extract<DeepResearchRunEvent, { type: "run_finished" }>,
): { status: Exclude<ResearchRunStatus, "running">; terminal: TerminalOutcome; protocolError: ResearchProtocolState | null } {
  const payload = event.payload;
  const validCompletedProof = payload.status !== "completed" || (
    payload.report_accepted
    && payload.publishable
    && Boolean(payload.final_artifact_version_id)
  );
  const invalidNonCompletedPublication = payload.status !== "completed" && (
    payload.report_accepted || payload.publishable || Boolean(payload.final_artifact_version_id)
  );
  const valid = validCompletedProof && !invalidNonCompletedPublication;
  const status = valid ? payload.status : "incomplete";
  const protocolError = valid ? null : {
    code: "invalid_terminal_proof",
    message: "The server terminal event did not provide a valid publication proof. The run was kept incomplete.",
    recoverable: false,
    lastGoodSeq: event.seq - 1,
  };
  return {
    status,
    protocolError,
    terminal: {
      status,
      report_accepted: valid && payload.status === "completed" ? payload.report_accepted : false,
      publishable: valid && payload.status === "completed" ? payload.publishable : false,
      terminal_reason_code: valid ? payload.terminal_reason_code : "invalid_terminal_proof",
      terminal_reason: valid ? payload.terminal_reason : protocolError!.message,
      candidate_artifact_version_id: payload.candidate_artifact_version_id,
      final_artifact_version_id: valid && payload.status === "completed" ? payload.final_artifact_version_id : null,
      deliverable_id: valid && payload.status === "completed" ? payload.deliverable_id : null,
      result: payload.result,
      finished_at: event.emitted_at,
    },
  };
}

export function runFromSnapshot(snapshot: DeepResearchRunSnapshot): ResearchRunView {
  const questionsById = mapById(snapshot.sub_questions);
  const roundsById = mapById(snapshot.decision_rounds);
  const artifactsById = mapById(snapshot.artifacts);
  const segmentsById = mapSegments(snapshot.report_segments);
  let status = snapshot.status;
  let terminal = snapshot.terminal;
  let protocolError: ResearchProtocolState | null = null;

  if (status === "completed" && (!terminal?.report_accepted || !terminal.publishable || !terminal.final_artifact_version_id)) {
    status = "incomplete";
    terminal = terminal ? {
      ...terminal,
      status: "incomplete",
      report_accepted: false,
      publishable: false,
      final_artifact_version_id: null,
      deliverable_id: null,
      terminal_reason_code: "invalid_terminal_proof",
      terminal_reason: "The run snapshot has no valid accepted final-report binding.",
    } : null;
    protocolError = {
      code: "invalid_terminal_proof",
      message: "The run snapshot has no valid accepted final-report binding.",
      recoverable: false,
      lastGoodSeq: snapshot.snapshot_seq,
    };
  }

  return {
    runId: snapshot.run_id,
    workspaceId: snapshot.workspace_id,
    topic: snapshot.topic,
    status,
    graphVersion: snapshot.graph_version,
    currentPhase: snapshot.current_phase,
    planVersion: snapshot.plan_version,
    corpusVersion: snapshot.corpus_version,
    reportVersion: snapshot.report_version,
    lastSeq: snapshot.snapshot_seq,
    lastEventId: snapshot.last_event_id,
    connection: status === "running" ? "connecting" : "closed",
    protocolError,
    budget: snapshot.budget,
    questionsById,
    questionOrder: questionOrder(questionsById),
    roundsById,
    roundOrder: roundOrder(roundsById),
    artifactsById,
    artifactOrder: artifactOrder(artifactsById),
    segmentsById,
    segmentOrder: snapshot.report_segments.map((segment) => segment.segment_id),
    latestCheckpoint: snapshot.latest_checkpoint,
    resume: snapshot.resume,
    terminal,
    createdAt: snapshot.created_at,
    updatedAt: snapshot.updated_at,
  };
}

function phaseToEvaluationPhase(phase: ResearchPhase): EvaluationPhase | null {
  if (phase === "pre_synthesis_evaluation") return "pre_synthesis";
  if (phase === "post_synthesis_evaluation" || phase === "report_revision") return "post_synthesis";
  return null;
}

export function reduceResearchRunEvent(
  current: ResearchRunView | undefined,
  event: DeepResearchRunEvent,
): EventApplyResult {
  let run = current;
  if (!run) {
    if (event.type !== "run_started") return { code: "unknown_run", run: null };
    run = runFromStarted(event);
  } else if (run.runId !== event.run_id) {
    return { code: "unknown_run", run };
  }

  if (event.seq < run.lastSeq) return { code: "duplicate", run };
  if (event.seq === run.lastSeq) {
    return {
      code: event.event_id === run.lastEventId ? "duplicate" : "sequence_conflict",
      run,
    };
  }
  if (event.seq > run.lastSeq + 1) {
    return {
      code: "sequence_gap",
      run: {
        ...run,
        connection: "resync_required",
        protocolError: {
          code: "sequence_gap",
          message: `Expected event ${run.lastSeq + 1}, received ${event.seq}. Reloading the authoritative run snapshot is required.`,
          recoverable: true,
          lastGoodSeq: run.lastSeq,
        },
      },
    };
  }

  let next: ResearchRunView = {
    ...run,
    lastSeq: event.seq,
    lastEventId: event.event_id,
    planVersion: event.plan_version,
    corpusVersion: event.corpus_version,
    reportVersion: event.report_version,
    connection: run.status === "running" ? "live" : run.connection,
    updatedAt: event.emitted_at,
  };

  switch (event.type) {
    case "run_started":
      next = {
        ...next,
        status: "running",
        graphVersion: event.payload.graph_version,
        budget: event.payload.budget,
        resume: event.payload.resume,
        terminal: null,
        protocolError: null,
        connection: "live",
      };
      break;
    case "phase_started": {
      const reopeningInterruptedRun = next.status === "interrupted";
      next = {
        ...next,
        status: reopeningInterruptedRun ? "running" : next.status,
        terminal: reopeningInterruptedRun ? null : next.terminal,
        protocolError: reopeningInterruptedRun ? null : next.protocolError,
        connection: reopeningInterruptedRun ? "live" : next.connection,
        currentPhase: event.payload.phase,
      };
      const roundId = event.payload.round_id;
      const evaluationPhase = event.payload.evaluation_phase ?? phaseToEvaluationPhase(event.payload.phase);
      if (roundId && evaluationPhase && ["targeted_repair", "partial_replan", "full_replan", "report_revision"].includes(event.payload.phase)) {
        const round = next.roundsById[roundId] ?? createRound(event, roundId, evaluationPhase);
        const roundsById: Record<string, DecisionRound> = {
          ...next.roundsById,
          [roundId]: {
            ...round,
            repair: {
              phase: event.payload.phase,
              status: "running",
              label: event.payload.label,
              target_sub_question_ids: event.payload.target_sub_question_ids,
              target_report_segment_ids: event.payload.target_report_segment_ids,
              output_artifact_version_ids: [],
              started_at: event.emitted_at,
              completed_at: null,
              duration_ms: null,
            },
          },
        };
        next = { ...next, roundsById, roundOrder: roundOrder(roundsById) };
      }
      break;
    }
    case "phase_completed": {
      const roundId = event.payload.round_id;
      if (roundId && next.roundsById[roundId]?.repair) {
        const previous = next.roundsById[roundId];
        const roundsById: Record<string, DecisionRound> = {
          ...next.roundsById,
          [roundId]: {
            ...previous,
            repair: {
              ...previous.repair!,
              status: event.payload.status === "failed" ? "failed" : "completed",
              output_artifact_version_ids: event.payload.output_artifact_version_ids,
              completed_at: event.emitted_at,
              duration_ms: event.payload.duration_ms,
            },
          },
        };
        next = { ...next, roundsById };
      }
      break;
    }
    case "subquestion_upserted": {
      const question = event.payload.sub_question;
      const questionsById = { ...next.questionsById, [question.id]: question };
      next = { ...next, questionsById, questionOrder: questionOrder(questionsById) };
      break;
    }
    case "subquestion_progressed": {
      const payload = event.payload;
      const question = next.questionsById[payload.sub_question_id];
      if (!question) {
        next = {
          ...next,
          connection: "resync_required",
          protocolError: {
            code: "unknown_subquestion",
            message: `Progress referenced unknown subquestion ${payload.sub_question_id}.`,
            recoverable: true,
            lastGoodSeq: event.seq - 1,
          },
        };
        break;
      }
      next = {
        ...next,
        questionsById: {
          ...next.questionsById,
          [question.id]: {
            ...question,
            status: payload.status,
            attempt: payload.attempt,
            confidence: payload.confidence,
            duration_ms: payload.duration_ms,
            error_code: payload.error_code,
            error_message: payload.error_message,
            sub_report_artifact_version_id: payload.sub_report_artifact_version_id,
          },
        },
      };
      break;
    }
    case "evaluation_started": {
      const payload = event.payload;
      const round = next.roundsById[payload.round_id] ?? createRound(event, payload.round_id, payload.phase);
      const roundsById: Record<string, DecisionRound> = {
        ...next.roundsById,
        [round.id]: {
          ...round,
          evaluation: {
            evaluation_id: payload.evaluation_id,
            round_id: payload.round_id,
            phase: payload.phase,
            status: "running",
            subject: payload.subject,
            evaluator_model: payload.evaluator_model,
            attempts: 0,
            duration_ms: null,
            scores: {},
            issues: [],
            summary: null,
            error_code: null,
            artifact_version_id: null,
            started_at: event.emitted_at,
            completed_at: null,
          },
        },
      };
      next = { ...next, roundsById, roundOrder: roundOrder(roundsById) };
      break;
    }
    case "evaluation_completed": {
      const payload = event.payload;
      const round = next.roundsById[payload.round_id] ?? createRound(event, payload.round_id, payload.phase);
      const roundsById: Record<string, DecisionRound> = {
        ...next.roundsById,
        [round.id]: {
          ...round,
          evaluation: {
            evaluation_id: payload.evaluation_id,
            round_id: payload.round_id,
            phase: payload.phase,
            status: payload.status,
            subject: payload.subject,
            evaluator_model: payload.evaluator_model,
            attempts: payload.attempts,
            duration_ms: payload.duration_ms,
            scores: payload.scores,
            issues: payload.issues,
            summary: payload.summary,
            error_code: payload.error_code,
            artifact_version_id: payload.artifact_version_id,
            started_at: round.evaluation?.started_at ?? event.emitted_at,
            completed_at: event.emitted_at,
          },
        },
      };
      next = { ...next, roundsById, roundOrder: roundOrder(roundsById) };
      break;
    }
    case "route_selected": {
      const payload = event.payload;
      const round = next.roundsById[payload.round_id] ?? createRound(event, payload.round_id, payload.phase);
      const roundsById: Record<string, DecisionRound> = {
        ...next.roundsById,
        [round.id]: {
          ...round,
          route: {
            decision_id: payload.decision_id,
            round_id: payload.round_id,
            evaluation_id: payload.evaluation_id,
            phase: payload.phase,
            route: payload.route,
            repair_stage: payload.repair_stage,
            weighted_overall_score: payload.weighted_overall_score,
            reason_code: payload.reason_code,
            reason: payload.reason,
            target_sub_question_ids: payload.target_sub_question_ids,
            target_report_segment_ids: payload.target_report_segment_ids,
            artifact_version_id: payload.artifact_version_id,
            selected_at: event.emitted_at,
          },
        },
      };
      next = { ...next, roundsById, roundOrder: roundOrder(roundsById), budget: payload.budget };
      break;
    }
    case "artifact_version_created": {
      const artifact = event.payload.artifact;
      const artifactsById = { ...next.artifactsById, [artifact.id]: artifact };
      next = { ...next, artifactsById, artifactOrder: artifactOrder(artifactsById) };
      break;
    }
    case "checkpoint_saved":
      next = { ...next, latestCheckpoint: event.payload.checkpoint, resume: event.payload.resume };
      break;
    case "budget_updated":
      next = { ...next, budget: event.payload.budget };
      break;
    case "synthesis_section_updated": {
      const segment = event.payload.segment;
      next = {
        ...next,
        segmentsById: { ...next.segmentsById, [segment.segment_id]: segment },
        segmentOrder: next.segmentOrder.includes(segment.segment_id)
          ? next.segmentOrder
          : [...next.segmentOrder, segment.segment_id],
      };
      break;
    }
    case "run_finished": {
      const resolved = failClosedTerminal(event);
      next = {
        ...next,
        status: resolved.status,
        terminal: resolved.terminal,
        protocolError: resolved.protocolError ?? next.protocolError,
        resume: event.payload.resume,
        currentPhase: null,
        connection: "closed",
      };
      break;
    }
    case "protocol_error":
      next = {
        ...next,
        protocolError: {
          code: event.payload.code,
          message: event.payload.message,
          recoverable: event.payload.recoverable,
          lastGoodSeq: event.payload.last_good_seq,
        },
        connection: event.payload.recoverable ? "resync_required" : "offline",
      };
      break;
  }

  return { code: "applied", run: next };
}

interface DeepResearchState {
  status: DeepResearchStatus;
  input: DeepResearchInput;
  generatedPlan: GeneratedDRPlan | null;
  clarificationQuestions: ClarificationQuestion[];
  errorMessage: string | null;
  createdDeliverableId: string | null;
  pendingWorkspaceId: string | null;

  activeRunIdByWorkspace: Record<string, string | null>;
  runsById: Record<string, ResearchRunView>;
  materializedDeliverablesByRun: Record<string, string>;

  setInput: (partial: Partial<DeepResearchInput>) => void;
  setStatus: (status: DeepResearchStatus) => void;
  setClarification: (questions: ClarificationQuestion[]) => void;
  setFailed: (message: string) => void;
  setBlocked: (message: string) => void;
  setCreatedDeliverableId: (id: string) => void;
  setGeneratedPlan: (plan: GeneratedDRPlan) => void;
  clearPlan: () => void;
  startPlanGeneration: () => void;
  completePlanGeneration: () => void;
  startRun: (workspaceId?: string) => void;
  loadRun: (snapshot: DeepResearchRunSnapshot) => void;
  applyRunEvent: (event: DeepResearchRunEvent) => EventApplyResult;
  setRunConnection: (runId: string, connection: ResearchConnectionState) => void;
  setRunProtocolError: (runId: string, error: ResearchProtocolState) => void;
  markMaterialized: (runId: string, deliverableId: string) => void;
  selectRun: (workspaceId: string, runId: string | null) => void;
  resetLauncher: () => void;
  reset: () => void;
}

export const useDeepResearchStore = create<DeepResearchState>()(
  persist(
    (set, get) => ({
      status: "idle",
      input: { ...DEFAULT_INPUT },
      generatedPlan: null,
      clarificationQuestions: [],
      errorMessage: null,
      createdDeliverableId: null,
      pendingWorkspaceId: null,
      activeRunIdByWorkspace: {},
      runsById: {},
      materializedDeliverablesByRun: {},

      setInput: (partial) => set((state) => ({ input: { ...state.input, ...partial } })),
      setStatus: (status) => set({ status }),
      setClarification: (clarificationQuestions) => set({ status: "needs_clarification", clarificationQuestions }),
      setFailed: (errorMessage) => set({ status: "failed", errorMessage, pendingWorkspaceId: null }),
      setBlocked: (errorMessage) => set({ status: "blocked", errorMessage, pendingWorkspaceId: null }),
      setCreatedDeliverableId: (createdDeliverableId) => set({ createdDeliverableId }),
      setGeneratedPlan: (generatedPlan) => set({ generatedPlan, status: "plan_ready", errorMessage: null }),
      clearPlan: () => set({ generatedPlan: null, status: "idle" }),
      startPlanGeneration: () => set({ status: "generating_plan", errorMessage: null }),
      completePlanGeneration: () => set((state) => ({ status: state.generatedPlan ? "plan_ready" : state.status })),
      startRun: (workspaceId) => set({
        status: "validating",
        pendingWorkspaceId: workspaceId ?? null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
      }),
      loadRun: (snapshot) => set((state) => {
        const run = runFromSnapshot(snapshot);
        return {
          runsById: { ...state.runsById, [run.runId]: run },
          activeRunIdByWorkspace: { ...state.activeRunIdByWorkspace, [run.workspaceId]: run.runId },
          status: run.status === "running" ? "running" : run.status,
          pendingWorkspaceId: null,
          errorMessage: run.protocolError?.message ?? null,
        };
      }),
      applyRunEvent: (event) => {
        const state = get();
        const result = reduceResearchRunEvent(state.runsById[event.run_id], event);
        if (!result.run) return result;

        if (result.code === "applied" || result.code === "sequence_gap") {
          const run = result.run;
          const status: DeepResearchStatus = run.status === "running" ? "running" : run.status;
          set({
            runsById: { ...state.runsById, [run.runId]: run },
            activeRunIdByWorkspace: { ...state.activeRunIdByWorkspace, [run.workspaceId]: run.runId },
            pendingWorkspaceId: null,
            status,
            errorMessage: run.protocolError?.message ?? null,
          });
        }
        return result;
      },
      setRunConnection: (runId, connection) => set((state) => {
        const run = state.runsById[runId];
        return run ? { runsById: { ...state.runsById, [runId]: { ...run, connection } } } : state;
      }),
      setRunProtocolError: (runId, protocolError) => set((state) => {
        const run = state.runsById[runId];
        return run ? {
          runsById: {
            ...state.runsById,
            [runId]: {
              ...run,
              protocolError,
              connection: protocolError.recoverable ? "resync_required" : "offline",
            },
          },
          errorMessage: protocolError.message,
        } : state;
      }),
      markMaterialized: (runId, deliverableId) => set((state) => ({
        materializedDeliverablesByRun: { ...state.materializedDeliverablesByRun, [runId]: deliverableId },
        createdDeliverableId: deliverableId,
      })),
      selectRun: (workspaceId, runId) => set((state) => ({
        activeRunIdByWorkspace: { ...state.activeRunIdByWorkspace, [workspaceId]: runId },
        status: runId ? (state.runsById[runId]?.status ?? "loading_run") : (state.generatedPlan ? "plan_ready" : "idle"),
      })),
      resetLauncher: () => set((state) => ({
        status: "idle",
        input: { ...DEFAULT_INPUT },
        generatedPlan: null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
        pendingWorkspaceId: null,
        activeRunIdByWorkspace: { ...state.activeRunIdByWorkspace },
      })),
      reset: () => set({
        status: "idle",
        input: { ...DEFAULT_INPUT },
        generatedPlan: null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
        pendingWorkspaceId: null,
        activeRunIdByWorkspace: {},
        runsById: {},
        materializedDeliverablesByRun: {},
      }),
    }),
    {
      name: "pp_deep_research",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        input: state.input,
        generatedPlan: state.generatedPlan,
        activeRunIdByWorkspace: state.activeRunIdByWorkspace,
        materializedDeliverablesByRun: state.materializedDeliverablesByRun,
      }),
      merge: (persisted, current) => ({
        ...current,
        ...(persisted as Partial<DeepResearchState>),
        status: "idle",
        runsById: {},
        pendingWorkspaceId: null,
        clarificationQuestions: [],
        errorMessage: null,
      }),
    },
  ),
);

export function selectActiveResearchRun(state: DeepResearchState, workspaceId: string): ResearchRunView | null {
  const runId = state.activeRunIdByWorkspace[workspaceId];
  return runId ? state.runsById[runId] ?? null : null;
}

export function canMaterializeRun(run: ResearchRunView): boolean {
  return run.status === "completed"
    && run.terminal?.status === "completed"
    && run.terminal.report_accepted
    && run.terminal.publishable
    && Boolean(run.terminal.final_artifact_version_id);
}

export { EMPTY_BUDGET, RESUME_UNAVAILABLE };

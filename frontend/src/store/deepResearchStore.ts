import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ClarificationQuestion, DeepResearchRunResult } from "@/types";

export type DeepResearchStatus =
  | "idle"
  | "generating_plan"
  | "plan_ready"
  | "validating"
  | "needs_clarification"
  | "planning"
  | "executing"
  | "evaluating"
  | "replanning"
  | "synthesizing"
  | "interrupted"
  | "completed"
  | "blocked"
  | "failed";

const RUNNING_STATUSES: DeepResearchStatus[] = [
  "generating_plan", "validating", "planning", "executing", "evaluating", "replanning", "synthesizing",
];

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

export interface SectionProgress {
  title: string;
  status: "pending" | "drafting" | "done" | "skipped";
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
  type: "thinking" | "searching" | "reading" | "deciding" | "writing" | "tool_call";
  label: string;
  detail?: string;
  status: "active" | "done";
}

export interface GeneratedDRPlan {
  subQuestions: { id: string; question: string; rationale: string; searchQueries: string[]; priority: number }[];
  overallApproach: string;
  recommendedDepth: string;
  sourcesStrategy: string;
  focusNote: string | null;
}

interface DeepResearchState {
  status: DeepResearchStatus;
  input: DeepResearchInput;
  result: DeepResearchRunResult | null;
  clarificationQuestions: ClarificationQuestion[];
  errorMessage: string | null;
  createdDeliverableId: string | null;

  // Plan generation
  generatedPlan: GeneratedDRPlan | null;

  // Streaming state
  currentStageMessage: string | null;
  sectionsProgress: SectionProgress[];
  sourcesFound: number;
  sourcesSelected: number;
  generatedTitle: string | null;

  // Dynamic stages + activity
  dynamicStages: DynamicStage[];
  activityLog: ActivityEvent[];

  // Timeline v2 (vertical timeline)
  macroStages: MacroStage[];
  subQuestions: SubQuestionProgress[];
  sectionsProgressV2: SectionProgressV2[];
  planSummary: string | null;
  currentActivity: string | null;

  setInput: (partial: Partial<DeepResearchInput>) => void;
  startRun: () => void;
  setStatus: (status: DeepResearchStatus) => void;
  setResult: (result: DeepResearchRunResult) => void;
  setClarification: (questions: ClarificationQuestion[]) => void;
  setFailed: (message: string) => void;
  setBlocked: (message: string) => void;
  setCreatedDeliverableId: (id: string) => void;
  setGeneratedPlan: (plan: GeneratedDRPlan) => void;
  clearPlan: () => void;
  startPlanGeneration: () => void;
  completePlanGeneration: () => void;
  // Streaming actions
  setStageMessage: (message: string) => void;
  initSectionsProgress: (titles: string[]) => void;
  setSectionStatus: (index: number, status: SectionProgress["status"], preview?: string) => void;
  setSourcesFound: (count: number) => void;
  setSourcesSelected: (count: number) => void;
  setGeneratedTitle: (title: string) => void;
  // Dynamic stage actions
  pushStage: (key: string, label: string) => void;
  completeCurrentStage: () => void;
  pushActivity: (event: Omit<ActivityEvent, "id" | "timestamp">) => void;
  completeActivity: (id: string) => void;
  // Timeline v2 actions
  setMacroStageStatus: (key: MacroStageKey, status: MacroStageStatus) => void;
  initSubQuestions: (questions: { id: string; question: string }[]) => void;
  updateSubQuestion: (index: number, update: Partial<SubQuestionProgress>) => void;
  appendSubQuestions: (questions: { id: string; question: string }[]) => void;
  setPlanSummary: (summary: string) => void;
  setCurrentActivity: (activity: string | null) => void;
  initSectionsV2: (titles: string[]) => void;
  setSectionV2Status: (index: number, status: SectionProgressV2["status"], durationMs?: number) => void;
  reset: () => void;
}

export const useDeepResearchStore = create<DeepResearchState>()(
  persist(
    (set) => ({
      status: "idle",
      input: { ...DEFAULT_INPUT },
      result: null,
      clarificationQuestions: [],
      errorMessage: null,
      createdDeliverableId: null,
      generatedPlan: null,
      currentStageMessage: null,
      sectionsProgress: [],
      sourcesFound: 0,
      sourcesSelected: 0,
      generatedTitle: null,
      dynamicStages: [],
      activityLog: [],
      macroStages: [],
      subQuestions: [],
      sectionsProgressV2: [],
      planSummary: null,
      currentActivity: null,

      setInput: (partial) => set((s) => ({ input: { ...s.input, ...partial } })),

      startRun: () => set((s) => {
        const hasPlan = !!s.generatedPlan;
        const existingPlanStage = s.macroStages.find((st) => st.key === "plan");
        const planStage = existingPlanStage && existingPlanStage.status === "completed"
          ? existingPlanStage
          : { key: "plan" as const, label: "Plan", status: "pending" as const };

        return {
          status: "validating",
          result: null,
          clarificationQuestions: [],
          errorMessage: null,
          createdDeliverableId: null,
          currentStageMessage: null,
          sectionsProgress: [],
          sourcesFound: 0,
          sourcesSelected: 0,
          generatedTitle: null,
          dynamicStages: [],
          activityLog: [],
          macroStages: [
            planStage,
            { key: "research" as const, label: "Research", status: "pending" as const },
            { key: "evaluate" as const, label: "Evaluate", status: "pending" as const },
            { key: "write" as const, label: "Write", status: "pending" as const },
          ],
          subQuestions: hasPlan
            ? s.generatedPlan!.subQuestions.map((sq) => ({
                id: sq.id,
                question: sq.question,
                status: "pending" as const,
                retryCount: 0,
              }))
            : [],
          sectionsProgressV2: [],
          planSummary: hasPlan
            ? `Generated ${s.generatedPlan!.subQuestions.length} research questions`
            : null,
          currentActivity: null,
        };
      }),

      setStatus: (status) => set({ status }),

      setResult: (result) => set({ status: "completed", result }),

      setClarification: (questions) => set({
        status: "needs_clarification",
        clarificationQuestions: questions,
      }),

      setFailed: (message) => set((s) => ({
        status: "failed",
        errorMessage: message,
        currentActivity: null,
        macroStages: s.macroStages.map((stage) =>
          stage.status === "in_progress" ? { ...stage, status: "pending" as const } : stage
        ),
      })),

      setBlocked: (message) => set((s) => ({
        status: "blocked",
        errorMessage: message,
        currentActivity: null,
        macroStages: s.macroStages.map((stage) =>
          stage.status === "in_progress" ? { ...stage, status: "pending" as const } : stage
        ),
      })),

      setCreatedDeliverableId: (id) => set({ createdDeliverableId: id }),

      setGeneratedPlan: (plan) => set({ generatedPlan: plan, status: "plan_ready" }),

      clearPlan: () => set({ generatedPlan: null, status: "idle" }),

      startPlanGeneration: () => set({
        status: "generating_plan",
        macroStages: [
          { key: "plan" as const, label: "Plan", status: "in_progress" as const, startedAt: Date.now() },
          { key: "research" as const, label: "Research", status: "pending" as const },
          { key: "evaluate" as const, label: "Evaluate", status: "pending" as const },
          { key: "write" as const, label: "Write", status: "pending" as const },
        ],
        currentActivity: "Generating research plan...",
      }),

      completePlanGeneration: () => set((s) => ({
        macroStages: s.macroStages.map((stage) => {
          if (stage.key !== "plan") return stage;
          const now = Date.now();
          return {
            ...stage,
            status: "completed" as const,
            completedAt: now,
            durationMs: stage.startedAt ? now - stage.startedAt : undefined,
          };
        }),
        currentActivity: null,
      })),

      setStageMessage: (message) => set({ currentStageMessage: message }),

      initSectionsProgress: (titles) => set({
        sectionsProgress: titles.map((title) => ({ title, status: "pending" as const })),
      }),

      setSectionStatus: (index, status, preview) => set((s) => ({
        sectionsProgress: s.sectionsProgress.map((sec, i) =>
          i === index ? { ...sec, status, ...(preview !== undefined ? { preview } : {}) } : sec
        ),
      })),

      setSourcesFound: (count) => set({ sourcesFound: count }),

      setSourcesSelected: (count) => set({ sourcesSelected: count }),

      setGeneratedTitle: (title) => set({ generatedTitle: title }),

      pushStage: (key, label) => set((s) => {
        const updated = s.dynamicStages.map((st) =>
          st.status === "active" ? { ...st, status: "completed" as const, completedAt: Date.now() } : st
        );
        return {
          dynamicStages: [...updated, { key, label, status: "active" as const, startedAt: Date.now() }],
        };
      }),

      completeCurrentStage: () => set((s) => ({
        dynamicStages: s.dynamicStages.map((st) =>
          st.status === "active" ? { ...st, status: "completed" as const, completedAt: Date.now() } : st
        ),
      })),

      pushActivity: (event) => set((s) => {
        const newEvent: ActivityEvent = {
          ...event,
          id: `act_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          timestamp: Date.now(),
        };
        // Mark previous active events of same type as done
        const updated = s.activityLog.map((e) =>
          e.status === "active" ? { ...e, status: "done" as const } : e
        );
        return { activityLog: [...updated.slice(-19), newEvent] };
      }),

      completeActivity: (id) => set((s) => ({
        activityLog: s.activityLog.map((e) =>
          e.id === id ? { ...e, status: "done" as const } : e
        ),
      })),

      setMacroStageStatus: (key, status) => set((s) => ({
        macroStages: s.macroStages.map((stage) => {
          if (stage.key !== key) return stage;
          const now = Date.now();
          return {
            ...stage,
            status,
            ...(status === "in_progress" ? { startedAt: now } : {}),
            ...(status === "completed" || status === "failed" ? {
              completedAt: now,
              durationMs: stage.startedAt ? now - stage.startedAt : undefined,
            } : {}),
          };
        }),
      })),

      initSubQuestions: (questions) => set({
        subQuestions: questions.map((q) => ({
          id: q.id,
          question: q.question,
          status: "pending" as const,
          retryCount: 0,
        })),
      }),

      updateSubQuestion: (index, update) => set((s) => ({
        subQuestions: s.subQuestions.map((sq, i) =>
          i === index ? { ...sq, ...update } : sq
        ),
      })),

      appendSubQuestions: (questions) => set((s) => ({
        subQuestions: [
          ...s.subQuestions,
          ...questions.map((q) => ({
            id: q.id,
            question: q.question,
            status: "pending" as const,
            retryCount: 0,
            isSupplementary: true,
          })),
        ],
      })),

      setPlanSummary: (summary) => set({ planSummary: summary }),

      setCurrentActivity: (activity) => set({ currentActivity: activity }),

      initSectionsV2: (titles) => set({
        sectionsProgressV2: titles.map((title) => ({
          title,
          status: "pending" as const,
        })),
      }),

      setSectionV2Status: (index, status, durationMs) => set((s) => ({
        sectionsProgressV2: s.sectionsProgressV2.map((sec, i) =>
          i === index ? { ...sec, status, ...(durationMs !== undefined ? { durationMs } : {}) } : sec
        ),
      })),

      reset: () => set({
        status: "idle",
        input: { ...DEFAULT_INPUT },
        result: null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
        generatedPlan: null,
        currentStageMessage: null,
        sectionsProgress: [],
        sourcesFound: 0,
        sourcesSelected: 0,
        generatedTitle: null,
        dynamicStages: [],
        activityLog: [],
        macroStages: [],
        subQuestions: [],
        sectionsProgressV2: [],
        planSummary: null,
        currentActivity: null,
      }),
    }),
    {
      name: "pp_deep_research",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (RUNNING_STATUSES.includes(state.status)) {
          state.status = "interrupted";
          state.errorMessage = "Research was interrupted (page was refreshed or closed).";
        }
      },
    }
  )
);

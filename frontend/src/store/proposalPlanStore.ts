import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ClarificationQuestion, ProposalPlanRunResult, ProposalPlanMode } from "@/types";
import type { DynamicStage, ActivityEvent, MacroStage, MacroStageStatus, SectionProgressV2 } from "@/store/deepResearchStore";

export type PPStatus =
  | "idle"
  | "validating"
  | "needs_clarification"
  | "selecting_context"
  | "generating_outline"
  | "drafting"
  | "updating_agenda"
  | "interrupted"
  | "completed"
  | "blocked"
  | "failed";

const RUNNING_STATUSES: PPStatus[] = [
  "validating", "selecting_context", "generating_outline", "drafting", "updating_agenda",
];

export interface ProposalPlanInput {
  mode: ProposalPlanMode;
  topic: string;
  problemStatement: string;
  focus: string;
  targetDeliverableId: string | null;
  useWorkspaceSources: boolean;
  useDeepResearchContext: boolean;
  deepResearchDeliverableIds: string[];
  notes: string;
  // Proposal-specific
  motivation: string;
  proposedIdea: string;
  evaluationDirection: string;
  constraints: string;
  // Research-plan-specific
  planningHorizon: string;
  intendedDeliverables: string;
  risks: string;
  milestoneNotes: string;
}

const DEFAULT_INPUT: ProposalPlanInput = {
  mode: "proposal",
  topic: "",
  problemStatement: "",
  focus: "",
  targetDeliverableId: null,
  useWorkspaceSources: true,
  useDeepResearchContext: false,
  deepResearchDeliverableIds: [],
  notes: "",
  motivation: "",
  proposedIdea: "",
  evaluationDirection: "",
  constraints: "",
  planningHorizon: "",
  intendedDeliverables: "",
  risks: "",
  milestoneNotes: "",
};

export interface PPSectionProgress {
  title: string;
  status: "pending" | "drafting" | "done" | "skipped";
  preview?: string;
}

interface ProposalPlanState {
  status: PPStatus;
  input: ProposalPlanInput;
  result: ProposalPlanRunResult | null;
  clarificationQuestions: ClarificationQuestion[];
  errorMessage: string | null;
  createdDeliverableId: string | null;

  // Streaming state
  currentStageMessage: string | null;
  sectionsProgress: PPSectionProgress[];
  sourcesSelected: number;
  generatedTitle: string | null;

  // Dynamic stages + activity
  dynamicStages: DynamicStage[];
  activityLog: ActivityEvent[];

  // Timeline v2 (vertical timeline)
  macroStages: MacroStage[];
  sectionsProgressV2: SectionProgressV2[];
  currentActivity: string | null;

  setInput: (partial: Partial<ProposalPlanInput>) => void;
  startRun: () => void;
  setStatus: (status: PPStatus) => void;
  setResult: (result: ProposalPlanRunResult) => void;
  setClarification: (questions: ClarificationQuestion[]) => void;
  setFailed: (message: string) => void;
  setBlocked: (message: string) => void;
  setCreatedDeliverableId: (id: string) => void;
  // Streaming actions
  setStageMessage: (message: string) => void;
  initSectionsProgress: (titles: string[]) => void;
  setSectionStatus: (index: number, status: PPSectionProgress["status"], preview?: string) => void;
  setSourcesSelected: (count: number) => void;
  setGeneratedTitle: (title: string) => void;
  // Dynamic stage actions
  pushStage: (key: string, label: string) => void;
  completeCurrentStage: () => void;
  pushActivity: (event: Omit<ActivityEvent, "id" | "timestamp">) => void;
  completeActivity: (id: string) => void;
  // Timeline v2 actions
  setMacroStageStatus: (key: string, status: MacroStageStatus) => void;
  setCurrentActivity: (activity: string | null) => void;
  initSectionsV2: (titles: string[]) => void;
  setSectionV2Status: (index: number, status: SectionProgressV2["status"], durationMs?: number) => void;
  reset: () => void;
}

export const useProposalPlanStore = create<ProposalPlanState>()(
  persist(
    (set) => ({
      status: "idle",
      input: { ...DEFAULT_INPUT },
      result: null,
      clarificationQuestions: [],
      errorMessage: null,
      createdDeliverableId: null,
      currentStageMessage: null,
      sectionsProgress: [],
      sourcesSelected: 0,
      generatedTitle: null,
      dynamicStages: [],
      activityLog: [],
      macroStages: [],
      sectionsProgressV2: [],
      currentActivity: null,

      setInput: (partial) => set((s) => ({ input: { ...s.input, ...partial } })),

      startRun: () => set({
        status: "validating",
        result: null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
        currentStageMessage: null,
        sectionsProgress: [],
        sourcesSelected: 0,
        generatedTitle: null,
        dynamicStages: [],
        activityLog: [],
        macroStages: [
          { key: "context", label: "Context", status: "pending" },
          { key: "draft", label: "Draft", status: "pending" },
          { key: "finalize", label: "Finalize", status: "pending" },
        ],
        sectionsProgressV2: [],
        currentActivity: null,
      }),

      setStatus: (status) => set({ status }),

      setResult: (result) => set({ status: "completed", result }),

      setClarification: (questions) => set({
        status: "needs_clarification",
        clarificationQuestions: questions,
      }),

      setFailed: (message) => set({ status: "failed", errorMessage: message }),

      setBlocked: (message) => set({ status: "blocked", errorMessage: message }),

      setCreatedDeliverableId: (id) => set({ createdDeliverableId: id }),

      setStageMessage: (message) => set({ currentStageMessage: message }),

      initSectionsProgress: (titles) => set({
        sectionsProgress: titles.map((title) => ({ title, status: "pending" as const })),
      }),

      setSectionStatus: (index, status, preview) => set((s) => ({
        sectionsProgress: s.sectionsProgress.map((sec, i) =>
          i === index ? { ...sec, status, ...(preview !== undefined ? { preview } : {}) } : sec
        ),
      })),

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

      setCurrentActivity: (activity) => set({ currentActivity: activity }),

      initSectionsV2: (titles) => set({
        sectionsProgressV2: titles.map((title) => ({ title, status: "pending" as const })),
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
        currentStageMessage: null,
        sectionsProgress: [],
        sourcesSelected: 0,
        generatedTitle: null,
        dynamicStages: [],
        activityLog: [],
        macroStages: [],
        sectionsProgressV2: [],
        currentActivity: null,
      }),
    }),
    {
      name: "pp_proposal_plan",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (RUNNING_STATUSES.includes(state.status)) {
          state.status = "interrupted";
          state.errorMessage = "Run was interrupted (page was refreshed or closed).";
        }
      },
    }
  )
);

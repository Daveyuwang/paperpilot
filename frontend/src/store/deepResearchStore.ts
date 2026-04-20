import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ClarificationQuestion, DeepResearchRunResult } from "@/types";

export type DeepResearchStatus =
  | "idle"
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
  "validating", "planning", "executing", "evaluating", "replanning", "synthesizing",
];

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

interface DeepResearchState {
  status: DeepResearchStatus;
  input: DeepResearchInput;
  result: DeepResearchRunResult | null;
  clarificationQuestions: ClarificationQuestion[];
  errorMessage: string | null;
  createdDeliverableId: string | null;

  // Streaming state
  currentStageMessage: string | null;
  sectionsProgress: SectionProgress[];
  sourcesFound: number;
  sourcesSelected: number;
  generatedTitle: string | null;

  setInput: (partial: Partial<DeepResearchInput>) => void;
  startRun: () => void;
  setStatus: (status: DeepResearchStatus) => void;
  setResult: (result: DeepResearchRunResult) => void;
  setClarification: (questions: ClarificationQuestion[]) => void;
  setFailed: (message: string) => void;
  setBlocked: (message: string) => void;
  setCreatedDeliverableId: (id: string) => void;
  // Streaming actions
  setStageMessage: (message: string) => void;
  initSectionsProgress: (titles: string[]) => void;
  setSectionStatus: (index: number, status: SectionProgress["status"], preview?: string) => void;
  setSourcesFound: (count: number) => void;
  setSourcesSelected: (count: number) => void;
  setGeneratedTitle: (title: string) => void;
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
      currentStageMessage: null,
      sectionsProgress: [],
      sourcesFound: 0,
      sourcesSelected: 0,
      generatedTitle: null,

      setInput: (partial) => set((s) => ({ input: { ...s.input, ...partial } })),

      startRun: () => set({
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

      setSourcesFound: (count) => set({ sourcesFound: count }),

      setSourcesSelected: (count) => set({ sourcesSelected: count }),

      setGeneratedTitle: (title) => set({ generatedTitle: title }),

      reset: () => set({
        status: "idle",
        input: { ...DEFAULT_INPUT },
        result: null,
        clarificationQuestions: [],
        errorMessage: null,
        createdDeliverableId: null,
        currentStageMessage: null,
        sectionsProgress: [],
        sourcesFound: 0,
        sourcesSelected: 0,
        generatedTitle: null,
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

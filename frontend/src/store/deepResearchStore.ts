import { create } from "zustand";
import type { ClarificationQuestion, DeepResearchRunResult } from "@/types";

export type DeepResearchStatus =
  | "idle"
  | "validating"
  | "needs_clarification"
  | "preparing_queries"
  | "discovering_sources"
  | "selecting_sources"
  | "generating_outline"
  | "drafting"
  | "updating_agenda"
  | "completed"
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

export const useDeepResearchStore = create<DeepResearchState>()((set) => ({
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

  // Streaming actions
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
}));

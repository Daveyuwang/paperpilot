import { create } from "zustand";
import type { ClarificationQuestion, ProposalPlanRunResult, ProposalPlanMode } from "@/types";

export type PPStatus =
  | "idle"
  | "validating"
  | "needs_clarification"
  | "selecting_context"
  | "generating_outline"
  | "drafting"
  | "updating_agenda"
  | "completed"
  | "blocked"
  | "failed";

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
  reset: () => void;
}

export const useProposalPlanStore = create<ProposalPlanState>()((set, get) => ({
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
    sourcesSelected: 0,
    generatedTitle: null,
  }),
}));

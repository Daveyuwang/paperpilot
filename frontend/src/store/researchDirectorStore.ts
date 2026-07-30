import { create } from "zustand";
import type {
  ResearchBrief,
  ResearchDirectorArtifact,
  ResearchDirectorRequestStatus,
  ResearchHandoffBundle,
  ResearchPlanBundle,
  ResearchPlanReview,
  ResearchProject,
  ResearchDirectorSnapshot,
} from "@/types/researchDirector";

export const EMPTY_RESEARCH_BRIEF: ResearchBrief = {
  title: "",
  research_question: "",
  objective: "",
  problem_statement: "",
  intended_contribution: "",
  scope: "",
  success_criteria: [],
  constraints: [],
  desired_deliverables: ["Evidence-backed implementation plan"],
  source_policy: {
    use_workspace_sources: true,
    discover_external_sources: false,
    prefer_primary_sources: true,
    time_horizon: "broad",
    must_include: [],
    must_exclude: [],
  },
  notes: "",
};

interface ResearchDirectorState {
  project: ResearchProject | null;
  plan: ResearchPlanBundle | null;
  review: ResearchPlanReview | null;
  handoff: ResearchHandoffBundle | null;
  briefDraft: ResearchBrief;
  activeArtifact: ResearchDirectorArtifact;
  selectedHypothesisId: string | null;
  revisionInstruction: string;
  requestStatus: ResearchDirectorRequestStatus;
  errorMessage: string | null;

  setBriefDraft: (partial: Partial<ResearchBrief>) => void;
  setSourcePolicy: (partial: Partial<ResearchBrief["source_policy"]>) => void;
  setActiveArtifact: (artifact: ResearchDirectorArtifact) => void;
  setSelectedHypothesis: (id: string | null) => void;
  setRevisionInstruction: (instruction: string) => void;
  startRequest: (status: Exclude<ResearchDirectorRequestStatus, "idle">) => void;
  finishRequest: () => void;
  setError: (message: string) => void;
  clearError: () => void;
  loadProject: (project: ResearchProject, plan?: ResearchPlanBundle | null) => void;
  loadSnapshot: (snapshot: ResearchDirectorSnapshot) => void;
  loadPlan: (plan: ResearchPlanBundle) => void;
  setReview: (review: ResearchPlanReview) => void;
  markApproved: (plan: ResearchPlanBundle) => void;
  setPreparedHandoff: (handoff: ResearchHandoffBundle) => void;
  setConfirmedHandoff: (handoff: ResearchHandoffBundle) => void;
  reset: () => void;
}

function nowIso() {
  return new Date().toISOString();
}

export const useResearchDirectorStore = create<ResearchDirectorState>()((set) => ({
  project: null,
  plan: null,
  review: null,
  handoff: null,
  briefDraft: { ...EMPTY_RESEARCH_BRIEF },
  activeArtifact: "brief",
  selectedHypothesisId: null,
  revisionInstruction: "",
  requestStatus: "idle",
  errorMessage: null,

  setBriefDraft: (partial) => set((state) => ({
    briefDraft: { ...state.briefDraft, ...partial },
  })),

  setSourcePolicy: (partial) => set((state) => ({
    briefDraft: {
      ...state.briefDraft,
      source_policy: { ...state.briefDraft.source_policy, ...partial },
    },
  })),

  setActiveArtifact: (activeArtifact) => set({ activeArtifact }),
  setSelectedHypothesis: (selectedHypothesisId) => set({ selectedHypothesisId }),
  setRevisionInstruction: (revisionInstruction) => set({ revisionInstruction }),

  startRequest: (requestStatus) => set({ requestStatus, errorMessage: null }),
  finishRequest: () => set({ requestStatus: "idle" }),
  setError: (errorMessage) => set({ requestStatus: "idle", errorMessage }),
  clearError: () => set({ errorMessage: null }),

  loadProject: (project, plan = null) => set({
    project,
    plan,
    review: plan?.review ?? null,
    handoff: null,
    briefDraft: plan?.research_brief ?? { ...EMPTY_RESEARCH_BRIEF, title: project.title, objective: project.objective },
    activeArtifact: plan ? "plan" : "brief",
    selectedHypothesisId: plan?.hypotheses[0]?.id ?? null,
    requestStatus: "idle",
    errorMessage: null,
  }),

  loadSnapshot: ({ project, plan, review, handoff }) => set({
    project,
    plan: { ...plan, review },
    review,
    handoff,
    briefDraft: plan.research_brief,
    activeArtifact: handoff ? "implementation" : review ? "review" : "plan",
    selectedHypothesisId: plan.hypotheses[0]?.id ?? null,
    revisionInstruction: "",
    requestStatus: "idle",
    errorMessage: null,
  }),

  loadPlan: (plan) => set((state) => ({
    plan,
    project: state.project
      ? {
          ...state.project,
          title: plan.research_brief.title || state.project.title,
          objective: plan.research_brief.objective || state.project.objective,
          brief_snapshot: plan.research_brief,
          brief_snapshot_source: plan.research_brief_source,
          status: plan.status,
          updated_at: plan.updated_at,
        }
      : {
          id: plan.research_project_id,
          workspace_id: plan.workspace_id,
          title: plan.research_brief.title,
          objective: plan.research_brief.objective,
          brief_snapshot: plan.research_brief,
          brief_snapshot_source: plan.research_brief_source,
          status: plan.status,
          created_at: plan.created_at,
          updated_at: plan.updated_at,
        },
    review: plan.review ?? null,
    handoff: null,
    briefDraft: plan.research_brief,
    activeArtifact: "plan",
    selectedHypothesisId: plan.hypotheses[0]?.id ?? null,
    revisionInstruction: "",
    requestStatus: "idle",
    errorMessage: null,
  })),

  setReview: (review) => set((state) => ({
    review,
    plan: state.plan ? { ...state.plan, status: "reviewed", review, updated_at: nowIso() } : null,
    project: state.project ? { ...state.project, status: "reviewed", updated_at: nowIso() } : null,
    activeArtifact: "review",
    requestStatus: "idle",
    errorMessage: null,
  })),

  markApproved: (approvedPlan) => set((state) => {
    const plan = approvedPlan;
    return {
      plan,
      project: state.project && plan
        ? { ...state.project, status: "approved", updated_at: plan.updated_at }
        : state.project,
      activeArtifact: "implementation",
      requestStatus: "idle",
      errorMessage: null,
    };
  }),

  setPreparedHandoff: (handoff) => set({
    handoff,
    activeArtifact: "implementation",
    requestStatus: "idle",
    errorMessage: null,
  }),

  setConfirmedHandoff: (handoff) => set((state) => ({
    handoff,
    plan: state.plan ? {
      ...state.plan,
      status: "handed_off",
      implementation_plan: {
        ...state.plan.implementation_plan,
        handoff: {
          ...state.plan.implementation_plan.handoff,
          status: "handed_off",
        },
      },
      updated_at: nowIso(),
    } : null,
    project: state.project ? { ...state.project, status: "handed_off", updated_at: nowIso() } : null,
    activeArtifact: "implementation",
    requestStatus: "idle",
    errorMessage: null,
  })),

  reset: () => set({
    project: null,
    plan: null,
    review: null,
    handoff: null,
    briefDraft: { ...EMPTY_RESEARCH_BRIEF },
    activeArtifact: "brief",
    selectedHypothesisId: null,
    revisionInstruction: "",
    requestStatus: "idle",
    errorMessage: null,
  }),
}));

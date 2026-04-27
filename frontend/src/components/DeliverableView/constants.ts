import type { DeliverableType } from "@/types";

export const TYPE_LABELS: Record<DeliverableType, string> = {
  deep_research: "Deep Research",
  proposal: "Proposal",
  research_plan: "Research Plan",
  notes: "Notes",
};

export const TYPE_DESCRIPTIONS: Record<DeliverableType, string> = {
  deep_research: "Structured brief for exploring a research problem in depth.",
  proposal: "Full proposal draft with problem, method, and evaluation plan.",
  research_plan: "Planning document for organizing your research process.",
  notes: "Freeform notes, questions, and ideas.",
};

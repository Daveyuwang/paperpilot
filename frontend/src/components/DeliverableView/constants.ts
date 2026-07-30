import type { DeliverableType } from "@/types";

export const TYPE_LABELS: Record<DeliverableType, string> = {
  deep_research: "Research brief",
  proposal: "Proposal",
  research_plan: "Research plan",
  notes: "Notes",
};

export const TYPE_DESCRIPTIONS: Record<DeliverableType, string> = {
  deep_research: "Synthesize a question and its evidence.",
  proposal: "Frame a problem, method, and evaluation.",
  research_plan: "Organize questions, milestones, and outputs.",
  notes: "Capture ideas and open questions.",
};

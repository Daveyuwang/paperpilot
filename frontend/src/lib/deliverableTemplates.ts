import type { DeliverableType, DeliverableSection } from "@/types";

let _counter = 0;
function sectionId(): string {
  return `sec-${Date.now()}-${++_counter}-${Math.random().toString(36).slice(2, 6)}`;
}

function makeSection(title: string, order: number): DeliverableSection {
  const now = Date.now();
  return { id: sectionId(), title, content: "", order, linkedSourceIds: [], createdAt: now, updatedAt: now };
}

const TEMPLATES: Record<DeliverableType, { defaultTitle: string; sections: string[] }> = {
  deep_research: {
    defaultTitle: "Deep Research Brief",
    sections: ["Problem Framing", "Current Landscape", "Key Approaches and Tradeoffs", "Open Questions / Next Directions"],
  },
  proposal: {
    defaultTitle: "Proposal Draft",
    sections: ["Problem", "Motivation", "Related Work", "Proposed Idea", "Method Sketch", "Evaluation Plan", "Risks / Limitations"],
  },
  research_plan: {
    defaultTitle: "Research Plan",
    sections: ["Goal", "Research Questions", "Source Collection Plan", "Reading Plan", "Deliverables", "Risks", "Milestones"],
  },
  notes: {
    defaultTitle: "Notes",
    sections: ["Notes", "Open Questions", "Ideas"],
  },
};

export function getTemplateInfo(type: DeliverableType) {
  return TEMPLATES[type];
}

export function createTemplateSections(type: DeliverableType): DeliverableSection[] {
  const t = TEMPLATES[type];
  return t.sections.map((title, i) => makeSection(title, i));
}

export function createCustomSections(titles: string[]): DeliverableSection[] {
  return titles.map((title, i) => makeSection(title, i));
}

export function createBlankSection(order: number): DeliverableSection {
  return makeSection("New Section", order);
}

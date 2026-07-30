import type { Deliverable } from "@/types";

export function getDeliverableRevision(deliverable: Deliverable): string {
  return JSON.stringify({
    title: deliverable.title,
    sections: [...deliverable.sections]
      .sort((left, right) => left.order - right.order)
      .map((section) => ({
        id: section.id,
        title: section.title,
        content: section.content,
        order: section.order,
        linkedSourceIds: [...section.linkedSourceIds].sort(),
      })),
  });
}

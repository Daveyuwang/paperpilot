import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { Deliverable, DeliverableType, DeliverableSection } from "@/types";
import { createTemplateSections, createCustomSections, createBlankSection, getTemplateInfo } from "@/lib/deliverableTemplates";

interface DeliverableStore {
  deliverablesByWorkspace: Record<string, Deliverable[]>;
  activeDeliverableIdByWorkspace: Record<string, string | null>;
  selectedSectionIdByDeliverable: Record<string, string | null>;

  getDeliverables: (workspaceId: string) => Deliverable[];
  getActiveDeliverable: (workspaceId: string) => Deliverable | null;
  getSelectedSectionId: (deliverableId: string) => string | null;

  createDeliverable: (workspaceId: string, type: DeliverableType, title?: string) => Deliverable;
  deleteDeliverable: (workspaceId: string, deliverableId: string) => void;
  duplicateDeliverable: (workspaceId: string, deliverableId: string) => Deliverable | null;
  renameDeliverable: (workspaceId: string, deliverableId: string, title: string) => void;
  setActiveDeliverable: (workspaceId: string, deliverableId: string | null) => void;

  selectSection: (deliverableId: string, sectionId: string | null) => void;
  updateSectionTitle: (workspaceId: string, deliverableId: string, sectionId: string, title: string) => void;
  updateSectionContent: (workspaceId: string, deliverableId: string, sectionId: string, content: string) => void;
  addSection: (workspaceId: string, deliverableId: string, afterSectionId?: string) => void;
  deleteSection: (workspaceId: string, deliverableId: string, sectionId: string) => void;
  moveSection: (workspaceId: string, deliverableId: string, sectionId: string, direction: "up" | "down") => void;

  linkSourceToSection: (workspaceId: string, deliverableId: string, sectionId: string, sourceId: string) => void;
  unlinkSourceFromSection: (workspaceId: string, deliverableId: string, sectionId: string, sourceId: string) => void;

  applyAIContent: (workspaceId: string, deliverableId: string, sectionId: string, content: string, mode: "draft" | "revise", sourceIdsUsed: string[]) => void;
  clearWorkspace: (workspaceId: string) => void;
}

function uid(): string {
  return `del-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function updateDeliverable(
  state: DeliverableStore,
  workspaceId: string,
  deliverableId: string,
  updater: (d: Deliverable) => Deliverable,
): Partial<DeliverableStore> {
  const list = state.deliverablesByWorkspace[workspaceId] ?? [];
  return {
    deliverablesByWorkspace: {
      ...state.deliverablesByWorkspace,
      [workspaceId]: list.map((d) => (d.id === deliverableId ? updater(d) : d)),
    },
  };
}

function updateSection(
  d: Deliverable,
  sectionId: string,
  updater: (s: DeliverableSection) => DeliverableSection,
): Deliverable {
  return {
    ...d,
    updatedAt: Date.now(),
    sections: d.sections.map((s) => (s.id === sectionId ? updater(s) : s)),
  };
}

function ensureUniqueTitle(base: string, existing: Deliverable[]): string {
  const titles = new Set(existing.map((d) => d.title));
  if (!titles.has(base)) return base;
  for (let i = 2; i <= 99; i++) {
    const candidate = `${base} (${i})`;
    if (!titles.has(candidate)) return candidate;
  }
  const ts = new Date().toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${base} — ${ts}`;
}

export const useDeliverableStore = create<DeliverableStore>()(
  persist(
    (set, get) => ({
      deliverablesByWorkspace: {},
      activeDeliverableIdByWorkspace: {},
      selectedSectionIdByDeliverable: {},

      getDeliverables: (wid) => get().deliverablesByWorkspace[wid] ?? [],

      getActiveDeliverable: (wid) => {
        const id = get().activeDeliverableIdByWorkspace[wid];
        if (!id) return null;
        return (get().deliverablesByWorkspace[wid] ?? []).find((d) => d.id === id) ?? null;
      },

      getSelectedSectionId: (did) => get().selectedSectionIdByDeliverable[did] ?? null,

      createDeliverable: (wid, type, title) => {
        const info = getTemplateInfo(type);
        const now = Date.now();
        const sections = createTemplateSections(type);
        const baseTitle = title ?? info.defaultTitle;
        const existing = get().deliverablesByWorkspace[wid] ?? [];
        const finalTitle = ensureUniqueTitle(baseTitle, existing);
        const d: Deliverable = {
          id: uid(),
          workspaceId: wid,
          type,
          title: finalTitle,
          sections,
          createdAt: now,
          updatedAt: now,
        };
        set((s) => ({
          deliverablesByWorkspace: {
            ...s.deliverablesByWorkspace,
            [wid]: [...(s.deliverablesByWorkspace[wid] ?? []), d],
          },
          activeDeliverableIdByWorkspace: {
            ...s.activeDeliverableIdByWorkspace,
            [wid]: d.id,
          },
          selectedSectionIdByDeliverable: {
            ...s.selectedSectionIdByDeliverable,
            [d.id]: sections[0]?.id ?? null,
          },
        }));
        return d;
      },

      deleteDeliverable: (wid, did) =>
        set((s) => {
          const list = (s.deliverablesByWorkspace[wid] ?? []).filter((d) => d.id !== did);
          const wasActive = s.activeDeliverableIdByWorkspace[wid] === did;
          const newActive = wasActive ? (list[0]?.id ?? null) : s.activeDeliverableIdByWorkspace[wid];
          const { [did]: _, ...restSelected } = s.selectedSectionIdByDeliverable;
          return {
            deliverablesByWorkspace: { ...s.deliverablesByWorkspace, [wid]: list },
            activeDeliverableIdByWorkspace: { ...s.activeDeliverableIdByWorkspace, [wid]: newActive },
            selectedSectionIdByDeliverable: restSelected,
          };
        }),

      duplicateDeliverable: (wid, did) => {
        const orig = (get().deliverablesByWorkspace[wid] ?? []).find((d) => d.id === did);
        if (!orig) return null;
        const now = Date.now();
        let counter = 0;
        const sections: DeliverableSection[] = orig.sections.map((s) => ({
          ...s,
          id: `sec-${now}-${++counter}-${Math.random().toString(36).slice(2, 6)}`,
          createdAt: now,
          updatedAt: now,
        }));
        const dup: Deliverable = {
          ...orig,
          id: uid(),
          title: `${orig.title} (copy)`,
          sections,
          createdAt: now,
          updatedAt: now,
        };
        set((s) => ({
          deliverablesByWorkspace: {
            ...s.deliverablesByWorkspace,
            [wid]: [...(s.deliverablesByWorkspace[wid] ?? []), dup],
          },
          activeDeliverableIdByWorkspace: {
            ...s.activeDeliverableIdByWorkspace,
            [wid]: dup.id,
          },
          selectedSectionIdByDeliverable: {
            ...s.selectedSectionIdByDeliverable,
            [dup.id]: sections[0]?.id ?? null,
          },
        }));
        return dup;
      },

      renameDeliverable: (wid, did, title) =>
        set((s) => updateDeliverable(s, wid, did, (d) => ({ ...d, title, updatedAt: Date.now() }))),

      setActiveDeliverable: (wid, did) =>
        set((s) => ({
          activeDeliverableIdByWorkspace: { ...s.activeDeliverableIdByWorkspace, [wid]: did },
        })),

      selectSection: (did, sid) =>
        set((s) => ({
          selectedSectionIdByDeliverable: { ...s.selectedSectionIdByDeliverable, [did]: sid },
        })),

      updateSectionTitle: (wid, did, sid, title) =>
        set((s) => updateDeliverable(s, wid, did, (d) => updateSection(d, sid, (sec) => ({ ...sec, title, updatedAt: Date.now() })))),

      updateSectionContent: (wid, did, sid, content) =>
        set((s) => updateDeliverable(s, wid, did, (d) => updateSection(d, sid, (sec) => ({ ...sec, content, updatedAt: Date.now() })))),

      addSection: (wid, did, afterSectionId) =>
        set((s) => {
          const list = s.deliverablesByWorkspace[wid] ?? [];
          const d = list.find((x) => x.id === did);
          if (!d) return s;
          const sorted = [...d.sections].sort((a, b) => a.order - b.order);
          let insertIdx = sorted.length;
          if (afterSectionId) {
            const idx = sorted.findIndex((sec) => sec.id === afterSectionId);
            if (idx >= 0) insertIdx = idx + 1;
          }
          const newSec = createBlankSection(insertIdx);
          const reordered = [
            ...sorted.slice(0, insertIdx),
            newSec,
            ...sorted.slice(insertIdx),
          ].map((sec, i) => ({ ...sec, order: i }));
          return {
            ...updateDeliverable(s, wid, did, (dd) => ({ ...dd, sections: reordered, updatedAt: Date.now() })),
            selectedSectionIdByDeliverable: { ...s.selectedSectionIdByDeliverable, [did]: newSec.id },
          };
        }),

      deleteSection: (wid, did, sid) =>
        set((s) => {
          const list = s.deliverablesByWorkspace[wid] ?? [];
          const d = list.find((x) => x.id === did);
          if (!d) return s;
          const sorted = [...d.sections].sort((a, b) => a.order - b.order);
          const idx = sorted.findIndex((sec) => sec.id === sid);
          if (idx < 0) return s;
          const remaining = sorted.filter((sec) => sec.id !== sid).map((sec, i) => ({ ...sec, order: i }));
          const currentSelected = s.selectedSectionIdByDeliverable[did];
          let newSelected = currentSelected;
          if (currentSelected === sid) {
            newSelected = remaining[Math.min(idx, remaining.length - 1)]?.id ?? null;
          }
          return {
            ...updateDeliverable(s, wid, did, (dd) => ({ ...dd, sections: remaining, updatedAt: Date.now() })),
            selectedSectionIdByDeliverable: { ...s.selectedSectionIdByDeliverable, [did]: newSelected },
          };
        }),

      moveSection: (wid, did, sid, direction) =>
        set((s) => {
          const list = s.deliverablesByWorkspace[wid] ?? [];
          const d = list.find((x) => x.id === did);
          if (!d) return s;
          const sorted = [...d.sections].sort((a, b) => a.order - b.order);
          const idx = sorted.findIndex((sec) => sec.id === sid);
          if (idx < 0) return s;
          const swapIdx = direction === "up" ? idx - 1 : idx + 1;
          if (swapIdx < 0 || swapIdx >= sorted.length) return s;
          [sorted[idx], sorted[swapIdx]] = [sorted[swapIdx], sorted[idx]];
          const reordered = sorted.map((sec, i) => ({ ...sec, order: i }));
          return updateDeliverable(s, wid, did, (dd) => ({ ...dd, sections: reordered, updatedAt: Date.now() }));
        }),

      linkSourceToSection: (wid, did, sid, sourceId) =>
        set((s) => updateDeliverable(s, wid, did, (d) => updateSection(d, sid, (sec) => ({
          ...sec,
          linkedSourceIds: sec.linkedSourceIds.includes(sourceId) ? sec.linkedSourceIds : [...sec.linkedSourceIds, sourceId],
          updatedAt: Date.now(),
        })))),

      unlinkSourceFromSection: (wid, did, sid, sourceId) =>
        set((s) => updateDeliverable(s, wid, did, (d) => updateSection(d, sid, (sec) => ({
          ...sec,
          linkedSourceIds: sec.linkedSourceIds.filter((id) => id !== sourceId),
          updatedAt: Date.now(),
        })))),

      applyAIContent: (wid, did, sid, content, mode, sourceIdsUsed) =>
        set((s) => updateDeliverable(s, wid, did, (d) => updateSection(d, sid, (sec) => ({
          ...sec,
          content,
          updatedAt: Date.now(),
          lastUpdatedBy: "ai",
          lastAIMode: mode,
          lastSourceIdsUsed: sourceIdsUsed,
        })))),

      clearWorkspace: (workspaceId) =>
        set((s) => {
          const { [workspaceId]: _, ...restDeliverables } = s.deliverablesByWorkspace;
          const { [workspaceId]: __, ...restActive } = s.activeDeliverableIdByWorkspace;
          const removedIds = (s.deliverablesByWorkspace[workspaceId] ?? []).map((d) => d.id);
          const selectedCopy = { ...s.selectedSectionIdByDeliverable };
          for (const id of removedIds) delete selectedCopy[id];
          return {
            deliverablesByWorkspace: restDeliverables,
            activeDeliverableIdByWorkspace: restActive,
            selectedSectionIdByDeliverable: selectedCopy,
          };
        }),
    }),
    {
      name: "pp_deliverables",
      storage: createJSONStorage(() => localStorage),
    }
  )
);

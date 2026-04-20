import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { GuideQuestion } from "@/types";

// ── Types ─────────────────────────────────────────────────────────────────

export type AgendaItemStatus = "pending" | "active" | "done" | "snoozed";
export type AgendaItemSource = "reading_path" | "user_question" | "system_followup";

export interface AgendaItem {
  id: string;
  paperId: string | null;
  title: string;
  description?: string;
  category?: "motivation" | "approach" | "experiments" | "takeaways" | "custom";
  status: AgendaItemStatus;
  source: AgendaItemSource;
  priority: number;
  linkedTrailQuestionId?: string;
  createdAt: number;
  updatedAt: number;
}

interface AgendaState {
  items: AgendaItem[];
  itemsByPaper: Record<string, AgendaItem[]>;

  bootstrapFromTrail: (paperId: string, questions: GuideQuestion[]) => void;
  setActive: (itemId: string) => void;
  markDone: (itemId: string) => void;
  markDoneByTrailId: (trailQuestionId: string) => void;
  snooze: (itemId: string) => void;
  reactivate: (itemId: string) => void;
  addUserQuestionItem: (paperId: string, question: string) => string;
  addSystemFollowup: (paperId: string | null, title: string, description?: string, category?: string, priority?: number) => string;
  resolveUpNext: () => AgendaItem | null;
  switchPaperAgenda: (paperId: string) => void;
  clearVolatile: () => void;
  getUpNext: () => AgendaItem | null;
}

function now() {
  return Date.now();
}

let _counter = 0;
function agendaId() {
  return `ag_${Date.now()}_${++_counter}`;
}

// ── Deterministic Up Next resolution ──────────────────────────────────────
// Priority: active item → first pending reading_path → first pending system_followup → null
// user_question items are lightweight history — they never drive the agenda.

function findUpNext(items: AgendaItem[]): AgendaItem | null {
  const active = items.find((i) => i.status === "active");
  if (active) return active;

  const pendingReadingPath = items
    .filter((i) => i.status === "pending" && i.source === "reading_path")
    .sort((a, b) => a.priority - b.priority);
  if (pendingReadingPath.length > 0) return pendingReadingPath[0];

  const pendingFollowup = items
    .filter((i) => i.status === "pending" && i.source === "system_followup")
    .sort((a, b) => a.priority - b.priority);
  if (pendingFollowup.length > 0) return pendingFollowup[0];

  return null;
}

// ── Store ─────────────────────────────────────────────────────────────────

export const useAgendaStore = create<AgendaState>()(
  persist(
    (set, get) => ({
      items: [],
      itemsByPaper: {},

      bootstrapFromTrail: (paperId, questions) => {
        const { itemsByPaper } = get();
        if (itemsByPaper[paperId]?.some((i) => i.source === "reading_path")) {
          set({ items: itemsByPaper[paperId] });
          return;
        }

        const newItems: AgendaItem[] = questions.map((q, idx) => ({
          id: agendaId(),
          paperId,
          title: q.question,
          category: q.stage,
          status: idx === 0 ? "active" as const : "pending" as const,
          source: "reading_path" as const,
          priority: q.order_index,
          linkedTrailQuestionId: q.id,
          createdAt: now(),
          updatedAt: now(),
        }));

        set((s) => ({
          items: newItems,
          itemsByPaper: { ...s.itemsByPaper, [paperId]: newItems },
        }));
      },

      setActive: (itemId) =>
        set((s) => {
          const items = s.items.map((i) => {
            if (i.id === itemId) return { ...i, status: "active" as const, updatedAt: now() };
            if (i.status === "active") return { ...i, status: "pending" as const, updatedAt: now() };
            return i;
          });
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        }),

      markDone: (itemId) =>
        set((s) => {
          const items = s.items.map((i) =>
            i.id === itemId ? { ...i, status: "done" as const, updatedAt: now() } : i
          );
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        }),

      markDoneByTrailId: (trailQuestionId) =>
        set((s) => {
          const items = s.items.map((i) =>
            i.linkedTrailQuestionId === trailQuestionId
              ? { ...i, status: "done" as const, updatedAt: now() }
              : i
          );
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        }),

      snooze: (itemId) =>
        set((s) => {
          const items = s.items.map((i) =>
            i.id === itemId ? { ...i, status: "snoozed" as const, updatedAt: now() } : i
          );
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        }),

      reactivate: (itemId) =>
        set((s) => {
          const items = s.items.map((i) => {
            if (i.id === itemId) return { ...i, status: "active" as const, updatedAt: now() };
            if (i.status === "active") return { ...i, status: "pending" as const, updatedAt: now() };
            return i;
          });
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        }),

      addUserQuestionItem: (paperId, question) => {
        const id = agendaId();
        const maxPriority = Math.max(0, ...get().items.map((i) => i.priority));
        const item: AgendaItem = {
          id,
          paperId,
          title: question,
          category: "custom",
          status: "pending",
          source: "user_question",
          priority: maxPriority + 1,
          createdAt: now(),
          updatedAt: now(),
        };
        set((s) => {
          const items = [...s.items, item];
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        });
        return id;
      },

      addSystemFollowup: (paperId, title, description, category, priority) => {
        const id = agendaId();
        const maxPriority = Math.max(0, ...get().items.map((i) => i.priority));
        const item: AgendaItem = {
          id,
          paperId: paperId ?? null,
          title,
          description,
          category: (category as AgendaItem["category"]) ?? "custom",
          status: "pending",
          source: "system_followup",
          priority: priority ?? maxPriority + 1,
          createdAt: now(),
          updatedAt: now(),
        };
        set((s) => {
          const items = [...s.items, item];
          return { items, itemsByPaper: syncPaperBucket(s.itemsByPaper, items) };
        });
        return id;
      },

      resolveUpNext: () => {
        const { items } = get();
        const upNext = findUpNext(items);
        if (upNext && upNext.status !== "active") {
          get().setActive(upNext.id);
          return { ...upNext, status: "active" as const };
        }
        return upNext;
      },

      switchPaperAgenda: (paperId) => {
        const { items, itemsByPaper } = get();
        const currentPaperId = items[0]?.paperId;
        if (currentPaperId && currentPaperId !== paperId) {
          const updated = { ...itemsByPaper, [currentPaperId]: items };
          const restored = updated[paperId] ?? [];
          set({ items: restored, itemsByPaper: updated });
        } else if (!currentPaperId) {
          set({ items: itemsByPaper[paperId] ?? [] });
        }
      },

      clearVolatile: () => {
        const { items, itemsByPaper } = get();
        // Save current items back to their paper bucket before clearing
        const currentPaperId = items[0]?.paperId;
        if (currentPaperId) {
          set({
            items: [],
            itemsByPaper: { ...itemsByPaper, [currentPaperId]: items },
          });
        } else {
          set({ items: [] });
        }
      },

      getUpNext: () => findUpNext(get().items),
    }),
    {
      name: "pp_agenda",
      storage: createJSONStorage(() => localStorage),
    }
  )
);

function syncPaperBucket(
  byPaper: Record<string, AgendaItem[]>,
  items: AgendaItem[]
): Record<string, AgendaItem[]> {
  const paperId = items[0]?.paperId;
  if (!paperId) return byPaper;
  return { ...byPaper, [paperId]: items };
}

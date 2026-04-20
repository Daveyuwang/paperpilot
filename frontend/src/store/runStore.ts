import { create } from "zustand";

export type RunStatus = "idle" | "preparing" | "generating" | "awaiting_apply" | "completed" | "failed" | "blocked";

export interface SectionPreview {
  sectionId: string;
  mode: "fill_empty" | "preview_replace";
  generatedContent: string;
  sourceIdsUsed: string[];
  notes?: string;
}

interface RunState {
  status: RunStatus;
  action: string | null;
  message: string | null;
  previews: SectionPreview[];
  skippedSectionIds: string[];

  setStatus: (status: RunStatus, message?: string) => void;
  startRun: (action: string) => void;
  setResult: (previews: SectionPreview[], skipped: string[], message?: string) => void;
  setFailed: (message: string) => void;
  setBlocked: (message: string) => void;
  clearPreviews: () => void;
  removePreview: (sectionId: string) => void;
  reset: () => void;
}

export const useRunStore = create<RunState>()((set) => ({
  status: "idle",
  action: null,
  message: null,
  previews: [],
  skippedSectionIds: [],

  setStatus: (status, message) => set({ status, message: message ?? null }),

  startRun: (action) => set({ status: "preparing", action, message: null, previews: [], skippedSectionIds: [] }),

  setResult: (previews, skipped, message) => {
    const hasReplace = previews.some((p) => p.mode === "preview_replace");
    set({
      status: hasReplace ? "awaiting_apply" : "completed",
      previews,
      skippedSectionIds: skipped,
      message: message ?? null,
    });
  },

  setFailed: (message) => set({ status: "failed", message }),

  setBlocked: (message) => set({ status: "blocked", message }),

  clearPreviews: () => set({ status: "idle", previews: [], skippedSectionIds: [], message: null }),

  removePreview: (sectionId) =>
    set((s) => {
      const remaining = s.previews.filter((p) => p.sectionId !== sectionId);
      return {
        previews: remaining,
        status: remaining.length === 0 ? "idle" : s.status,
      };
    }),

  reset: () => set({ status: "idle", action: null, message: null, previews: [], skippedSectionIds: [] }),
}));

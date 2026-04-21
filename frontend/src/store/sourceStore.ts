import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { WorkspaceSource, SourceLabel, DiscoveredSource, PaperListItem, Paper } from "@/types";

function normalizeTitle(t: string): string {
  return t.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function isDuplicateIn(existing: WorkspaceSource[], candidate: { doi?: string | null; arxiv_id?: string | null; title: string }): boolean {
  for (const s of existing) {
    if (candidate.doi && s.doi && candidate.doi.toLowerCase() === s.doi.toLowerCase()) return true;
    if (candidate.arxiv_id && s.arxiv_id && candidate.arxiv_id.toLowerCase() === s.arxiv_id.toLowerCase()) return true;
    if (normalizeTitle(candidate.title) === normalizeTitle(s.title)) return true;
  }
  return false;
}

interface SourceStore {
  sourcesByWorkspace: Record<string, WorkspaceSource[]>;

  getSources: (workspaceId: string) => WorkspaceSource[];
  getIncludedSources: (workspaceId: string) => WorkspaceSource[];
  getByLabel: (workspaceId: string, label: SourceLabel) => WorkspaceSource[];
  isDuplicate: (workspaceId: string, d: DiscoveredSource) => boolean;

  addFromUpload: (workspaceId: string, paper: Paper | PaperListItem) => void;
  addFromDiscovery: (workspaceId: string, d: DiscoveredSource) => void;
  setLabel: (workspaceId: string, id: string, label: SourceLabel) => void;
  setIncluded: (workspaceId: string, id: string, included: boolean) => void;
  setAllIncluded: (workspaceId: string, included: boolean) => void;
  removeSource: (workspaceId: string, id: string) => void;
  syncUploads: (workspaceId: string, papers: PaperListItem[]) => void;
  clearWorkspace: (workspaceId: string) => void;
}

export const useSourceStore = create<SourceStore>()(
  persist(
    (set, get) => ({
      sourcesByWorkspace: {},

      getSources: (workspaceId) => get().sourcesByWorkspace[workspaceId] ?? [],

      getIncludedSources: (workspaceId) =>
        (get().sourcesByWorkspace[workspaceId] ?? []).filter((s) => s.included),

      getByLabel: (workspaceId, label) =>
        (get().sourcesByWorkspace[workspaceId] ?? []).filter((s) => s.label === label),

      isDuplicate: (workspaceId, d) =>
        isDuplicateIn(get().sourcesByWorkspace[workspaceId] ?? [], d),

      addFromUpload: (workspaceId, paper) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        if (existing.some((s) => s.paper_id === paper.id)) return;
        const title = paper.title || paper.filename;
        if (isDuplicateIn(existing, { title })) return;
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: [
              ...existing,
              {
                id: `upload-${paper.id}`,
                title,
                authors: [],
                year: null,
                doi: null,
                arxiv_id: null,
                abstract: null,
                url: null,
                citation_count: null,
                provider: "upload",
                paper_id: paper.id,
                label: "core",
                added_at: new Date().toISOString(),
                included: true,
              },
            ],
          },
        });
      },

      addFromDiscovery: (workspaceId, d) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        if (isDuplicateIn(existing, d)) return;
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: [
              ...existing,
              {
                id: `${d.provider}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                title: d.title,
                authors: d.authors,
                year: d.year,
                doi: d.doi,
                arxiv_id: d.arxiv_id,
                abstract: d.abstract,
                url: d.url,
                citation_count: d.citation_count,
                provider: d.provider as "upload" | "openalex" | "arxiv",
                paper_id: null,
                label: "general",
                added_at: new Date().toISOString(),
                included: true,
              },
            ],
          },
        });
      },

      setLabel: (workspaceId, id, label) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: existing.map((src) =>
              src.id === id ? { ...src, label } : src
            ),
          },
        });
      },

      setIncluded: (workspaceId, id, included) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: existing.map((src) =>
              src.id === id ? { ...src, included } : src
            ),
          },
        });
      },

      setAllIncluded: (workspaceId, included) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: existing.map((src) => ({ ...src, included })),
          },
        });
      },

      removeSource: (workspaceId, id) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: existing.filter((src) => src.id !== id),
          },
        });
      },

      syncUploads: (workspaceId, papers) => {
        const existing = get().sourcesByWorkspace[workspaceId] ?? [];
        const uploadIds = new Set(existing.filter((s) => s.provider === "upload").map((s) => s.paper_id));
        const newSources: WorkspaceSource[] = [];
        for (const p of papers) {
          if (p.status !== "ready") continue;
          if (uploadIds.has(p.id)) continue;
          const title = p.title || p.filename;
          if (isDuplicateIn(existing, { title })) continue;
          newSources.push({
            id: `upload-${p.id}`,
            title,
            authors: [],
            year: null,
            doi: null,
            arxiv_id: null,
            abstract: null,
            url: null,
            citation_count: null,
            provider: "upload",
            paper_id: p.id,
            label: "core",
            added_at: new Date().toISOString(),
            included: true,
          });
        }
        if (newSources.length > 0) {
          set({
            sourcesByWorkspace: {
              ...get().sourcesByWorkspace,
              [workspaceId]: [...existing, ...newSources],
            },
          });
        }
      },

      clearWorkspace: (workspaceId) =>
        set((s) => {
          const { [workspaceId]: _, ...rest } = s.sourcesByWorkspace;
          return { sourcesByWorkspace: rest };
        }),
    }),
    {
      name: "pp_sources",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        const raw = state as unknown as Record<string, unknown>;
        // Migrate old flat sources[] → sourcesByWorkspace["default"]
        if (Array.isArray(raw.sources) && !state.sourcesByWorkspace) {
          const oldSources = raw.sources as WorkspaceSource[];
          state.sourcesByWorkspace = {
            default: oldSources.map((s) => ({
              ...s,
              label: (s.label as string) === "discarded" || (s.label as string) === "maybe" ? "general" as const : s.label,
              included: (s.label as string) === "discarded" ? false : (s.included ?? true),
            })),
          };
          delete raw.sources;
        }
        // Ensure included field exists + migrate old labels on all sources
        for (const wid of Object.keys(state.sourcesByWorkspace)) {
          state.sourcesByWorkspace[wid] = state.sourcesByWorkspace[wid].map((s: WorkspaceSource) => ({
            ...s,
            label: (s.label as string) === "discarded" || (s.label as string) === "maybe"
              ? "general" as const
              : s.label,
            included: (s.label as string) === "discarded"
              ? false
              : (s.included ?? true),
          }));
        }
      },
    }
  )
);

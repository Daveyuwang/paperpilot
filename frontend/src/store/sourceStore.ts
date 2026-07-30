import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { WorkspaceSource, SourceLabel, DiscoveredSource, PaperListItem, Paper } from "@/types";

function normalizeTitle(t: string): string {
  return (t.normalize("NFKC").toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).join("");
}

function findDuplicateIndex(existing: WorkspaceSource[], candidate: { doi?: string | null; arxiv_id?: string | null; title: string }): number {
  const candidateTitle = normalizeTitle(candidate.title);
  for (let index = 0; index < existing.length; index += 1) {
    const s = existing[index];
    if (candidate.doi && s.doi && candidate.doi.toLowerCase() === s.doi.toLowerCase()) return index;
    if (candidate.arxiv_id && s.arxiv_id && candidate.arxiv_id.toLowerCase() === s.arxiv_id.toLowerCase()) return index;
    const existingTitle = normalizeTitle(s.title);
    if (candidateTitle && existingTitle && candidateTitle === existingTitle) return index;
  }
  return -1;
}

function isDuplicateIn(existing: WorkspaceSource[], candidate: { doi?: string | null; arxiv_id?: string | null; title: string }): boolean {
  return findDuplicateIndex(existing, candidate) >= 0;
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
        const duplicateIndex = findDuplicateIndex(existing, { title });
        if (duplicateIndex >= 0) {
          set({
            sourcesByWorkspace: {
              ...get().sourcesByWorkspace,
              [workspaceId]: existing.map((source, index) =>
                index === duplicateIndex ? { ...source, paper_id: paper.id } : source
              ),
            },
          });
          return;
        }
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
        const duplicateIndex = findDuplicateIndex(existing, d);
        if (duplicateIndex >= 0) {
          set({
            sourcesByWorkspace: {
              ...get().sourcesByWorkspace,
              [workspaceId]: existing.map((source, index) => index === duplicateIndex ? {
                ...source,
                title: d.title || source.title,
                authors: d.authors.length > 0 ? d.authors : source.authors,
                year: d.year ?? source.year,
                doi: d.doi ?? source.doi,
                arxiv_id: d.arxiv_id ?? source.arxiv_id,
                abstract: d.abstract ?? source.abstract,
                url: d.url ?? source.url,
                citation_count: d.citation_count ?? source.citation_count,
              } : source),
            },
          });
          return;
        }
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
        const canonicalPaperIds = new Set(papers.map((paper) => paper.id));
        const priorUploads = new Map(
          existing
            .filter((source) => source.provider === "upload" && source.paper_id)
            .map((source) => [source.paper_id as string, source]),
        );
        const reconciled: WorkspaceSource[] = existing
          .filter((source) => source.provider !== "upload")
          .map((source) => source.paper_id && !canonicalPaperIds.has(source.paper_id)
            ? { ...source, paper_id: null }
            : source
          );

        for (const p of papers) {
          const title = p.title || p.filename;
          const linkedIndex = reconciled.findIndex((source) => source.paper_id === p.id);
          if (linkedIndex >= 0) continue;

          const priorUpload = priorUploads.get(p.id);
          if (priorUpload) {
            reconciled.push({ ...priorUpload, title });
            continue;
          }
          if (p.status !== "ready") continue;

          const duplicateIndex = findDuplicateIndex(reconciled, { title });
          if (duplicateIndex >= 0) {
            reconciled[duplicateIndex] = { ...reconciled[duplicateIndex], paper_id: p.id };
            continue;
          }
          reconciled.push({
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
        set({
          sourcesByWorkspace: {
            ...get().sourcesByWorkspace,
            [workspaceId]: reconciled,
          },
        });
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

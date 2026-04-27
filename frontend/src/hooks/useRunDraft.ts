import { useCallback, useRef } from "react";
import { useSourceStore } from "@/store/sourceStore";
import { usePaperStore } from "@/store/paperStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useRunStore } from "@/store/runStore";
import { api } from "@/api/client";
import type { Deliverable, DeliverableSection, WorkspaceSource } from "@/types";

function buildSourcesPayload(sources: WorkspaceSource[]) {
  return sources
    .map((s) => ({
      id: s.id,
      title: s.title,
      authors: s.authors,
      year: s.year,
      abstract: s.abstract,
      provider: s.provider,
      paper_id: s.paper_id,
    }));
}

function buildSectionsPayload(sections: DeliverableSection[]) {
  return sections.map((s) => ({
    id: s.id,
    title: s.title,
    content: s.content,
    order: s.order,
    linkedSourceIds: s.linkedSourceIds,
  }));
}

export function useRunDraft(deliverable: Deliverable | null, workspaceId: string) {
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const { applyAIContent } = useDeliverableStore();
  const { startRun, setResult, setFailed, setBlocked } = useRunStore();
  const sources = getIncludedSources(workspaceId);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (action: string, selectedSectionId?: string, revisionInstruction?: string) => {
      if (!deliverable) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      startRun(action);
      try {
        const store = useRunStore.getState();
        store.setStatus("generating");

        const payload = {
          action,
          workspace_id: workspaceId,
          deliverable_id: deliverable.id,
          deliverable_type: deliverable.type,
          deliverable_title: deliverable.title,
          sections: buildSectionsPayload(deliverable.sections),
          sources: buildSourcesPayload(sources),
          selected_section_id: selectedSectionId ?? null,
          revision_instruction: revisionInstruction ?? null,
          active_paper_id: activePaper?.id ?? null,
        };

        await api.runDraftStream(payload, (event) => {
          if (controller.signal.aborted) return;
          const s = useRunStore.getState();
          const type = event.type as string;

          if (type === "stage") {
            const msg = event.message as string | undefined;
            s.setStatus("generating", msg);
          } else if (type === "blocked") {
            s.setStatus("blocked", (event.message as string) ?? "Blocked");
          } else if (type === "error") {
            s.setStatus("failed", (event.message as string) ?? "Generation failed");
          } else if (type === "result") {
            const data = event.data as Record<string, unknown>;
            const updates = (data.updates as { sectionId: string; mode: string; generatedContent: string; sourceIdsUsed: string[]; notes?: string }[]) ?? [];
            const skipped = (data.skippedSectionIds as string[]) ?? [];
            const message = data.message as string | undefined;

            if (data.status === "blocked") {
              s.setStatus("blocked", message ?? "Blocked");
            } else if (data.status === "failed") {
              s.setStatus("failed", message ?? "Generation failed");
            } else {
              const allPreviews = updates.map((u) => ({
                sectionId: u.sectionId,
                mode: u.mode as "fill_empty" | "preview_replace",
                generatedContent: u.generatedContent,
                sourceIdsUsed: u.sourceIdsUsed,
                notes: u.notes,
              }));

              // Auto-apply fill_empty results (no user review needed for empty sections)
              const fillEmpty = allPreviews.filter((p) => p.mode === "fill_empty");
              const needsReview = allPreviews.filter((p) => p.mode === "preview_replace");

              for (const p of fillEmpty) {
                applyAIContent(workspaceId, deliverable!.id, p.sectionId, p.generatedContent, "draft", p.sourceIdsUsed);
              }

              if (needsReview.length > 0) {
                setResult(needsReview, skipped, message);
              } else {
                setResult([], skipped, message ?? `Auto-applied ${fillEmpty.length} section${fillEmpty.length !== 1 ? "s" : ""}.`);
              }
            }
          }
        }, controller.signal);

        // Fallback if stream ended without terminal event
        const finalStatus = useRunStore.getState().status;
        if (finalStatus === "generating") {
          setFailed("Stream ended unexpectedly. Please try again.");
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setFailed(err instanceof Error ? err.message : "Request failed");
      }
    },
    [deliverable, workspaceId, sources, activePaper, startRun, setResult, setFailed, setBlocked, applyAIContent],
  );

  return run;
}

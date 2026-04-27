import { useCallback, useRef } from "react";
import { useProposalPlanStore, type PPStatus } from "@/store/proposalPlanStore";
import type { ActivityEvent } from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import type { ProposalPlanRunResult, ClarificationQuestion, DeliverableType } from "@/types";

export function useProposalPlanRun(workspaceId: string) {
  const store = useProposalPlanStore();
  const { input, startRun, setFailed } = store;
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const deliverableStore = useDeliverableStore();
  const agendaStore = useAgendaStore();
  const { setActiveViewerTab, setSelectedNav } = useWorkspaceStore();

  const sources = getIncludedSources(workspaceId);
  const allDeliverables = deliverableStore.getDeliverables(workspaceId);
  const drDeliverables = allDeliverables.filter((d) => d.type === "deep_research");
  const abortRef = useRef<AbortController | null>(null);

  const runStream = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    startRun();

    const wsSources = sources.map((s) => ({
      id: s.id, title: s.title, authors: s.authors, year: s.year,
      abstract: s.abstract, provider: s.provider, paper_id: s.paper_id, label: s.label,
    }));

    const drContext = input.useDeepResearchContext
      ? drDeliverables
          .filter((d) => input.deepResearchDeliverableIds.includes(d.id))
          .map((d) => ({
            deliverable_id: d.id,
            title: d.title,
            sections: d.sections.map((s) => ({
              id: s.id, title: s.title, content: s.content, order: s.order, linkedSourceIds: s.linkedSourceIds,
            })),
          }))
      : [];

    let existingSections: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[] = [];
    if (input.targetDeliverableId) {
      const target = allDeliverables.find((d) => d.id === input.targetDeliverableId);
      if (target) {
        existingSections = target.sections.map((s) => ({
          id: s.id, title: s.title, content: s.content, order: s.order, linkedSourceIds: s.linkedSourceIds,
        }));
      }
    }

    const payload = {
      input: {
        mode: input.mode,
        topic: input.topic,
        problem_statement: input.problemStatement || null,
        focus: input.focus || null,
        target_deliverable_id: input.targetDeliverableId,
        use_workspace_sources: input.useWorkspaceSources,
        use_deep_research_context: input.useDeepResearchContext,
        deep_research_deliverable_ids: input.deepResearchDeliverableIds,
        notes: input.notes || null,
        motivation: input.motivation || null,
        proposed_idea: input.proposedIdea || null,
        evaluation_direction: input.evaluationDirection || null,
        constraints: input.constraints || null,
        planning_horizon: input.planningHorizon || null,
        intended_deliverables: input.intendedDeliverables || null,
        risks: input.risks || null,
        milestone_notes: input.milestoneNotes || null,
      },
      workspace_id: workspaceId,
      workspace_sources: wsSources,
      existing_sections: existingSections.length > 0 ? existingSections : undefined,
      deep_research_context: drContext.length > 0 ? drContext : undefined,
      active_paper_id: activePaper?.id ?? null,
    };

    try {
      await api.runProposalPlanStream(payload, (event) => {
        if (controller.signal.aborted) return;
        const s = useProposalPlanStore.getState();
        const type = event.type as string;

        if (type === "stage") {
          const stage = event.stage as string;
          const msg = event.message as string | undefined;
          s.setStatus(stage as PPStatus);
          if (msg) s.setStageMessage(msg);
          s.pushStage(stage, msg || stage);
          if (stage === "validating" || stage === "selecting_context") {
            s.setMacroStageStatus("context", "in_progress");
            s.setCurrentActivity(msg || "Preparing context...");
          } else if (stage === "generating_outline") {
            s.setMacroStageStatus("context", "completed");
            s.setMacroStageStatus("draft", "in_progress");
            s.setCurrentActivity(msg || "Generating outline...");
          } else if (stage === "drafting") {
            s.setCurrentActivity(msg || "Drafting sections...");
          } else if (stage === "updating_agenda") {
            s.setMacroStageStatus("draft", "completed");
            s.setMacroStageStatus("finalize", "in_progress");
            s.setCurrentActivity(msg || "Updating agenda...");
          }
        } else if (type === "activity") {
          const actType = (event.activity_type as string) || "thinking";
          const label = (event.label as string) || "Working...";
          s.pushActivity({ type: actType as ActivityEvent["type"], label, status: "active" });
          if (actType !== "done") s.setCurrentActivity(label);
        } else if (type === "progress") {
          if (event.sources_selected !== undefined) s.setSourcesSelected(event.sources_selected as number);
          if (event.message) s.setStageMessage(event.message as string);
        } else if (type === "synthesize_outline") {
          const headings = event.section_headings as string[];
          const title = event.title as string;
          if (title) s.setGeneratedTitle(title);
          if (headings) {
            s.initSectionsProgress(headings);
            s.initSectionsV2(headings);
          }
          s.setCurrentActivity("Writing sections...");
        } else if (type === "synthesize_section") {
          const idx = event.section_index as number;
          const sectionStatus = event.status as string;
          const secTitle = event.section_title as string | undefined;
          const durationMs = event.duration_ms as number | undefined;
          if (sectionStatus === "writing") {
            s.setSectionStatus(idx, "drafting");
            s.setSectionV2Status(idx, "drafting");
            if (secTitle) s.setCurrentActivity(`Writing: ${secTitle}`);
          } else if (sectionStatus === "done") {
            s.setSectionStatus(idx, "done");
            s.setSectionV2Status(idx, "done", durationMs);
          } else if (sectionStatus === "failed") {
            s.setSectionStatus(idx, "done");
            s.setSectionV2Status(idx, "failed");
          }
        } else if (type === "sections_outline") {
          const titles = event.titles as string[];
          if (titles) s.initSectionsProgress(titles);
        } else if (type === "tailored_outline") {
          const genTitle = event.generated_title as string | undefined;
          const sections = event.sections as string[] | undefined;
          if (genTitle) s.setGeneratedTitle(genTitle);
          if (sections) s.initSectionsProgress(sections);
        } else if (type === "section_start") {
          const idx = event.index as number;
          s.setSectionStatus(idx, "drafting");
        } else if (type === "section_complete") {
          const idx = event.index as number;
          const skipped = event.skipped as boolean;
          if (skipped) {
            s.setSectionStatus(idx, "skipped");
          } else {
            const preview = event.preview as string | undefined;
            s.setSectionStatus(idx, "done", preview);
          }
        } else if (type === "result") {
          const status = event.status as string;

          if (status === "needs_clarification") {
            const questions = event.clarification_questions as ClarificationQuestion[];
            s.setClarification(questions);
            return;
          }
          if (status === "failed") {
            s.setFailed((event.message as string) ?? "Run failed");
            return;
          }
          if (status === "blocked") {
            s.setBlocked((event.message as string) ?? "Blocked");
            return;
          }

          const res = (event.data ?? event) as ProposalPlanRunResult;

          let deliverable = input.targetDeliverableId
            ? allDeliverables.find((d) => d.id === input.targetDeliverableId)
            : null;

          if (!deliverable) {
            const title = res.generated_title || (input.mode === "proposal" ? "Proposal" : "Research Plan");
            const delType: DeliverableType = input.mode;
            const newDel = deliverableStore.createDeliverable(workspaceId, delType, title);
            deliverable = newDel;
            s.setCreatedDeliverableId(newDel.id);
          } else if (res.generated_title) {
            deliverableStore.renameDeliverable(workspaceId, deliverable.id, res.generated_title);
          }

          if (deliverable && res.section_updates) {
            const sortedSections = [...deliverable.sections].sort((a, b) => a.order - b.order);
            for (const update of res.section_updates) {
              if (!update.generated_content.trim()) continue;
              const targetSection = sortedSections.find((sec) => sec.id === update.section_id) ?? sortedSections[0];
              if (!targetSection) continue;
              deliverableStore.applyAIContent(
                workspaceId, deliverable.id, targetSection.id,
                update.generated_content, "draft", update.source_ids_used,
              );
            }
          }

          if (res.follow_up_items) {
            for (const item of res.follow_up_items) {
              agendaStore.addSystemFollowup(
                activePaper?.id ?? null,
                item.title,
                item.description ?? undefined,
                item.category ?? undefined,
                item.priority,
              );
            }
          }

          s.setResult(res);
          s.setMacroStageStatus("finalize", "completed");
          s.setCurrentActivity(null);

          const finalDelId = s.createdDeliverableId ?? deliverable?.id;
          if (finalDelId) {
            deliverableStore.setActiveDeliverable(workspaceId, finalDelId);
            const del = deliverableStore.getDeliverables(workspaceId).find((d) => d.id === finalDelId);
            const firstSection = del?.sections.sort((a, b) => a.order - b.order)[0];
            if (firstSection) deliverableStore.selectSection(finalDelId, firstSection.id);
            setActiveViewerTab("deliverable");
            setSelectedNav("reader");
          }
        }
      }, controller.signal);

      const finalStatus = useProposalPlanStore.getState().status;
      if (!["completed", "failed", "blocked", "needs_clarification"].includes(finalStatus)) {
        setFailed("Stream ended unexpectedly. Please try again.");
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setFailed(err instanceof Error ? err.message : "Request failed");
    }
  }, [input, sources, workspaceId, activePaper, allDeliverables, drDeliverables, startRun, setFailed, deliverableStore, agendaStore, setActiveViewerTab, setSelectedNav]);

  return runStream;
}

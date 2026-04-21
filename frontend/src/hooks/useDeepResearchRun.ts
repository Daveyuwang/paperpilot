import { useCallback } from "react";
import { useDeepResearchStore, type DeepResearchStatus } from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import type { DeepResearchRunResult, ClarificationQuestion } from "@/types";

export function useDeepResearchRun(workspaceId: string) {
  const store = useDeepResearchStore();
  const { input, startRun, setFailed } = store;
  const { getIncludedSources, addFromDiscovery, setLabel } = useSourceStore();
  const { activePaper } = usePaperStore();
  const { getDeliverables, createDeliverable, applyAIContent, setActiveDeliverable, renameDeliverable, selectSection } = useDeliverableStore();
  const { addSystemFollowup } = useAgendaStore();
  const { setSelectedNav, setActiveViewerTab } = useWorkspaceStore();

  const sources = getIncludedSources(workspaceId);
  const deliverables = getDeliverables(workspaceId);

  const runStream = useCallback(async () => {
    const { generatedPlan } = useDeepResearchStore.getState();
    startRun();

    const wsPayload = sources.map((s) => ({
      id: s.id, title: s.title, authors: s.authors,
      year: s.year, abstract: s.abstract, provider: s.provider,
      paper_id: s.paper_id, label: s.label,
    }));

    const existingDel = input.targetDeliverableId
      ? deliverables.find((d) => d.id === input.targetDeliverableId)
      : null;

    const existingSections = existingDel
      ? existingDel.sections.map((s) => ({
          id: s.id, title: s.title, content: s.content,
          order: s.order, linkedSourceIds: s.linkedSourceIds,
        }))
      : [];

    const prePlan = generatedPlan
      ? {
          sub_questions: generatedPlan.subQuestions.map((sq) => ({
            id: sq.id,
            question: sq.question,
            search_queries: sq.searchQueries,
            priority: sq.priority,
            rationale: sq.rationale,
          })),
          depth: generatedPlan.recommendedDepth || "standard",
        }
      : null;

    const payload = {
      input: {
        topic: input.topic,
        focus: input.focus || null,
        time_horizon: input.timeHorizon,
        output_length: input.outputLength,
        use_workspace_sources: input.useWorkspaceSources,
        discover_new_sources: input.discoverNewSources,
        must_include: input.mustInclude || null,
        must_exclude: input.mustExclude || null,
        notes: input.notes || null,
        target_deliverable_id: input.targetDeliverableId,
      },
      workspace_id: workspaceId,
      workspace_sources: wsPayload,
      existing_sections: existingSections,
      active_paper_id: activePaper?.id ?? null,
      ...(prePlan && { pre_plan: prePlan }),
    };

    try {
      await api.runDeepResearchStream(payload, (event) => {
        const s = useDeepResearchStore.getState();
        const type = event.type as string;

        if (type === "stage") {
          const stage = event.stage as string;
          const msg = event.message as string | undefined;
          s.setStatus(stage as DeepResearchStatus);
          if (msg) s.setStageMessage(msg);
          s.pushStage(stage, msg || stage);
          if (stage === "planning") {
            const planStage = s.macroStages?.find((st: any) => st.key === "plan");
            if (!planStage || planStage.status !== "completed") {
              s.setMacroStageStatus("plan", "in_progress");
            }
            s.setCurrentActivity(msg || "Planning...");
          } else if (stage === "executing") {
            s.setMacroStageStatus("research", "in_progress");
            s.setCurrentActivity(msg || "Researching...");
          } else if (stage === "evaluating") {
            s.setMacroStageStatus("research", "completed");
            s.setMacroStageStatus("evaluate", "in_progress");
            s.setCurrentActivity(msg || "Evaluating...");
          } else if (stage === "replanning") {
            s.setMacroStageStatus("evaluate", "completed");
            s.setCurrentActivity(msg || "Replanning...");
          } else if (stage === "synthesizing") {
            s.setMacroStageStatus("evaluate", "completed");
            s.setMacroStageStatus("write", "in_progress");
            s.setCurrentActivity(msg || "Writing report...");
          }
        } else if (type === "activity") {
          const actType = (event.activity_type as string) || "thinking";
          const label = (event.label as string) || "Working...";
          s.pushActivity({ type: actType as any, label, status: "active" });
          if (actType !== "done") {
            s.setCurrentActivity(label);
          }
          const sqIdx = event.sq_index as number | undefined;
          if (sqIdx !== undefined && actType !== "done") {
            s.updateSubQuestion(sqIdx, { status: "in_progress", startedAt: Date.now() });
          }
        } else if (type === "progress") {
          if (event.sources_found !== undefined) s.setSourcesFound(event.sources_found as number);
          if (event.sources_selected !== undefined) s.setSourcesSelected(event.sources_selected as number);
          if (event.message) s.setStageMessage(event.message as string);
          if (event.sub_questions) {
            const qs = event.sub_questions as { id: string; question: string }[];
            s.initSectionsProgress(qs.map((q) => q.question));
            s.initSubQuestions(qs);
            s.setMacroStageStatus("plan", "completed");
            s.setPlanSummary(`Generated ${qs.length} research questions`);
          }
          if (event.supplementary_questions) {
            const qs = event.supplementary_questions as { id: string; question: string }[];
            s.appendSubQuestions(qs);
            s.setMacroStageStatus("research", "in_progress");
          }
          if (event.sub_reports_summary) {
            const reports = event.sub_reports_summary as { sub_question_id: string; confidence: number; question: string }[];
            reports.forEach((_, i) => s.setSectionStatus(i, "done"));
          }
          if (event.sq_index !== undefined && event.confidence !== undefined) {
            const idx = event.sq_index as number;
            const confidence = event.confidence as number;
            const durationMs = event.duration_ms as number | undefined;
            const error = event.error as string | undefined;
            const question = event.question as string | undefined;
            s.setSectionStatus(idx, "done");
            const isFailed = (confidence === 0 && !error) || !!error;
            let failReason: string | undefined;
            if (isFailed) {
              if (error && /timeout|timed?\s*out/i.test(error)) {
                failReason = "Search timed out";
              } else if (error && /unavailable|503|502|rate.?limit/i.test(error)) {
                failReason = "Search service unavailable";
              } else if (error) {
                failReason = error.length > 80 ? error.slice(0, 77) + "..." : error;
              } else {
                failReason = "No relevant results found";
              }
            }
            s.updateSubQuestion(idx, {
              status: isFailed ? "failed" : "completed",
              confidence,
              durationMs,
              failReason,
            });
          }
        } else if (type === "synthesize_outline") {
          const headings = event.section_headings as string[];
          const title = event.title as string;
          if (title) s.setGeneratedTitle(title);
          if (headings) {
            s.initSectionsProgress(headings);
            s.initSectionsV2(headings);
          }
          s.setCurrentActivity("Writing report sections...");
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

          const res = (event.data ?? event) as DeepResearchRunResult;

          if (res.discovered_sources) {
            for (const ds of res.discovered_sources) {
              addFromDiscovery(workspaceId, ds);
            }
          }
          if (res.saved_source_ids) {
            for (const sid of res.saved_source_ids) {
              setLabel(workspaceId, sid, "background");
            }
          }

          let delId = input.targetDeliverableId;
          if (!delId) {
            const title = res.generated_title || "Deep Research Brief";
            const newDel = createDeliverable(workspaceId, "deep_research", title);
            delId = newDel.id;
            s.setCreatedDeliverableId(delId);
          } else if (res.generated_title) {
            renameDeliverable(workspaceId, delId, res.generated_title);
          }

          const currentDel = useDeliverableStore.getState().getDeliverables(workspaceId).find((d) => d.id === delId);
          if (currentDel && res.section_updates) {
            const sortedSections = [...currentDel.sections].sort((a, b) => a.order - b.order);
            for (const update of res.section_updates) {
              if (!update.generated_content.trim()) continue;
              const targetSection = sortedSections[update.section_index];
              if (!targetSection) continue;
              if (targetSection.content.trim() && update.mode !== "fill_empty") continue;
              applyAIContent(workspaceId, delId!, targetSection.id, update.generated_content, "draft", update.source_ids_used);
            }
          }

          if (res.follow_up_items) {
            for (const item of res.follow_up_items) {
              addSystemFollowup(
                activePaper?.id ?? null,
                item.title,
                item.description ?? undefined,
                item.category ?? undefined,
                item.priority,
              );
            }
          }

          s.setResult(res);
          s.setMacroStageStatus("write", "completed");
          s.setCurrentActivity(null);

          const finalDelId = s.createdDeliverableId ?? delId;
          if (finalDelId) {
            setActiveDeliverable(workspaceId, finalDelId);
            const del = getDeliverables(workspaceId).find((d) => d.id === finalDelId);
            const firstSection = del?.sections.sort((a, b) => a.order - b.order)[0];
            if (firstSection) selectSection(finalDelId, firstSection.id);
            setActiveViewerTab("deliverable");
            setSelectedNav("reader");
          }
        }
      });

      const finalStatus = useDeepResearchStore.getState().status;
      if (!["completed", "failed", "blocked", "needs_clarification"].includes(finalStatus)) {
        setFailed("Stream ended unexpectedly. Please try again.");
      }
    } catch (err: unknown) {
      setFailed(err instanceof Error ? err.message : "Request failed");
    }
  }, [input, sources, workspaceId, activePaper, deliverables, startRun, setFailed, addFromDiscovery, setLabel, createDeliverable, applyAIContent, addSystemFollowup, setActiveDeliverable, renameDeliverable, selectSection, getDeliverables, setActiveViewerTab, setSelectedNav]);

  return runStream;
}

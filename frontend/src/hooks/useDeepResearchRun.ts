import { useCallback, useEffect, useRef } from "react";
import { deepResearchApi, DeepResearchHttpError, type DeepResearchRunRequest } from "@/api/deepResearch";
import { useAgendaStore } from "@/store/agendaStore";
import {
  canMaterializeRun,
  useDeepResearchStore,
  type ResearchProtocolState,
  type ResearchRunView,
} from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { usePaperStore } from "@/store/paperStore";
import { useSourceStore } from "@/store/sourceStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import type { DeepResearchRunResult, ClarificationQuestion } from "@/types";
import type { DeepResearchProtocolError, DeepResearchRunEvent } from "@/types/deepResearch";

function protocolState(error: DeepResearchProtocolError, lastGoodSeq = 0): ResearchProtocolState {
  return {
    code: error.code,
    message: error.message,
    recoverable: ["unexpected_eof", "sequence_gap", "invalid_json"].includes(error.code),
    lastGoodSeq,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function acceptedResult(run: ResearchRunView): DeepResearchRunResult | null {
  if (!canMaterializeRun(run)) return null;
  const raw = asRecord(run.terminal?.result);
  if (!raw) return null;
  const sectionUpdates = Array.isArray(raw.section_updates) ? raw.section_updates : [];
  return {
    run_id: run.runId,
    status: "completed",
    clarification_questions: [],
    generated_title: typeof raw.generated_title === "string" ? raw.generated_title : null,
    generated_outline: Array.isArray(raw.generated_outline) && raw.generated_outline.every((item) => typeof item === "string")
      ? raw.generated_outline as string[]
      : null,
    section_updates: sectionUpdates.filter((item) => asRecord(item)).map((item) => {
      const value = item as Record<string, unknown>;
      return {
        section_index: typeof value.section_index === "number" ? value.section_index : 0,
        title: typeof value.title === "string" ? value.title : "Untitled section",
        mode: typeof value.mode === "string" ? value.mode : "draft",
        generated_content: typeof value.generated_content === "string" ? value.generated_content : "",
        source_ids_used: Array.isArray(value.source_ids_used)
          ? value.source_ids_used.filter((source): source is string => typeof source === "string")
          : [],
        notes: typeof value.notes === "string" ? value.notes : null,
      };
    }),
    discovered_sources: Array.isArray(raw.discovered_sources) ? raw.discovered_sources as DeepResearchRunResult["discovered_sources"] : [],
    saved_source_ids: Array.isArray(raw.saved_source_ids) ? raw.saved_source_ids.filter((item): item is string => typeof item === "string") : [],
    selected_source_ids: Array.isArray(raw.selected_source_ids) ? raw.selected_source_ids.filter((item): item is string => typeof item === "string") : [],
    unresolved_questions: Array.isArray(raw.unresolved_questions) ? raw.unresolved_questions.filter((item): item is string => typeof item === "string") : [],
    follow_up_items: Array.isArray(raw.follow_up_items) ? raw.follow_up_items as DeepResearchRunResult["follow_up_items"] : [],
    summary: typeof raw.summary === "string" ? raw.summary : null,
    message: typeof raw.message === "string" ? raw.message : null,
  };
}

export interface DeepResearchRunActions {
  start: () => Promise<void>;
  load: (runId: string, connectIfRunning?: boolean) => Promise<void>;
  reconnect: (runId: string) => Promise<void>;
  resume: (runId: string) => Promise<void>;
  cancelConnection: () => void;
}

export function useDeepResearchRun(workspaceId: string): DeepResearchRunActions {
  const abortRef = useRef<AbortController | null>(null);
  const materializingRef = useRef(new Set<string>());
  const { getIncludedSources, addFromDiscovery, setLabel } = useSourceStore();
  const { activePaper } = usePaperStore();
  const {
    getDeliverables,
    createDeliverable,
    applyAIContent,
    setActiveDeliverable,
    renameDeliverable,
    replaceSections,
    selectSection,
  } = useDeliverableStore();
  const { addSystemFollowup } = useAgendaStore();
  const { setSelectedNav, setActiveViewerTab, setConsolePanelTab } = useWorkspaceStore();

  useEffect(() => () => abortRef.current?.abort(), []);

  const materialize = useCallback((run: ResearchRunView) => {
    const state = useDeepResearchStore.getState();
    if (!canMaterializeRun(run)) return;
    if (state.materializedDeliverablesByRun[run.runId] || materializingRef.current.has(run.runId)) return;
    const result = acceptedResult(run);
    if (!result) {
      state.setRunProtocolError(run.runId, {
        code: "missing_final_result",
        message: "The accepted terminal event did not include a materializable final report.",
        recoverable: false,
        lastGoodSeq: run.lastSeq,
      });
      return;
    }

    materializingRef.current.add(run.runId);
    try {
      for (const source of result.discovered_sources ?? []) addFromDiscovery(workspaceId, source);
      for (const sourceId of result.saved_source_ids ?? []) setLabel(workspaceId, sourceId, "background");

      const currentInput = useDeepResearchStore.getState().input;
      let deliverableId = currentInput.targetDeliverableId;
      const existing = deliverableId
        ? getDeliverables(workspaceId).find((deliverable) => deliverable.id === deliverableId)
        : null;
      if (!existing) {
        const created = createDeliverable(
          workspaceId,
          "deep_research",
          result.generated_title || run.topic || "Deep Research Brief",
        );
        deliverableId = created.id;
      } else if (result.generated_title) {
        renameDeliverable(workspaceId, deliverableId!, result.generated_title);
      }

      if (!deliverableId) return;
      if (result.generated_outline?.length) replaceSections(workspaceId, deliverableId, result.generated_outline);
      const deliverable = useDeliverableStore.getState().getDeliverables(workspaceId).find((item) => item.id === deliverableId);
      const sections = deliverable ? [...deliverable.sections].sort((left, right) => left.order - right.order) : [];
      for (const update of result.section_updates ?? []) {
        if (!update.generated_content.trim()) continue;
        const target = sections[update.section_index];
        if (!target || (target.content.trim() && update.mode !== "fill_empty")) continue;
        applyAIContent(workspaceId, deliverableId, target.id, update.generated_content, "draft", update.source_ids_used);
      }

      for (const item of result.follow_up_items ?? []) {
        addSystemFollowup(activePaper?.id ?? null, item.title, item.description ?? undefined, item.category ?? undefined, item.priority);
      }

      useDeepResearchStore.getState().markMaterialized(run.runId, deliverableId);
      setActiveDeliverable(workspaceId, deliverableId);
      const finalDeliverable = useDeliverableStore.getState().getDeliverables(workspaceId).find((item) => item.id === deliverableId);
      const firstSection = finalDeliverable ? [...finalDeliverable.sections].sort((left, right) => left.order - right.order)[0] : null;
      if (firstSection) selectSection(deliverableId, firstSection.id);
      setActiveViewerTab("deliverable");
      setSelectedNav("console");
      setConsolePanelTab("deliverable");
    } finally {
      materializingRef.current.delete(run.runId);
    }
  }, [activePaper?.id, addFromDiscovery, addSystemFollowup, applyAIContent, createDeliverable, getDeliverables, renameDeliverable, replaceSections, selectSection, setActiveDeliverable, setActiveViewerTab, setConsolePanelTab, setLabel, setSelectedNav, workspaceId]);

  const processEvent = useCallback((event: DeepResearchRunEvent): "continue" | "resync" => {
    const result = useDeepResearchStore.getState().applyRunEvent(event);
    if (result.code === "sequence_gap" || result.code === "sequence_conflict" || result.code === "unknown_run") {
      if (result.run) {
        useDeepResearchStore.getState().setRunProtocolError(result.run.runId, {
          code: result.code,
          message: result.code === "sequence_gap"
            ? "The event stream has a sequence gap; the run snapshot must be reloaded."
            : "The event stream no longer matches the active server run.",
          recoverable: true,
          lastGoodSeq: result.run.lastSeq,
        });
      }
      return "resync";
    }
    if (event.type === "run_finished" && result.run) materialize(result.run);
    return "continue";
  }, [materialize]);

  const streamOptions = useCallback((controller: AbortController, expectedRunId?: string) => ({
    signal: controller.signal,
    expectedRunId,
    onEvent: (event: DeepResearchRunEvent) => {
      if (processEvent(event) === "resync") controller.abort();
    },
    onProtocolError: (error: DeepResearchProtocolError) => {
      const state = useDeepResearchStore.getState();
      const runId = expectedRunId ?? state.activeRunIdByWorkspace[workspaceId];
      if (runId && state.runsById[runId]) state.setRunProtocolError(runId, protocolState(error, state.runsById[runId].lastSeq));
      else state.setFailed(error.message);
    },
  }), [processEvent, workspaceId]);

  const reconcile = useCallback(async (runId: string, connectIfRunning: boolean) => {
    const snapshot = await deepResearchApi.getRun(runId, workspaceId);
    if (snapshot.workspace_id !== workspaceId) throw new Error("The selected research run belongs to another workspace.");
    useDeepResearchStore.getState().loadRun(snapshot);
    if (!connectIfRunning || snapshot.status !== "running") return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    useDeepResearchStore.getState().setRunConnection(runId, "reconnecting");
    let afterSeq = snapshot.snapshot_seq;
    while (!controller.signal.aborted) {
      const page = await deepResearchApi.getEvents(runId, workspaceId, afterSeq);
      for (const event of page.events) {
        if (processEvent(event) === "resync") {
          controller.abort();
          return;
        }
        afterSeq = event.seq;
        if (event.type === "run_finished" && event.payload.status !== "interrupted") return;
      }
      const current = useDeepResearchStore.getState().runsById[runId];
      if (!current || current.status !== "running") return;
      if (page.has_more) continue;
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, 1500);
        controller.signal.addEventListener("abort", () => {
          window.clearTimeout(timer);
          resolve();
        }, { once: true });
      });
    }
  }, [processEvent, workspaceId]);

  const handleStreamError = useCallback(async (error: unknown, runId?: string) => {
    if (error instanceof DOMException && error.name === "AbortError") return;
    const state = useDeepResearchStore.getState();
    const resolvedRunId = runId ?? state.activeRunIdByWorkspace[workspaceId] ?? undefined;
    const message = error instanceof DeepResearchHttpError
      ? error.status === 409
        ? "This run is already active elsewhere, or its checkpoint is incompatible with the current graph version."
        : error.message
      : error instanceof Error ? error.message : "Research stream failed.";
    if (resolvedRunId && state.runsById[resolvedRunId]) {
      state.setRunConnection(resolvedRunId, "offline");
      state.setRunProtocolError(resolvedRunId, {
        code: error instanceof DeepResearchHttpError ? `http_${error.status}` : "stream_error",
        message,
        recoverable: !(error instanceof DeepResearchHttpError) || error.status >= 500,
        lastGoodSeq: state.runsById[resolvedRunId].lastSeq,
      });
    } else {
      state.setFailed(message);
    }
  }, [workspaceId]);

  const start = useCallback(async () => {
    const state = useDeepResearchStore.getState();
    const { input, generatedPlan } = state;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    state.startRun(workspaceId);

    const sources = getIncludedSources(workspaceId);
    const deliverables = getDeliverables(workspaceId);
    const existingDeliverable = input.targetDeliverableId
      ? deliverables.find((deliverable) => deliverable.id === input.targetDeliverableId)
      : null;
    const payload: DeepResearchRunRequest = {
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
      workspace_sources: sources.map((source) => ({
        id: source.id,
        title: source.title,
        authors: source.authors,
        year: source.year,
        abstract: source.abstract,
        provider: source.provider,
        paper_id: source.paper_id,
        label: source.label,
      })),
      existing_sections: existingDeliverable?.sections.map((section) => ({
        id: section.id,
        title: section.title,
        content: section.content,
        order: section.order,
        linkedSourceIds: section.linkedSourceIds,
      })) ?? [],
      active_paper_id: activePaper?.id ?? null,
      pre_plan: generatedPlan ? {
        sub_questions: generatedPlan.subQuestions.map((question) => ({
          id: question.id,
          question: question.question,
          search_queries: question.searchQueries,
          priority: question.priority,
          rationale: question.rationale,
        })),
        depth: generatedPlan.recommendedDepth || "standard",
      } : null,
    };

    try {
      await deepResearchApi.streamNewRun(payload, streamOptions(controller));
    } catch (error) {
      const activeRunId = useDeepResearchStore.getState().activeRunIdByWorkspace[workspaceId] ?? undefined;
      await handleStreamError(error, activeRunId);
    }
  }, [activePaper?.id, getDeliverables, getIncludedSources, handleStreamError, streamOptions, workspaceId]);

  const load = useCallback(async (runId: string, connectIfRunning = true) => {
    useDeepResearchStore.getState().setStatus("loading_run");
    try {
      await reconcile(runId, connectIfRunning);
    } catch (error) {
      await handleStreamError(error, runId);
    }
  }, [handleStreamError, reconcile]);

  const reconnect = useCallback(async (runId: string) => {
    try {
      await reconcile(runId, true);
    } catch (error) {
      await handleStreamError(error, runId);
    }
  }, [handleStreamError, reconcile]);

  const resume = useCallback(async (runId: string) => {
    const state = useDeepResearchStore.getState();
    const run = state.runsById[runId];
    if (!run?.resume.allowed || !run.resume.checkpoint_id) {
      if (run) state.setRunProtocolError(runId, {
        code: run.resume.reason_code,
        message: run.resume.reason,
        recoverable: false,
        lastGoodSeq: run.lastSeq,
      });
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    state.setStatus("resuming");
    state.setRunConnection(runId, "connecting");
    try {
      await deepResearchApi.resumeRun(runId, workspaceId, {
        ...streamOptions(controller, runId),
        lastEventId: run.lastEventId,
      });
    } catch (error) {
      await handleStreamError(error, runId);
    }
  }, [handleStreamError, streamOptions, workspaceId]);

  return {
    start,
    load,
    reconnect,
    resume,
    cancelConnection: () => abortRef.current?.abort(),
  };
}

export function isClarificationResult(value: unknown): value is { clarification_questions: ClarificationQuestion[] } {
  const record = asRecord(value);
  return Boolean(record && Array.isArray(record.clarification_questions));
}

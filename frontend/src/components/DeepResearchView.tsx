import { useCallback } from "react";
import {
  FlaskConical, Play, RotateCcw, ExternalLink, Check,
  HelpCircle, ChevronDown,
} from "lucide-react";
import { useDeepResearchStore, type DeepResearchInput, type DeepResearchStatus } from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import { TaskPageShell } from "./shared/TaskPageShell";
import { WorkflowError } from "./shared/WorkflowError";
import { WorkflowRunPanel } from "./shared/WorkflowRunPanel";
import { StatGrid } from "./shared/StatGrid";
import { ClarificationPanel } from "./shared/ClarificationPanel";
import type { DeepResearchRunResult, ClarificationQuestion } from "@/types";

const DR_STAGES = [
  { key: "validating", label: "Validating input" },
  { key: "planning", label: "Decomposing research topic" },
  { key: "executing", label: "Investigating sub-questions" },
  { key: "evaluating", label: "Evaluating research quality" },
  { key: "replanning", label: "Generating follow-up questions" },
  { key: "synthesizing", label: "Producing final report" },
];

export function DeepResearchView() {
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";
  const store = useDeepResearchStore();
  const { status } = store;

  const isRunning = !["idle", "needs_clarification", "completed", "blocked", "failed", "interrupted"].includes(status);

  return (
    <TaskPageShell
      icon={<FlaskConical className="w-4 h-4 text-accent-600" />}
      title="Deep Research"
    >
      {status === "idle" && <InputForm workspaceId={wid} />}
      {status === "needs_clarification" && (
        <ClarificationPanel
          questions={store.clarificationQuestions}
          onRetry={() => store.setStatus("idle")}
          onReset={store.reset}
        />
      )}
      {isRunning && <LiveProgress />}
      {status === "interrupted" && <InterruptedState />}
      {status === "completed" && store.result && (
        <ResultSummary result={store.result} workspaceId={wid} />
      )}
      {(status === "failed" || status === "blocked") && (
        <WorkflowError
          message={store.errorMessage}
          title={status === "blocked" ? "Blocked" : "Something went wrong"}
          onReset={store.reset}
          onRetry={() => store.setStatus("idle")}
        />
      )}
    </TaskPageShell>
  );
}

/* ── Live progress (SSE-driven) ─────────────────────────────────────────── */

function LiveProgress() {
  const { status, currentStageMessage, sectionsProgress, sourcesFound, sourcesSelected, generatedTitle } = useDeepResearchStore();

  return (
    <div className="space-y-3">
      {generatedTitle && (
        <div className="text-sm font-medium text-surface-700 px-1">
          {generatedTitle}
        </div>
      )}
      <WorkflowRunPanel
        stages={DR_STAGES}
        currentStatus={status}
        stageMessage={currentStageMessage}
        sectionsProgress={sectionsProgress}
        sourcesFound={sourcesFound}
        sourcesSelected={sourcesSelected}
      />
    </div>
  );
}

/* ── Interrupted state (shown after page refresh during a run) ─────────── */

function InterruptedState() {
  const { currentStageMessage, sectionsProgress, generatedTitle, input, reset } = useDeepResearchStore();
  const completedSections = sectionsProgress.filter((s) => s.status === "done").length;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200">
        <RotateCcw className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-800">Research was interrupted</p>
          <p className="text-xs text-amber-600 mt-1">
            The page was refreshed while research was running. The stream cannot be resumed.
          </p>
        </div>
      </div>

      {(generatedTitle || currentStageMessage || completedSections > 0) && (
        <div className="px-4 py-3 rounded-lg bg-surface-50 border border-surface-200 space-y-2">
          <p className="text-xs font-medium text-surface-600">Last known progress:</p>
          {generatedTitle && (
            <p className="text-xs text-surface-700">Title: {generatedTitle}</p>
          )}
          {currentStageMessage && (
            <p className="text-xs text-surface-500">Stage: {currentStageMessage}</p>
          )}
          {completedSections > 0 && (
            <p className="text-xs text-surface-500">
              {completedSections} of {sectionsProgress.length} sub-questions completed
            </p>
          )}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={reset}
          className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
        >
          <RotateCcw className="w-3 h-3" />
          Start Over
        </button>
        <p className="text-[11px] text-surface-400">
          Topic: {input.topic || "—"}
        </p>
      </div>
    </div>
  );
}

/* ── Input form ────────────────────────────────────────────────────────── */

function InputForm({ workspaceId }: { workspaceId: string }) {
  const store = useDeepResearchStore();
  const { input, setInput, startRun, setStatus, setResult, setClarification, setFailed, setBlocked, setCreatedDeliverableId } = store;
  const { getIncludedSources, addFromDiscovery, setLabel } = useSourceStore();
  const { activePaper } = usePaperStore();
  const { getDeliverables, createDeliverable, applyAIContent, setActiveDeliverable, renameDeliverable } = useDeliverableStore();
  const { addSystemFollowup } = useAgendaStore();
  const { setSelectedNav, setActiveViewerTab } = useWorkspaceStore();

  const sources = getIncludedSources(workspaceId);
  const deliverables = getDeliverables(workspaceId);
  const drDeliverables = deliverables.filter((d) => d.type === "deep_research");

  const handleRun = useCallback(async () => {
    startRun();

    const wsPayload = sources
      .map((s) => ({
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
        } else if (type === "progress") {
          if (event.sources_found !== undefined) s.setSourcesFound(event.sources_found as number);
          if (event.sources_selected !== undefined) s.setSourcesSelected(event.sources_selected as number);
          if (event.message) s.setStageMessage(event.message as string);
          // Sub-questions from planning phase — show as sections preview
          if (event.sub_questions) {
            const qs = event.sub_questions as { id: string; question: string }[];
            s.initSectionsProgress(qs.map((q) => q.question));
          }
          // Sub-reports from execution phase — mark completed
          if (event.sub_reports_summary) {
            const reports = event.sub_reports_summary as { sub_question_id: string; confidence: number; question: string }[];
            reports.forEach((_, i) => s.setSectionStatus(i, "done"));
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

          // Apply discovered sources
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

          // Create or use existing deliverable
          let delId = input.targetDeliverableId;
          if (!delId) {
            const title = res.generated_title || input.topic || "Deep Research Brief";
            const newDel = createDeliverable(workspaceId, "deep_research", title);
            delId = newDel.id;
            s.setCreatedDeliverableId(delId);
          } else if (res.generated_title) {
            renameDeliverable(workspaceId, delId, res.generated_title);
          }

          // Apply section content
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

          // Add follow-up agenda items
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

          // Auto-navigate to the deliverable
          const finalDelId = s.createdDeliverableId ?? delId;
          if (finalDelId) {
            setActiveDeliverable(workspaceId, finalDelId);
            setActiveViewerTab("deliverable");
            setSelectedNav("reader");
          }
        }
      });

      // If stream ended without a result event, check current status
      const finalStatus = useDeepResearchStore.getState().status;
      if (!["completed", "failed", "blocked", "needs_clarification"].includes(finalStatus)) {
        setFailed("Stream ended unexpectedly. Please try again.");
      }
    } catch (err: unknown) {
      setFailed(err instanceof Error ? err.message : "Request failed");
    }
  }, [input, sources, workspaceId, activePaper, deliverables, startRun, setStatus, setResult, setClarification, setFailed, setBlocked, setCreatedDeliverableId, addFromDiscovery, setLabel, createDeliverable, applyAIContent, addSystemFollowup, setActiveDeliverable, setActiveViewerTab, setSelectedNav]);

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">
          Research topic / question <span className="text-red-400">*</span>
        </label>
        <textarea
          value={input.topic}
          onChange={(e) => setInput({ topic: e.target.value })}
          placeholder="e.g. How do modern attention mechanisms compare for long-context tasks?"
          rows={3}
          className="input-base w-full resize-none"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Focus / angle (optional)</label>
        <input
          value={input.focus}
          onChange={(e) => setInput({ focus: e.target.value })}
          placeholder="e.g. efficiency vs accuracy tradeoffs"
          className="input-base w-full"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Time horizon</label>
          <select
            value={input.timeHorizon}
            onChange={(e) => setInput({ timeHorizon: e.target.value as DeepResearchInput["timeHorizon"] })}
            className="select-base w-full"
          >
            <option value="recent_2y">Recent 2 years</option>
            <option value="recent_5y">Recent 5 years</option>
            <option value="broad">Broad background</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Output length</label>
          <select
            value={input.outputLength}
            onChange={(e) => setInput({ outputLength: e.target.value as DeepResearchInput["outputLength"] })}
            className="select-base w-full"
          >
            <option value="short">Short</option>
            <option value="medium">Medium</option>
          </select>
        </div>
      </div>

      <div className="space-y-2">
        <ToggleRow
          label="Use existing workspace sources"
          checked={input.useWorkspaceSources}
          onChange={(v) => setInput({ useWorkspaceSources: v })}
          count={sources.length}
        />
        <ToggleRow
          label="Discover new related sources"
          checked={input.discoverNewSources}
          onChange={(v) => setInput({ discoverNewSources: v })}
        />
      </div>

      <AdvancedFields input={input} setInput={setInput} />

      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Deliverable target</label>
        <select
          value={input.targetDeliverableId ?? ""}
          onChange={(e) => setInput({ targetDeliverableId: e.target.value || null })}
          className="select-base w-full"
        >
          <option value="">Create new deep research deliverable</option>
          {drDeliverables.map((d) => (
            <option key={d.id} value={d.id}>{d.title}</option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleRun}
          disabled={!input.topic.trim()}
          className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
        >
          <Play className="w-3.5 h-3.5" />
          Run Deep Research
        </button>
        <button
          onClick={store.reset}
          className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset
        </button>
      </div>
    </div>
  );
}

/* ── Advanced fields (collapsed) ──────────────────────────────────────── */

function AdvancedFields({ input, setInput }: { input: DeepResearchInput; setInput: (p: Partial<DeepResearchInput>) => void }) {
  const hasAdvanced = !!(input.mustInclude || input.mustExclude || input.notes);

  return (
    <details className="group" open={hasAdvanced || undefined}>
      <summary className="flex items-center gap-1 text-xs text-surface-500 hover:text-surface-700 cursor-pointer transition-colors select-none">
        <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-0 -rotate-90" />
        Advanced options
      </summary>
      <div className="mt-3 space-y-3 pl-1">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-surface-600 mb-1">Must include (optional)</label>
            <input
              value={input.mustInclude}
              onChange={(e) => setInput({ mustInclude: e.target.value })}
              placeholder="e.g. transformer, BERT"
              className="input-base w-full"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-surface-600 mb-1">Must exclude (optional)</label>
            <input
              value={input.mustExclude}
              onChange={(e) => setInput({ mustExclude: e.target.value })}
              placeholder="e.g. computer vision"
              className="input-base w-full"
            />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Notes (optional)</label>
          <textarea
            value={input.notes}
            onChange={(e) => setInput({ notes: e.target.value })}
            placeholder="Any additional context or constraints..."
            rows={2}
            className="input-base w-full resize-none"
          />
        </div>
      </div>
    </details>
  );
}

/* ── Toggle row ────────────────────────────────────────────────────────── */

function ToggleRow({ label, checked, onChange, count }: { label: string; checked: boolean; onChange: (v: boolean) => void; count?: number }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="rounded border-surface-300 text-accent-600 focus:ring-accent-400"
      />
      <span className="text-xs text-surface-600">{label}</span>
      {count !== undefined && (
        <span className="text-[10px] text-surface-400">({count} available)</span>
      )}
    </label>
  );
}

/* ── Result summary ────────────────────────────────────────────────────── */

function ResultSummary({ result, workspaceId }: { result: DeepResearchRunResult; workspaceId: string }) {
  const { createdDeliverableId } = useDeepResearchStore();
  const { setSelectedNav, setActiveViewerTab } = useWorkspaceStore();
  const { setActiveDeliverable } = useDeliverableStore();
  const { reset } = useDeepResearchStore();

  const draftedCount = result.section_updates.filter((u) => u.generated_content.trim()).length;
  const skippedCount = result.section_updates.filter((u) => !u.generated_content.trim()).length;

  const handleOpenDeliverable = () => {
    if (createdDeliverableId) {
      setActiveDeliverable(workspaceId, createdDeliverableId);
    }
    setSelectedNav("reader");
    setActiveViewerTab("deliverable");
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-emerald-700">
        <Check className="w-4 h-4" />
        <span className="text-sm font-medium">Deep research complete</span>
      </div>

      {result.summary && (
        <p className="text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg px-3 py-2">
          {result.summary}
        </p>
      )}

      <StatGrid stats={[
        { label: "Sections drafted", value: draftedCount },
        { label: "Sections skipped", value: skippedCount },
        { label: "Sources used", value: result.selected_source_ids.length },
        { label: "Sources discovered", value: result.discovered_sources.length },
      ]} />

      {result.discovered_sources.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1.5">Discovered sources saved</h4>
          <div className="space-y-1">
            {result.discovered_sources.map((s, i) => (
              <div key={i} className="text-xs text-surface-600 bg-white border border-surface-200 rounded px-2.5 py-1.5 flex items-center gap-2">
                <span className="text-[10px] text-surface-400 shrink-0">{s.provider}</span>
                <span className="truncate">{s.title}</span>
                {s.year && <span className="text-[10px] text-surface-400 shrink-0">{s.year}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {result.unresolved_questions.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">Open questions identified</h4>
          <p className="text-[11px] text-surface-400 mb-1.5">
            The following gaps or uncertainties were found during research.
            {result.follow_up_items.length > 0 && (
              <> The most actionable ones were promoted to your Agenda.</>
            )}
          </p>
          <div className="space-y-1">
            {result.unresolved_questions.map((q, i) => {
              const promoted = result.follow_up_items.some(
                (f) => f.title.toLowerCase().includes(q.slice(0, 30).toLowerCase()) ||
                       q.toLowerCase().includes(f.title.slice(0, 30).toLowerCase())
              );
              return (
                <div key={i} className="text-xs text-surface-600 bg-amber-50 border border-amber-200 rounded px-2.5 py-1.5 flex items-start gap-2">
                  <HelpCircle className="w-3 h-3 text-amber-500 shrink-0 mt-0.5" />
                  <span className="flex-1">{q}</span>
                  {promoted && (
                    <span className="text-[9px] text-accent-600 bg-accent-50 border border-accent-200 px-1.5 py-0.5 rounded shrink-0">
                      → Agenda
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {result.follow_up_items.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">
            Follow-up agenda items added
            <span className="text-[10px] text-surface-400 font-normal ml-1">
              ({Math.min(result.follow_up_items.length, 5)} of {result.unresolved_questions.length} questions)
            </span>
          </h4>
          <div className="space-y-1">
            {result.follow_up_items.slice(0, 5).map((item, i) => (
              <div key={i} className="text-xs text-surface-600 bg-accent-50 border border-accent-200 rounded px-2.5 py-1.5">
                <span className="font-medium">{item.title}</span>
                {item.description && (
                  <p className="text-[11px] text-surface-400 mt-0.5 line-clamp-1">{item.description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleOpenDeliverable}
          className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
        >
          <ExternalLink className="w-3 h-3" />
          Open Deliverable
        </button>
        <button
          onClick={reset}
          className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs"
        >
          <RotateCcw className="w-3 h-3" />
          New Research
        </button>
      </div>
    </div>
  );
}

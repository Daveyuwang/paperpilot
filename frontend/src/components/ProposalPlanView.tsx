import { useCallback } from "react";
import {
  FileText, Play, RotateCcw, ExternalLink,
  BookOpen, FlaskConical, Check, ChevronRight,
} from "lucide-react";
import clsx from "clsx";
import { useProposalPlanStore, type PPStatus } from "@/store/proposalPlanStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import { TaskPageShell } from "./shared/TaskPageShell";
import { WorkflowError } from "./shared/WorkflowError";
import { WorkflowRunPanel } from "./shared/WorkflowRunPanel";
import { StatGrid } from "./shared/StatGrid";
import { ClarificationPanel } from "./shared/ClarificationPanel";
import type { ProposalPlanMode, ProposalPlanRunResult, ClarificationQuestion, DeliverableType } from "@/types";

const PP_STAGES = [
  { key: "validating", label: "Validating input" },
  { key: "selecting_context", label: "Selecting context" },
  { key: "generating_outline", label: "Generating outline" },
  { key: "drafting", label: "Drafting sections" },
  { key: "updating_agenda", label: "Updating agenda" },
];

export function ProposalPlanView() {
  const store = useProposalPlanStore();
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";

  const { status, result, clarificationQuestions, errorMessage } = store;

  const isRunning = !["idle", "needs_clarification", "completed", "blocked", "failed", "interrupted"].includes(status);

  return (
    <TaskPageShell
      icon={<FileText className="w-4 h-4 text-accent-600" />}
      title="Proposal / Plan"
    >
      {status === "idle" && <InputForm workspaceId={wid} />}
      {status === "needs_clarification" && (
        <ClarificationPanel
          questions={clarificationQuestions}
          onRetry={() => store.setStatus("idle")}
          onReset={store.reset}
        />
      )}
      {isRunning && <PPLiveProgress />}
      {status === "interrupted" && <PPInterruptedState />}
      {status === "completed" && result && (
        <ResultSummary
          result={result}
          workspaceId={wid}
          onReset={store.reset}
        />
      )}
      {(status === "failed" || status === "blocked") && (
        <WorkflowError
          message={errorMessage}
          title={status === "blocked" ? "Blocked" : "Something went wrong"}
          onReset={store.reset}
          onRetry={() => store.setStatus("idle")}
        />
      )}
    </TaskPageShell>
  );
}

/* ── Live progress (SSE-driven) ─────────────────────────────────────────── */

function PPLiveProgress() {
  const { status, currentStageMessage, sectionsProgress, sourcesSelected } = useProposalPlanStore();

  return (
    <WorkflowRunPanel
      stages={PP_STAGES}
      currentStatus={status}
      stageMessage={currentStageMessage}
      sectionsProgress={sectionsProgress}
      sourcesSelected={sourcesSelected}
    />
  );
}

/* ── Interrupted state ─────────────────────────────────────────────────── */

function PPInterruptedState() {
  const { currentStageMessage, sectionsProgress, input, reset } = useProposalPlanStore();
  const completedSections = sectionsProgress.filter((s) => s.status === "done").length;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200">
        <RotateCcw className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-800">Run was interrupted</p>
          <p className="text-xs text-amber-600 mt-1">
            The page was refreshed while generating. The stream cannot be resumed.
          </p>
        </div>
      </div>

      {(currentStageMessage || completedSections > 0) && (
        <div className="px-4 py-3 rounded-lg bg-surface-50 border border-surface-200 space-y-2">
          <p className="text-xs font-medium text-surface-600">Last known progress:</p>
          {currentStageMessage && (
            <p className="text-xs text-surface-500">Stage: {currentStageMessage}</p>
          )}
          {completedSections > 0 && (
            <p className="text-xs text-surface-500">
              {completedSections} of {sectionsProgress.length} sections drafted
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

/* ── InputForm ────────────────────────────────────────────────────────── */

function InputForm({ workspaceId }: { workspaceId: string }) {
  const { input, setInput } = useProposalPlanStore();
  const { getActiveWorkspace, setActiveViewerTab, setSelectedNav } = useWorkspaceStore();
  const wksp = getActiveWorkspace();
  const deliverableStore = useDeliverableStore();
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const agendaStore = useAgendaStore();
  const allDeliverables = deliverableStore.getDeliverables(workspaceId);
  const drDeliverables = allDeliverables.filter((d) => d.type === "deep_research");
  const ppDeliverables = allDeliverables.filter((d) => d.type === input.mode);
  const sources = getIncludedSources(workspaceId);

  const store = useProposalPlanStore();

  const handleRun = useCallback(async () => {
    store.startRun();

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
        const s = useProposalPlanStore.getState();
        const type = event.type as string;

        if (type === "stage") {
          const stage = event.stage as string;
          const msg = event.message as string | undefined;
          s.setStatus(stage as PPStatus);
          if (msg) s.setStageMessage(msg);
        } else if (type === "progress") {
          if (event.sources_selected !== undefined) s.setSourcesSelected(event.sources_selected as number);
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

          // Create or find deliverable
          const delType: DeliverableType = input.mode === "proposal" ? "proposal" : "research_plan";
          let deliverable;
          if (input.targetDeliverableId) {
            deliverable = allDeliverables.find((d) => d.id === input.targetDeliverableId) ?? null;
          }
          if (!deliverable) {
            const title = res.generated_title || input.topic;
            deliverable = deliverableStore.createDeliverable(workspaceId, delType, title);
          } else if (res.generated_title) {
            deliverableStore.renameDeliverable(workspaceId, deliverable.id, res.generated_title);
          }

          if (deliverable) {
            s.setCreatedDeliverableId(deliverable.id);
            if (res.section_updates) {
              for (const update of res.section_updates) {
                if (!update.generated_content.trim()) continue;
                const targetSection = update.section_id.startsWith("new-")
                  ? deliverable.sections[parseInt(update.section_id.replace("new-", ""), 10)]
                  : deliverable.sections.find((sec) => sec.id === update.section_id);
                if (!targetSection) continue;
                if (targetSection.content.trim() && update.mode === "fill_empty") continue;
                deliverableStore.applyAIContent(
                  workspaceId, deliverable.id, targetSection.id,
                  update.generated_content, "draft", update.source_ids_used,
                );
              }
            }
          }

          // Add follow-up agenda items
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

          // Auto-navigate to deliverable
          const finalDelId = s.createdDeliverableId ?? deliverable?.id;
          if (finalDelId) {
            deliverableStore.setActiveDeliverable(workspaceId, finalDelId);
            setActiveViewerTab("deliverable");
            setSelectedNav("reader");
          }
        }
      });

      // Fallback if stream ended without terminal event
      const finalStatus = useProposalPlanStore.getState().status;
      if (!["completed", "failed", "blocked", "needs_clarification"].includes(finalStatus)) {
        store.setFailed("Stream ended unexpectedly. Please try again.");
      }
    } catch (err: any) {
      store.setFailed(err?.message ?? "Unexpected error.");
    }
  }, [store, input, sources, drDeliverables, allDeliverables, workspaceId, activePaper?.id, deliverableStore, agendaStore, setActiveViewerTab, setSelectedNav]);

  return (
    <div className="space-y-4">
      {/* Mode selector */}
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1.5">Mode</label>
        <div className="flex gap-2">
          {(["proposal", "research_plan"] as ProposalPlanMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setInput({ mode: m })}
              className={clsx(
                "px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                input.mode === m
                  ? "bg-accent-50 text-accent-700 border-accent-200"
                  : "bg-surface-50 text-surface-500 border-surface-200 hover:bg-surface-100"
              )}
            >
              {m === "proposal" ? "Proposal" : "Research Plan"}
            </button>
          ))}
        </div>
      </div>

      {/* Topic */}
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">
          Topic <span className="text-red-400">*</span>
        </label>
        <textarea
          value={input.topic}
          onChange={(e) => setInput({ topic: e.target.value })}
          placeholder="What is this about?"
          className="input-base w-full h-20 resize-none"
        />
      </div>

      {/* Shared fields */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Focus (optional)</label>
          <input
            value={input.focus}
            onChange={(e) => setInput({ focus: e.target.value })}
            placeholder="Specific angle"
            className="input-base w-full"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Problem statement (optional)</label>
          <input
            value={input.problemStatement}
            onChange={(e) => setInput({ problemStatement: e.target.value })}
            placeholder="Core problem"
            className="input-base w-full"
          />
        </div>
      </div>

      {/* Mode-specific fields */}
      {input.mode === "proposal" && <ProposalFields />}
      {input.mode === "research_plan" && <ResearchPlanFields />}

      {/* Source toggles */}
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-xs text-surface-600 cursor-pointer">
          <input
            type="checkbox"
            checked={input.useWorkspaceSources}
            onChange={(e) => setInput({ useWorkspaceSources: e.target.checked })}
            className="rounded border-surface-300 text-accent-600 focus:ring-accent-400"
          />
          <BookOpen className="w-3.5 h-3.5" />
          Use workspace sources
        </label>
        <label className="flex items-center gap-2 text-xs text-surface-600 cursor-pointer">
          <input
            type="checkbox"
            checked={input.useDeepResearchContext}
            onChange={(e) => setInput({ useDeepResearchContext: e.target.checked })}
            className="rounded border-surface-300 text-accent-600 focus:ring-accent-400"
          />
          <FlaskConical className="w-3.5 h-3.5" />
          Use deep research context
        </label>
      </div>

      {/* Deep research deliverable selector */}
      {input.useDeepResearchContext && (
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Deep research deliverables</label>
          {drDeliverables.length === 0 ? (
            <p className="text-xs text-surface-400">No deep research deliverables yet. Run Deep Research first.</p>
          ) : (
            <div className="space-y-1">
              {drDeliverables.map((d) => (
                <label key={d.id} className="flex items-center gap-2 text-xs text-surface-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={input.deepResearchDeliverableIds.includes(d.id)}
                    onChange={(e) => {
                      const ids = e.target.checked
                        ? [...input.deepResearchDeliverableIds, d.id]
                        : input.deepResearchDeliverableIds.filter((id) => id !== d.id);
                      setInput({ deepResearchDeliverableIds: ids });
                    }}
                    className="rounded border-surface-300 text-accent-600 focus:ring-accent-400"
                  />
                  {d.title}
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Target deliverable */}
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Target deliverable</label>
        <select
          value={input.targetDeliverableId ?? ""}
          onChange={(e) => setInput({ targetDeliverableId: e.target.value || null })}
          className="select-base w-full"
        >
          <option value="">Create new</option>
          {ppDeliverables.map((d) => (
            <option key={d.id} value={d.id}>{d.title}</option>
          ))}
        </select>
      </div>

      {/* Notes */}
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Notes (optional)</label>
        <textarea
          value={input.notes}
          onChange={(e) => setInput({ notes: e.target.value })}
          placeholder="Any additional context or instructions"
          className="input-base w-full h-16 resize-none"
        />
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-2">
        <button
          onClick={handleRun}
          disabled={!input.topic.trim()}
          className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
        >
          <Play className="w-3.5 h-3.5" />
          Generate Draft
        </button>
        <button onClick={store.reset} className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs">
          <RotateCcw className="w-3.5 h-3.5" />
          Reset
        </button>
      </div>
    </div>
  );
}

/* ── Proposal-specific fields ─────────────────────────────────────────── */

function ProposalFields() {
  const { input, setInput } = useProposalPlanStore();
  return (
    <div className="space-y-3 pl-3 border-l-2 border-accent-100">
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Motivation (optional)</label>
        <textarea
          value={input.motivation}
          onChange={(e) => setInput({ motivation: e.target.value })}
          placeholder="Why does this matter?"
          className="input-base w-full h-16 resize-none"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Proposed idea (optional)</label>
        <input
          value={input.proposedIdea}
          onChange={(e) => setInput({ proposedIdea: e.target.value })}
          placeholder="Central idea summary"
          className="input-base w-full"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Evaluation direction</label>
          <input
            value={input.evaluationDirection}
            onChange={(e) => setInput({ evaluationDirection: e.target.value })}
            placeholder="How to evaluate"
            className="input-base w-full"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Constraints</label>
          <input
            value={input.constraints}
            onChange={(e) => setInput({ constraints: e.target.value })}
            placeholder="Assumptions or limits"
            className="input-base w-full"
          />
        </div>
      </div>
    </div>
  );
}

/* ── Research-plan-specific fields ────────────────────────────────────── */

function ResearchPlanFields() {
  const { input, setInput } = useProposalPlanStore();
  return (
    <div className="space-y-3 pl-3 border-l-2 border-accent-100">
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Planning horizon</label>
        <select
          value={input.planningHorizon}
          onChange={(e) => setInput({ planningHorizon: e.target.value })}
          className="select-base w-full"
        >
          <option value="">Not specified</option>
          <option value="1_week">1 week</option>
          <option value="2_weeks">2 weeks</option>
          <option value="1_month">1 month</option>
          <option value="custom">Custom</option>
        </select>
      </div>
      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">Intended deliverables (optional)</label>
        <input
          value={input.intendedDeliverables}
          onChange={(e) => setInput({ intendedDeliverables: e.target.value })}
          placeholder="What artifacts to produce"
          className="input-base w-full"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Risks / blockers</label>
          <input
            value={input.risks}
            onChange={(e) => setInput({ risks: e.target.value })}
            placeholder="Known risks"
            className="input-base w-full"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-surface-600 mb-1">Milestone notes</label>
          <input
            value={input.milestoneNotes}
            onChange={(e) => setInput({ milestoneNotes: e.target.value })}
            placeholder="Key checkpoints"
            className="input-base w-full"
          />
        </div>
      </div>
    </div>
  );
}

/* ── ResultSummary ────────────────────────────────────────────────────── */

function ResultSummary({ result, workspaceId, onReset }: {
  result: ProposalPlanRunResult;
  workspaceId: string;
  onReset: () => void;
}) {
  const { createdDeliverableId } = useProposalPlanStore();
  const { setSelectedNav, setActiveViewerTab } = useWorkspaceStore();
  const { setActiveDeliverable } = useDeliverableStore();

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
        <span className="text-sm font-medium">
          {result.mode === "proposal" ? "Proposal" : "Research Plan"} draft complete
        </span>
      </div>

      {result.summary && (
        <p className="text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg px-3 py-2">
          {result.summary}
        </p>
      )}

      <StatGrid
        stats={[
          { label: "Sections drafted", value: result.updated_section_ids.length },
          { label: "Skipped", value: result.skipped_section_ids.length },
          { label: "Sources used", value: result.selected_source_ids.length },
        ]}
        columns={3}
      />

      {result.unresolved_questions.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">Open questions identified</h4>
          <p className="text-[11px] text-surface-400 mb-1.5">
            Gaps or uncertainties found during drafting.
            {result.follow_up_items.length > 0 && (
              <> The most actionable ones were added to Agenda.</>
            )}
          </p>
          <ul className="space-y-1">
            {result.unresolved_questions.map((q, i) => {
              const promoted = result.follow_up_items.some(
                (f) => f.title.toLowerCase().includes(q.slice(0, 30).toLowerCase()) ||
                       q.toLowerCase().includes(f.title.slice(0, 30).toLowerCase())
              );
              return (
                <li key={i} className="text-xs text-surface-500 flex items-start gap-1.5">
                  <ChevronRight className="w-3 h-3 mt-0.5 flex-shrink-0 text-surface-400" />
                  <span className="flex-1">{q}</span>
                  {promoted && (
                    <span className="text-[9px] text-accent-600 bg-accent-50 border border-accent-200 px-1.5 py-0.5 rounded shrink-0">
                      → Agenda
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {result.follow_up_items.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">
            Follow-up agenda items added
            <span className="text-[10px] text-surface-400 font-normal ml-1">
              ({result.follow_up_items.length} promoted)
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
        {createdDeliverableId && (
          <button onClick={handleOpenDeliverable} className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs">
            <ExternalLink className="w-3.5 h-3.5" />
            Open Deliverable
          </button>
        )}
        <button onClick={onReset} className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs">
          <RotateCcw className="w-3.5 h-3.5" />
          New run
        </button>
      </div>
    </div>
  );
}

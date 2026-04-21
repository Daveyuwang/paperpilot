import { useCallback, useEffect, useRef } from "react";
import {
  FlaskConical, Play, RotateCcw, ExternalLink, Check,
  HelpCircle, Loader2, Sparkles, Lightbulb, ListChecks,
} from "lucide-react";
import { useDeepResearchStore, type DeepResearchStatus } from "@/store/deepResearchStore";
import type { GeneratedDRPlan } from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import { useDeepResearchRun } from "@/hooks/useDeepResearchRun";
import { TaskPageShell } from "./shared/TaskPageShell";
import { WorkflowError } from "./shared/WorkflowError";
import { StatGrid } from "./shared/StatGrid";
import { ClarificationPanel } from "./shared/ClarificationPanel";
import { VerticalTimeline } from "./DeepResearchProgress/VerticalTimeline";
import type { DeepResearchRunResult } from "@/types";

/* ── Helpers ───────────────────────────────────────────────────────────────── */

const STATUS_ORDER: DeepResearchStatus[] = [
  "idle", "generating_plan", "plan_ready",
  "validating", "needs_clarification", "planning", "executing",
  "evaluating", "replanning", "synthesizing",
  "interrupted", "completed", "blocked", "failed",
];

function reached(cur: DeepResearchStatus, target: DeepResearchStatus) {
  return STATUS_ORDER.indexOf(cur) >= STATUS_ORDER.indexOf(target);
}

/* ── Root ──────────────────────────────────────────────────────────────────── */

export function DeepResearchView() {
  const { getActiveWorkspace } = useWorkspaceStore();
  const wid = getActiveWorkspace()?.id ?? "default";
  const store = useDeepResearchStore();
  const { status } = store;

  const pastIdle = reached(status, "generating_plan");
  const pastPlan = reached(status, "validating");
  const isRunning = ["validating", "planning", "executing", "evaluating", "replanning", "synthesizing"].includes(status);
  const showTimeline = isRunning || ["generating_plan", "plan_ready"].includes(status) || reached(status, "interrupted");

  return (
    <TaskPageShell
      icon={<FlaskConical className="w-4 h-4 text-accent-600" />}
      title="Deep Research"
    >
      <div className="space-y-4">
        {/* ① Input — always visible once topic exists */}
        <TopicInput workspaceId={wid} locked={pastIdle} />

        {/* ② Plan generation / review — grows below input */}
        {pastIdle && <PlanSection workspaceId={wid} />}

        {/* ③ Clarification — inline block if needed */}
        {status === "needs_clarification" && (
          <ClarificationPanel
            questions={store.clarificationQuestions}
            onRetry={() => store.setStatus("idle")}
            onReset={store.reset}
          />
        )}

        {/* ④ Progress timeline — grows below plan */}
        {showTimeline && <LiveProgress />}

        {/* ⑤ Interrupted banner */}
        {status === "interrupted" && <InterruptedBanner />}

        {/* ⑥ Result — grows below progress */}
        {status === "completed" && store.result && (
          <ResultSummary result={store.result} workspaceId={wid} />
        )}

        {/* ⑦ Error */}
        {(status === "failed" || status === "blocked") && (
          <WorkflowError
            message={store.errorMessage}
            title={status === "blocked" ? "Blocked" : "Something went wrong"}
            onReset={store.reset}
            onRetry={() => store.setStatus("idle")}
          />
        )}
      </div>
    </TaskPageShell>
  );
}

/* ── ① Topic input ─────────────────────────────────────────────────────────── */

function TopicInput({ workspaceId, locked }: { workspaceId: string; locked: boolean }) {
  const store = useDeepResearchStore();
  const { input, setInput } = store;
  const runStream = useDeepResearchRun(workspaceId);
  const { getIncludedSources } = useSourceStore();
  const sources = getIncludedSources(workspaceId);

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-surface-600">
        What do you want to research?
      </label>
      <textarea
        value={input.topic}
        onChange={(e) => setInput({ topic: e.target.value })}
        placeholder="e.g. How do modern attention mechanisms compare for long-context tasks?"
        rows={locked ? 2 : 3}
        disabled={locked}
        className={`input-base w-full resize-none ${locked ? "opacity-60 cursor-default" : ""}`}
      />
      {sources.length > 0 && !locked && (
        <p className="text-[11px] text-surface-400">
          {sources.length} workspace source{sources.length !== 1 ? "s" : ""} will be used
        </p>
      )}

      {/* Action buttons — only when idle */}
      {!locked && (
        <div className="flex items-center gap-2">
          <button
            onClick={() => store.setStatus("generating_plan")}
            disabled={!input.topic.trim()}
            className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
          >
            <FlaskConical className="w-3.5 h-3.5" />
            Start
          </button>
          <button
            onClick={runStream}
            disabled={!input.topic.trim()}
            className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs text-surface-500"
          >
            <Play className="w-3.5 h-3.5" />
            Run Directly
          </button>
        </div>
      )}
    </div>
  );
}

/* ── ② Plan section (generating → review → collapsed) ──────────────────────── */

function PlanSection({ workspaceId }: { workspaceId: string }) {
  const store = useDeepResearchStore();
  const { status, input, generatedPlan } = store;
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const runStream = useDeepResearchRun(workspaceId);
  const generating = useRef(false);

  const pastPlan = reached(status, "validating");

  const handleGeneratePlan = useCallback(async () => {
    if (generating.current) return;
    generating.current = true;
    store.startPlanGeneration();
    try {
      const sources = getIncludedSources(workspaceId);
      const wsPayload = sources.map((s) => ({
        id: s.id, title: s.title, authors: s.authors,
        year: s.year, abstract: s.abstract, provider: s.provider,
        paper_id: s.paper_id, label: s.label,
      }));
      const res = await api.generateDRPlan({
        topic: input.topic,
        workspace_id: workspaceId,
        workspace_sources: wsPayload,
        active_paper_id: activePaper?.id ?? null,
      });
      store.setGeneratedPlan({
        subQuestions: res.sub_questions.map((sq) => ({
          id: sq.id, question: sq.question, rationale: sq.rationale,
          searchQueries: sq.search_queries, priority: sq.priority,
        })),
        overallApproach: res.overall_approach,
        recommendedDepth: res.recommended_depth,
        sourcesStrategy: res.sources_strategy,
        focusNote: res.focus_note,
      });
      store.completePlanGeneration();
      store.setStatus("plan_ready");
    } catch (err: unknown) {
      store.setFailed(err instanceof Error ? err.message : "Plan generation failed");
    } finally {
      generating.current = false;
    }
  }, [input.topic, workspaceId, activePaper, getIncludedSources, store]);

  useEffect(() => {
    if (status === "generating_plan" && !generatedPlan && !generating.current) {
      handleGeneratePlan();
    }
  }, [status, generatedPlan, handleGeneratePlan]);

  // Generating spinner
  if (status === "generating_plan" && !generatedPlan) {
    return (
      <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-surface-50 border border-surface-200">
        <Loader2 className="w-4 h-4 text-accent-600 animate-spin shrink-0" />
        <div>
          <p className="text-sm font-medium text-surface-700">Generating research plan...</p>
          <p className="text-xs text-surface-500 mt-0.5">Analyzing topic and identifying sub-questions</p>
        </div>
      </div>
    );
  }

  if (!generatedPlan) return null;

  // Collapsed after confirm
  if (pastPlan) {
    return (
      <div className="px-3 py-2 rounded-lg bg-surface-50 border border-surface-200 space-y-1">
        <div className="flex items-center gap-2">
          <Check className="w-3 h-3 text-emerald-600 shrink-0" />
          <span className="text-xs font-medium text-surface-600">
            Plan confirmed — {generatedPlan.subQuestions.length} sub-questions
          </span>
        </div>
        <p className="text-[11px] text-surface-500 truncate pl-5">{generatedPlan.overallApproach}</p>
      </div>
    );
  }

  // Full plan review (plan_ready)
  return (
    <div className="space-y-4">
      {/* Approach */}
      <div className="px-4 py-3 rounded-lg bg-surface-50 border border-surface-200 space-y-2">
        <div className="flex items-center gap-2">
          <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
          <span className="text-xs font-medium text-surface-700">Approach</span>
        </div>
        <p className="text-xs text-surface-600">{generatedPlan.overallApproach}</p>
        <div className="flex gap-3 text-[11px] text-surface-500">
          <span>Depth: {generatedPlan.recommendedDepth}</span>
          <span>Sources: {generatedPlan.sourcesStrategy}</span>
        </div>
        {generatedPlan.focusNote && (
          <p className="text-[11px] text-accent-600 italic">{generatedPlan.focusNote}</p>
        )}
      </div>

      {/* Sub-questions */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 px-1">
          <ListChecks className="w-3.5 h-3.5 text-surface-500" />
          <span className="text-xs font-medium text-surface-700">
            Research questions ({generatedPlan.subQuestions.length})
          </span>
        </div>
        <div className="space-y-1">
          {generatedPlan.subQuestions.map((q, i) => (
            <div key={q.id} className="px-3 py-2 rounded border border-surface-200 bg-white">
              <div className="flex items-start gap-2">
                <span className="text-[10px] font-mono text-surface-400 mt-0.5 shrink-0">{i + 1}.</span>
                <p className="text-xs text-surface-700 flex-1">{q.question}</p>
                <span className="text-[10px] text-surface-400 shrink-0">P{q.priority}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={runStream}
          className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs"
        >
          <Check className="w-3.5 h-3.5" />
          Looks Good — Run
        </button>
        <button
          onClick={store.reset}
          className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Start Over
        </button>
      </div>
    </div>
  );
}

/* ── ④ Live progress ───────────────────────────────────────────────────────── */

function LiveProgress() {
  const {
    generatedTitle, macroStages, subQuestions,
    sectionsProgressV2, planSummary, currentActivity,
  } = useDeepResearchStore();

  return (
    <div className="space-y-3">
      {generatedTitle && (
        <div className="text-sm font-medium text-surface-700 px-1">
          {generatedTitle}
        </div>
      )}
      <VerticalTimeline
        macroStages={macroStages}
        subQuestions={subQuestions}
        sectionsProgress={sectionsProgressV2}
        planSummary={planSummary}
        currentActivity={currentActivity}
        generatedTitle={generatedTitle}
      />
    </div>
  );
}

/* ── ⑤ Interrupted banner ──────────────────────────────────────────────────── */

function InterruptedBanner() {
  const { currentStageMessage, sectionsProgress, generatedTitle, reset } = useDeepResearchStore();
  const completedSections = sectionsProgress.filter((s) => s.status === "done").length;

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200">
        <RotateCcw className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-800">Research was interrupted</p>
          <p className="text-xs text-amber-600 mt-1">
            The page was refreshed while research was running. The stream cannot be resumed.
          </p>
        </div>
      </div>

      {(generatedTitle || currentStageMessage || completedSections > 0) && (
        <div className="px-4 py-3 rounded-lg bg-surface-50 border border-surface-200 space-y-1 text-xs">
          <p className="font-medium text-surface-600">Last known progress:</p>
          {generatedTitle && <p className="text-surface-700">Title: {generatedTitle}</p>}
          {currentStageMessage && <p className="text-surface-500">Stage: {currentStageMessage}</p>}
          {completedSections > 0 && (
            <p className="text-surface-500">
              {completedSections} of {sectionsProgress.length} sub-questions completed
            </p>
          )}
        </div>
      )}

      <button onClick={reset} className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs">
        <RotateCcw className="w-3 h-3" />
        Start Over
      </button>
    </div>
  );
}

/* ── ⑥ Result summary ─────────────────────────────────────────────────────── */

function ResultSummary({ result, workspaceId }: { result: DeepResearchRunResult; workspaceId: string }) {
  const { createdDeliverableId, reset } = useDeepResearchStore();
  const { setSelectedNav, setConsolePanelTab } = useWorkspaceStore();
  const { setActiveDeliverable, getDeliverables, selectSection } = useDeliverableStore();

  const draftedCount = (result.section_updates ?? []).filter((u) => u.generated_content.trim()).length;
  const skippedCount = (result.section_updates ?? []).filter((u) => !u.generated_content.trim()).length;

  const handleOpenDeliverable = () => {
    if (createdDeliverableId) {
      setActiveDeliverable(workspaceId, createdDeliverableId);
      const del = getDeliverables(workspaceId).find((d) => d.id === createdDeliverableId);
      const firstSection = del?.sections.sort((a, b) => a.order - b.order)[0];
      if (firstSection) selectSection(createdDeliverableId, firstSection.id);
    }
    setSelectedNav("console");
    setConsolePanelTab("deliverable");
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
        { label: "Sources used", value: (result.selected_source_ids ?? []).length },
        { label: "Sources discovered", value: (result.discovered_sources ?? []).length },
      ]} />

      {(result.discovered_sources ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1.5">Discovered sources saved</h4>
          <div className="space-y-1">
            {(result.discovered_sources ?? []).map((s, i) => (
              <div key={i} className="text-xs text-surface-600 bg-white border border-surface-200 rounded px-2.5 py-1.5 flex items-center gap-2">
                <span className="text-[10px] text-surface-400 shrink-0">{s.provider}</span>
                <span className="truncate">{s.title}</span>
                {s.year && <span className="text-[10px] text-surface-400 shrink-0">{s.year}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {(result.unresolved_questions ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">Open questions identified</h4>
          <p className="text-[11px] text-surface-400 mb-1.5">
            The following gaps or uncertainties were found during research.
            {(result.follow_up_items ?? []).length > 0 && (
              <> The most actionable ones were promoted to your Agenda.</>
            )}
          </p>
          <div className="space-y-1">
            {(result.unresolved_questions ?? []).map((q, i) => {
              const promoted = (result.follow_up_items ?? []).some(
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

      {(result.follow_up_items ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">
            Follow-up agenda items added
            <span className="text-[10px] text-surface-400 font-normal ml-1">
              ({Math.min((result.follow_up_items ?? []).length, 5)} of {(result.unresolved_questions ?? []).length} questions)
            </span>
          </h4>
          <div className="space-y-1">
            {(result.follow_up_items ?? []).slice(0, 5).map((item, i) => (
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

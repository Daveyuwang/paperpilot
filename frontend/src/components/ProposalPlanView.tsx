import { useCallback, useEffect, useRef } from "react";
import {
  FileText, Play, RotateCcw, ExternalLink,
  FlaskConical, Check,
} from "lucide-react";
import clsx from "clsx";
import { useProposalPlanStore } from "@/store/proposalPlanStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import { useProposalPlanRun } from "@/hooks/useProposalPlanRun";
import { TaskPageShell } from "./shared/TaskPageShell";
import { WorkflowError } from "./shared/WorkflowError";
import { VerticalTimeline } from "./DeepResearchProgress/VerticalTimeline";
import { StatGrid } from "./shared/StatGrid";
import { ClarificationPanel } from "./shared/ClarificationPanel";
import { ConversationalFlow, type GeneratedPlan } from "./shared/ConversationalFlow";
import type { ProposalPlanMode, ProposalPlanRunResult } from "@/types";

export function ProposalPlanView() {
  const store = useProposalPlanStore();
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";

  const { status, result, clarificationQuestions, errorMessage } = store;

  const isRunning = !["idle", "generating_plan", "plan_ready", "needs_clarification", "completed", "blocked", "failed", "interrupted"].includes(status);

  return (
    <TaskPageShell
      icon={<FileText className="w-4 h-4 text-accent-600" />}
      title="Proposal / Plan"
    >
      {status === "idle" && <InputForm workspaceId={wid} />}
      {(status === "generating_plan" || status === "plan_ready") && (
        <PPPlanStep workspaceId={wid} />
      )}
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
  const {
    generatedTitle,
    macroStages, sectionsProgressV2, currentActivity,
  } = useProposalPlanStore();

  return (
    <div className="space-y-3">
      {generatedTitle && (
        <div className="text-sm font-medium text-surface-700 px-1">
          {generatedTitle}
        </div>
      )}
      <VerticalTimeline
        macroStages={macroStages}
        subQuestions={[]}
        sectionsProgress={sectionsProgressV2}
        planSummary={null}
        currentActivity={currentActivity}
        generatedTitle={generatedTitle}
      />
    </div>
  );
}

/* ── Interrupted state ─────────────────────────────────────────────────── */

function PPInterruptedState() {
  const { currentStageMessage, sectionsProgress, sectionsProgressV2, generatedTitle, input, reset } = useProposalPlanStore();
  const completedSections = sectionsProgressV2.length > 0
    ? sectionsProgressV2.filter((s) => s.status === "done").length
    : sectionsProgress.filter((s) => s.status === "done").length;
  const totalSections = sectionsProgressV2.length > 0 ? sectionsProgressV2.length : sectionsProgress.length;

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
              {completedSections} of {totalSections} sections drafted
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

/* ── InputForm (simplified) ──────────────────────────────────────────── */

function InputForm({ workspaceId }: { workspaceId: string }) {
  const store = useProposalPlanStore();
  const { input, setInput } = store;
  const runStream = useProposalPlanRun(workspaceId);
  const { getIncludedSources } = useSourceStore();
  const sources = getIncludedSources(workspaceId);

  return (
    <div className="space-y-4">
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

      <div>
        <label className="block text-xs font-medium text-surface-600 mb-1">
          What do you want to {input.mode === "proposal" ? "propose" : "plan"}?
        </label>
        <textarea
          value={input.topic}
          onChange={(e) => setInput({ topic: e.target.value })}
          placeholder={input.mode === "proposal"
            ? "e.g. A novel approach to few-shot learning using retrieval-augmented generation"
            : "e.g. 3-month research plan for investigating LLM reasoning capabilities"
          }
          rows={3}
          className="input-base w-full resize-none"
        />
        {sources.length > 0 && (
          <p className="text-[11px] text-surface-400 mt-1">
            {sources.length} workspace source{sources.length !== 1 ? "s" : ""} will be used
          </p>
        )}
      </div>

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
    </div>
  );
}

/* ── Plan step (conversational flow) ──────────────────────────────────── */

function PPPlanStep({ workspaceId }: { workspaceId: string }) {
  const store = useProposalPlanStore();
  const { status, input, generatedPlan } = store;
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const runStream = useProposalPlanRun(workspaceId);
  const generating = useRef(false);

  const handleGeneratePlan = useCallback(async () => {
    if (generating.current) return;
    generating.current = true;
    store.setStatus("generating_plan");
    try {
      const sources = getIncludedSources(workspaceId);
      const wsPayload = sources.map((s) => ({
        id: s.id, title: s.title, authors: s.authors,
        year: s.year, abstract: s.abstract, provider: s.provider,
        paper_id: s.paper_id, label: s.label,
      }));
      const res = await api.generatePPPlan({
        mode: input.mode,
        topic: input.topic,
        workspace_id: workspaceId,
        workspace_sources: wsPayload,
        active_paper_id: activePaper?.id ?? null,
      });
      store.setGeneratedPlan({
        outlineSections: res.outline_sections,
        overallApproach: res.overall_approach,
        recommendedDepth: res.recommended_depth,
        sourcesStrategy: res.sources_strategy,
        focusNote: res.focus_note,
      });
      store.setStatus("plan_ready");
    } catch (err: unknown) {
      store.setFailed(err instanceof Error ? err.message : "Plan generation failed");
    } finally {
      generating.current = false;
    }
  }, [input.topic, input.mode, workspaceId, activePaper, getIncludedSources, store]);

  useEffect(() => {
    if (status === "generating_plan" && !generatedPlan && !generating.current) {
      handleGeneratePlan();
    }
  }, [status, generatedPlan, handleGeneratePlan]);

  const handleConfirmPlan = useCallback((_plan: GeneratedPlan) => {
    runStream();
  }, [runStream]);

  const plan: GeneratedPlan | null = generatedPlan
    ? {
        type: "proposal_plan",
        outlineSections: generatedPlan.outlineSections,
        overallApproach: generatedPlan.overallApproach,
        recommendedDepth: generatedPlan.recommendedDepth,
        sourcesStrategy: generatedPlan.sourcesStrategy,
        focusNote: generatedPlan.focusNote,
      }
    : null;

  return (
    <ConversationalFlow
      topic={input.topic}
      plan={plan}
      isGenerating={status === "generating_plan"}
      onGeneratePlan={handleGeneratePlan}
      onConfirmPlan={handleConfirmPlan}
      onCancel={store.reset}
    />
  );
}

/* ── ResultSummary ────────────────────────────────────────────────────── */

function ResultSummary({ result, workspaceId, onReset }: {
  result: ProposalPlanRunResult;
  workspaceId: string;
  onReset: () => void;
}) {
  const { createdDeliverableId } = useProposalPlanStore();
  const { setSelectedNav, setConsolePanelTab } = useWorkspaceStore();
  const { setActiveDeliverable, getDeliverables, selectSection } = useDeliverableStore();

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
          { label: "Sections drafted", value: (result.section_updates ?? []).filter((u) => u.generated_content.trim()).length },
          { label: "Sections skipped", value: (result.skipped_section_ids ?? []).length },
          { label: "Sources used", value: (result.selected_source_ids ?? []).length },
          { label: "DR context used", value: (result.deep_research_context_ids ?? []).length },
        ]}
      />

      {(result.unresolved_questions ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">Open questions</h4>
          <div className="space-y-1">
            {(result.unresolved_questions ?? []).slice(0, 5).map((q, i) => (
              <div key={i} className="text-xs text-surface-600 bg-amber-50 border border-amber-200 rounded px-2.5 py-1.5">
                {q}
              </div>
            ))}
          </div>
        </div>
      )}

      {(result.follow_up_items ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-surface-600 mb-1">
            Follow-up agenda items added
            <span className="text-[10px] text-surface-400 font-normal ml-1">
              ({(result.follow_up_items ?? []).length} promoted)
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

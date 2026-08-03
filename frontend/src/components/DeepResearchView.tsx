import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Check, ChevronDown, ChevronRight, FlaskConical, Lightbulb, ListChecks, Loader2, Play, RotateCcw } from "lucide-react";
import { api } from "@/api/client";
import { useDeepResearchRun } from "@/hooks/useDeepResearchRun";
import { useDeepResearchStore } from "@/store/deepResearchStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { usePaperStore } from "@/store/paperStore";
import { useSourceStore } from "@/store/sourceStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { ClarificationPanel } from "./shared/ClarificationPanel";
import { TaskPageShell } from "./shared/TaskPageShell";
import { WorkflowError } from "./shared/WorkflowError";
import { ResearchRunConsole } from "./ResearchRun/ResearchRunConsole";

export function DeepResearchView() {
  const workspace = useWorkspaceStore((state) => state.getActiveWorkspace());
  const workspaceId = workspace?.id ?? "default";
  const store = useDeepResearchStore();
  const actions = useDeepResearchRun(workspaceId);
  const activeRunId = store.activeRunIdByWorkspace[workspaceId] ?? null;
  const run = activeRunId ? store.runsById[activeRunId] ?? null : null;
  const loadAttempted = useRef<string | null>(null);
  const { setSelectedNav, setConsolePanelTab, setActiveViewerTab } = useWorkspaceStore();
  const { setActiveDeliverable, getDeliverables, selectSection } = useDeliverableStore();

  useEffect(() => {
    if (!activeRunId || run || loadAttempted.current === activeRunId) return;
    loadAttempted.current = activeRunId;
    void actions.load(activeRunId, true);
  }, [actions, activeRunId, run]);

  const startNew = useCallback(() => {
    actions.cancelConnection();
    store.selectRun(workspaceId, null);
    store.resetLauncher();
    loadAttempted.current = null;
  }, [actions, store, workspaceId]);

  const openDeliverable = useCallback(() => {
    if (!run) return;
    const deliverableId = store.materializedDeliverablesByRun[run.runId];
    if (deliverableId) {
      setActiveDeliverable(workspaceId, deliverableId);
      const deliverable = getDeliverables(workspaceId).find((item) => item.id === deliverableId);
      const firstSection = deliverable ? [...deliverable.sections].sort((left, right) => left.order - right.order)[0] : null;
      if (firstSection) selectSection(deliverableId, firstSection.id);
    }
    setActiveViewerTab("deliverable");
    setSelectedNav("console");
    setConsolePanelTab("deliverable");
  }, [getDeliverables, run, selectSection, setActiveDeliverable, setActiveViewerTab, setConsolePanelTab, setSelectedNav, store.materializedDeliverablesByRun, workspaceId]);

  if (run) {
    return (
      <ResearchRunConsole
        run={run}
        onResume={() => void actions.resume(run.runId)}
        onReconnect={() => void actions.reconnect(run.runId)}
        onNewResearch={startNew}
        onOpenDeliverable={store.materializedDeliverablesByRun[run.runId] ? openDeliverable : undefined}
      />
    );
  }

  return (
    <TaskPageShell icon={<FlaskConical className="h-4 w-4 text-accent-600" />} title="Deep Research" description="Plan a bounded investigation, then inspect every evaluation and repair decision.">
      <div className="space-y-4">
        <TopicInput workspaceId={workspaceId} locked={["generating_plan", "validating", "loading_run"].includes(store.status)} onRunDirectly={actions.start} />

        {["generating_plan", "plan_ready"].includes(store.status) && <PlanSection workspaceId={workspaceId} onRun={actions.start} />}

        {store.status === "validating" && (
          <div className="flex items-center gap-3 rounded-lg bg-surface-50 px-4 py-3 ring-1 ring-inset ring-surface-200" role="status">
            <Loader2 className="h-4 w-4 text-accent-600 motion-safe:animate-spin" aria-hidden="true" />
            <div><p className="text-sm font-medium text-surface-800">Creating durable research run</p><p className="mt-0.5 text-xs text-surface-600">Waiting for the server-authoritative run ID and first checkpointed event.</p></div>
          </div>
        )}

        {store.status === "loading_run" && (
          <div className="flex min-h-48 flex-col items-center justify-center rounded-xl bg-surface-50 px-6 py-10 text-center ring-1 ring-inset ring-surface-200" role="status">
            <Loader2 className="h-5 w-5 text-accent-600 motion-safe:animate-spin" aria-hidden="true" />
            <p className="mt-3 text-sm font-medium text-surface-800">Loading authoritative run snapshot</p>
          </div>
        )}

        {store.status === "needs_clarification" && (
          <ClarificationPanel questions={store.clarificationQuestions} onRetry={() => store.setStatus("idle")} onReset={store.resetLauncher} />
        )}

        {["failed", "blocked"].includes(store.status) && (
          <WorkflowError
            message={store.errorMessage}
            title={store.status === "blocked" ? "Research blocked" : "Could not start research"}
            onReset={startNew}
            onRetry={() => store.generatedPlan ? store.setStatus("plan_ready") : store.setStatus("idle")}
          />
        )}
      </div>
    </TaskPageShell>
  );
}

function TopicInput({ workspaceId, locked, onRunDirectly }: { workspaceId: string; locked: boolean; onRunDirectly: () => Promise<void> }) {
  const { input, setInput, setStatus } = useDeepResearchStore();
  const { getSources, getIncludedSources, setIncluded } = useSourceStore();
  const allSources = getSources(workspaceId);
  const includedSources = getIncludedSources(workspaceId);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  return (
    <section className="space-y-2" aria-labelledby="research-topic-label">
      <label id="research-topic-label" htmlFor="deep-research-topic" className="block text-xs font-medium text-surface-700">What do you want to research?</label>
      <textarea id="deep-research-topic" value={input.topic} onChange={(event) => setInput({ topic: event.target.value })} placeholder="e.g. How do modern attention mechanisms compare for long-context tasks?" rows={locked ? 2 : 3} disabled={locked} className={`input-base w-full resize-none ${locked ? "cursor-default opacity-60" : ""}`} />

      {allSources.length > 0 && !locked && (
        <div className="rounded-lg bg-surface-50/50 ring-1 ring-inset ring-surface-200">
          <button type="button" onClick={() => setSourcesOpen((open) => !open)} aria-expanded={sourcesOpen} aria-controls="deep-research-sources" className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">
            <BookOpen className="h-3.5 w-3.5 shrink-0 text-surface-600" aria-hidden="true" />
            <span className="flex-1 text-xs text-surface-700">{includedSources.length} of {allSources.length} sources selected</span>
            {sourcesOpen ? <ChevronDown className="h-3 w-3 text-surface-600" /> : <ChevronRight className="h-3 w-3 text-surface-600" />}
          </button>
          {sourcesOpen && <div id="deep-research-sources" className="space-y-0.5 px-2 pb-2">{allSources.map((source) => <label key={source.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-100"><input type="checkbox" checked={source.included} onChange={(event) => setIncluded(workspaceId, source.id, event.target.checked)} className="h-3.5 w-3.5 rounded border-surface-300 text-accent-600 focus:ring-accent-400" /><span className="min-w-0 flex-1 truncate text-xs text-surface-700">{source.title}</span><span className="shrink-0 text-[10px] text-surface-600">{source.provider}</span></label>)}</div>}
        </div>
      )}

      {!locked && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button type="button" onClick={() => setStatus("generating_plan")} disabled={!input.topic.trim()} className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-xs"><FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />Review plan</button>
          <button type="button" onClick={() => void onRunDirectly()} disabled={!input.topic.trim()} className="btn-secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Play className="h-3.5 w-3.5" aria-hidden="true" />Run directly</button>
        </div>
      )}
    </section>
  );
}

function PlanSection({ workspaceId, onRun }: { workspaceId: string; onRun: () => Promise<void> }) {
  const store = useDeepResearchStore();
  const { status, input, generatedPlan } = store;
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const generating = useRef(false);

  const generate = useCallback(async () => {
    if (generating.current) return;
    generating.current = true;
    store.startPlanGeneration();
    try {
      const response = await api.generateDRPlan({
        topic: input.topic,
        workspace_id: workspaceId,
        workspace_sources: getIncludedSources(workspaceId).map((source) => ({ id: source.id, title: source.title, authors: source.authors, year: source.year, abstract: source.abstract, provider: source.provider, paper_id: source.paper_id, label: source.label })),
        active_paper_id: activePaper?.id ?? null,
      });
      store.setGeneratedPlan({
        subQuestions: response.sub_questions.map((question) => ({ id: question.id, question: question.question, rationale: question.rationale, searchQueries: question.search_queries, priority: question.priority })),
        overallApproach: response.overall_approach,
        recommendedDepth: response.recommended_depth,
        sourcesStrategy: response.sources_strategy,
        focusNote: response.focus_note,
      });
    } catch (error) {
      store.setFailed(error instanceof Error ? error.message : "Plan generation failed.");
    } finally {
      generating.current = false;
    }
  }, [activePaper?.id, getIncludedSources, input.topic, store, workspaceId]);

  useEffect(() => {
    if (status === "generating_plan" && !generatedPlan && !generating.current) void generate();
  }, [generate, generatedPlan, status]);

  if (!generatedPlan) return <div className="flex items-center gap-3 rounded-lg bg-surface-50 px-4 py-3 ring-1 ring-inset ring-surface-200" role="status"><Loader2 className="h-4 w-4 text-accent-600 motion-safe:animate-spin" aria-hidden="true" /><div><p className="text-sm font-medium text-surface-800">Generating research plan</p><p className="mt-0.5 text-xs text-surface-600">Defining stable subquestions and search scope.</p></div></div>;

  return (
    <section className="space-y-4" aria-labelledby="research-plan-heading">
      <div className="rounded-lg bg-surface-50 px-4 py-3 ring-1 ring-inset ring-surface-200">
        <div className="flex items-center gap-2"><Lightbulb className="h-3.5 w-3.5 text-amber-600" aria-hidden="true" /><h2 id="research-plan-heading" className="text-xs font-semibold text-surface-800">Research approach</h2></div>
        <p className="mt-2 text-xs leading-5 text-surface-700">{generatedPlan.overallApproach}</p>
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-surface-600"><span>Depth: {generatedPlan.recommendedDepth}</span><span>Sources: {generatedPlan.sourcesStrategy}</span></div>
      </div>
      <div>
        <div className="flex items-center gap-2"><ListChecks className="h-3.5 w-3.5 text-surface-600" aria-hidden="true" /><h3 className="text-xs font-semibold text-surface-800">Research questions ({generatedPlan.subQuestions.length})</h3></div>
        <ol className="mt-2 divide-y divide-surface-200 rounded-lg bg-white ring-1 ring-inset ring-surface-200">{generatedPlan.subQuestions.map((question, index) => <li key={question.id} className="flex items-start gap-2 px-3 py-2.5"><span className="mt-0.5 font-mono text-[10px] text-surface-600">{index + 1}</span><span className="min-w-0 flex-1 text-xs leading-5 text-surface-800">{question.question}</span><span className="text-[10px] text-surface-600">P{question.priority}</span></li>)}</ol>
      </div>
      <div className="flex flex-wrap gap-2"><button type="button" onClick={() => void onRun()} className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-xs"><Check className="h-3.5 w-3.5" aria-hidden="true" />Confirm and run</button><button type="button" onClick={store.resetLauncher} className="btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />Start over</button></div>
    </section>
  );
}

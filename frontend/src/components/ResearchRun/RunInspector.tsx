import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Archive, Check, Circle, Clipboard, FileClock, Loader2, ShieldCheck, XCircle } from "lucide-react";
import clsx from "clsx";
import type { ResearchRunView } from "@/store/deepResearchStore";
import type { ArtifactVersionRef, BudgetSnapshot, ResearchSubQuestion } from "@/types/deepResearch";
import { formatDuration, humanize, shortId } from "./format";

export type InspectorTab = "questions" | "artifacts" | "run";

interface Props {
  run: ResearchRunView;
  selectedArtifactId: string | null;
  onSelectArtifact: (artifactId: string) => void;
  activeTab?: InspectorTab;
  onTabChange?: (tab: InspectorTab) => void;
  showTabs?: boolean;
}

const TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: "questions", label: "Questions" },
  { id: "artifacts", label: "Artifacts" },
  { id: "run", label: "Run" },
];

function QuestionStatus({ question }: { question: ResearchSubQuestion }) {
  const config = question.status === "completed"
    ? { icon: Check, label: "Completed", style: "text-emerald-700" }
    : question.status === "failed"
      ? { icon: XCircle, label: "Failed", style: "text-red-700" }
      : question.status === "in_progress"
        ? { icon: Loader2, label: "In progress", style: "text-accent-700" }
        : { icon: Circle, label: humanize(question.status), style: "text-surface-600" };
  const Icon = config.icon;
  return <span className={clsx("inline-flex items-center gap-1 text-[11px] font-medium", config.style)}><Icon className={clsx("h-3 w-3", question.status === "in_progress" && "motion-safe:animate-spin")} aria-hidden="true" />{config.label}</span>;
}

function QuestionsPanel({ run }: { run: ResearchRunView }) {
  return (
    <div id="research-inspector-questions" role="tabpanel" aria-label="Research questions" className="space-y-2">
      {run.questionOrder.length > 0 ? run.questionOrder.map((id) => {
        const question = run.questionsById[id];
        return (
          <article key={id} className="rounded-lg bg-white px-3 py-3 ring-1 ring-inset ring-surface-200">
            <div className="flex items-center gap-2">
              <QuestionStatus question={question} />
              <span className="ml-auto font-mono text-[10px] text-surface-600" title={question.id}>{shortId(question.id, 6)}</span>
            </div>
            <p className="mt-2 text-xs leading-5 text-surface-800">{question.question}</p>
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-surface-600">
              <span>Plan {question.plan_version}</span>
              <span>Attempt {question.attempt}</span>
              {question.duration_ms != null && <span>{formatDuration(question.duration_ms)}</span>}
              {question.confidence != null && <span>Confidence {Math.round(question.confidence * 100)}%</span>}
            </div>
            {question.error_message && <p className="mt-2 text-[11px] leading-4 text-red-700">{question.error_message}</p>}
          </article>
        );
      }) : <p className="rounded-lg bg-surface-50 px-3 py-3 text-xs leading-5 text-surface-600 ring-1 ring-inset ring-surface-200">Questions will appear with stable server IDs after planning.</p>}
    </div>
  );
}

function ArtifactDetail({ artifact }: { artifact: ArtifactVersionRef }) {
  return (
    <article className="mt-3 rounded-lg bg-white px-3 py-3 ring-1 ring-inset ring-surface-200">
      <div className="flex items-center gap-2">
        <Archive className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />
        <h3 className="text-xs font-semibold text-surface-800">{humanize(artifact.artifact_kind)}</h3>
        <span className="ml-auto rounded-full bg-surface-100 px-2 py-0.5 text-[10px] font-medium text-surface-700">Artifact v{artifact.version_number}</span>
      </div>
      <dl className="mt-3 space-y-2 text-xs">
        <div><dt className="text-surface-600">Logical ID</dt><dd className="mt-0.5 break-all font-mono text-[11px] text-surface-800">{artifact.logical_artifact_id}</dd></div>
        <div className="grid grid-cols-2 gap-3"><div><dt className="text-surface-600">Plan</dt><dd className="font-medium text-surface-800">{artifact.plan_version}</dd></div><div><dt className="text-surface-600">Cycle</dt><dd className="font-medium text-surface-800">{artifact.controller_cycle}</dd></div></div>
        <div><dt className="text-surface-600">Checkpoint</dt><dd className="mt-0.5 break-all font-mono text-[11px] text-surface-800">{artifact.source_checkpoint_id ?? "Not recorded"}</dd></div>
        <div><dt className="text-surface-600">SHA-256 integrity hash</dt><dd className="mt-0.5 break-all font-mono text-[10px] text-surface-800">{artifact.content_hash}</dd></div>
      </dl>
      {artifact.payload && (
        <details className="group mt-3 text-xs">
          <summary className="cursor-pointer font-medium text-accent-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">Frozen payload</summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-50 p-3 font-mono text-[10px] leading-4 text-surface-800 ring-1 ring-inset ring-surface-200">{JSON.stringify(artifact.payload, null, 2)}</pre>
        </details>
      )}
    </article>
  );
}

function ArtifactsPanel({ run, selectedArtifactId, onSelectArtifact }: Pick<Props, "run" | "selectedArtifactId" | "onSelectArtifact">) {
  const selected = selectedArtifactId ? run.artifactsById[selectedArtifactId] : null;
  return (
    <div id="research-inspector-artifacts" role="tabpanel" aria-label="Artifact versions">
      {run.artifactOrder.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {run.artifactOrder.map((id) => {
            const artifact = run.artifactsById[id];
            return (
              <button
                key={id}
                type="button"
                onClick={() => onSelectArtifact(id)}
                aria-pressed={selectedArtifactId === id}
                className={clsx(
                  "rounded-md px-2 py-1.5 text-left text-[11px] ring-1 ring-inset focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400",
                  selectedArtifactId === id ? "bg-accent-50 text-accent-800 ring-accent-200" : "bg-white text-surface-700 ring-surface-200 hover:bg-surface-50",
                )}
              >
                {humanize(artifact.artifact_kind)} · v{artifact.version_number}
              </button>
            );
          })}
        </div>
      ) : <p className="rounded-lg bg-surface-50 px-3 py-3 text-xs leading-5 text-surface-600 ring-1 ring-inset ring-surface-200">Immutable artifact versions will appear after their database commit.</p>}
      {selected && <ArtifactDetail artifact={selected} />}
    </div>
  );
}

const BUDGET_ROWS: Array<{ used: keyof BudgetSnapshot; limit: keyof BudgetSnapshot; label: string }> = [
  { used: "pre_evaluations_used", limit: "pre_evaluation_limit", label: "Evidence evaluations" },
  { used: "targeted_repairs_used", limit: "targeted_repair_limit", label: "Targeted repairs" },
  { used: "partial_replans_used", limit: "partial_replan_limit", label: "Partial replans" },
  { used: "full_replans_used", limit: "full_replan_limit", label: "Full replans" },
  { used: "post_evaluations_used", limit: "post_evaluation_limit", label: "Claim evaluations" },
  { used: "synthesis_repairs_used", limit: "synthesis_repair_limit", label: "Report revisions" },
  { used: "total_recoveries_used", limit: "total_recovery_limit", label: "Total recoveries" },
];

function RunPanel({ run }: { run: ResearchRunView }) {
  const [copied, setCopied] = useState(false);
  async function copyCheckpoint() {
    if (!run.latestCheckpoint) return;
    await navigator.clipboard.writeText(run.latestCheckpoint.checkpoint_id);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }
  return (
    <div id="research-inspector-run" role="tabpanel" aria-label="Run budgets and checkpoint" className="space-y-5">
      <section aria-labelledby="budget-heading">
        <h3 id="budget-heading" className="text-xs font-semibold text-surface-800">Recovery budgets</h3>
        <div className="mt-3 space-y-3">
          {BUDGET_ROWS.map((row) => {
            const used = run.budget[row.used];
            const limit = run.budget[row.limit];
            return (
              <div key={row.label}>
                <div className="flex items-center gap-2 text-[11px] text-surface-700"><span className="flex-1">{row.label}</span><span className="font-mono tabular-nums">{used} / {limit}</span></div>
                <progress className="mt-1 h-1.5 w-full accent-accent-600" value={Math.min(used, limit)} max={Math.max(1, limit)} aria-label={`${row.label}: ${used} of ${limit}`} />
              </div>
            );
          })}
        </div>
      </section>

      <section className="border-t border-surface-200 pt-4" aria-labelledby="checkpoint-heading">
        <h3 id="checkpoint-heading" className="flex items-center gap-1.5 text-xs font-semibold text-surface-800"><FileClock className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />Latest checkpoint</h3>
        {run.latestCheckpoint ? (
          <div className="mt-3 rounded-lg bg-white px-3 py-3 text-xs ring-1 ring-inset ring-surface-200">
            <div className="flex items-center gap-2"><span className="font-mono text-[11px] text-surface-800" title={run.latestCheckpoint.checkpoint_id}>{shortId(run.latestCheckpoint.checkpoint_id, 7)}</span><button type="button" onClick={copyCheckpoint} className="ml-auto rounded p-1 text-surface-600 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400" aria-label="Copy checkpoint ID">{copied ? <Check className="h-3.5 w-3.5" /> : <Clipboard className="h-3.5 w-3.5" />}</button></div>
            <p className="mt-2 text-surface-700">Graph {run.latestCheckpoint.graph_version}</p>
            <p className={clsx("mt-1 font-medium", run.latestCheckpoint.restorable ? "text-emerald-800" : "text-amber-900")}>{run.latestCheckpoint.restorable ? "Restorable" : "Not restorable"}</p>
            {run.latestCheckpoint.next_nodes.length > 0 && <p className="mt-1 leading-4 text-surface-600">Next: {run.latestCheckpoint.next_nodes.join(", ")}</p>}
          </div>
        ) : <p className="mt-2 text-xs leading-5 text-surface-600">No server checkpoint has been confirmed.</p>}
      </section>

      <section className="border-t border-surface-200 pt-4" aria-labelledby="proof-heading">
        <h3 id="proof-heading" className="flex items-center gap-1.5 text-xs font-semibold text-surface-800"><ShieldCheck className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />Run identity</h3>
        <dl className="mt-3 space-y-2 text-xs"><div><dt className="text-surface-600">Run</dt><dd className="break-all font-mono text-[10px] text-surface-800">{run.runId}</dd></div><div><dt className="text-surface-600">Graph</dt><dd className="text-surface-800">{run.graphVersion}</dd></div><div><dt className="text-surface-600">Event cursor</dt><dd className="font-mono text-surface-800">{run.lastSeq}</dd></div></dl>
      </section>
    </div>
  );
}

export function RunInspector({ run, selectedArtifactId, onSelectArtifact, activeTab, onTabChange, showTabs = true }: Props) {
  const [internalTab, setInternalTab] = useState<InspectorTab>(selectedArtifactId ? "artifacts" : "questions");
  const tab = activeTab ?? internalTab;
  const prefix = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (selectedArtifactId) {
      if (onTabChange) onTabChange("artifacts");
      else setInternalTab("artifacts");
    }
  }, [onTabChange, selectedArtifactId]);

  function select(next: InspectorTab) {
    if (onTabChange) onTabChange(next);
    else setInternalTab(next);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    select(TABS[nextIndex].id);
    tabRefs.current[nextIndex]?.focus();
  }

  return (
    <div className="min-w-0">
      {showTabs && <div role="tablist" aria-label="Research run inspector" className="mb-4 flex rounded-lg bg-surface-100 p-1">
        {TABS.map((item, index) => (
          <button
            key={item.id}
            ref={(node) => { tabRefs.current[index] = node; }}
            type="button"
            role="tab"
            id={`${prefix}-${item.id}-tab`}
            aria-selected={tab === item.id}
            aria-controls={`${prefix}-${item.id}-panel`}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => select(item.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={clsx("min-w-0 flex-1 rounded-md px-2 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400", tab === item.id ? "bg-white text-surface-900 shadow-sm" : "text-surface-600 hover:text-surface-800")}
          >{item.label}</button>
        ))}
      </div>}
      <div id={`${prefix}-${tab}-panel`} aria-labelledby={`${prefix}-${tab}-tab`}>
        {tab === "questions" && <QuestionsPanel run={run} />}
        {tab === "artifacts" && <ArtifactsPanel run={run} selectedArtifactId={selectedArtifactId} onSelectArtifact={onSelectArtifact} />}
        {tab === "run" && <RunPanel run={run} />}
      </div>
    </div>
  );
}

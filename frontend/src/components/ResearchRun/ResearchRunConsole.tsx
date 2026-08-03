import { useEffect, useState } from "react";
import { Archive, Clipboard, Check, FileClock, FlaskConical, ListChecks, PlugZap, RefreshCw, RotateCcw, WifiOff } from "lucide-react";
import clsx from "clsx";
import type { ResearchRunView } from "@/store/deepResearchStore";
import { DecisionLoopHistory } from "./DecisionLoopHistory";
import { RunInspector, type InspectorTab } from "./RunInspector";
import { STATUS_LABELS, shortId } from "./format";
import { TerminalOutcomePanel } from "./TerminalOutcome";

type MobileView = "loop" | InspectorTab;

interface Props {
  run: ResearchRunView;
  onResume: () => void;
  onReconnect: () => void;
  onNewResearch: () => void;
  onOpenDeliverable?: () => void;
}

const MOBILE_VIEWS: Array<{ id: MobileView; label: string; icon: typeof ListChecks }> = [
  { id: "loop", label: "Decision loop", icon: RefreshCw },
  { id: "questions", label: "Questions", icon: ListChecks },
  { id: "artifacts", label: "Artifacts", icon: Archive },
  { id: "run", label: "Run", icon: FileClock },
];

const STATUS_STYLES: Record<ResearchRunView["status"], string> = {
  running: "bg-accent-50 text-accent-800 ring-accent-200",
  interrupted: "bg-amber-50 text-amber-900 ring-amber-200",
  completed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  incomplete: "bg-amber-50 text-amber-900 ring-amber-200",
  failed: "bg-red-50 text-red-800 ring-red-200",
};

export function ResearchRunConsole({ run, onResume, onReconnect, onNewResearch, onOpenDeliverable }: Props) {
  const [copied, setCopied] = useState(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("questions");
  const [mobileView, setMobileView] = useState<MobileView>("loop");

  useEffect(() => {
    if (selectedArtifactId) {
      setInspectorTab("artifacts");
      setMobileView("artifacts");
    }
  }, [selectedArtifactId]);

  async function copyRunId() {
    await navigator.clipboard.writeText(run.runId);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  const disconnected = run.status === "running" && ["offline", "resync_required"].includes(run.connection);
  const canResume = run.resume.allowed && Boolean(run.resume.checkpoint_id) && run.status !== "running";

  return (
    <div className="flex h-full min-w-0 flex-col bg-white">
      <header className="flex flex-shrink-0 flex-wrap items-start gap-3 border-b border-surface-200 bg-white px-4 py-4 sm:px-6">
        <span className="mt-0.5 rounded-lg bg-accent-50 p-2 text-accent-700" aria-hidden="true"><FlaskConical className="h-4 w-4" /></span>
        <div className="min-w-[220px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-base font-semibold tracking-[-0.015em] text-surface-900">Research Run</h1>
            <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset", STATUS_STYLES[run.status])}>{STATUS_LABELS[run.status]}</span>
          </div>
          <p className="mt-1 max-w-[70ch] text-xs leading-5 text-surface-700">{run.topic}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-surface-600">
            <span className="font-mono" title={run.runId}>Run {shortId(run.runId, 7)}</span>
            <button type="button" onClick={copyRunId} className="rounded p-0.5 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400" aria-label="Copy run ID">{copied ? <Check className="h-3 w-3" /> : <Clipboard className="h-3 w-3" />}</button>
            <span>Graph {run.graphVersion}</span>
            <span>Event {run.lastSeq}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {disconnected && <button type="button" onClick={onReconnect} className="btn-secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><PlugZap className="h-3.5 w-3.5" aria-hidden="true" />Reconnect</button>}
          {canResume && <button type="button" onClick={onResume} className="btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />Resume from checkpoint</button>}
          {run.status !== "running" && <button type="button" onClick={onNewResearch} className="btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />New research</button>}
        </div>
        {!canResume && run.status === "interrupted" && <p id="resume-unavailable-reason" className="w-full text-right text-[11px] text-surface-700">Resume unavailable: {run.resume.reason}</p>}
      </header>

      <div className="sr-only" aria-live="polite">Run status {STATUS_LABELS[run.status]}. Connection {run.connection}.</div>

      {(disconnected || run.protocolError) && (
        <div className={clsx("mx-4 mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-xs ring-1 ring-inset sm:mx-6", run.protocolError?.recoverable ? "bg-amber-50 text-amber-900 ring-amber-200" : "bg-red-50 text-red-800 ring-red-200")} role="alert">
          <WifiOff className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="flex-1">{run.protocolError?.message ?? "The live connection ended. Reload the server snapshot before continuing."}</span>
        </div>
      )}

      <nav className="flex flex-shrink-0 overflow-x-auto border-b border-surface-200 px-3 py-2 lg:hidden" aria-label="Research Run views">
        {MOBILE_VIEWS.map((view) => {
          const Icon = view.icon;
          return <button key={view.id} type="button" onClick={() => setMobileView(view.id)} aria-current={mobileView === view.id ? "page" : undefined} className={clsx("flex min-w-fit items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400", mobileView === view.id ? "bg-surface-100 text-surface-900" : "text-surface-600 hover:text-surface-800")}><Icon className="h-3.5 w-3.5" aria-hidden="true" />{view.label}</button>;
        })}
      </nav>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className={clsx("min-h-0 min-w-0 overflow-y-auto px-4 py-5 sm:px-6 lg:block", mobileView !== "loop" && "hidden")}>
          <div className="mx-auto max-w-4xl">
            <DecisionLoopHistory run={run} onSelectArtifact={setSelectedArtifactId} />
            <TerminalOutcomePanel run={run} onNewResearch={onNewResearch} onOpenDeliverable={onOpenDeliverable} onSelectArtifact={setSelectedArtifactId} />
          </div>
        </main>

        <aside className="hidden min-h-0 overflow-y-auto border-l border-surface-200 bg-surface-50/60 px-4 py-5 lg:block" aria-label="Research Run inspector">
          <RunInspector run={run} selectedArtifactId={selectedArtifactId} onSelectArtifact={setSelectedArtifactId} activeTab={inspectorTab} onTabChange={setInspectorTab} />
        </aside>

        {mobileView !== "loop" && (
          <section className="min-h-0 overflow-y-auto px-4 py-5 sm:px-6 lg:hidden" aria-label={`${mobileView} inspector`}>
            <RunInspector run={run} selectedArtifactId={selectedArtifactId} onSelectArtifact={setSelectedArtifactId} activeTab={mobileView} onTabChange={(tab) => setMobileView(tab)} showTabs={false} />
          </section>
        )}
      </div>
    </div>
  );
}

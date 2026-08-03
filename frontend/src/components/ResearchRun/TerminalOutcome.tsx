import { AlertTriangle, CheckCircle2, ExternalLink, FileWarning, RotateCcw, XCircle } from "lucide-react";
import clsx from "clsx";
import type { ResearchRunView } from "@/store/deepResearchStore";

interface Props {
  run: ResearchRunView;
  onNewResearch: () => void;
  onOpenDeliverable?: () => void;
  onSelectArtifact: (artifactId: string) => void;
}

export function TerminalOutcomePanel({ run, onNewResearch, onOpenDeliverable, onSelectArtifact }: Props) {
  const terminal = run.terminal;
  if (!terminal) return null;
  const completed = terminal.status === "completed" && terminal.report_accepted && terminal.publishable && Boolean(terminal.final_artifact_version_id);
  const incomplete = terminal.status === "incomplete";
  const interrupted = terminal.status === "interrupted";
  const Icon = completed ? CheckCircle2 : incomplete || interrupted ? FileWarning : XCircle;
  const title = completed ? "Final report accepted" : incomplete ? "Research ended incomplete" : interrupted ? "Research interrupted" : "Research run failed";

  return (
    <section
      aria-labelledby="terminal-outcome-heading"
      className={clsx(
        "mt-5 rounded-xl px-4 py-4 ring-1 ring-inset sm:px-5",
        completed ? "bg-emerald-50 ring-emerald-200" : incomplete || interrupted ? "bg-amber-50 ring-amber-200" : "bg-red-50 ring-red-200",
      )}
    >
      <div className="flex items-start gap-3">
        <Icon className={clsx("mt-0.5 h-5 w-5 shrink-0", completed ? "text-emerald-700" : incomplete || interrupted ? "text-amber-800" : "text-red-700")} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 id="terminal-outcome-heading" className={clsx("text-sm font-semibold", completed ? "text-emerald-900" : incomplete || interrupted ? "text-amber-950" : "text-red-900")}>{title}</h2>
          <p className={clsx("mt-1 text-xs leading-5", completed ? "text-emerald-800" : incomplete || interrupted ? "text-amber-900" : "text-red-800")}>
            {terminal.terminal_reason || (completed ? "The final report is bound to an accepted post-synthesis evaluation and deterministic controller decision." : "No publishable final report was produced.")}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {completed && onOpenDeliverable && (
              <button type="button" onClick={onOpenDeliverable} className="btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />Open deliverable</button>
            )}
            {!completed && terminal.candidate_artifact_version_id && (
              <button type="button" onClick={() => onSelectArtifact(terminal.candidate_artifact_version_id!)} className="btn-secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"><AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />Inspect candidate</button>
            )}
            <button type="button" onClick={onNewResearch} className="btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />New research</button>
          </div>
          {!completed && (
            <p className="mt-3 text-[11px] leading-4 text-amber-900">Candidate content remains inspectable as an immutable artifact and has not been added to a deliverable.</p>
          )}
        </div>
      </div>
    </section>
  );
}

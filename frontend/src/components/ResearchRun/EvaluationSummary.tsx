import { AlertTriangle, Bot, CheckCircle2, ChevronDown, Loader2 } from "lucide-react";
import clsx from "clsx";
import type { EvaluationSummary as EvaluationSummaryType } from "@/types/deepResearch";
import { EVALUATION_LABELS, formatDuration, humanize } from "./format";

interface Props {
  evaluation: EvaluationSummaryType | null;
  onSelectArtifact: (artifactId: string) => void;
}

export function EvaluationSummary({ evaluation, onSelectArtifact }: Props) {
  if (!evaluation) {
    return (
      <section aria-label="LLM evaluation" className="min-w-0 py-2">
        <p className="text-xs font-semibold text-surface-700">LLM evaluation</p>
        <p className="mt-2 text-xs leading-5 text-surface-600">Waiting for a server evaluator run.</p>
      </section>
    );
  }

  const running = evaluation.status === "running";
  const failed = evaluation.status === "failed";
  const StatusIcon = running ? Loader2 : failed ? AlertTriangle : CheckCircle2;

  return (
    <section aria-label="LLM evaluation" className="min-w-0 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-surface-800">
          <Bot className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />
          LLM evaluation
        </span>
        <span className={clsx(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
          failed ? "bg-red-50 text-red-800" : running ? "bg-accent-50 text-accent-700" : "bg-emerald-50 text-emerald-800",
        )}>
          <StatusIcon className={clsx("h-3 w-3", running && "motion-safe:animate-spin")} aria-hidden="true" />
          {running ? "Running" : failed ? "Failed" : EVALUATION_LABELS[evaluation.phase]}
        </span>
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="text-surface-600">Model</dt>
          <dd className="truncate font-medium text-surface-800" title={evaluation.evaluator_model}>{evaluation.evaluator_model}</dd>
        </div>
        <div>
          <dt className="text-surface-600">Server duration</dt>
          <dd className="font-medium tabular-nums text-surface-800">{formatDuration(evaluation.duration_ms)}</dd>
        </div>
        <div>
          <dt className="text-surface-600">Attempts</dt>
          <dd className="font-medium tabular-nums text-surface-800">{running ? "—" : evaluation.attempts}</dd>
        </div>
        <div>
          <dt className="text-surface-600">Issues</dt>
          <dd className="font-medium tabular-nums text-surface-800">{evaluation.issues.length}</dd>
        </div>
      </dl>

      {evaluation.summary && <p className="mt-3 text-xs leading-5 text-surface-700">{evaluation.summary}</p>}
      {evaluation.error_code && <p className="mt-2 text-xs text-red-700">Evaluator error: {humanize(evaluation.error_code)}</p>}

      {(Object.keys(evaluation.scores).length > 0 || evaluation.issues.length > 0) && (
        <details className="group mt-3 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-1 font-medium text-accent-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">
            <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
            Evaluation diagnostics
          </summary>
          {Object.keys(evaluation.scores).length > 0 && (
            <dl className="mt-2 divide-y divide-surface-200 rounded-lg bg-surface-50 px-3 ring-1 ring-inset ring-surface-200">
              {Object.entries(evaluation.scores).sort(([left], [right]) => left.localeCompare(right)).map(([name, score]) => (
                <div key={name} className="flex items-center gap-3 py-2">
                  <dt className="min-w-0 flex-1 text-surface-700">{humanize(name)}</dt>
                  <dd className="font-mono font-medium tabular-nums text-surface-800">{score}</dd>
                </div>
              ))}
            </dl>
          )}
          {evaluation.issues.length > 0 && (
            <ul className="mt-2 space-y-2">
              {evaluation.issues.map((issue) => (
                <li key={issue.id} className="flex flex-wrap items-center gap-2 rounded-md bg-surface-50 px-3 py-2 text-surface-700 ring-1 ring-inset ring-surface-200">
                  <span className={clsx(
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                    issue.severity === "blocker" ? "bg-red-50 text-red-800" : issue.severity === "major" ? "bg-amber-50 text-amber-800" : "bg-surface-100 text-surface-700",
                  )}>{humanize(issue.severity)}</span>
                  <span>{humanize(issue.category)}</span>
                  <span className="font-mono text-[10px] text-surface-600">{issue.id}</span>
                </li>
              ))}
            </ul>
          )}
        </details>
      )}

      {evaluation.artifact_version_id && (
        <button
          type="button"
          onClick={() => onSelectArtifact(evaluation.artifact_version_id!)}
          className="mt-3 rounded-md text-xs font-medium text-accent-700 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
        >
          Inspect frozen evaluation
        </button>
      )}
    </section>
  );
}

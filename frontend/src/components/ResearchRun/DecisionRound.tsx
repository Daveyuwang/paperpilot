import { ArrowDown, CheckCircle2, GitBranch, Loader2, RotateCcw, StopCircle } from "lucide-react";
import clsx from "clsx";
import type { DecisionRound as DecisionRoundType } from "@/types/deepResearch";
import { EvaluationSummary } from "./EvaluationSummary";
import { EVALUATION_LABELS, ROUTE_LABELS, formatDuration } from "./format";

interface Props {
  round: DecisionRoundType;
  active: boolean;
  onSelectArtifact: (artifactId: string) => void;
}

function RouteAndRepair({ round, onSelectArtifact }: Pick<Props, "round" | "onSelectArtifact">) {
  const route = round.route;
  if (!route) {
    return (
      <section aria-label="Deterministic route" className="min-w-0 py-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-surface-800">
          <GitBranch className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />
          Deterministic route
        </p>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-surface-600">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden="true" />
          Waiting for the controller
        </p>
      </section>
    );
  }

  const terminalRoute = route.route === "accept" || route.route === "stop_incomplete";
  const RouteIcon = route.route === "accept" ? CheckCircle2 : route.route === "stop_incomplete" ? StopCircle : RotateCcw;
  return (
    <section aria-label="Deterministic route" className="min-w-0 py-2">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-surface-800">
        <GitBranch className="h-3.5 w-3.5 text-accent-600" aria-hidden="true" />
        Deterministic route
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className={clsx(
          "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold",
          route.route === "accept" ? "bg-emerald-50 text-emerald-800" : route.route === "stop_incomplete" ? "bg-amber-50 text-amber-900" : "bg-accent-50 text-accent-800",
        )}>
          <RouteIcon className="h-3 w-3" aria-hidden="true" />
          {ROUTE_LABELS[route.route]}
        </span>
        {route.weighted_overall_score != null && (
          <span className="text-xs tabular-nums text-surface-700">Weighted score {route.weighted_overall_score}</span>
        )}
      </div>
      <p className="mt-2 text-xs leading-5 text-surface-700">{route.reason}</p>
      <p className="mt-1 font-mono text-[10px] text-surface-600">{route.reason_code}</p>

      {(route.target_sub_question_ids.length > 0 || route.target_report_segment_ids.length > 0) && (
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Repair targets">
          {route.target_sub_question_ids.map((id) => <span key={id} className="rounded-full bg-surface-100 px-2 py-0.5 font-mono text-[10px] text-surface-700">Question {id}</span>)}
          {route.target_report_segment_ids.map((id) => <span key={id} className="rounded-full bg-surface-100 px-2 py-0.5 font-mono text-[10px] text-surface-700">Segment {id}</span>)}
        </div>
      )}
      {route.artifact_version_id && (
        <button type="button" onClick={() => onSelectArtifact(route.artifact_version_id!)} className="mt-3 text-xs font-medium text-accent-700 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">
          Inspect controller transition
        </button>
      )}

      {!terminalRoute && (
        <div className="mt-4 border-t border-surface-200 pt-3">
          <p className="text-xs font-semibold text-surface-800">Repair outcome</p>
          {round.repair ? (
            <>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-surface-700">
                {round.repair.status === "running" && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-600" aria-hidden="true" />}
                {round.repair.status === "completed" && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700" aria-hidden="true" />}
                {round.repair.status === "failed" && <StopCircle className="h-3.5 w-3.5 text-red-700" aria-hidden="true" />}
                <span>{round.repair.label}</span>
                {round.repair.duration_ms != null && <span className="tabular-nums text-surface-600">{formatDuration(round.repair.duration_ms)}</span>}
              </div>
              {round.repair.output_artifact_version_ids.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {round.repair.output_artifact_version_ids.map((artifactId) => (
                    <button key={artifactId} type="button" onClick={() => onSelectArtifact(artifactId)} className="rounded-md bg-surface-50 px-2 py-1 font-mono text-[10px] text-accent-700 ring-1 ring-inset ring-surface-200 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">
                      Artifact {artifactId.slice(0, 8)}
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : <p className="mt-2 text-xs text-surface-600">Waiting for repair execution.</p>}
        </div>
      )}
    </section>
  );
}

export function DecisionRound({ round, active, onSelectArtifact }: Props) {
  return (
    <li>
      <article
        aria-current={active ? "step" : undefined}
        aria-labelledby={`round-${round.id}`}
        className={clsx(
          "rounded-xl bg-white px-4 py-4 ring-1 ring-inset sm:px-5",
          active ? "ring-accent-300" : "ring-surface-200",
        )}
      >
        <header className="flex flex-wrap items-center gap-2 border-b border-surface-200 pb-3">
          <h3 id={`round-${round.id}`} className="text-sm font-semibold text-surface-900">
            Decision round {round.cycle}
          </h3>
          <span className="rounded-full bg-surface-100 px-2 py-0.5 text-[11px] font-medium text-surface-700">
            {EVALUATION_LABELS[round.phase]}
          </span>
          <span className="ml-auto text-[11px] tabular-nums text-surface-600">
            Plan {round.plan_version} · Corpus {round.corpus_version}{round.report_version != null ? ` · Report ${round.report_version}` : ""}
          </span>
        </header>

        <div className="grid min-w-0 gap-2 md:grid-cols-[minmax(0,1fr)_24px_minmax(0,1fr)] md:gap-4">
          <EvaluationSummary evaluation={round.evaluation} onSelectArtifact={onSelectArtifact} />
          <div className="flex items-center justify-center text-surface-300" aria-hidden="true">
            <ArrowDown className="h-4 w-4 md:-rotate-90" />
          </div>
          <RouteAndRepair round={round} onSelectArtifact={onSelectArtifact} />
        </div>
      </article>
    </li>
  );
}

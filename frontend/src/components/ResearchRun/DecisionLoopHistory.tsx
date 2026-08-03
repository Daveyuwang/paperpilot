import { Activity, CheckCircle2 } from "lucide-react";
import type { ResearchRunView } from "@/store/deepResearchStore";
import { DecisionRound } from "./DecisionRound";
import { PHASE_LABELS } from "./format";

interface Props {
  run: ResearchRunView;
  onSelectArtifact: (artifactId: string) => void;
}

export function DecisionLoopHistory({ run, onSelectArtifact }: Props) {
  const rounds = run.roundOrder.map((id) => run.roundsById[id]).filter(Boolean);
  const completedQuestions = run.questionOrder.filter((id) => run.questionsById[id]?.status === "completed").length;
  const currentRoundId = rounds.at(-1)?.id;

  return (
    <section aria-labelledby="decision-loop-heading" className="min-w-0">
      <div className="mb-5 flex flex-wrap items-start gap-3 border-b border-surface-200 pb-4">
        <span className="mt-0.5 rounded-lg bg-accent-50 p-2 text-accent-700" aria-hidden="true"><Activity className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <h2 id="decision-loop-heading" className="text-base font-semibold tracking-[-0.015em] text-surface-900">Decision loop</h2>
          <p className="mt-1 max-w-[70ch] text-xs leading-5 text-surface-600">Each round keeps the LLM evaluation, deterministic route, repair scope, and next outcome connected.</p>
        </div>
        <span className="text-xs tabular-nums text-surface-700">{completedQuestions} / {run.questionOrder.length} questions researched</span>
      </div>

      {run.currentPhase && run.status === "running" && (
        <div className="mb-4 flex items-center gap-2 rounded-lg bg-accent-50 px-3 py-2 text-xs text-accent-800 ring-1 ring-inset ring-accent-200" role="status">
          <Activity className="h-3.5 w-3.5 motion-safe:animate-pulse" aria-hidden="true" />
          <span className="font-medium">Current phase:</span>
          <span>{PHASE_LABELS[run.currentPhase]}</span>
        </div>
      )}

      {rounds.length > 0 ? (
        <ol className="space-y-3">
          {rounds.map((round) => (
            <DecisionRound key={round.id} round={round} active={run.status === "running" && round.id === currentRoundId} onSelectArtifact={onSelectArtifact} />
          ))}
        </ol>
      ) : (
        <div className="flex min-h-48 flex-col items-center justify-center rounded-xl bg-surface-50 px-6 py-10 text-center ring-1 ring-inset ring-surface-200">
          {run.status === "running" ? <Activity className="h-6 w-6 text-accent-600 motion-safe:animate-pulse" aria-hidden="true" /> : <CheckCircle2 className="h-6 w-6 text-surface-400" aria-hidden="true" />}
          <h3 className="mt-3 text-sm font-semibold text-surface-800">{run.status === "running" ? "Building the evidence corpus" : "No evaluator round was recorded"}</h3>
          <p className="mt-1 max-w-md text-xs leading-5 text-surface-600">The first decision round appears after the pre-synthesis LLM evaluator finishes.</p>
        </div>
      )}
    </section>
  );
}

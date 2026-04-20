import { Check, Loader2, Circle } from "lucide-react";
import clsx from "clsx";

interface Stage {
  key: string;
  label: string;
}

interface Props {
  stages: Stage[];
  currentStageKey: string | null;
  runningLabel?: string;
}

export function ProgressStages({ stages, currentStageKey, runningLabel = "Running..." }: Props) {
  const currentIdx = stages.findIndex((s) => s.key === currentStageKey);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-accent-700">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span className="text-sm font-medium">{runningLabel}</span>
      </div>
      <div className="space-y-1.5">
        {stages.map((stage, idx) => {
          const isDone = idx < currentIdx;
          const isCurrent = idx === currentIdx;
          return (
            <div
              key={stage.key}
              className={clsx(
                "flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors",
                isDone && "text-emerald-600 bg-emerald-50",
                isCurrent && "text-accent-700 bg-accent-50 font-medium",
                !isDone && !isCurrent && "text-surface-400",
              )}
            >
              {isDone ? (
                <Check className="w-3.5 h-3.5" />
              ) : isCurrent ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Circle className="w-3.5 h-3.5 opacity-40" />
              )}
              <span>{stage.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

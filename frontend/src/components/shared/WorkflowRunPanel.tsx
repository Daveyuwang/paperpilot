import { useState } from "react";
import { Check, Circle, Loader2, ChevronDown } from "lucide-react";
import clsx from "clsx";

export interface StageDefinition {
  key: string;
  label: string;
}

export interface SectionProgressItem {
  title: string;
  status: "pending" | "drafting" | "done" | "skipped";
  preview?: string;
}

interface Props {
  stages: StageDefinition[];
  currentStatus: string;
  stageMessage: string | null;
  sectionsProgress: SectionProgressItem[];
  sourcesFound?: number;
  sourcesSelected?: number;
}

export function WorkflowRunPanel({
  stages,
  currentStatus,
  stageMessage,
  sectionsProgress,
  sourcesFound,
  sourcesSelected,
}: Props) {
  const currentIdx = stages.findIndex((s) => s.key === currentStatus);
  const [expandHistory, setExpandHistory] = useState(false);

  const completedStages = stages.filter((_, i) => i < currentIdx);
  const currentStage = currentIdx >= 0 ? stages[currentIdx] : null;
  const pendingStages = stages.filter((_, i) => i > currentIdx);
  const progressPct = stages.length > 0 ? Math.round(((currentIdx + 1) / stages.length) * 100) : 0;

  return (
    <div className="space-y-3">
      {/* Progress bar */}
      {currentIdx >= 0 && (
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-surface-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-accent-500 rounded-full transition-all duration-700 ease-out"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-[11px] text-surface-400 tabular-nums">{progressPct}%</span>
        </div>
      )}
      {/* Completed stages — collapsed by default */}
      {completedStages.length > 0 && (
        <button
          onClick={() => setExpandHistory((v) => !v)}
          className="flex items-center gap-1.5 text-[11px] text-emerald-600 hover:text-emerald-700 transition-colors"
        >
          <ChevronDown className={clsx("w-3 h-3 transition-transform", !expandHistory && "-rotate-90")} />
          <span>{completedStages.length} stage{completedStages.length > 1 ? "s" : ""} completed</span>
        </button>
      )}
      {expandHistory && completedStages.length > 0 && (
        <div className="space-y-0.5 pl-1">
          {completedStages.map((stage) => (
            <div key={stage.key} className="flex items-center gap-2 text-[11px] text-emerald-600">
              <Check className="w-3 h-3 flex-shrink-0" />
              <span>{stage.label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Current active stage */}
      {currentStage && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-accent-50 text-accent-700 text-sm font-medium">
          <Loader2 className="w-4 h-4 flex-shrink-0 animate-spin" />
          <span>{currentStage.label}</span>
        </div>
      )}

      {/* Stage detail message */}
      {stageMessage && (
        <div className="text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg px-3 py-2">
          {stageMessage}
        </div>
      )}

      {/* Source stats */}
      {(sourcesFound !== undefined && sourcesFound > 0) || (sourcesSelected !== undefined && sourcesSelected > 0) ? (
        <div className="text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg px-3 py-2">
          {sourcesFound !== undefined && sourcesFound > 0 && (
            <span>Found {sourcesFound} candidates</span>
          )}
          {sourcesFound !== undefined && sourcesFound > 0 && sourcesSelected !== undefined && sourcesSelected > 0 && (
            <span> &rarr; </span>
          )}
          {sourcesSelected !== undefined && sourcesSelected > 0 && (
            <span>Selected {sourcesSelected} for drafting</span>
          )}
        </div>
      ) : null}

      {/* Per-section progress */}
      {sectionsProgress.length > 0 && (
        <div className="space-y-1">
          {sectionsProgress.map((sec, i) => (
            <div
              key={i}
              className={clsx(
                "flex items-start gap-2 px-3 py-1.5 rounded-md text-xs transition-colors",
                sec.status === "done" && "text-emerald-600",
                sec.status === "drafting" && "text-accent-700 bg-accent-50 font-medium",
                sec.status === "skipped" && "text-surface-400",
                sec.status === "pending" && "text-surface-300",
              )}
            >
              {sec.status === "done" ? (
                <Check className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              ) : sec.status === "drafting" ? (
                <Loader2 className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 animate-spin" />
              ) : sec.status === "skipped" ? (
                <span className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-center">—</span>
              ) : (
                <Circle className="w-3 h-3 mt-0.5 flex-shrink-0 opacity-30" />
              )}
              <div className="flex-1 min-w-0">
                <span>{sec.title}</span>
                {sec.status === "done" && sec.preview && (
                  <p className="text-[11px] text-surface-400 mt-0.5 line-clamp-1 leading-relaxed">
                    {sec.preview}
                  </p>
                )}
                {sec.status === "skipped" && (
                  <span className="text-[11px] text-surface-400 ml-1">(has content)</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pending stages — subtle */}
      {pendingStages.length > 0 && sectionsProgress.length === 0 && (
        <div className="space-y-0.5 pl-1">
          {pendingStages.map((stage) => (
            <div key={stage.key} className="flex items-center gap-2 text-[11px] text-surface-300">
              <Circle className="w-3 h-3 flex-shrink-0 opacity-40" />
              <span>{stage.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

import type { ReactNode } from "react";
import { Check, Loader2, Circle, AlertCircle } from "lucide-react";
import clsx from "clsx";
import type { MacroStage, MacroStageStatus } from "@/store/deepResearchStore";

interface Props {
  stage: MacroStage;
  rightLabel?: string;
  children?: ReactNode;
}

const dotStyles: Record<MacroStageStatus, string> = {
  pending: "bg-surface-200 text-surface-400",
  in_progress: "bg-accent-100 text-accent-600",
  completed: "bg-emerald-100 text-emerald-600",
  failed: "bg-red-100 text-red-500",
};

const labelStyles: Record<MacroStageStatus, string> = {
  pending: "text-surface-400",
  in_progress: "text-accent-700 font-medium",
  completed: "text-surface-700",
  failed: "text-red-600",
};

function StatusIcon({ status }: { status: MacroStageStatus }) {
  switch (status) {
    case "completed":
      return <Check className="w-3.5 h-3.5" />;
    case "in_progress":
      return <Loader2 className="w-3.5 h-3.5 animate-spin" />;
    case "failed":
      return <AlertCircle className="w-3.5 h-3.5" />;
    default:
      return <Circle className="w-3.5 h-3.5 opacity-50" />;
  }
}

export function TimelineNode({ stage, rightLabel, children }: Props) {
  return (
    <div className="flex items-start gap-3">
      <div
        className={clsx(
          "flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center",
          dotStyles[stage.status],
        )}
        style={stage.status === "in_progress" ? { animation: "timeline-pulse 2s ease-in-out infinite" } : undefined}
      >
        <StatusIcon status={stage.status} />
      </div>
      <div className="flex-1 min-w-0 pt-0.5">
        <div className="flex items-center justify-between">
          <span className={clsx("text-sm", labelStyles[stage.status])}>
            {stage.label}
          </span>
          {rightLabel && (
            <span className="text-xs text-surface-400 tabular-nums">{rightLabel}</span>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

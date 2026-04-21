import { Fragment } from "react";
import type { MacroStage, SubQuestionProgress, SectionProgressV2 } from "@/store/deepResearchStore";
import { TimelineNode } from "./TimelineNode";
import { TimelineConnector } from "./TimelineConnector";
import { SubQuestionList } from "./SubQuestionList";
import { SectionProgressList } from "./SectionProgressList";
import { CurrentActivity } from "./CurrentActivity";

interface Props {
  macroStages: MacroStage[];
  subQuestions: SubQuestionProgress[];
  sectionsProgress: SectionProgressV2[];
  planSummary: string | null;
  currentActivity: string | null;
  generatedTitle: string | null;
}

function formatDuration(ms?: number): string {
  if (ms === undefined) return "";
  if (ms < 1000) return "<1s";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function VerticalTimeline({
  macroStages,
  subQuestions,
  sectionsProgress,
  planSummary,
  currentActivity,
}: Props) {
  const completedSQ = subQuestions.filter((sq) => sq.status === "completed").length;
  const totalSQ = subQuestions.length;
  const completedSec = sectionsProgress.filter((s) => s.status === "done").length;
  const totalSec = sectionsProgress.length;

  function getRightLabel(stage: MacroStage): string {
    if (stage.status === "completed" && stage.durationMs) {
      return formatDuration(stage.durationMs);
    }
    if (stage.key === "research" && stage.status === "in_progress" && totalSQ > 0) {
      return `${completedSQ} of ${totalSQ}`;
    }
    if ((stage.key === "write" || stage.key === "draft") && stage.status === "in_progress" && totalSec > 0) {
      return `${completedSec} of ${totalSec}`;
    }
    return "";
  }

  function getChildren(stage: MacroStage) {
    if (stage.key === "plan" && planSummary && (stage.status === "completed" || stage.status === "in_progress")) {
      return (
        <p className="text-xs text-surface-500 mt-1">{planSummary}</p>
      );
    }
    if (stage.key === "research" && subQuestions.length > 0 && stage.status !== "pending") {
      return <SubQuestionList subQuestions={subQuestions} />;
    }
    if ((stage.key === "write" || stage.key === "draft") && sectionsProgress.length > 0 && stage.status !== "pending") {
      return <SectionProgressList sections={sectionsProgress} />;
    }
    return null;
  }

  return (
    <div>
      <div className="relative">
        {macroStages.map((stage, i) => (
          <Fragment key={stage.key}>
            <TimelineNode
              stage={stage}
              rightLabel={getRightLabel(stage)}
            >
              {getChildren(stage)}
            </TimelineNode>
            {i < macroStages.length - 1 && (
              <TimelineConnector status={stage.status} />
            )}
          </Fragment>
        ))}
      </div>
      <CurrentActivity text={currentActivity} />

      <style>{`
        @keyframes timeline-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.35); }
          50% { box-shadow: 0 0 0 5px rgba(99, 102, 241, 0); }
        }
        @keyframes sq-slide-in {
          from { opacity: 0; transform: translateX(-6px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes activity-fade {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}

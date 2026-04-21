import { Check, Loader2, Circle, AlertCircle } from "lucide-react";
import clsx from "clsx";
import type { SubQuestionProgress } from "@/store/deepResearchStore";

interface Props {
  subQuestions: SubQuestionProgress[];
}

function SQIcon({ status }: { status: SubQuestionProgress["status"] }) {
  switch (status) {
    case "completed":
      return <Check className="w-3 h-3 text-emerald-500" />;
    case "in_progress":
      return <Loader2 className="w-3 h-3 text-accent-500 animate-spin" />;
    case "failed":
      return <AlertCircle className="w-3 h-3 text-orange-500" />;
    default:
      return <Circle className="w-3 h-3 text-surface-300" />;
  }
}

export function SubQuestionList({ subQuestions }: Props) {
  const firstSupp = subQuestions.findIndex((sq) => sq.isSupplementary);

  return (
    <ul className="mt-2 space-y-1">
      {subQuestions.map((sq, i) => (
        <li key={sq.id}>
          {i === firstSupp && firstSupp > 0 && (
            <div className="flex items-center gap-2 py-1 my-0.5">
              <div className="flex-1 border-t border-dashed border-surface-200" />
              <span className="text-[10px] text-surface-400 shrink-0">supplementary</span>
              <div className="flex-1 border-t border-dashed border-surface-200" />
            </div>
          )}
          <div
            className={clsx(
              "flex items-start gap-2 text-xs leading-relaxed",
              sq.status === "pending" && "text-surface-400",
              sq.status === "in_progress" && "text-accent-700",
              sq.status === "completed" && "text-surface-600",
              sq.status === "failed" && "text-orange-600",
            )}
            style={
              sq.status !== "pending"
                ? { animation: "sq-slide-in 0.25s ease-out both", animationDelay: `${i * 30}ms` }
                : undefined
            }
          >
            <span className="mt-0.5 flex-shrink-0">
              <SQIcon status={sq.status} />
            </span>
            <span className="flex-1 min-w-0 break-words">{sq.question}</span>
            {sq.durationMs !== undefined && sq.status === "completed" && (
              <span className="text-surface-400 tabular-nums shrink-0">
                {sq.durationMs < 1000 ? "<1s" : `${Math.round(sq.durationMs / 1000)}s`}
              </span>
            )}
            {sq.status === "failed" && (
              <span className="text-orange-500 text-[11px] shrink-0">failed</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

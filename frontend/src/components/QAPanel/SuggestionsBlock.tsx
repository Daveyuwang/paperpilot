import clsx from "clsx";
import { ArrowRight } from "lucide-react";
import type { SuggestedQuestion } from "@/types";

const STAGE_DOT: Record<string, string> = {
  motivation:  "bg-rose-400",
  approach:    "bg-amber-400",
  experiments: "bg-emerald-400",
  takeaways:   "bg-accent-400",
};

const STAGE_LABEL: Record<string, string> = {
  motivation: "Motivation",
  approach: "Approach",
  experiments: "Experiments",
  takeaways: "Takeaways",
};

export function SuggestionsBlock({
  suggestions,
  onAsk,
}: {
  suggestions: SuggestedQuestion[];
  onAsk: (q: string, id: string) => void;
}) {
  const primary = suggestions.find((s) => s.is_primary);
  const secondary = suggestions.filter((s) => !s.is_primary);

  return (
    <div className="pl-11 space-y-2">
      {primary && (
        <button
          className="w-full text-left px-4 py-3 rounded-xl border border-accent-200 bg-accent-50 hover:bg-accent-100 flex items-start gap-3 group transition-colors"
          onClick={() => onAsk(primary.question, primary.id)}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-1">
              <span className={clsx("w-1.5 h-1.5 rounded-full flex-shrink-0", STAGE_DOT[primary.stage] ?? "bg-surface-400")} />
              <span className="text-[10px] font-semibold text-surface-500 uppercase tracking-wider">
                Up next · {STAGE_LABEL[primary.stage] ?? primary.stage}
              </span>
            </div>
            <p className="text-sm text-surface-700 leading-snug">{primary.question}</p>
          </div>
          <ArrowRight className="w-4 h-4 flex-shrink-0 mt-0.5 text-accent-400 group-hover:text-accent-600 transition-colors" />
        </button>
      )}

      {secondary.length > 0 && (
        <div className="space-y-1.5">
          {secondary.map((q) => (
            <button
              key={q.id}
              className="w-full text-left px-3 py-2.5 rounded-lg border border-surface-200 bg-surface-50 hover:bg-surface-100 transition-colors"
              onClick={() => onAsk(q.question, q.id)}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className={clsx("w-1 h-1 rounded-full flex-shrink-0", STAGE_DOT[q.stage] ?? "bg-surface-400")} />
                <span className="text-[10px] text-surface-500 uppercase tracking-wide">
                  {STAGE_LABEL[q.stage] ?? q.stage}
                </span>
              </div>
              <p className="text-xs text-surface-600 leading-snug line-clamp-2">{q.question}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

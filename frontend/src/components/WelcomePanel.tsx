import clsx from "clsx";
import { ChevronRight, BookOpen } from "lucide-react";
import type { Paper, GuideQuestion } from "@/types";

const STAGE_COLOR: Record<string, string> = {
  motivation:  "text-rose-400   border-rose-500/30   bg-rose-500/5",
  approach:    "text-amber-400  border-amber-500/30  bg-amber-500/5",
  experiments: "text-emerald-400 border-emerald-500/30 bg-emerald-500/5",
  takeaways:   "text-accent-400 border-accent-500/30 bg-accent-500/5",
};

interface Props {
  paper: Paper;
  questions: GuideQuestion[];
  onAsk: (question: string, questionId: string) => void;
}

/**
 * First screen shown after a paper is ready and no questions have been asked.
 * Shows: title, one-line abstract summary, and the first guided question per stage.
 */
export function WelcomePanel({ paper, questions, onAsk }: Props) {
  // Pick the first question from each stage as entry points
  const stageOrder = ["motivation", "approach", "experiments", "takeaways"];
  const entryPoints = stageOrder
    .map((s) => questions.find((q) => q.stage === s))
    .filter(Boolean) as GuideQuestion[];

  // Two-sentence abstract preview
  const abstractPreview = paper.abstract
    ? paper.abstract.split(/\.\s+/).slice(0, 2).join(". ") + "."
    : null;

  return (
    <div className="flex flex-col h-full overflow-y-auto p-5 space-y-5">
      {/* Paper header */}
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-accent-600/20 flex items-center justify-center flex-shrink-0 mt-0.5">
          <BookOpen className="w-4.5 h-4.5 text-accent-400" />
        </div>
        <div>
          <h2 className="text-base font-semibold text-gray-100 leading-snug">
            {paper.title ?? paper.filename}
          </h2>
          {abstractPreview && (
            <p className="text-xs text-gray-400 mt-1.5 leading-relaxed line-clamp-3">
              {abstractPreview}
            </p>
          )}
        </div>
      </div>

      {/* Divider + prompt */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
          Start with a guided question
        </p>
        <div className="space-y-2">
          {entryPoints.map((q) => {
            const colorClass = STAGE_COLOR[q.stage] ?? STAGE_COLOR.motivation;
            return (
              <button
                key={q.id}
                className={clsx(
                  "w-full text-left px-4 py-3 rounded-xl border flex items-start gap-3",
                  "transition-all hover:scale-[1.01] active:scale-[0.99]",
                  colorClass
                )}
                onClick={() => onAsk(q.question, q.id)}
              >
                <div className="flex-1 min-w-0">
                  <span className="text-[10px] font-semibold uppercase tracking-wider opacity-70 block mb-0.5">
                    {q.stage}
                  </span>
                  <span className="text-sm text-gray-200 leading-snug">{q.question}</span>
                </div>
                <ChevronRight className="w-4 h-4 flex-shrink-0 mt-0.5 opacity-50" />
              </button>
            );
          })}
        </div>
      </div>

      {/* Free-form nudge */}
      <p className="text-xs text-gray-600 text-center pt-1">
        Or type any question in the box below
      </p>
    </div>
  );
}

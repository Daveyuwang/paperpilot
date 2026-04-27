import React from "react";
import clsx from "clsx";
import { CheckCircle2, Circle, Loader2, BookOpen } from "lucide-react";
import { usePaperStore } from "@/store/paperStore";
import { useChatStore } from "@/store/chatStore";
import type { GuideQuestion } from "@/types";

const STAGE_META: Record<string, {
  label: string;
  description: string;
  color: string;
  dimColor: string;
  activeBg: string;
  bg: string;
  border: string;
  activeBorder: string;
  barColor: string;
}> = {
  motivation: {
    label: "Motivation",    description: "Why this paper exists",
    color: "text-rose-700",    dimColor: "text-rose-400",
    activeBg: "bg-rose-50", bg: "bg-rose-50/50",
    border: "border-rose-200", activeBorder: "border-rose-300",
    barColor: "bg-rose-500",
  },
  approach: {
    label: "Approach",      description: "How it was done",
    color: "text-amber-700",   dimColor: "text-amber-400",
    activeBg: "bg-amber-50", bg: "bg-amber-50/50",
    border: "border-amber-200", activeBorder: "border-amber-300",
    barColor: "bg-amber-500",
  },
  experiments: {
    label: "Experiments",   description: "What was tested & found",
    color: "text-emerald-700", dimColor: "text-emerald-400",
    activeBg: "bg-emerald-50", bg: "bg-emerald-50/50",
    border: "border-emerald-200", activeBorder: "border-emerald-300",
    barColor: "bg-emerald-500",
  },
  takeaways: {
    label: "Takeaways",     description: "Implications & conclusions",
    color: "text-accent-700",  dimColor: "text-accent-400",
    activeBg: "bg-accent-50", bg: "bg-accent-50/50",
    border: "border-accent-200", activeBorder: "border-accent-300",
    barColor: "bg-accent-500",
  },
};

const STAGE_ORDER = ["motivation", "approach", "experiments", "takeaways"];

interface Props {
  onAsk?: (q: { id: string; question: string }) => void;
}

export function TrailTracker({ onAsk }: Props) {
  const { questions } = usePaperStore();
  const coveredQuestionIds = useChatStore((s) => s.coveredQuestionIds);
  const activeQuestionId   = useChatStore((s) => s.activeQuestionId);
  const isGenerating       = useChatStore((s) => s.isGenerating);

  if (questions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-32 gap-2 text-surface-400">
        <BookOpen className="w-6 h-6 opacity-40" />
        <p className="text-xs">No guide questions yet</p>
      </div>
    );
  }

  const grouped = STAGE_ORDER.reduce<Record<string, GuideQuestion[]>>((acc, stage) => {
    acc[stage] = questions.filter((q) => q.stage === stage);
    return acc;
  }, {});

  const totalCovered = questions.filter((q) => coveredQuestionIds.includes(q.id)).length;
  const progressPct  = Math.round((totalCovered / questions.length) * 100);

  const handleAsk = (q: GuideQuestion) => {
    console.debug("[PaperPilot] trail_click", { questionId: q.id, stage: q.stage });
    if (onAsk) {
      onAsk({ id: q.id, question: q.question });
    } else {
      (window as Window & { __askGuideQuestion?: (q: { id: string; question: string }) => void }).__askGuideQuestion?.({ id: q.id, question: q.question });
    }
  };

  return (
    <div className="space-y-5 py-1">
      {/* Overall progress */}
      <div className="px-1">
        <div className="flex justify-between items-baseline mb-2">
          <span className="text-xs font-semibold text-surface-500">Reading map</span>
          <span className="text-xs text-surface-400">{totalCovered} / {questions.length} explored</span>
        </div>
        <div className="h-1 bg-surface-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-accent-500 to-accent-400 rounded-full transition-all duration-700"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Stages */}
      {STAGE_ORDER.map((stage) => {
        const qs = grouped[stage];
        if (!qs?.length) return null;
        const meta = STAGE_META[stage];
        const stageCovered = qs.filter((q) => coveredQuestionIds.includes(q.id)).length;
        const stageHasActive = qs.some((q) => q.id === activeQuestionId);

        return (
          <div
            key={stage}
            className={clsx(
              "rounded-xl border overflow-hidden transition-colors",
              stageHasActive ? meta.activeBorder : meta.border,
              stageHasActive ? meta.activeBg : meta.bg,
            )}
          >
            {/* Stage header */}
            <div className="px-3 py-2.5 flex items-center gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={clsx("text-xs font-semibold", meta.color)}>{meta.label}</span>
                  {stageCovered === qs.length && (
                    <span className={clsx("text-[10px] font-medium px-1.5 py-0.5 rounded", meta.bg, meta.color, "border", meta.border)}>
                      Done
                    </span>
                  )}
                </div>
                <p className={clsx("text-[10px] mt-0.5", meta.dimColor)}>{meta.description}</p>
              </div>
              <span className="text-[10px] text-surface-400 flex-shrink-0">{stageCovered}/{qs.length}</span>
            </div>

            {/* Per-stage mini progress bar */}
            {stageCovered > 0 && (
              <div className="h-0.5 bg-surface-200">
                <div
                  className={clsx("h-full transition-all duration-500", meta.barColor)}
                  style={{ width: `${(stageCovered / qs.length) * 100}%` }}
                />
              </div>
            )}

            {/* Questions */}
            <div className="divide-y divide-surface-200">
              {qs.map((q) => {
                const done   = coveredQuestionIds.includes(q.id);
                const active = isGenerating && q.id === activeQuestionId;
                return (
                  <QuestionRow
                    key={q.id}
                    question={q}
                    done={done}
                    active={active}
                    meta={meta}
                    onAsk={handleAsk}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Question row ──────────────────────────────────────────────────────────

function QuestionRow({
  question,
  done,
  active,
  meta,
  onAsk,
}: {
  question: GuideQuestion;
  done: boolean;
  active: boolean;
  meta: typeof STAGE_META[string];
  onAsk: (q: GuideQuestion) => void;
}) {
  // Active = currently generating answer
  if (active) {
    return (
      <div className={clsx("flex items-start gap-2.5 px-3 py-2.5 text-xs", meta.activeBg)}>
        <Loader2 className={clsx("w-3.5 h-3.5 flex-shrink-0 mt-0.5 animate-spin", meta.color)} />
        <span className={clsx("flex-1 leading-snug font-medium", meta.color)}>
          {question.question}
        </span>
      </div>
    );
  }

  // Done = answered; weakened but readable
  if (done) {
    return (
      <button
        className="w-full text-left flex items-start gap-2.5 px-3 py-2.5 text-xs hover:bg-surface-100 transition-colors cursor-pointer"
        onClick={() => onAsk(question)}
        title="Ask again"
      >
        <CheckCircle2 className={clsx("w-3.5 h-3.5 flex-shrink-0 mt-0.5", meta.color, "opacity-50")} />
        <span className="flex-1 leading-snug text-surface-400 line-through decoration-surface-300">
          {question.question}
        </span>
      </button>
    );
  }

  // Default = not yet answered
  return (
    <button
      className="w-full text-left flex items-start gap-2.5 px-3 py-2.5 text-xs hover:bg-surface-100 transition-colors group"
      onClick={() => onAsk(question)}
      title="Ask this question"
    >
      <Circle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-surface-300 group-hover:text-surface-500 transition-colors" />
      <span className="flex-1 leading-snug text-surface-600 group-hover:text-surface-800 transition-colors">
        {question.question}
      </span>
    </button>
  );
}

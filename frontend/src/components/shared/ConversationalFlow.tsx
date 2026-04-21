import { useState } from "react";
import { Sparkles, ChevronRight, Check, RotateCcw, Loader2, ListChecks, Lightbulb } from "lucide-react";

// ── Types ────────────────────────────────────────────────────────────────────

export interface PlanSubQuestion {
  id: string;
  question: string;
  rationale: string;
  searchQueries: string[];
  priority: number;
}

export interface PlanOutlineSection {
  title: string;
  description: string;
}

export interface GeneratedPlan {
  type: "deep_research" | "proposal_plan";
  subQuestions?: PlanSubQuestion[];
  outlineSections?: PlanOutlineSection[];
  overallApproach: string;
  recommendedDepth: string;
  sourcesStrategy: string;
  focusNote: string | null;
}

interface Props {
  topic: string;
  plan: GeneratedPlan | null;
  isGenerating: boolean;
  onGeneratePlan: () => void;
  onConfirmPlan: (plan: GeneratedPlan) => void;
  onCancel: () => void;
}

// ── Component ────────────────────────────────────────────────────────────────

export function ConversationalFlow({ topic, plan, isGenerating, onGeneratePlan, onConfirmPlan, onCancel }: Props) {
  const [expandedQ, setExpandedQ] = useState<string | null>(null);

  if (isGenerating) {
    return <GeneratingState topic={topic} />;
  }

  if (plan) {
    return (
      <PlanReview
        plan={plan}
        expandedQ={expandedQ}
        onToggleQ={setExpandedQ}
        onConfirm={() => onConfirmPlan(plan)}
        onCancel={onCancel}
      />
    );
  }

  return <PlanPrompt topic={topic} onGenerate={onGeneratePlan} onSkip={onCancel} />;
}

// ── Plan prompt (pre-generation) ─────────────────────────────────────────────

function PlanPrompt({ topic, onGenerate, onSkip }: { topic: string; onGenerate: () => void; onSkip: () => void }) {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 px-4 py-3 rounded-lg bg-accent-50 border border-accent-200">
        <Sparkles className="w-4 h-4 text-accent-600 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-accent-800">Generate a research plan?</p>
          <p className="text-xs text-accent-600 mt-1">
            I'll analyze your topic and create a structured plan with sub-questions and search strategy before running.
          </p>
        </div>
      </div>

      <div className="px-4 py-2 rounded bg-surface-50 border border-surface-200">
        <p className="text-xs text-surface-500">Topic</p>
        <p className="text-sm text-surface-700 mt-0.5">{topic}</p>
      </div>

      <div className="flex items-center gap-2">
        <button onClick={onGenerate} className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs">
          <Sparkles className="w-3.5 h-3.5" />
          Generate Plan
        </button>
        <button onClick={onSkip} className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs">
          <ChevronRight className="w-3.5 h-3.5" />
          Skip & Run Directly
        </button>
      </div>
    </div>
  );
}

// ── Generating state (loading) ───────────────────────────────────────────────

function GeneratingState({ topic }: { topic: string }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-surface-50 border border-surface-200">
        <Loader2 className="w-4 h-4 text-accent-600 animate-spin flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-surface-700">Generating research plan...</p>
          <p className="text-xs text-surface-500 mt-0.5">Analyzing topic and identifying sub-questions</p>
        </div>
      </div>
      <div className="px-4 py-2 rounded bg-surface-50 border border-surface-100">
        <p className="text-xs text-surface-400 truncate">{topic}</p>
      </div>
    </div>
  );
}

// ── Plan review ──────────────────────────────────────────────────────────────

function PlanReview({
  plan, expandedQ, onToggleQ, onConfirm, onCancel,
}: {
  plan: GeneratedPlan;
  expandedQ: string | null;
  onToggleQ: (id: string | null) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-4">
      {/* Approach summary */}
      <div className="px-4 py-3 rounded-lg bg-surface-50 border border-surface-200 space-y-2">
        <div className="flex items-center gap-2">
          <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
          <span className="text-xs font-medium text-surface-700">Approach</span>
        </div>
        <p className="text-xs text-surface-600">{plan.overallApproach}</p>
        <div className="flex gap-3 text-[11px] text-surface-500">
          <span>Depth: {plan.recommendedDepth}</span>
          <span>Sources: {plan.sourcesStrategy}</span>
        </div>
        {plan.focusNote && (
          <p className="text-[11px] text-accent-600 italic">{plan.focusNote}</p>
        )}
      </div>

      {/* Sub-questions (DR) */}
      {plan.subQuestions && plan.subQuestions.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 px-1">
            <ListChecks className="w-3.5 h-3.5 text-surface-500" />
            <span className="text-xs font-medium text-surface-700">
              Research questions ({plan.subQuestions.length})
            </span>
          </div>
          <div className="space-y-1">
            {plan.subQuestions.map((q, i) => (
              <SubQuestionCard
                key={q.id}
                question={q}
                index={i}
                isExpanded={expandedQ === q.id}
                onToggle={() => onToggleQ(expandedQ === q.id ? null : q.id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Outline sections (PP) */}
      {plan.outlineSections && plan.outlineSections.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 px-1">
            <ListChecks className="w-3.5 h-3.5 text-surface-500" />
            <span className="text-xs font-medium text-surface-700">
              Outline ({plan.outlineSections.length} sections)
            </span>
          </div>
          <div className="space-y-1">
            {plan.outlineSections.map((sec, i) => (
              <div key={i} className="px-3 py-2 rounded border border-surface-200 bg-white">
                <p className="text-xs font-medium text-surface-700">{sec.title}</p>
                <p className="text-[11px] text-surface-500 mt-0.5">{sec.description}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        <button onClick={onConfirm} className="btn-primary flex items-center gap-1.5 px-4 py-2 text-xs">
          <Check className="w-3.5 h-3.5" />
          Looks Good — Run
        </button>
        <button onClick={onCancel} className="btn-ghost flex items-center gap-1.5 px-3 py-2 text-xs">
          <RotateCcw className="w-3.5 h-3.5" />
          Start Over
        </button>
      </div>
    </div>
  );
}

// ── Sub-question card ────────────────────────────────────────────────────────

function SubQuestionCard({
  question, index, isExpanded, onToggle,
}: {
  question: PlanSubQuestion;
  index: number;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className="px-3 py-2 rounded border border-surface-200 bg-white cursor-pointer hover:border-accent-200 transition-colors"
      onClick={onToggle}
    >
      <div className="flex items-start gap-2">
        <span className="text-[10px] font-mono text-surface-400 mt-0.5 shrink-0">
          {index + 1}.
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-xs text-surface-700">{question.question}</p>
          {isExpanded && (
            <div className="mt-2 space-y-1.5 text-[11px] text-surface-500">
              <p><span className="font-medium text-surface-600">Why:</span> {question.rationale}</p>
              {question.searchQueries.length > 0 && (
                <div>
                  <span className="font-medium text-surface-600">Searches:</span>
                  <ul className="mt-0.5 space-y-0.5 pl-3">
                    {question.searchQueries.map((q, i) => (
                      <li key={i} className="text-surface-500">"{q}"</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
        <span className="text-[10px] text-surface-400 shrink-0">P{question.priority}</span>
      </div>
    </div>
  );
}

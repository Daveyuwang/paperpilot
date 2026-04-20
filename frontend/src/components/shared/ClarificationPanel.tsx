import { HelpCircle } from "lucide-react";

interface Question {
  question: string;
  suggestion?: string | null;
  field?: string;
}

interface Props {
  questions: Question[];
  onRetry: () => void;
  onReset?: () => void;
}

export function ClarificationPanel({ questions, onRetry, onReset }: Props) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-amber-700">
        <HelpCircle className="w-4 h-4" />
        <span className="text-sm font-medium">Clarification needed</span>
      </div>
      <div className="space-y-3">
        {questions.map((q, i) => (
          <div key={i} className="border border-amber-200 rounded-lg bg-amber-50/50 px-4 py-3">
            <p className="text-sm text-surface-700">{q.question}</p>
            {q.suggestion && (
              <p className="text-xs text-surface-400 mt-1">{q.suggestion}</p>
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-accent-700 bg-accent-50 border border-accent-200 rounded-md hover:bg-accent-100 transition-colors"
        >
          Update and retry
        </button>
        {onReset && (
          <button
            onClick={onReset}
            className="text-xs text-surface-500 hover:text-surface-700 transition-colors"
          >
            Start over
          </button>
        )}
      </div>
    </div>
  );
}

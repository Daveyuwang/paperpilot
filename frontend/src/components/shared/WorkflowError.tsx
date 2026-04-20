import { AlertCircle, RotateCcw, Play } from "lucide-react";

interface Props {
  message: string | null;
  title?: string;
  onReset: () => void;
  onRetry?: () => void;
}

export function WorkflowError({ message, title = "Something went wrong", onReset, onRetry }: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-red-600">
        <AlertCircle className="w-4 h-4" />
        <span className="text-sm font-medium">{title}</span>
      </div>
      {message && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {message}
        </p>
      )}
      <div className="flex items-center gap-2">
        {onRetry && (
          <button
            onClick={onRetry}
            className="btn-primary flex items-center gap-1.5 px-3 py-1.5 text-xs"
          >
            <Play className="w-3 h-3" />
            Retry
          </button>
        )}
        <button
          onClick={onReset}
          className="btn-ghost flex items-center gap-1.5 px-3 py-1.5 text-xs"
        >
          <RotateCcw className="w-3 h-3" />
          Start over
        </button>
      </div>
    </div>
  );
}

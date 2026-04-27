import { AlertCircle, RefreshCw } from "lucide-react";

interface Props {
  error: string | null;
  onRetry?: () => void;
  children: React.ReactNode;
}

export function StreamingErrorBoundary({ error, onRetry, children }: Props) {
  if (!error) return <>{children}</>;

  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-4 flex flex-col items-center gap-3">
      <div className="flex items-center gap-2 text-red-600">
        <AlertCircle className="w-4 h-4" />
        <span className="text-sm font-medium">Stream Error</span>
      </div>
      <p className="text-xs text-red-500 text-center max-w-sm">{error}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-100 hover:bg-red-200 text-red-700 text-xs font-medium transition-colors"
        >
          <RefreshCw className="w-3 h-3" />
          Retry
        </button>
      )}
    </div>
  );
}

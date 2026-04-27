import clsx from "clsx";
import { Loader2, AlertCircle, X } from "lucide-react";

export function RunStatusBar({ status, message, onDismiss }: { status: string; message: string | null; onDismiss: () => void }) {
  if (status === "idle") return null;

  const isActive = status === "preparing" || status === "generating";
  const isError = status === "failed" || status === "blocked";

  return (
    <div
      className={clsx(
        "flex-shrink-0 flex items-center gap-2 px-4 py-1.5 text-xs border-b",
        isActive && "bg-accent-50 border-accent-200 text-accent-700",
        status === "awaiting_apply" && "bg-amber-50 border-amber-200 text-amber-700",
        status === "completed" && "bg-emerald-50 border-emerald-200 text-emerald-700",
        isError && "bg-red-50 border-red-200 text-red-700",
      )}
    >
      {isActive && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
      {isError && <AlertCircle className="w-3 h-3 shrink-0" />}
      <span className="truncate flex-1">
        {status === "preparing" && "Preparing draft..."}
        {status === "generating" && "Generating content..."}
        {status === "awaiting_apply" && "Review generated content below. Apply or discard each section."}
        {status === "completed" && (message ?? "Done.")}
        {isError && (message ?? "Something went wrong.")}
      </span>
      {!isActive && (
        <button onClick={onDismiss} className="p-0.5 rounded hover:bg-black/5 shrink-0">
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

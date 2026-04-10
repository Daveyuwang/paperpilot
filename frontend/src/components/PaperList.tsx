import React, { useEffect } from "react";
import { FileText, Loader2, CheckCircle, AlertCircle, Clock, Trash2 } from "lucide-react";
import clsx from "clsx";
import { usePaperStore } from "@/store/paperStore";
import type { PaperStatus } from "@/types";

function StatusBadge({ status }: { status: PaperStatus }) {
  const cfg: Record<PaperStatus, { label: string; icon: React.ReactNode; color: string }> = {
    pending:    { label: "Pending",    icon: <Clock className="w-3 h-3" />,       color: "text-gray-400" },
    processing: { label: "Processing", icon: <Loader2 className="w-3 h-3 animate-spin" />, color: "text-accent-400" },
    ready:      { label: "Ready",      icon: <CheckCircle className="w-3 h-3" />, color: "text-emerald-400" },
    error:      { label: "Error",      icon: <AlertCircle className="w-3 h-3" />, color: "text-red-400" },
  };
  const { label, icon, color } = cfg[status];
  return (
    <span className={clsx("flex items-center gap-1 text-xs font-medium", color)}>
      {icon}{label}
    </span>
  );
}

interface Props {
  onSelect?: (id: string) => void;
}

export function PaperList({ onSelect }: Props) {
  const { papers, activePaper, isLoading, loadPapers, selectPaper, deletePaper } = usePaperStore();
  const handleSelect = onSelect ?? selectPaper;
  // Debounce: prevent rapid double-clicks while paper/session is being loaded
  const isSwitching = isLoading && !!activePaper;

  useEffect(() => {
    loadPapers();
  }, [loadPapers]);

  if (isLoading && papers.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading…
      </div>
    );
  }

  if (papers.length === 0) {
    return (
      <p className="text-center text-sm text-gray-500 py-6">No papers yet.</p>
    );
  }

  return (
    <ul className="space-y-1">
      {papers.map((paper) => (
        <li key={paper.id}>
          <div
            className={clsx(
              "w-full px-3 py-2.5 rounded-lg flex items-start gap-2.5 transition-colors group",
              activePaper?.id === paper.id
                ? "bg-accent-600/20 border border-accent-600/30"
                : "hover:bg-white/5"
            )}
          >
            <button
              className="flex flex-1 min-w-0 items-start gap-2.5 text-left disabled:cursor-default"
              onClick={() => paper.status === "ready" && !isSwitching && handleSelect(paper.id)}
              disabled={paper.status !== "ready" || isSwitching}
            >
              <FileText className="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-500" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-200 truncate">
                  {paper.title ?? paper.filename}
                </p>
                <StatusBadge status={paper.status} />
              </div>
            </button>
            <button
              type="button"
              className="opacity-0 group-hover:opacity-100 p-1 text-gray-500 hover:text-red-400 transition rounded"
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm("Delete this paper and its chat session?")) {
                  console.debug("[PaperPilot] delete_confirm", { paperId: paper.id });
                  deletePaper(paper.id);
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && window.confirm("Delete this paper and its chat session?")) {
                  deletePaper(paper.id);
                }
              }}
              title="Delete paper"
              aria-label="Delete paper"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

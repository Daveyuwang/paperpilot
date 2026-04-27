import React, { useEffect, useState } from "react";
import { FileText, Loader2, CheckCircle, AlertCircle, Clock, Trash2 } from "lucide-react";
import clsx from "clsx";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useIngestionProgress, stageLabel } from "@/hooks/useIngestionProgress";
import type { PaperStatus } from "@/types";

function ProgressBadge({ paperId }: { paperId: string }) {
  const { stage, progress } = useIngestionProgress(paperId, true);
  const label = stageLabel(stage);
  const pct = progress ?? 0;

  return (
    <div className="flex flex-col gap-0.5 mt-0.5">
      <span className="flex items-center gap-1 text-[10px] text-accent-500">
        <Loader2 className="w-2.5 h-2.5 animate-spin" />
        {label}{progress != null ? ` ${pct}%` : ""}
      </span>
      {progress != null && (
        <div className="w-full h-1 bg-surface-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent-400 rounded-full transition-all duration-500"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, paperId }: { status: PaperStatus; paperId: string }) {
  if (status === "processing") {
    return <ProgressBadge paperId={paperId} />;
  }
  const cfg: Record<Exclude<PaperStatus, "processing">, { label: string; icon: React.ReactNode; color: string }> = {
    pending:    { label: "Pending",    icon: <Clock className="w-2.5 h-2.5" />,       color: "text-surface-400" },
    ready:      { label: "Ready",      icon: <CheckCircle className="w-2.5 h-2.5" />, color: "text-emerald-600" },
    error:      { label: "Error",      icon: <AlertCircle className="w-2.5 h-2.5" />, color: "text-red-500" },
  };
  const { label, icon, color } = cfg[status];
  return (
    <span className={clsx("flex items-center gap-1 text-[10px]", color)}>
      {icon}{label}
    </span>
  );
}

interface Props {
  onSelect?: (id: string) => void;
}

export function PaperList({ onSelect }: Props) {
  const { papers, activePaper, isLoading, selectPaper, deselectPaper, deletePaper } = usePaperStore();
  const { selectedNav } = useWorkspaceStore();
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const handleSelect = onSelect ?? selectPaper;
  const isSwitching = isLoading && !!activePaper;
  const showActive = selectedNav === "reader";

  useEffect(() => {
    if (!confirmingDelete) return;
    const t = setTimeout(() => setConfirmingDelete(null), 3000);
    return () => clearTimeout(t);
  }, [confirmingDelete]);

  if (isLoading && papers.length === 0) {
    return (
      <div className="flex items-center justify-center py-6 text-surface-500 text-xs">
        <Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading…
      </div>
    );
  }

  if (papers.length === 0) {
    return (
      <p className="text-center text-xs text-surface-400 py-4">No papers uploaded yet.</p>
    );
  }

  return (
    <ul className="space-y-0.5">
      {papers.map((paper) => (
        <li key={paper.id}>
          <div
            className={clsx(
              "w-full px-2.5 py-2 rounded-lg flex items-start gap-2 transition-colors group",
              showActive && activePaper?.id === paper.id
                ? "bg-accent-50 border border-accent-200"
                : "hover:bg-surface-100"
            )}
          >
            <button
              className="flex flex-1 min-w-0 items-start gap-2 text-left disabled:cursor-default"
              onClick={() => {
                if (paper.status !== "ready" || isSwitching) return;
                if (showActive && activePaper?.id === paper.id) {
                  deselectPaper();
                } else {
                  handleSelect(paper.id);
                }
              }}
              disabled={paper.status !== "ready" || isSwitching}
            >
              <FileText className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-surface-400" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-surface-700 truncate leading-snug">
                  {paper.title ?? paper.filename}
                </p>
                <StatusBadge status={paper.status} paperId={paper.id} />
              </div>
            </button>
            {confirmingDelete === paper.id ? (
              <button
                type="button"
                className="text-[10px] text-red-500 hover:text-red-700 font-medium px-1.5 py-0.5 rounded bg-red-50 transition-colors flex-shrink-0"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmingDelete(null);
                  deletePaper(paper.id);
                }}
              >
                Delete?
              </button>
            ) : (
              <button
                type="button"
                className="opacity-0 group-hover:opacity-100 p-1 text-surface-400 hover:text-red-500 transition rounded flex-shrink-0"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmingDelete(paper.id);
                }}
                title="Delete paper"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

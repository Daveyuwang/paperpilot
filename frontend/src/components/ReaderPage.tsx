import { FileText, MessageSquare, ArrowUpRight } from "lucide-react";
import type { Citation } from "@/types";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { ViewerLane } from "./ViewerLane";
import { QAPanel } from "./QAPanel";

type QueuedQuestion = { id?: string; question: string; nonce: number } | null;

interface Props {
  highlightBboxes: NonNullable<Citation["bbox"]>[];
  targetPage: number | undefined;
  jumpCounter: number;
  onExplainConcept: (label: string) => void;
  onShowInPaper: (page: number) => void;
  onTrailAsk: (q: { id: string; question: string }) => void;
  onHighlight: (citations: Citation[]) => void;
  queuedQuestion?: QueuedQuestion;
  onQueuedQuestionHandled?: (nonce: number) => void;
}

export function ReaderPage({
  highlightBboxes,
  targetPage,
  jumpCounter,
  onExplainConcept,
  onShowInPaper,
  onTrailAsk,
  onHighlight,
  queuedQuestion,
  onQueuedQuestionHandled,
}: Props) {
  const { activePaper } = usePaperStore();
  const { setSelectedNav } = useWorkspaceStore();

  if (!activePaper) {
    return (
      <div className="flex items-center justify-center h-full bg-white">
        <div className="text-center">
          <FileText className="w-10 h-10 text-surface-300 mx-auto mb-3" />
          <p className="text-sm font-medium text-surface-600">No paper selected</p>
          <p className="text-xs text-surface-400 mt-1">Select a paper from the source rail to start reading</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 bg-white">
      {/* Left: Paper QA chat */}
      <div className="flex flex-col flex-[6] min-w-[400px] max-w-[600px] border-r border-surface-200">
        {/* Identity header */}
        <div className="flex-shrink-0 px-4 py-2 border-b border-surface-200 bg-surface-50 flex items-center gap-2">
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-accent-600 bg-accent-50 border border-accent-200 px-1.5 py-0.5 rounded uppercase tracking-wider">
            <MessageSquare className="w-2.5 h-2.5" />
            Paper QA
          </span>
          <span className="text-xs text-surface-500 truncate flex-1">{activePaper.title ?? activePaper.filename}</span>
          <button
            onClick={() => setSelectedNav("console")}
            className="inline-flex items-center gap-1 text-[10px] text-surface-400 hover:text-accent-600 transition-colors flex-shrink-0"
            title="Switch to workspace console (keeps paper context)"
          >
            <ArrowUpRight className="w-2.5 h-2.5" />
            Console
          </button>
        </div>
        <div className="flex-1 min-h-0">
          <QAPanel
            onHighlight={onHighlight}
            queuedQuestion={queuedQuestion}
            onQueuedQuestionHandled={onQueuedQuestionHandled}
          />
        </div>
      </div>

      {/* Right: Viewer tabs */}
      <div className="flex-[7] min-w-0">
        <ViewerLane
          highlightBboxes={highlightBboxes}
          targetPage={targetPage}
          jumpCounter={jumpCounter}
          onExplainConcept={onExplainConcept}
          onShowInPaper={onShowInPaper}
          onTrailAsk={onTrailAsk}
        />
      </div>
    </div>
  );
}

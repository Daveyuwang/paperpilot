import { ArrowUpRight, MessageSquare } from "lucide-react";
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
  const { setSelectedNav, getActiveWorkspace } = useWorkspaceStore();
  const activeViewerTab = getActiveWorkspace()?.activeViewerTab;

  return (
    <div className="flex h-full min-w-0 flex-col overflow-y-auto bg-white 2xl:flex-row 2xl:overflow-hidden">
      {activePaper && activeViewerTab === "reader" && (
        <section className="flex min-h-[55vh] min-w-0 flex-col border-b border-surface-200 2xl:min-h-0 2xl:flex-[6] 2xl:border-b-0 2xl:border-r" aria-label="Paper chat">
          <div className="flex flex-shrink-0 items-center gap-2 border-b border-surface-200 bg-surface-50 px-4 py-2.5">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-accent-700">
              <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
              This paper
            </span>
            <span className="min-w-0 flex-1 truncate text-xs text-surface-500">
              {activePaper.title ?? activePaper.filename}
            </span>
            <button
              onClick={() => setSelectedNav("console")}
              className="inline-flex flex-shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-surface-500 hover:bg-surface-100 hover:text-accent-700"
              aria-label="Ask across all workspace sources"
            >
              Ask all sources
              <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
          <div className="min-h-0 flex-1">
            <QAPanel
              onHighlight={onHighlight}
              queuedQuestion={queuedQuestion}
              onQueuedQuestionHandled={onQueuedQuestionHandled}
            />
          </div>
        </section>
      )}

      <section className="min-h-[60vh] min-w-0 flex-[7] 2xl:min-h-0" aria-label="Library viewer">
        <ViewerLane
          highlightBboxes={highlightBboxes}
          targetPage={targetPage}
          jumpCounter={jumpCounter}
          onExplainConcept={onExplainConcept}
          onShowInPaper={onShowInPaper}
          onTrailAsk={onTrailAsk}
        />
      </section>
    </div>
  );
}

import { useCallback, useState, useRef } from "react";
import { MessageSquare, FileText, Library, BookOpen, Layers, PanelRight } from "lucide-react";
import type { Citation } from "@/types";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { QAPanel } from "./QAPanel";
import { ConsoleSidePanel } from "./console/ConsoleSidePanel";

type QueuedQuestion = { id?: string; question: string; nonce: number } | null;

interface Props {
  onHighlight: (citations: Citation[]) => void;
  queuedQuestion: QueuedQuestion;
  onQueuedQuestionHandled: (nonce: number) => void;
  onTrailAsk: (q: { id: string; question: string }) => void;
}

export function ConsolePage({ onHighlight, queuedQuestion, onQueuedQuestionHandled }: Props) {
  const { activePaper } = usePaperStore();
  const { getActiveWorkspace, consolePanelOpen, setConsolePanelOpen } = useWorkspaceStore();
  const { getActiveDeliverable, getSelectedSectionId } = useDeliverableStore();
  const { getIncludedSources } = useSourceStore();
  const nonceRef = useRef(Date.now());
  const [localQueued, setLocalQueued] = useState<{ id?: string; question: string; nonce: number } | null>(null);

  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";
  const activeDeliverable = workspace ? getActiveDeliverable(wid) : null;
  const selectedSectionId = activeDeliverable ? getSelectedSectionId(activeDeliverable.id) : null;
  const selectedSection = activeDeliverable?.sections.find((s) => s.id === selectedSectionId);
  const includedCount = getIncludedSources(wid).length;

  const effectiveQueued = queuedQuestion ?? localQueued;

  const handleDraftRequest = useCallback((sectionTitle: string) => {
    nonceRef.current += 1;
    setLocalQueued({ question: `Draft the "${sectionTitle}" section for my deliverable`, nonce: nonceRef.current });
  }, []);

  const handleQueuedHandled = useCallback((nonce: number) => {
    if (localQueued?.nonce === nonce) {
      setLocalQueued(null);
    }
    onQueuedQuestionHandled?.(nonce);
  }, [localQueued, onQueuedQuestionHandled]);

  return (
    <div className="flex h-full min-w-0 bg-white">
      {/* Chat column */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Identity header with context chips */}
        <div className="flex-shrink-0 px-4 py-2 border-b border-surface-200 bg-surface-50">
          <div className="max-w-[820px] mx-auto flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-surface-600 bg-surface-100 border border-surface-200 px-1.5 py-0.5 rounded uppercase tracking-wider">
              <MessageSquare className="w-2.5 h-2.5" />
              Workspace Console
            </span>

            {activePaper && (
              <span className="inline-flex items-center gap-1 text-[10px] text-surface-500 bg-surface-100 px-1.5 py-0.5 rounded truncate max-w-[160px]" title={activePaper.title ?? undefined}>
                <FileText className="w-2.5 h-2.5 flex-shrink-0" />
                {activePaper.title ?? activePaper.filename}
              </span>
            )}

            {activeDeliverable && (
              <span className="inline-flex items-center gap-1 text-[10px] text-surface-500 bg-surface-100 px-1.5 py-0.5 rounded truncate max-w-[120px]">
                <BookOpen className="w-2.5 h-2.5 flex-shrink-0" />
                {activeDeliverable.title}
              </span>
            )}

            {selectedSection && (
              <span className="inline-flex items-center gap-1 text-[10px] text-accent-600 bg-accent-50 border border-accent-200 px-1.5 py-0.5 rounded truncate max-w-[140px]">
                <Layers className="w-2.5 h-2.5 flex-shrink-0" />
                {selectedSection.title}
              </span>
            )}

            {includedCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[10px] text-surface-500 bg-surface-100 px-1.5 py-0.5 rounded flex-shrink-0">
                <Library className="w-2.5 h-2.5" />
                {includedCount} sources
              </span>
            )}

            {/* Toggle side panel */}
            <button
              onClick={() => setConsolePanelOpen(!consolePanelOpen)}
              className={`ml-auto inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-md transition-colors ${
                consolePanelOpen
                  ? "text-accent-600 bg-accent-50 border border-accent-200"
                  : "text-surface-600 bg-surface-100 hover:bg-surface-200 border border-surface-200"
              }`}
              title={consolePanelOpen ? "Hide panel" : "Show deliverables & sources"}
            >
              <PanelRight className="w-3 h-3" />
              {consolePanelOpen ? "Hide" : "Sources & Deliverables"}
            </button>
          </div>
        </div>

        {/* Chat area */}
        <div className="flex-1 min-h-0">
          <QAPanel
            onHighlight={onHighlight}
            queuedQuestion={effectiveQueued}
            onQueuedQuestionHandled={handleQueuedHandled}
            forceConsole
            centered
          />
        </div>
      </div>

      {/* Side panel */}
      {consolePanelOpen && (
        <div className="w-[340px] flex-shrink-0 h-full">
          <ConsoleSidePanel workspaceId={wid} onDraftRequest={handleDraftRequest} />
        </div>
      )}
    </div>
  );
}


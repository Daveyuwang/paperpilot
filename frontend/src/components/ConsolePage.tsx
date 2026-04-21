import React, { useCallback, useState, useRef } from "react";
import type { Citation } from "@/types";
import { useWorkspaceStore } from "@/store/workspaceStore";
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
  const { getActiveWorkspace, consolePanelOpen } = useWorkspaceStore();
  const nonceRef = useRef(Date.now());
  const fillInputRef = useRef<((text: string) => void) | null>(null);
  const [localQueued, setLocalQueued] = useState<{ id?: string; question: string; nonce: number } | null>(null);

  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";

  const effectiveQueued = queuedQuestion ?? localQueued;

  const handleDraftRequest = useCallback((sectionTitle: string) => {
    nonceRef.current += 1;
    setLocalQueued({ question: `Draft the "${sectionTitle}" section for my deliverable`, nonce: nonceRef.current });
  }, []);

  const handleFillInput = useCallback((text: string) => {
    fillInputRef.current?.(text);
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
        <QAPanel
          onHighlight={onHighlight}
          queuedQuestion={effectiveQueued}
          onQueuedQuestionHandled={handleQueuedHandled}
          forceConsole
          centered
          fillInputRef={fillInputRef}
        />
      </div>

      {/* Side panel — animated slide */}
      <div
        className="flex-shrink-0 h-full overflow-hidden transition-[width] duration-300 ease-in-out"
        style={{ width: consolePanelOpen ? 340 : 0 }}
      >
        <div className="w-[340px] h-full">
          <ConsoleSidePanel
            workspaceId={wid}
            onDraftRequest={handleDraftRequest}
            onFillInput={handleFillInput}
          />
        </div>
      </div>
    </div>
  );
}

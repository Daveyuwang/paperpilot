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
  const nonceRef = useRef(Date.now());
  const fillInputRef = useRef<((text: string) => void) | null>(null);
  const [localQueued, setLocalQueued] = useState<{ id?: string; question: string; nonce: number } | null>(null);

  const effectiveQueued = queuedQuestion ?? localQueued;

  const handleQueuedHandled = useCallback((nonce: number) => {
    if (localQueued?.nonce === nonce) {
      setLocalQueued(null);
    }
    onQueuedQuestionHandled?.(nonce);
  }, [localQueued, onQueuedQuestionHandled]);

  return (
    <div className="flex h-full min-w-0 bg-white">
      {/* Left: Chat */}
      <div className="flex flex-col flex-[6] min-w-[400px] max-w-[600px] border-r border-surface-200">
        <div className="flex-1 min-h-0">
          <QAPanel
            onHighlight={onHighlight}
            queuedQuestion={effectiveQueued}
            onQueuedQuestionHandled={handleQueuedHandled}
            forceConsole
            centered
            fillInputRef={fillInputRef}
          />
        </div>
      </div>

      {/* Right: Deliverable / Sources viewer */}
      <div className="flex-[7] min-w-0 h-full">
        <ConsoleSidePanel />
      </div>
    </div>
  );
}

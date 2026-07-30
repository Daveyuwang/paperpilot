import type { Citation } from "@/types";
import { QAPanel } from "./QAPanel";

type QueuedQuestion = { id?: string; question: string; nonce: number } | null;

interface Props {
  onHighlight: (citations: Citation[]) => void;
  queuedQuestion: QueuedQuestion;
  onQueuedQuestionHandled: (nonce: number) => void;
}

export function ConsolePage({ onHighlight, queuedQuestion, onQueuedQuestionHandled }: Props) {
  return (
    <div className="h-full min-w-0 bg-white">
      <section className="flex h-full min-w-0 flex-col" aria-label="Workspace chat">
        <div className="flex-1 min-h-0">
          <QAPanel
            onHighlight={onHighlight}
            queuedQuestion={queuedQuestion}
            onQueuedQuestionHandled={onQueuedQuestionHandled}
            forceConsole
            centered
          />
        </div>
      </section>
    </div>
  );
}

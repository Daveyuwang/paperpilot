import clsx from "clsx";
import { FileText, Map } from "lucide-react";
import { useWorkspaceStore, type ViewerTab } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { PDFViewer } from "./PDFViewer";
import { AgendaView } from "./AgendaView";
import { ConceptMap } from "./ConceptMap";
import { SourcesView } from "./SourcesView";
import { DeliverableView } from "./DeliverableView";
import { EmptyState } from "./shared/EmptyState";
import type { Citation } from "@/types";

const TABS: { id: ViewerTab; label: string }[] = [
  { id: "reader",      label: "Reader" },
  { id: "deliverable", label: "Deliverable" },
  { id: "sources",     label: "Sources" },
  { id: "agenda",      label: "Agenda" },
  { id: "concepts",    label: "Concepts" },
];

interface Props {
  highlightBboxes: NonNullable<Citation["bbox"]>[];
  targetPage: number | undefined;
  jumpCounter: number;
  onExplainConcept: (label: string) => void;
  onShowInPaper: (page: number) => void;
  onTrailAsk: (q: { id: string; question: string }) => void;
}

export function ViewerLane({
  highlightBboxes,
  targetPage,
  jumpCounter,
  onExplainConcept,
  onShowInPaper,
  onTrailAsk,
}: Props) {
  const { getActiveWorkspace, setActiveViewerTab } = useWorkspaceStore();
  const { activePaper } = usePaperStore();
  const workspace = getActiveWorkspace();
  const activeTab = workspace?.activeViewerTab ?? "reader";

  return (
    <div className="flex flex-col h-full min-w-0 bg-surface-50">
      {/* Tab bar */}
      <div className="flex-shrink-0 flex items-center gap-1 px-4 py-2 border-b border-surface-200 bg-surface-50">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveViewerTab(tab.id)}
            className={clsx(
              activeTab === tab.id ? "viewer-tab-active" : "viewer-tab"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {/* Reader — always mounted to preserve PDF state */}
        <div className={clsx("h-full", activeTab !== "reader" && "hidden")}>
          {activePaper ? (
            <PDFViewer
              paperId={activePaper.id}
              highlightBboxes={highlightBboxes}
              targetPage={targetPage}
              jumpCounter={jumpCounter}
              key={activePaper.id}
            />
          ) : (
            <EmptyState
              icon={<FileText className="w-10 h-10" />}
              heading="No paper loaded"
              description="Select or upload a paper to start reading"
            />
          )}
        </div>

        {activeTab === "agenda" && (
          <div className="h-full overflow-y-auto p-4">
            <AgendaView onAsk={onTrailAsk} />
          </div>
        )}

        {activeTab === "concepts" && activePaper && (
          <div className="h-full">
            <ConceptMap
              paperId={activePaper.id}
              paperTitle={activePaper.title ?? activePaper.filename}
              onExplainConcept={onExplainConcept}
              onShowInPaper={onShowInPaper}
            />
          </div>
        )}
        {activeTab === "concepts" && !activePaper && (
          <EmptyState
            icon={<Map className="w-10 h-10" />}
            heading="No paper loaded"
            description="Upload a paper to explore its concept map"
          />
        )}

        {activeTab === "deliverable" && (
          <div className="h-full">
            <DeliverableView />
          </div>
        )}

        {activeTab === "sources" && (
          <div className="h-full">
            <SourcesView />
          </div>
        )}
      </div>
    </div>
  );
}

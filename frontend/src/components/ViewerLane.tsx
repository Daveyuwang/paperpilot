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
  { id: "reader",      label: "Paper" },
  { id: "deliverable", label: "Draft" },
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
  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
      ? TABS.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    const nextTab = TABS[nextIndex];
    setActiveViewerTab(nextTab.id);
    document.getElementById(`library-tab-${nextTab.id}`)?.focus();
  };

  return (
    <div className="flex flex-col h-full min-w-0 bg-surface-50">
      {/* Tab bar */}
      <div className="flex flex-shrink-0 items-center gap-1 overflow-x-auto border-b border-surface-200 bg-surface-50 px-3 py-2 sm:px-4" role="tablist" aria-label="Library views">
        {TABS.map((tab, index) => (
          <button
            key={tab.id}
            id={`library-tab-${tab.id}`}
            onClick={() => setActiveViewerTab(tab.id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`library-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
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
        <div
          id="library-panel-reader"
          role="tabpanel"
          aria-labelledby="library-tab-reader"
          className={clsx("h-full", activeTab !== "reader" && "hidden")}
        >
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
              heading="Choose a paper"
              description="Select one from the library or upload a PDF."
            />
          )}
        </div>

        {activeTab === "agenda" && (
          <div id="library-panel-agenda" role="tabpanel" aria-labelledby="library-tab-agenda" className="h-full overflow-y-auto p-4">
            <AgendaView onAsk={onTrailAsk} />
          </div>
        )}

        {activeTab === "concepts" && activePaper && (
          <div id="library-panel-concepts" role="tabpanel" aria-labelledby="library-tab-concepts" className="h-full">
            <ConceptMap
              paperId={activePaper.id}
              paperTitle={activePaper.title ?? activePaper.filename}
              onExplainConcept={onExplainConcept}
              onShowInPaper={onShowInPaper}
            />
          </div>
        )}
        {activeTab === "concepts" && !activePaper && (
          <div id="library-panel-concepts" role="tabpanel" aria-labelledby="library-tab-concepts" className="h-full">
            <EmptyState
              icon={<Map className="w-10 h-10" />}
              heading="Choose a paper"
              description="Concepts appear after a paper is ready."
            />
          </div>
        )}

        {activeTab === "deliverable" && (
          <div id="library-panel-deliverable" role="tabpanel" aria-labelledby="library-tab-deliverable" className="h-full">
            <DeliverableView />
          </div>
        )}

        {activeTab === "sources" && (
          <div id="library-panel-sources" role="tabpanel" aria-labelledby="library-tab-sources" className="h-full">
            <SourcesView />
          </div>
        )}
      </div>
    </div>
  );
}

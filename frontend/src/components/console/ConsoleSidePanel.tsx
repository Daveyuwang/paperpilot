import { PanelRightClose, FileText, Library } from "lucide-react";
import clsx from "clsx";
import { useWorkspaceStore, type ConsolePanelTab } from "@/store/workspaceStore";
import { MiniDeliverableView } from "./MiniDeliverableView";
import { MiniSourcesView } from "./MiniSourcesView";

const TABS: { id: ConsolePanelTab; label: string; icon: typeof FileText }[] = [
  { id: "deliverable", label: "Deliverable", icon: FileText },
  { id: "sources", label: "Sources", icon: Library },
];

interface Props {
  workspaceId: string;
  onDraftRequest?: (sectionTitle: string) => void;
}

export function ConsoleSidePanel({ workspaceId, onDraftRequest }: Props) {
  const { consolePanelTab, setConsolePanelTab, setConsolePanelOpen } = useWorkspaceStore();

  return (
    <div className="flex flex-col h-full bg-white border-l border-surface-200">
      {/* Header with tabs */}
      <div className="flex-shrink-0 flex items-center justify-between px-2 py-1.5 border-b border-surface-200 bg-surface-50">
        <div className="flex items-center gap-0.5">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setConsolePanelTab(tab.id)}
                className={clsx(
                  "inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors",
                  consolePanelTab === tab.id
                    ? "bg-white text-surface-800 shadow-sm border border-surface-200"
                    : "text-surface-500 hover:text-surface-700 hover:bg-surface-100"
                )}
              >
                <Icon className="w-3 h-3" />
                {tab.label}
              </button>
            );
          })}
        </div>
        <button
          onClick={() => setConsolePanelOpen(false)}
          className="p-1 rounded hover:bg-surface-100 text-surface-400 hover:text-surface-600 transition-colors"
          title="Close panel"
        >
          <PanelRightClose className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {consolePanelTab === "deliverable" && (
          <MiniDeliverableView workspaceId={workspaceId} onDraftRequest={onDraftRequest} />
        )}
        {consolePanelTab === "sources" && (
          <MiniSourcesView workspaceId={workspaceId} />
        )}
      </div>
    </div>
  );
}

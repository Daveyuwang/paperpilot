import { FileText, Library } from "lucide-react";
import clsx from "clsx";
import { useWorkspaceStore, type ConsolePanelTab } from "@/store/workspaceStore";
import { DeliverableView } from "../DeliverableView";
import { SourcesView } from "../SourcesView";

const TABS: { id: ConsolePanelTab; label: string; icon: typeof FileText }[] = [
  { id: "deliverable", label: "Deliverable", icon: FileText },
  { id: "sources", label: "Sources", icon: Library },
];

export function ConsoleSidePanel() {
  const { consolePanelTab, setConsolePanelTab } = useWorkspaceStore();

  return (
    <div className="flex flex-col h-full bg-white border-l border-surface-200">
      {/* Tab bar */}
      <div className="flex-shrink-0 flex items-center gap-1 px-4 py-2 border-b border-surface-200 bg-surface-50">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setConsolePanelTab(tab.id)}
              className={clsx(
                "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
                consolePanelTab === tab.id
                  ? "bg-white text-surface-800 shadow-sm border border-surface-200"
                  : "text-surface-500 hover:text-surface-700 hover:bg-surface-100"
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {consolePanelTab === "deliverable" && (
          <div className="h-full">
            <DeliverableView />
          </div>
        )}
        {consolePanelTab === "sources" && (
          <div className="h-full">
            <SourcesView />
          </div>
        )}
      </div>
    </div>
  );
}

import { useWorkspaceStore } from "@/store/workspaceStore";
import { usePaperStore } from "@/store/paperStore";
import { useDeliverableStore } from "@/store/deliverableStore";

export function WorkspaceHeader() {
  const { getActiveWorkspace, selectedNav } = useWorkspaceStore();
  const { activePaper } = usePaperStore();
  const { getActiveDeliverable } = useDeliverableStore();
  const workspace = getActiveWorkspace();
  const activeDeliverable = workspace ? getActiveDeliverable(workspace.id) : null;

  return (
    <header className="flex-shrink-0 flex items-center justify-between px-6 py-2.5 border-b border-surface-200 bg-surface-50">
      <div className="flex items-center gap-3 min-w-0">
        <h1 className="heading-serif text-base truncate">{workspace?.title ?? "Workspace"}</h1>
        {workspace?.objective && (
          <span className="text-xs text-surface-400 truncate hidden md:block">
            {workspace.objective}
          </span>
        )}
      </div>

      <div className="flex items-center gap-3 flex-shrink-0">
        {selectedNav === "workspace" && activeDeliverable && workspace?.activeViewerTab === "deliverable" && (
          <span className="text-xs text-accent-600 truncate max-w-[200px] hidden lg:block">
            {activeDeliverable.title}
          </span>
        )}
        {selectedNav === "workspace" && activePaper && workspace?.activeViewerTab !== "deliverable" && (
          <span className="text-xs text-surface-400 truncate max-w-[200px] hidden lg:block">
            {activePaper.title ?? activePaper.filename}
          </span>
        )}
      </div>
    </header>
  );
}

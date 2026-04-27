import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useRunStore } from "@/store/runStore";
import { useRunDraft } from "@/hooks/useRunDraft";
import { EmptyState } from "./DeliverableView/EmptyState";
import { RunStatusBar } from "./DeliverableView/RunStatusBar";
import { DeliverableHeader } from "./DeliverableView/DeliverableHeader";
import { SectionOutline } from "./DeliverableView/SectionOutline";
import { SectionEditor } from "./DeliverableView/SectionEditor";

export function DeliverableView() {
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";
  const { getDeliverables, getActiveDeliverable } = useDeliverableStore();
  const deliverables = getDeliverables(wid);
  const active = getActiveDeliverable(wid);
  const runDraft = useRunDraft(active, wid);
  const { status, message, previews, reset } = useRunStore();

  if (deliverables.length === 0) return <EmptyState workspaceId={wid} />;

  return (
    <div className="flex flex-col h-full min-w-0">
      <DeliverableHeader
        deliverable={active}
        deliverables={deliverables}
        workspaceId={wid}
        onDraftAll={() => runDraft("draft_deliverable")}
      />
      <RunStatusBar status={status} message={message} onDismiss={reset} />
      {active ? (
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <SectionOutline deliverable={active} workspaceId={wid} previews={previews} />
          <SectionEditor deliverable={active} workspaceId={wid} runDraft={runDraft} />
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-surface-400">
          Select a deliverable to begin editing.
        </div>
      )}
    </div>
  );
}

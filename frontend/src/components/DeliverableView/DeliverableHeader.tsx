import { useState, useCallback } from "react";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useRunStore } from "@/store/runStore";
import {
  Plus, Trash2, Copy, ChevronRight,
  FileText, Pencil, Check, MoreHorizontal, Sparkles,
} from "lucide-react";
import clsx from "clsx";
import { TYPE_LABELS } from "./constants";
import type { Deliverable, DeliverableType } from "@/types";

export function DeliverableHeader({
  deliverable,
  deliverables,
  workspaceId,
  onDraftAll,
}: {
  deliverable: Deliverable | null;
  deliverables: Deliverable[];
  workspaceId: string;
  onDraftAll: () => void;
}) {
  const { setActiveDeliverable, deleteDeliverable, duplicateDeliverable, renameDeliverable, createDeliverable } =
    useDeliverableStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const handleStartRename = useCallback(() => {
    if (!deliverable) return;
    setRenameValue(deliverable.title);
    setRenaming(true);
    setMenuOpen(false);
  }, [deliverable]);

  const handleFinishRename = useCallback(() => {
    if (!deliverable || !renameValue.trim()) return;
    renameDeliverable(workspaceId, deliverable.id, renameValue.trim());
    setRenaming(false);
  }, [deliverable, renameValue, workspaceId, renameDeliverable]);

  return (
    <div className="flex-shrink-0 flex items-center gap-2 px-4 py-2 border-b border-surface-200 bg-white min-h-[44px]">
      {/* Switcher */}
      <div className="relative">
        <button
          onClick={() => setSwitcherOpen(!switcherOpen)}
          className="flex items-center gap-1 text-xs text-surface-500 hover:text-surface-700 transition-colors"
        >
          <ChevronRight className={clsx("w-3 h-3 transition-transform", switcherOpen && "rotate-90")} />
          <span className="font-medium">{deliverables.length} deliverable{deliverables.length !== 1 ? "s" : ""}</span>
        </button>
        {switcherOpen && (
          <div className="absolute left-0 top-full mt-1 z-20 bg-white border border-surface-200 rounded-lg shadow-lg py-1 min-w-[200px]">
            {deliverables.map((d) => (
              <button
                key={d.id}
                onClick={() => { setActiveDeliverable(workspaceId, d.id); setSwitcherOpen(false); }}
                className={clsx(
                  "w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2",
                  d.id === deliverable?.id ? "bg-accent-50 text-accent-700" : "text-surface-600 hover:bg-surface-50"
                )}
              >
                <FileText className="w-3 h-3 flex-shrink-0" />
                <span className="truncate">{d.title}</span>
                <span className="ml-auto text-[10px] text-surface-400">{TYPE_LABELS[d.type]}</span>
              </button>
            ))}
            <div className="border-t border-surface-100 mt-1 pt-1">
              {(["deep_research", "proposal", "research_plan", "notes"] as DeliverableType[]).map((type) => (
                <button
                  key={type}
                  onClick={() => { createDeliverable(workspaceId, type); setSwitcherOpen(false); }}
                  className="w-full text-left px-3 py-1.5 text-xs text-surface-500 hover:bg-surface-50 transition-colors flex items-center gap-2"
                >
                  <Plus className="w-3 h-3" />
                  New {TYPE_LABELS[type]}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Title */}
      {deliverable && (
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-surface-200">|</span>
          {renaming ? (
            <form
              onSubmit={(e) => { e.preventDefault(); handleFinishRename(); }}
              className="flex items-center gap-1 flex-1 min-w-0"
            >
              <input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={handleFinishRename}
                className="flex-1 min-w-0 text-xs font-medium text-surface-800 bg-transparent border-b border-accent-400 focus:outline-none py-0.5"
              />
              <button type="submit" className="p-0.5"><Check className="w-3 h-3 text-accent-600" /></button>
            </form>
          ) : (
            <>
              <span className="heading-serif text-sm text-surface-800 truncate">{deliverable.title}</span>
              <span className="text-[10px] text-surface-400 bg-surface-50 border border-surface-200 px-1.5 py-0.5 rounded shrink-0">
                {TYPE_LABELS[deliverable.type]}
              </span>
            </>
          )}
        </div>
      )}

      {/* Actions menu */}
      {deliverable && (
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={onDraftAll}
            disabled={useRunStore.getState().status === "preparing" || useRunStore.getState().status === "generating"}
            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-accent-700 bg-accent-50 border border-accent-200 rounded-md hover:bg-accent-100 disabled:opacity-50 transition-colors"
          >
            <Sparkles className="w-3 h-3" />
            Draft All
          </button>
          <div className="relative">
            <button onClick={() => setMenuOpen(!menuOpen)} className="p-1 rounded hover:bg-surface-100">
              <MoreHorizontal className="w-4 h-4 text-surface-400" />
            </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-20 bg-white border border-surface-200 rounded-lg shadow-lg py-1 min-w-[140px]">
              <button
                onClick={handleStartRename}
                className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              >
                <Pencil className="w-3 h-3" /> Rename
              </button>
              <button
                onClick={() => { duplicateDeliverable(workspaceId, deliverable.id); setMenuOpen(false); }}
                className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              >
                <Copy className="w-3 h-3" /> Duplicate
              </button>
              <button
                onClick={() => { deleteDeliverable(workspaceId, deliverable.id); setMenuOpen(false); }}
                className="w-full text-left px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 flex items-center gap-2"
              >
                <Trash2 className="w-3 h-3" /> Delete
              </button>
            </div>
          )}
        </div>
        </div>
      )}
    </div>
  );
}

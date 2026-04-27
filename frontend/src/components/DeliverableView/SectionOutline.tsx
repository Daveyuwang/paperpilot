import { useDeliverableStore } from "@/store/deliverableStore";
import type { SectionPreview } from "@/store/runStore";
import {
  Plus, Trash2, ChevronUp, ChevronDown, Link2, Sparkles,
} from "lucide-react";
import clsx from "clsx";
import type { Deliverable } from "@/types";

export function SectionOutline({ deliverable, workspaceId, previews }: { deliverable: Deliverable; workspaceId: string; previews: SectionPreview[] }) {
  const { selectSection, getSelectedSectionId, addSection, moveSection, deleteSection } = useDeliverableStore();
  const selectedId = getSelectedSectionId(deliverable.id);
  const sorted = [...deliverable.sections].sort((a, b) => a.order - b.order);

  return (
    <div className="flex-shrink-0 w-52 border-r border-surface-200 bg-surface-50 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-100">
        <span className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">Outline</span>
        <button
          onClick={() => addSection(workspaceId, deliverable.id, sorted[sorted.length - 1]?.id)}
          className="p-0.5 rounded hover:bg-surface-200 transition-colors"
          title="Add section"
        >
          <Plus className="w-3.5 h-3.5 text-surface-400" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {sorted.map((sec, idx) => {
          const wordCount = sec.content.trim() ? sec.content.trim().split(/\s+/).length : 0;
          const hasContent = wordCount > 0;
          return (
          <div key={sec.id} className="group">
            <button
              onClick={() => selectSection(deliverable.id, sec.id)}
              className={clsx(
                "w-full text-left px-3 py-1.5 text-xs flex items-start gap-1.5 transition-colors",
                selectedId === sec.id
                  ? "bg-accent-50 text-accent-700 border-l-2 border-accent-500"
                  : "text-surface-600 hover:bg-surface-100 border-l-2 border-transparent"
              )}
            >
              <div className="flex-1 min-w-0">
                <span className="truncate block">{sec.title || "Untitled"}</span>
                <span className={clsx(
                  "text-[10px] mt-0.5 block",
                  hasContent ? "text-surface-400" : "text-surface-300 italic"
                )}>
                  {hasContent ? `${wordCount} words` : "empty"}
                </span>
              </div>
              <div className="flex items-center gap-0.5 shrink-0 mt-0.5">
                {previews.some((p) => p.sectionId === sec.id) && (
                  <Sparkles className="w-2.5 h-2.5 text-amber-500" />
                )}
                {sec.linkedSourceIds.length > 0 && (
                  <Link2 className="w-2.5 h-2.5 text-surface-300" />
                )}
                {hasContent && (
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 ml-0.5" title="Has content" />
                )}
              </div>
            </button>
            {selectedId === sec.id && (
              <div className="flex items-center gap-0.5 px-3 py-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={() => moveSection(workspaceId, deliverable.id, sec.id, "up")}
                  disabled={idx === 0}
                  className="p-0.5 rounded hover:bg-surface-200 disabled:opacity-30"
                  title="Move up"
                >
                  <ChevronUp className="w-3 h-3 text-surface-400" />
                </button>
                <button
                  onClick={() => moveSection(workspaceId, deliverable.id, sec.id, "down")}
                  disabled={idx === sorted.length - 1}
                  className="p-0.5 rounded hover:bg-surface-200 disabled:opacity-30"
                  title="Move down"
                >
                  <ChevronDown className="w-3 h-3 text-surface-400" />
                </button>
                <button
                  onClick={() => addSection(workspaceId, deliverable.id, sec.id)}
                  className="p-0.5 rounded hover:bg-surface-200"
                  title="Add section below"
                >
                  <Plus className="w-3 h-3 text-surface-400" />
                </button>
                {sorted.length > 1 && (
                  <button
                    onClick={() => deleteSection(workspaceId, deliverable.id, sec.id)}
                    className="p-0.5 rounded hover:bg-red-50"
                    title="Delete section"
                  >
                    <Trash2 className="w-3 h-3 text-surface-400 hover:text-red-500" />
                  </button>
                )}
              </div>
            )}
          </div>
          );
        })}
      </div>
    </div>
  );
}

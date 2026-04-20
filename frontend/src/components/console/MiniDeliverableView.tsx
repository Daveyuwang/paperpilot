import { useState } from "react";
import { FileText, ChevronRight, Plus, Sparkles, Check, Circle } from "lucide-react";
import clsx from "clsx";
import { useDeliverableStore } from "@/store/deliverableStore";
import type { DeliverableSection, DeliverableType } from "@/types";

const TYPE_LABELS: Record<DeliverableType, string> = {
  deep_research: "Research Brief",
  proposal: "Proposal",
  research_plan: "Research Plan",
  notes: "Notes",
};

interface Props {
  workspaceId: string;
  onDraftRequest?: (sectionTitle: string) => void;
}

export function MiniDeliverableView({ workspaceId, onDraftRequest }: Props) {
  const { getDeliverables, createDeliverable, getActiveDeliverable, setActiveDeliverable, getSelectedSectionId, selectSection } = useDeliverableStore();
  const deliverables = getDeliverables(workspaceId);
  const activeDeliverable = getActiveDeliverable(workspaceId);
  const selectedSectionId = activeDeliverable ? getSelectedSectionId(activeDeliverable.id) : null;

  if (deliverables.length === 0) {
    return <EmptyDeliverables workspaceId={workspaceId} />;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Deliverable selector */}
      <div className="flex-shrink-0 px-3 py-2 border-b border-surface-200">
        <select
          value={activeDeliverable?.id ?? ""}
          onChange={(e) => setActiveDeliverable(workspaceId, e.target.value || null)}
          className="w-full text-xs bg-surface-50 border border-surface-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-accent-300"
        >
          {deliverables.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title} — {TYPE_LABELS[d.type]}
            </option>
          ))}
        </select>
      </div>

      {/* Section list */}
      {activeDeliverable && (
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
          {[...activeDeliverable.sections]
            .sort((a, b) => a.order - b.order)
            .map((section) => (
              <SectionRow
                key={section.id}
                section={section}
                isSelected={section.id === selectedSectionId}
                onSelect={() => selectSection(activeDeliverable.id, section.id)}
                onDraft={() => onDraftRequest?.(section.title)}
              />
            ))}
        </div>
      )}
    </div>
  );
}

function SectionRow({
  section,
  isSelected,
  onSelect,
  onDraft,
}: {
  section: DeliverableSection;
  isSelected: boolean;
  onSelect: () => void;
  onDraft: () => void;
}) {
  const hasContent = section.content.trim().length > 0;
  const wordCount = hasContent ? section.content.trim().split(/\s+/).length : 0;
  const isAIDrafted = section.lastUpdatedBy === "ai";

  return (
    <div
      onClick={onSelect}
      className={clsx(
        "group flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-all text-xs",
        isSelected
          ? "bg-accent-50 border border-accent-200"
          : "hover:bg-surface-50 border border-transparent"
      )}
    >
      {hasContent ? (
        <Check className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
      ) : (
        <Circle className="w-3.5 h-3.5 text-surface-300 flex-shrink-0" />
      )}
      <div className="flex-1 min-w-0">
        <div className="truncate font-medium text-surface-700">{section.title}</div>
        <div className="text-[10px] text-surface-400 mt-0.5">
          {hasContent ? (
            <span>
              {wordCount} words
              {isAIDrafted && <span className="ml-1 text-accent-500">• AI</span>}
            </span>
          ) : (
            <span className="text-surface-300">empty</span>
          )}
        </div>
      </div>
      {!hasContent && (
        <button
          onClick={(e) => { e.stopPropagation(); onDraft(); }}
          className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-accent-100 text-accent-600 transition-opacity"
          title="Ask AI to draft"
        >
          <Sparkles className="w-3 h-3" />
        </button>
      )}
      <ChevronRight className={clsx(
        "w-3 h-3 flex-shrink-0 transition-colors",
        isSelected ? "text-accent-500" : "text-surface-300"
      )} />
    </div>
  );
}

function EmptyDeliverables({ workspaceId }: { workspaceId: string }) {
  const { createDeliverable } = useDeliverableStore();
  const [showMenu, setShowMenu] = useState(false);

  const types: DeliverableType[] = ["deep_research", "proposal", "research_plan", "notes"];

  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-8 text-center">
      <FileText className="w-8 h-8 text-surface-300 mb-2" />
      <p className="text-xs text-surface-500 mb-3">No deliverables yet</p>
      <div className="relative">
        <button
          onClick={() => setShowMenu(!showMenu)}
          className="inline-flex items-center gap-1 text-xs text-accent-600 hover:text-accent-700 font-medium"
        >
          <Plus className="w-3 h-3" /> Create one
        </button>
        {showMenu && (
          <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 bg-white border border-surface-200 rounded-lg shadow-lg py-1 z-10 min-w-[140px]">
            {types.map((type) => (
              <button
                key={type}
                onClick={() => { createDeliverable(workspaceId, type); setShowMenu(false); }}
                className="block w-full text-left px-3 py-1.5 text-xs text-surface-700 hover:bg-surface-50"
              >
                {TYPE_LABELS[type]}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

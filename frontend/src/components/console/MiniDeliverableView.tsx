import { useState } from "react";
import { FileText, ChevronRight, Plus, Sparkles, Check, Circle, BookOpen, FlaskConical, ScrollText, StickyNote, ExternalLink, Wand2 } from "lucide-react";
import clsx from "clsx";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import type { DeliverableSection, DeliverableType } from "@/types";

const TYPE_LABELS: Record<DeliverableType, string> = {
  deep_research: "Research Brief",
  proposal: "Proposal",
  research_plan: "Research Plan",
  notes: "Notes",
};

const TYPE_ICONS: Record<DeliverableType, typeof BookOpen> = {
  deep_research: FlaskConical,
  proposal: ScrollText,
  research_plan: BookOpen,
  notes: StickyNote,
};

interface Props {
  workspaceId: string;
  onDraftRequest?: (sectionTitle: string) => void;
  onFillInput?: (text: string) => void;
}

export function MiniDeliverableView({ workspaceId, onDraftRequest, onFillInput }: Props) {
  const { getDeliverables, getActiveDeliverable, setActiveDeliverable, getSelectedSectionId, selectSection } = useDeliverableStore();
  const { setSelectedNav, setActiveViewerTab } = useWorkspaceStore();
  const deliverables = getDeliverables(workspaceId);
  const activeDeliverable = getActiveDeliverable(workspaceId);
  const selectedSectionId = activeDeliverable ? getSelectedSectionId(activeDeliverable.id) : null;

  if (deliverables.length === 0) {
    return <EmptyDeliverables workspaceId={workspaceId} />;
  }

  const filledCount = activeDeliverable?.sections.filter((s) => s.content.trim().length > 0).length ?? 0;
  const totalCount = activeDeliverable?.sections.length ?? 0;

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
        {totalCount > 0 && (
          <div className="flex items-center gap-2 mt-1.5">
            <div className="flex-1 h-1 bg-surface-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-400 rounded-full transition-all duration-300"
                style={{ width: `${Math.round((filledCount / totalCount) * 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-surface-400 tabular-nums">{filledCount}/{totalCount}</span>
          </div>
        )}
        <div className="flex items-center gap-1.5 mt-2">
          {filledCount < totalCount && (
            <button
              onClick={() => onFillInput?.("Draft all empty sections of my deliverable")}
              className="flex items-center gap-1 text-[10px] text-accent-600 hover:text-accent-700 font-medium px-2 py-1 rounded-md hover:bg-accent-50 transition-colors"
            >
              <Wand2 className="w-3 h-3" />
              Draft all
            </button>
          )}
          <button
            onClick={() => {
              setSelectedNav("reader");
              setActiveViewerTab("deliverable");
            }}
            className="flex items-center gap-1 text-[10px] text-surface-500 hover:text-surface-700 font-medium px-2 py-1 rounded-md hover:bg-surface-100 transition-colors ml-auto"
          >
            <ExternalLink className="w-3 h-3" />
            View in Reader
          </button>
        </div>
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
                onSelect={() => {
                  selectSection(activeDeliverable.id, section.id);
                }}
                onDraft={() => onDraftRequest?.(section.title)}
                onRevise={() => onFillInput?.(`Revise the "${section.title}" section of my deliverable`)}
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
  onRevise,
}: {
  section: DeliverableSection;
  isSelected: boolean;
  onSelect: () => void;
  onDraft: () => void;
  onRevise: () => void;
}) {
  const hasContent = section.content.trim().length > 0;
  const wordCount = hasContent ? section.content.trim().split(/\s+/).length : 0;
  const isAIDrafted = section.lastUpdatedBy === "ai";
  const preview = hasContent ? section.content.trim().slice(0, 200).replace(/\n+/g, " ") : "";

  return (
    <div>
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
          <Circle className="w-3.5 h-3.5 text-surface-300 flex-shrink-0 opacity-50" strokeDasharray="3 2" />
        )}
        <div className="flex-1 min-w-0">
          <div className="truncate font-medium text-surface-700">{section.title}</div>
          <div className="text-[10px] text-surface-400 mt-0.5">
            {hasContent ? (
              <span>
                {wordCount} words
                {isAIDrafted && <span className="ml-1 text-accent-500 font-medium">AI</span>}
              </span>
            ) : (
              <span className="text-surface-300 italic">empty — click to write</span>
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
        {hasContent && (
          <button
            onClick={(e) => { e.stopPropagation(); onRevise(); }}
            className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-accent-100 text-accent-600 transition-opacity"
            title="Ask AI to revise"
          >
            <Wand2 className="w-3 h-3" />
          </button>
        )}
        <ChevronRight className={clsx(
          "w-3 h-3 flex-shrink-0 transition-colors",
          isSelected ? "text-accent-500" : "text-surface-300"
        )} />
      </div>
      {isSelected && preview && (
        <div className="mx-2.5 mt-1 mb-1 px-2.5 py-2 bg-surface-50 rounded-md text-[11px] text-surface-500 leading-relaxed line-clamp-3">
          {preview}{section.content.trim().length > 200 ? "…" : ""}
        </div>
      )}
    </div>
  );
}

function EmptyDeliverables({ workspaceId }: { workspaceId: string }) {
  const { createDeliverable } = useDeliverableStore();
  const [showMenu, setShowMenu] = useState(false);

  const types: { type: DeliverableType; desc: string }[] = [
    { type: "deep_research", desc: "Synthesized research brief" },
    { type: "proposal", desc: "Grant or project proposal" },
    { type: "research_plan", desc: "Structured research plan" },
    { type: "notes", desc: "Freeform notes" },
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full px-5 py-8 text-center">
      <div className="w-10 h-10 rounded-full bg-surface-100 flex items-center justify-center mb-3">
        <FileText className="w-5 h-5 text-surface-400" />
      </div>
      <p className="text-xs font-medium text-surface-600 mb-1">No deliverables yet</p>
      <p className="text-[10px] text-surface-400 mb-4 leading-relaxed">
        Create a deliverable to organize your writing, or run Deep Research / Proposal Plan to generate one automatically.
      </p>
      <div className="relative">
        <button
          onClick={() => setShowMenu(!showMenu)}
          className="inline-flex items-center gap-1.5 text-xs text-white bg-accent-600 hover:bg-accent-700 font-medium px-3 py-1.5 rounded-lg transition-colors"
        >
          <Plus className="w-3 h-3" /> Create deliverable
        </button>
        {showMenu && (
          <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 bg-white border border-surface-200 rounded-lg shadow-lg py-1 z-10 min-w-[180px]">
            {types.map(({ type, desc }) => {
              const Icon = TYPE_ICONS[type];
              return (
                <button
                  key={type}
                  onClick={() => { createDeliverable(workspaceId, type); setShowMenu(false); }}
                  className="flex items-start gap-2 w-full text-left px-3 py-2 text-xs text-surface-700 hover:bg-surface-50"
                >
                  <Icon className="w-3.5 h-3.5 mt-0.5 text-surface-400 flex-shrink-0" />
                  <div>
                    <div className="font-medium">{TYPE_LABELS[type]}</div>
                    <div className="text-[10px] text-surface-400">{desc}</div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

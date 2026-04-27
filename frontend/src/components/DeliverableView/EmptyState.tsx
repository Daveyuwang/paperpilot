import { useDeliverableStore } from "@/store/deliverableStore";
import { FileText } from "lucide-react";
import { TYPE_LABELS, TYPE_DESCRIPTIONS } from "./constants";
import type { DeliverableType } from "@/types";

export function EmptyState({ workspaceId }: { workspaceId: string }) {
  const { createDeliverable } = useDeliverableStore();
  const types: DeliverableType[] = ["deep_research", "proposal", "research_plan", "notes"];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 px-8">
      <div className="text-center">
        <h3 className="heading-serif text-base text-surface-700">Create a Deliverable</h3>
        <p className="text-xs text-surface-400 mt-1 max-w-xs">
          Start a structured document for your research. Choose a template to begin.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 max-w-md w-full">
        {types.map((type) => (
          <button
            key={type}
            onClick={() => createDeliverable(workspaceId, type)}
            className="text-left px-4 py-3 rounded-xl border border-surface-200 bg-white hover:border-accent-300 hover:shadow-sm transition-all group"
          >
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-3.5 h-3.5 text-surface-400 group-hover:text-accent-500 transition-colors" />
              <span className="text-xs font-semibold text-surface-700">{TYPE_LABELS[type]}</span>
            </div>
            <p className="text-[11px] text-surface-400 leading-snug">{TYPE_DESCRIPTIONS[type]}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

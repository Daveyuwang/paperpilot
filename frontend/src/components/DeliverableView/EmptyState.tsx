import { useDeliverableStore } from "@/store/deliverableStore";
import { ArrowRight, FileText } from "lucide-react";
import { TYPE_LABELS, TYPE_DESCRIPTIONS } from "./constants";
import type { DeliverableType } from "@/types";

export function EmptyState({ workspaceId }: { workspaceId: string }) {
  const { createDeliverable } = useDeliverableStore();
  const types: DeliverableType[] = ["deep_research", "proposal", "research_plan", "notes"];

  return (
    <div className="flex h-full items-center justify-center px-5 py-10 sm:px-8">
      <section className="w-full max-w-md" aria-labelledby="create-draft-title">
        <h2 id="create-draft-title" className="text-base font-semibold text-surface-800">Create a draft</h2>
        <p className="mt-1 text-sm text-surface-500">Choose a starting structure.</p>
        <div className="mt-5 divide-y divide-surface-200 border-y border-surface-200">
          {types.map((type) => (
            <button
              key={type}
              onClick={() => createDeliverable(workspaceId, type)}
              className="group flex w-full items-center gap-3 py-3 text-left"
            >
              <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-surface-100 text-surface-500">
                <FileText className="h-4 w-4" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-surface-700">{TYPE_LABELS[type]}</span>
                <span className="mt-0.5 block text-xs text-surface-400">{TYPE_DESCRIPTIONS[type]}</span>
              </span>
              <ArrowRight className="h-4 w-4 flex-shrink-0 text-surface-300 transition-transform group-hover:translate-x-0.5 group-hover:text-surface-500" aria-hidden="true" />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

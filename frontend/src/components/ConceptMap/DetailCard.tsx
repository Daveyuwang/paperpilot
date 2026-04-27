import clsx from "clsx";
import { BookOpen, ExternalLink } from "lucide-react";
import type { ConceptEdge, ConceptNode, ConceptNodeType } from "@/types";

const TYPE_BADGE: Record<ConceptNodeType, string> = {
  Problem: "bg-red-50 text-red-700 border-red-200",
  Method: "bg-blue-50 text-blue-700 border-blue-200",
  Component: "bg-indigo-50 text-indigo-700 border-indigo-200",
  Baseline: "bg-slate-50 text-slate-700 border-slate-200",
  Dataset: "bg-emerald-50 text-emerald-700 border-emerald-200",
  Metric: "bg-amber-50 text-amber-700 border-amber-200",
  Finding: "bg-violet-50 text-violet-700 border-violet-200",
  Limitation: "bg-orange-50 text-orange-700 border-orange-200",
};

function formatRelationChip(relation: string): string {
  return relation.replace(/_/g, " ");
}

export function DetailCard({
  node,
  edges,
  allNodes,
  onExplain,
  onShowInPaper,
  onSelectNode,
}: {
  node: ConceptNode;
  edges: ConceptEdge[];
  allNodes: ConceptNode[];
  onExplain: (label: string) => void;
  onShowInPaper: (page: number) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const nodeById = Object.fromEntries(allNodes.map((candidate) => [candidate.id, candidate]));
  const directRelations = edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .slice(0, 6);
  const sentence = (node.short_description ?? "").split(/(?<=[.!?])\s+/)[0] ?? "";

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-surface-200 px-4 py-4">
        <h3 className="text-sm font-semibold text-surface-800 leading-snug">{node.label}</h3>
        <span
          className={clsx(
            "mt-2 inline-flex rounded-md border px-2 py-1 text-[10px] font-medium",
            TYPE_BADGE[node.type] ?? "bg-surface-100 text-surface-500 border-surface-200"
          )}
        >
          {node.type}
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-4">
        {sentence && (
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-surface-500">
              Summary
            </p>
            <p className="mt-2 text-xs leading-relaxed text-surface-600">{sentence}</p>
          </div>
        )}

        {node.evidence.length > 0 && (
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-surface-500">
              Evidence
            </p>
            <blockquote className="mt-2 border-l-2 border-surface-300 pl-3 text-xs italic leading-relaxed text-surface-500">
              "{node.evidence[0]}"
            </blockquote>
          </div>
        )}

        <div>
          <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-surface-500">
            Direct relations
          </p>
          <div className="mt-2 space-y-1.5">
            {directRelations.length > 0 ? (
              directRelations.map((edge, index) => {
                const isSource = edge.source === node.id;
                const otherId = isSource ? edge.target : edge.source;
                const other = nodeById[otherId];
                if (!other) return null;

                return (
                  <button
                    key={`${edge.relation}-${otherId}-${index}`}
                    onClick={() => onSelectNode(otherId)}
                    className="flex w-full items-center gap-2 rounded-lg border border-surface-200 bg-surface-50 px-2.5 py-2 text-left transition-colors hover:bg-surface-100"
                    title={other.label}
                  >
                    <span className="rounded-full border border-surface-200 bg-surface-100 px-2 py-0.5 text-[10px] text-surface-500">
                      {formatRelationChip(edge.relation)}
                    </span>
                    <span className="truncate text-xs text-surface-700">{other.label}</span>
                  </button>
                );
              })
            ) : (
              <p className="text-xs text-surface-400">No direct relations recorded.</p>
            )}
          </div>
        </div>
      </div>

      <div className="shrink-0 border-t border-surface-200 px-4 py-4 space-y-2 bg-white">
        <button
          onClick={() => onExplain(node.label)}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-accent-200 bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 transition-colors hover:bg-accent-100"
        >
          <BookOpen className="h-3.5 w-3.5" />
          Explain this concept
        </button>
        {node.page != null && (
          <button
            onClick={() => onShowInPaper(node.page!)}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-surface-200 bg-surface-50 px-3 py-2 text-xs font-medium text-surface-600 transition-colors hover:bg-surface-100"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Show in paper (p.{node.page})
          </button>
        )}
      </div>
    </div>
  );
}

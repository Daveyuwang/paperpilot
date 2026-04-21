import { useState } from "react";
import { Library, Search, Check } from "lucide-react";
import clsx from "clsx";
import { useSourceStore } from "@/store/sourceStore";
import type { WorkspaceSource } from "@/types";

interface Props {
  workspaceId: string;
  onFillInput?: (text: string) => void;
}

export function MiniSourcesView({ workspaceId, onFillInput }: Props) {
  const { getSources, getIncludedSources, setIncluded } = useSourceStore();
  const sources = getSources(workspaceId);
  const includedSources = getIncludedSources(workspaceId);
  const [search, setSearch] = useState("");

  const filtered = search.trim()
    ? sources.filter((s) =>
        s.title.toLowerCase().includes(search.toLowerCase()) ||
        (s.authors ?? []).join(" ").toLowerCase().includes(search.toLowerCase())
      )
    : sources;

  if (sources.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-5 py-8 text-center">
        <div className="w-10 h-10 rounded-full bg-surface-100 flex items-center justify-center mb-3">
          <Library className="w-5 h-5 text-surface-400" />
        </div>
        <p className="text-xs font-medium text-surface-600 mb-1">No sources yet</p>
        <p className="text-[10px] text-surface-400 leading-relaxed">
          Upload papers in the Reader, or run Deep Research to automatically discover and add sources.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search + count */}
      <div className="flex-shrink-0 px-3 py-2 border-b border-surface-200">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-surface-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter sources…"
            className="w-full text-xs bg-surface-50 border border-surface-200 rounded-md pl-6 pr-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-accent-300"
          />
        </div>
        <div className="flex items-center justify-between mt-1.5">
          <span className="text-[10px] text-surface-400">
            {includedSources.length} of {sources.length} included
          </span>
        </div>
      </div>

      {/* Source list */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {filtered.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            isIncluded={includedSources.some((s) => s.id === source.id)}
            onToggle={() => {
              const isInc = includedSources.some((s) => s.id === source.id);
              setIncluded(workspaceId, source.id, !isInc);
            }}
            onClick={() => {
              onFillInput?.(`Tell me about "${source.title}"`);
            }}
          />
        ))}
        {filtered.length === 0 && (
          <p className="text-[10px] text-surface-400 text-center py-4">No matches</p>
        )}
      </div>
    </div>
  );
}

function SourceRow({
  source,
  isIncluded,
  onToggle,
  onClick,
}: {
  source: WorkspaceSource;
  isIncluded: boolean;
  onToggle: () => void;
  onClick: () => void;
}) {
  return (
    <div
      className="group flex items-start gap-2 px-2.5 py-2 rounded-lg hover:bg-surface-50 cursor-pointer transition-colors text-xs"
      onClick={onClick}
    >
      <button
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        className={clsx(
          "mt-0.5 w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition-colors",
          isIncluded
            ? "bg-accent-500 border-accent-500 text-white"
            : "border-surface-300 hover:border-accent-400"
        )}
      >
        {isIncluded && <Check className="w-2.5 h-2.5" />}
      </button>
      <div className="flex-1 min-w-0">
        <div className="truncate font-medium text-surface-700 leading-snug">{source.title}</div>
        <div className="text-[10px] text-surface-400 mt-0.5 truncate">
          {source.authors?.length > 0 && <span>{source.authors[0]}{source.authors.length > 1 ? " et al." : ""}</span>}
          {source.year && <span> · {source.year}</span>}
          {source.label && (
            <span className={clsx(
              "ml-1 px-1 py-0 rounded text-[9px]",
              source.label === "core" && "bg-amber-100 text-amber-700",
              source.label === "background" && "bg-blue-100 text-blue-700",
              source.label === "general" && "bg-surface-100 text-surface-600",
            )}>
              {source.label}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

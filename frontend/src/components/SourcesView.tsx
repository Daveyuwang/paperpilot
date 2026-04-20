import { useState, useCallback } from "react";
import { Search, Plus, Check, ExternalLink, BookOpen, Tag, Trash2, ChevronDown, ChevronRight, Loader2, CheckCircle2, XCircle, Library } from "lucide-react";
import clsx from "clsx";
import { useSourceStore } from "@/store/sourceStore";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { api } from "@/api/client";
import type { WorkspaceSource, SourceLabel, DiscoveredSource } from "@/types";

const LABEL_CONFIG: Record<SourceLabel, { color: string; bg: string; label: string }> = {
  core:       { color: "text-blue-700",    bg: "bg-blue-50 border-blue-200",    label: "Core" },
  background: { color: "text-amber-700",   bg: "bg-amber-50 border-amber-200",  label: "Background" },
  general:    { color: "text-surface-500", bg: "bg-surface-50 border-surface-200", label: "General" },
};

const LABELS: SourceLabel[] = ["core", "background", "general"];

export function SourcesView() {
  const { getSources, getIncludedSources, getByLabel, setAllIncluded } = useSourceStore();
  const { activePaper } = usePaperStore();
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";

  const sources = getSources(wid);

  const [query, setQuery] = useState(activePaper?.title ?? "");
  const [results, setResults] = useState<DiscoveredSource[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [filter, setFilter] = useState<"all" | "included" | "excluded">("all");

  const handleSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearched(false);
    try {
      const resp = await api.discoverSources(q);
      setResults(resp.results);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
      setSearched(true);
    }
  }, [query]);

  const filteredSources = sources.filter((s) => {
    if (filter === "included") return s.included;
    if (filter === "excluded") return !s.included;
    return true;
  });
  const includedCount = sources.filter((s) => s.included).length;
  const excludedCount = sources.filter((s) => !s.included).length;
  const coreCount = getByLabel(wid, "core").length;
  const bgCount = getByLabel(wid, "background").length;
  const genCount = getByLabel(wid, "general").length;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Workspace Sources */}
        <section>
          <h3 className="heading-serif text-sm text-surface-700 mb-3">Workspace Sources</h3>

          {/* Label summary */}
          {sources.length > 0 && (
            <div className="flex items-center gap-3 mb-3 text-[11px]">
              <span className="text-blue-600">{coreCount} core</span>
              <span className="text-amber-600">{bgCount} background</span>
              <span className="text-surface-500">{genCount} general</span>
            </div>
          )}

          {/* Filter tabs + batch actions */}
          {sources.length > 0 && (
            <div className="flex items-center gap-1 mb-3">
              <FilterTab active={filter === "all"} onClick={() => setFilter("all")} label="All" count={sources.length} />
              <FilterTab active={filter === "included"} onClick={() => setFilter("included")} label="Included" count={includedCount} />
              <FilterTab active={filter === "excluded"} onClick={() => setFilter("excluded")} label="Excluded" count={excludedCount} />
              <div className="ml-auto flex items-center gap-1">
                <button
                  onClick={() => setAllIncluded(wid, true)}
                  disabled={includedCount === sources.length}
                  className="p-1 rounded text-emerald-500 hover:bg-emerald-50 disabled:opacity-30 disabled:cursor-not-allowed"
                  title="Include all"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setAllIncluded(wid, false)}
                  disabled={excludedCount === sources.length}
                  className="p-1 rounded text-surface-400 hover:bg-surface-100 disabled:opacity-30 disabled:cursor-not-allowed"
                  title="Exclude all"
                >
                  <XCircle className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {filteredSources.length === 0 && sources.length === 0 ? (
            <div className="flex flex-col items-center py-8 text-center">
              <Library className="w-8 h-8 text-surface-200 mb-3" />
              <p className="text-xs text-surface-500 font-medium mb-1">No sources yet</p>
              <p className="text-[11px] text-surface-400 max-w-[200px]">
                Use the Console to discover papers, or search below to find related work.
              </p>
            </div>
          ) : filteredSources.length === 0 ? (
            <p className="text-xs text-surface-400">No {filter} sources.</p>
          ) : (
            <div className="space-y-1.5">
              {filteredSources.map((s) => (
                <SourceRow key={s.id} source={s} workspaceId={wid} />
              ))}
            </div>
          )}
        </section>

        {/* Discover Related Work */}
        <section>
          <button
            onClick={() => setDiscoverOpen(!discoverOpen)}
            className="flex items-center gap-1.5 heading-serif text-sm text-surface-700 mb-3 hover:text-surface-900 transition-colors"
          >
            {discoverOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            Discover Related Work
          </button>

          {discoverOpen && (
            <div className="space-y-3">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-surface-400" />
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                    placeholder="Search by topic, title, or keywords..."
                    className="w-full pl-8 pr-3 py-2 text-xs border border-surface-200 rounded-lg bg-white focus:outline-none focus:ring-1 focus:ring-accent-500 focus:border-accent-500"
                  />
                </div>
                <button
                  onClick={handleSearch}
                  disabled={searching || !query.trim()}
                  className="px-3 py-2 text-xs font-medium rounded-lg bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
                >
                  {searching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  Search
                </button>
              </div>

              {/* Results */}
              {searching && (
                <div className="flex items-center justify-center py-8 text-surface-400">
                  <Loader2 className="w-5 h-5 animate-spin mr-2" />
                  <span className="text-xs">Searching OpenAlex and arXiv...</span>
                </div>
              )}

              {!searching && searched && results.length === 0 && (
                <p className="text-xs text-surface-400 text-center py-4">No results found. Try different keywords.</p>
              )}

              {!searching && results.length > 0 && (
                <div className="space-y-1.5">
                  {results.map((r, i) => (
                    <DiscoveryRow key={`${r.external_id}-${i}`} result={r} workspaceId={wid} />
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function SourceRow({ source, workspaceId }: { source: WorkspaceSource; workspaceId: string }) {
  const { setLabel, removeSource, setIncluded } = useSourceStore();
  const { setActiveViewerTab } = useWorkspaceStore();
  const { selectPaper } = usePaperStore();
  const [labelOpen, setLabelOpen] = useState(false);
  const cfg = LABEL_CONFIG[source.label];

  const handleOpenInReader = useCallback(async () => {
    if (!source.paper_id) return;
    setActiveViewerTab("reader");
    await selectPaper(source.paper_id);
  }, [source.paper_id, setActiveViewerTab, selectPaper]);

  return (
    <div className={clsx(
      "group flex items-start gap-2 px-3 py-2 rounded-lg border border-surface-100 bg-white hover:border-surface-200 transition-colors",
      !source.included && "opacity-50"
    )}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-surface-800 truncate">{source.title}</span>
          <span className={clsx("shrink-0 text-[10px] px-1.5 py-0.5 rounded border", cfg.bg, cfg.color)}>
            {cfg.label}
          </span>
        </div>
        {source.authors.length > 0 && (
          <p className="text-[11px] text-surface-400 truncate mt-0.5">
            {source.authors.slice(0, 3).join(", ")}{source.authors.length > 3 ? " et al." : ""}
          </p>
        )}
        <div className="flex items-center gap-2 mt-0.5">
          {source.year && <span className="text-[10px] text-surface-400">{source.year}</span>}
          {source.provider !== "upload" && (
            <span className="text-[10px] text-surface-300">{source.provider}</span>
          )}
          {source.provider === "upload" && (
            <span className="text-[10px] text-accent-500">uploaded</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1 shrink-0">
        {/* Include/exclude toggle — always visible */}
        <button
          onClick={() => setIncluded(workspaceId, source.id, !source.included)}
          className={clsx(
            "p-1 rounded transition-colors",
            source.included
              ? "text-emerald-500 bg-emerald-50 hover:bg-emerald-100"
              : "text-surface-300 bg-surface-50 hover:bg-surface-100"
          )}
          title={source.included ? "Exclude from research" : "Include in research"}
        >
          <Check className="w-3.5 h-3.5" />
        </button>
        {/* Secondary actions — hover only */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {source.paper_id && (
            <button onClick={handleOpenInReader} className="p-1 rounded hover:bg-surface-100" title="Open in Reader">
              <BookOpen className="w-3.5 h-3.5 text-surface-500" />
            </button>
          )}
          {source.url && !source.paper_id && (
            <a href={source.url} target="_blank" rel="noopener noreferrer" className="p-1 rounded hover:bg-surface-100" title="Open link">
              <ExternalLink className="w-3.5 h-3.5 text-surface-500" />
            </a>
          )}
          <div className="relative">
            <button onClick={() => setLabelOpen(!labelOpen)} className="p-1 rounded hover:bg-surface-100" title="Change label">
              <Tag className="w-3.5 h-3.5 text-surface-500" />
            </button>
            {labelOpen && (
              <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-surface-200 rounded-lg shadow-lg py-1 min-w-[100px]">
                {LABELS.map((l) => (
                  <button
                    key={l}
                    onClick={() => { setLabel(workspaceId, source.id, l); setLabelOpen(false); }}
                    className={clsx(
                      "w-full text-left px-3 py-1.5 text-xs hover:bg-surface-50 transition-colors",
                      source.label === l ? "font-medium text-accent-600" : "text-surface-600"
                    )}
                  >
                    {LABEL_CONFIG[l].label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button onClick={() => removeSource(workspaceId, source.id)} className="p-1 rounded hover:bg-red-50" title="Remove">
            <Trash2 className="w-3.5 h-3.5 text-surface-400 hover:text-red-500" />
          </button>
        </div>
      </div>
    </div>
  );
}

function DiscoveryRow({ result, workspaceId }: { result: DiscoveredSource; workspaceId: string }) {
  const { addFromDiscovery, isDuplicate } = useSourceStore();
  const alreadySaved = isDuplicate(workspaceId, result);
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="px-3 py-2 rounded-lg border border-surface-100 bg-white hover:border-surface-200 transition-colors">
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpanded(!expanded)}>
          <p className="text-xs font-medium text-surface-800 leading-snug">{result.title}</p>
          <div className="flex items-center gap-2 mt-0.5">
            {result.authors.length > 0 && (
              <span className="text-[11px] text-surface-400 truncate">
                {result.authors.slice(0, 2).join(", ")}{result.authors.length > 2 ? " et al." : ""}
              </span>
            )}
            {result.year && <span className="text-[10px] text-surface-400">{result.year}</span>}
            <span className="text-[10px] text-surface-300">{result.provider}</span>
            {result.citation_count != null && result.citation_count > 0 && (
              <span className="text-[10px] text-surface-400">{result.citation_count} cites</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {result.url && (
            <a href={result.url} target="_blank" rel="noopener noreferrer" className="p-1 rounded hover:bg-surface-100">
              <ExternalLink className="w-3.5 h-3.5 text-surface-400" />
            </a>
          )}
          {alreadySaved ? (
            <span className="p-1" title="Already saved">
              <Check className="w-3.5 h-3.5 text-emerald-500" />
            </span>
          ) : (
            <button
              onClick={() => addFromDiscovery(workspaceId, result)}
              className="p-1 rounded hover:bg-accent-50"
              title="Save to sources"
            >
              <Plus className="w-3.5 h-3.5 text-accent-600" />
            </button>
          )}
        </div>
      </div>
      {expanded && result.abstract && (
        <p className="text-[11px] text-surface-500 mt-2 leading-relaxed">{result.abstract}</p>
      )}
    </div>
  );
}

function FilterTab({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "px-2.5 py-1 text-[11px] rounded-md transition-colors",
        active
          ? "bg-accent-100 text-accent-700 font-medium"
          : "text-surface-500 hover:bg-surface-100"
      )}
    >
      {label} <span className="text-surface-400">({count})</span>
    </button>
  );
}

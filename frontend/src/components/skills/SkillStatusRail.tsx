import {
  CheckCircle2,
  Clipboard,
  ClipboardCheck,
  Clock3,
  Database,
  ExternalLink,
  HardDrive,
  Layers3,
} from "lucide-react";
import { useState } from "react";
import type { SkillsStatus } from "@/api/skills";

export interface RecentSkillPreview {
  id: string;
  query: string;
  flow: string;
  createdAt: number;
  skillNames: string[];
}

interface Props {
  status: SkillsStatus | null;
  recentPreviews: RecentSkillPreview[];
  onSelectPreview: (preview: RecentSkillPreview) => void;
  compact?: boolean;
}

function shortRevision(revision: string | null | undefined): string {
  return revision ? revision.slice(0, 7) : "Unavailable";
}

function formatBytes(bytes: number | undefined): string {
  const safeBytes = bytes ?? 0;
  if (safeBytes < 1024) return `${safeBytes} B`;
  if (safeBytes < 1024 * 1024) return `${(safeBytes / 1024).toFixed(1)} KB`;
  return `${(safeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceRevisionHref(sourceUrl: string, revision: string): string {
  return `${sourceUrl.replace(/\.git$/, "")}/tree/${revision}`;
}

function flowLabel(flow: string): string {
  const labels: Record<string, string> = {
    deep_research: "Deep Research",
    paper_qa: "Paper Q&A",
    console: "Console",
    proposal_plan: "Proposal & Plan",
  };
  return labels[flow] ?? flow;
}

function PreviewHistory({
  previews,
  onSelect,
}: {
  previews: RecentSkillPreview[];
  onSelect: (preview: RecentSkillPreview) => void;
}) {
  if (previews.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-surface-300 px-3 py-4 text-center">
        <Clock3 className="mx-auto h-4 w-4 text-surface-400" aria-hidden="true" />
        <p className="mt-2 text-xs font-medium text-surface-600">No previews yet</p>
        <p className="mt-1 text-2xs leading-4 text-surface-600">
          Route a task to build session history.
        </p>
      </div>
    );
  }

  return (
    <ol className="space-y-1">
      {previews.map((preview, index) => (
        <li key={preview.id}>
          <button
            type="button"
            onClick={() => onSelect(preview)}
            className="group w-full border-l-2 border-transparent px-3 py-2.5 text-left transition-colors hover:border-accent-500 hover:bg-surface-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-400"
          >
            <span className="line-clamp-2 text-xs font-medium leading-5 text-surface-700">
              {preview.query}
            </span>
            <span className="mt-1 flex items-center gap-1.5 text-2xs text-surface-600">
              <span>{flowLabel(preview.flow)}</span>
              <span aria-hidden="true">·</span>
              <time dateTime={new Date(preview.createdAt).toISOString()}>
                {new Intl.DateTimeFormat(undefined, {
                  hour: "numeric",
                  minute: "2-digit",
                }).format(preview.createdAt)}
              </time>
            </span>
            {preview.skillNames.length > 0 && (
              <span className="mt-2 flex flex-wrap gap-1">
                {preview.skillNames.slice(0, 2).map((name) => (
                  <span
                    key={name}
                    className="max-w-full truncate rounded border border-surface-200 bg-white px-1.5 py-0.5 text-2xs text-surface-600"
                  >
                    {name}
                  </span>
                ))}
              </span>
            )}
            <span className="sr-only">
              {index === 0 ? "Most recent preview." : ""} Open this routing preview.
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function SkillStatusRail({
  status,
  recentPreviews,
  onSelectPreview,
  compact = false,
}: Props) {
  const [copied, setCopied] = useState(false);
  const revision = status?.revision;
  const loaded = status?.loaded_count ?? 0;
  const cachedEntries = status?.cache_entry_count ?? loaded;
  const maxEntries = status?.cache_max_entries ?? 0;

  const copyRevision = async () => {
    if (!revision) return;
    try {
      await navigator.clipboard.writeText(revision);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  if (compact) {
    return (
      <section
        aria-label="Skill loader status"
        className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-surface-200 bg-surface-200 sm:grid-cols-4 min-[1180px]:hidden"
      >
        <div className="bg-white px-3 py-3">
          <span className="text-2xs uppercase tracking-wide text-surface-600">Catalog</span>
          <p className="mt-1 text-sm font-semibold text-surface-700">
            {status?.available_count ?? 0} available
          </p>
        </div>
        <div className="bg-white px-3 py-3">
          <span className="text-2xs uppercase tracking-wide text-surface-600">Worker bodies</span>
          <p className="mt-1 text-sm font-semibold text-surface-700">{loaded} bodies</p>
        </div>
        <div className="bg-white px-3 py-3">
          <span className="text-2xs uppercase tracking-wide text-surface-600">Worker cache</span>
          <p className="mt-1 text-sm font-semibold text-surface-700">
            {cachedEntries} / {maxEntries || "—"}
          </p>
        </div>
        <div className="bg-white px-3 py-3">
          <span className="text-2xs uppercase tracking-wide text-surface-600">Revision</span>
          <p className="mt-1 font-mono text-sm font-medium text-surface-700">
            {shortRevision(revision)}
          </p>
        </div>
      </section>
    );
  }

  return (
    <aside className="hidden h-full w-64 flex-shrink-0 flex-col overflow-y-auto border-l border-surface-200 bg-surface-50 min-[1180px]:flex">
      <section
        className="border-b border-surface-200 px-5 py-5"
        aria-labelledby="session-status-title"
        aria-live="polite"
      >
        <div className="flex items-center justify-between">
          <h2 id="session-status-title" className="text-xs font-semibold text-surface-800">
            Session status
          </h2>
          <span
            className={`inline-flex items-center gap-1 text-2xs font-medium ${
              status?.state === "ready" ? "text-emerald-700" : "text-amber-700"
            }`}
          >
            <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
            {status?.state ?? "loading"}
          </span>
        </div>

        <dl className="mt-5 space-y-4 text-xs">
          <div className="flex items-center justify-between gap-3">
            <dt className="flex items-center gap-2 text-surface-600">
              <Layers3 className="h-3.5 w-3.5" aria-hidden="true" />
              Available
            </dt>
            <dd className="font-semibold text-surface-800">{status?.available_count ?? 0}</dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="flex items-center gap-2 text-surface-600">
              <Database className="h-3.5 w-3.5" aria-hidden="true" />
              Worker bodies
            </dt>
            <dd className="font-semibold text-accent-600">{loaded}</dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="flex items-center gap-2 text-surface-600">
              <HardDrive className="h-3.5 w-3.5" aria-hidden="true" />
              Worker cache
            </dt>
            <dd className="font-medium text-surface-800">
              {cachedEntries} / {maxEntries || "—"}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-surface-600">Worker references</dt>
            <dd className="font-medium text-surface-800">
              {status?.loaded_reference_count ?? 0}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-surface-600">Worker memory</dt>
            <dd className="font-medium text-surface-800">
              {formatBytes(status?.cache_total_bytes ?? status?.loaded_bytes)}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-surface-600">Source revision</dt>
            <dd className="flex items-center gap-1.5 font-mono font-medium text-surface-800">
              {shortRevision(revision)}
              <button
                type="button"
                onClick={copyRevision}
                disabled={!revision}
                aria-label={copied ? "Revision copied" : "Copy source revision"}
                className="rounded p-1 text-surface-400 transition-colors hover:bg-surface-100 hover:text-surface-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {copied ? (
                  <ClipboardCheck className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <Clipboard className="h-3.5 w-3.5" aria-hidden="true" />
                )}
              </button>
            </dd>
          </div>
        </dl>
      </section>

      <section className="flex-1 px-2 py-5" aria-labelledby="recent-previews-title">
        <h2 id="recent-previews-title" className="px-3 text-xs font-semibold text-surface-800">
          Recent routing previews
        </h2>
        <div className="mt-3">
          <PreviewHistory previews={recentPreviews} onSelect={onSelectPreview} />
        </div>
      </section>

      <section className="border-t border-surface-200 px-5 py-4 text-2xs leading-5 text-surface-600">
        <p>
          Previews use catalog metadata only. Skill bodies load lazily when matching agent
          execution starts. Cache metrics are process-local.
        </p>
        {status?.source_url && revision && (
          <a
            href={sourceRevisionHref(status.source_url, revision)}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-flex items-center gap-1 font-medium text-accent-600 hover:text-accent-700"
          >
            View source
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
        )}
      </section>
    </aside>
  );
}

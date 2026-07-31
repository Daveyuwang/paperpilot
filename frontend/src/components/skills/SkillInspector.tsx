import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileCode2,
  Loader2,
  LockKeyhole,
  Package,
  Braces,
  BookOpenText,
  Database,
  RotateCw,
  ShieldCheck,
  Tag,
  X,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type { SkillDetailResponse } from "@/api/skills";

interface Props {
  skillName: string | null;
  detail: SkillDetailResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
}

function sourceHref(
  sourceUrl: string | undefined,
  revision: string | null | undefined,
  path: string,
): string | undefined {
  if (!sourceUrl || !revision || !path) return undefined;
  return `${sourceUrl.replace(/\.git$/, "")}/blob/${revision}/${path}`;
}

function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined) return "Not reported";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function SkillInspector({
  skillName,
  detail,
  loading,
  error,
  onClose,
  onRetry,
}: Props) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!skillName) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab" || !panelRef.current) return;

      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyboard);
    return () => {
      window.removeEventListener("keydown", handleKeyboard);
      previousFocus?.focus();
    };
  }, [skillName, onClose]);

  if (!skillName) return null;

  const sourceLink = detail
    ? sourceHref(detail.source_url, detail.source_revision, detail.source_path)
    : undefined;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button
        type="button"
        aria-label="Close skill inspector"
        onClick={onClose}
        className="absolute inset-0 bg-surface-900/20 backdrop-blur-[1px]"
      />
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-inspector-title"
        aria-busy={loading}
        className="relative flex h-full w-full max-w-md flex-col border-l border-surface-200 bg-surface-50 shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-surface-200 px-5 py-5">
          <div className="min-w-0">
            <p className="text-2xs font-semibold uppercase tracking-[0.14em] text-accent-600">
              Skill inspector
            </p>
            <h2
              id="skill-inspector-title"
              className="heading-serif mt-1 break-words text-xl text-surface-900"
            >
              {skillName}
            </h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close inspector"
            className="rounded-lg p-2 text-surface-400 transition-colors hover:bg-surface-100 hover:text-surface-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {loading && (
            <div
              className="flex h-64 flex-col items-center justify-center text-surface-600"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              <p className="mt-3 text-xs">Loading metadata…</p>
            </div>
          )}

          {!loading && error && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4" role="alert">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-600" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-red-800">Could not load this skill</p>
                  <p className="mt-1 text-xs leading-5 text-red-700">{error}</p>
                  <button
                    type="button"
                    onClick={onRetry}
                    className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
                  >
                    <RotateCw className="h-3 w-3" aria-hidden="true" />
                    Retry
                  </button>
                </div>
              </div>
            </div>
          )}

          {!loading && detail && (
            <div className="space-y-6">
              <div
                className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 text-xs font-medium ${
                  detail.availability === "available"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-red-200 bg-red-50 text-red-800"
                }`}
              >
                {detail.availability === "available" ? (
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <LockKeyhole className="h-4 w-4" aria-hidden="true" />
                )}
                {detail.availability === "available"
                  ? "Available for metadata routing"
                  : "Blocked by PaperPilot policy"}
              </div>

              <div>
                <h3 className="text-xs font-semibold text-surface-800">Capability</h3>
                <p className="mt-2 text-sm leading-6 text-surface-600">{detail.description}</p>
              </div>

              {detail.blocked_reason && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3">
                  <p className="text-xs font-semibold text-red-800">Why it is blocked</p>
                  <p className="mt-1 text-xs leading-5 text-red-700">{detail.blocked_reason}</p>
                </div>
              )}

              <dl className="divide-y divide-surface-200 overflow-hidden rounded-xl border border-surface-200 bg-white text-xs">
                <div className="flex items-start gap-3 px-3 py-3">
                  <Package className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">Version</dt>
                  <dd className="min-w-0 break-words font-medium text-surface-700">
                    {detail.version || "Not specified"}
                  </dd>
                </div>
                <div className="flex items-start gap-3 px-3 py-3">
                  <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">Author</dt>
                  <dd className="min-w-0 break-words font-medium text-surface-700">
                    {detail.author || "Upstream contributor"}
                  </dd>
                </div>
                <div className="flex items-start gap-3 px-3 py-3">
                  <Tag className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">Category</dt>
                  <dd className="min-w-0 break-words font-medium text-surface-700">
                    {detail.category || "Uncategorized"}
                  </dd>
                </div>
                <div className="flex items-start gap-3 px-3 py-3">
                  <FileCode2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">Source</dt>
                  <dd className="min-w-0 break-all font-mono text-2xs text-surface-600">
                    {detail.source_path}
                  </dd>
                </div>
                <div className="flex items-start gap-3 px-3 py-3">
                  <Braces className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">Source body</dt>
                  <dd className="min-w-0 break-words font-medium text-surface-700">
                    {detail.body_chars === undefined
                      ? "Measured on first load"
                      : `${detail.body_chars.toLocaleString()} characters`}
                  </dd>
                </div>
                <div className="flex items-start gap-3 px-3 py-3">
                  <BookOpenText className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                  <dt className="w-20 flex-shrink-0 text-surface-600">References</dt>
                  <dd className="min-w-0 break-words font-medium text-surface-700">
                    {detail.reference_count === undefined
                      ? "Not reported"
                      : detail.reference_count.toLocaleString()}
                    {detail.byte_size !== undefined && (
                      <span className="ml-2 font-normal text-surface-600">
                        · {formatBytes(detail.byte_size)} source
                      </span>
                    )}
                  </dd>
                </div>
              </dl>

              {detail.tags.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-surface-800">Routing tags</h3>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {detail.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-md border border-surface-200 bg-white px-2 py-1 text-2xs text-surface-600"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="rounded-xl border border-accent-100 bg-accent-50 px-4 py-3">
                <p className="flex items-center gap-2 text-xs font-semibold text-accent-700">
                  {detail.loaded && detail.availability === "available" ? (
                    <Database className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <LockKeyhole className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                  {detail.availability === "blocked"
                    ? "Body is policy blocked"
                    : detail.loaded
                      ? "Body cached by an earlier execution"
                      : "Body remains unloaded"}
                </p>
                <p className="mt-1.5 text-xs leading-5 text-surface-600">
                  This inspector exposes validated metadata only.{" "}
                  {detail.availability === "blocked"
                    ? "The body is never loaded while this policy is active."
                    : detail.loaded
                      ? "The cached body is not returned by the metadata API."
                      : "The skill body is read from the pinned snapshot only when matching agent execution begins."}
                </p>
              </div>

              {sourceLink && (
                <a
                  href={sourceLink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-accent-600 hover:text-accent-700"
                >
                  Open upstream file
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                </a>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

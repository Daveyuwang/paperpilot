import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Blocks,
  Bot,
  Check,
  ChevronRight,
  CircleHelp,
  Cpu,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  Info,
  Loader2,
  LockKeyhole,
  Network,
  RotateCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  skillsApi,
  type SkillDetailResponse,
  type SkillMetadata,
  type SkillPreviewResponse,
  type SkillsStatus,
} from "@/api/skills";
import {
  SkillStatusRail,
  type RecentSkillPreview,
} from "@/components/skills/SkillStatusRail";
import { SkillInspector } from "@/components/skills/SkillInspector";
import { skillCatalogPollInterval } from "@/components/skills/polling";

const DEFAULT_QUERY = "Plan distributed training for a 70B parameter language model";
const PREVIEW_LIMIT = 3;
const PAGE_SIZE = 8;
const RECENT_PREVIEWS_KEY = "paperpilot.skill-routing-previews";

const WORKFLOWS = [
  { value: "deep_research", label: "Deep Research" },
  { value: "paper_qa", label: "Paper Q&A" },
  { value: "console", label: "Console" },
] as const;

function readRecentPreviews(): RecentSkillPreview[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(sessionStorage.getItem(RECENT_PREVIEWS_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value
      .filter(
        (item): item is RecentSkillPreview =>
          typeof item?.id === "string" &&
          typeof item?.query === "string" &&
          typeof item?.flow === "string" &&
          typeof item?.createdAt === "number" &&
          Array.isArray(item?.skillNames),
      )
      .slice(0, 6);
  } catch {
    return [];
  }
}

function friendlyCategory(category: string): string {
  if (!category) return "Uncategorized";
  return category
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function flowLabel(flow: string): string {
  return WORKFLOWS.find((item) => item.value === flow)?.label ?? flow;
}

function matchStrength(rank: number): string {
  if (rank === 1) return "Top match";
  if (rank === 2) return "Strong match";
  return "Relevant";
}

function formatContextChars(chars: number | undefined): string {
  if (chars === undefined) return "Unknown";
  if (chars < 1_000) return `${chars} chars`;
  return `${(chars / 1_000).toFixed(1)}K chars`;
}

function shortRevision(revision: string | null | undefined): string {
  return revision ? revision.slice(0, 7) : "pending";
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred.";
}

function SkillIcon({ category, blocked = false }: { category: string; blocked?: boolean }) {
  const value = category.toLowerCase();
  let icon: ReactNode;
  if (blocked) icon = <LockKeyhole className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("distributed") || value.includes("system"))
    icon = <Network className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("data") || value.includes("retrieval"))
    icon = <Database className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("train") || value.includes("model"))
    icon = <Cpu className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("evaluat") || value.includes("benchmark"))
    icon = <Gauge className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("writ") || value.includes("paper"))
    icon = <FileText className="h-5 w-5" aria-hidden="true" />;
  else if (value.includes("research") || value.includes("experiment"))
    icon = <FlaskConical className="h-5 w-5" aria-hidden="true" />;
  else icon = <Bot className="h-5 w-5" aria-hidden="true" />;

  return (
    <span
      className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border ${
        blocked
          ? "border-red-200 bg-red-50 text-red-600"
          : "border-surface-200 bg-white text-surface-600"
      }`}
    >
      {icon}
    </span>
  );
}

function PageSkeleton() {
  return (
    <div className="space-y-5" aria-label="Loading skills" role="status">
      <div className="h-24 animate-pulse rounded-xl border border-surface-200 bg-white" />
      <div className="grid gap-3 xl:grid-cols-2">
        <div className="h-44 animate-pulse rounded-xl border border-surface-200 bg-white" />
        <div className="h-44 animate-pulse rounded-xl border border-surface-200 bg-white" />
      </div>
      <div className="h-72 animate-pulse rounded-xl border border-surface-200 bg-white" />
      <span className="sr-only">Loading the agent skill catalog.</span>
    </div>
  );
}

function CatalogError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-h-[360px] items-center justify-center">
      <div className="max-w-md rounded-xl border border-red-200 bg-red-50 px-6 py-7 text-center">
        <AlertCircle className="mx-auto h-6 w-6 text-red-600" aria-hidden="true" />
        <h2 className="heading-serif mt-3 text-lg text-red-900">Skill catalog unavailable</h2>
        <p className="mt-2 text-xs leading-5 text-red-700">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-medium text-red-700 transition-colors hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
          Retry catalog
        </button>
      </div>
    </div>
  );
}

interface RoutingPreviewProps {
  preview: SkillPreviewResponse | null;
  loading: boolean;
  error: string | null;
  availableCount: number;
  onInspect: (name: string) => void;
  onRetry: () => void;
}

function RoutingPreview({
  preview,
  loading,
  error,
  availableCount,
  onInspect,
  onRetry,
}: RoutingPreviewProps) {
  if (loading) {
    return (
      <section aria-labelledby="routing-preview-title" role="status">
        <div className="flex items-center justify-between">
          <h2 id="routing-preview-title" className="heading-serif text-base text-surface-800">
            Predicting what would load
          </h2>
          <Loader2 className="h-4 w-4 animate-spin text-accent-600" aria-hidden="true" />
        </div>
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          {[0, 1].map((item) => (
            <div
              key={item}
              className="h-44 animate-pulse rounded-xl border border-surface-200 bg-white"
            />
          ))}
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section
        aria-labelledby="routing-preview-error-title"
        role="alert"
        className="rounded-xl border border-red-200 bg-red-50 px-4 py-4"
      >
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-600" aria-hidden="true" />
          <div>
            <h2 id="routing-preview-error-title" className="text-sm font-semibold text-red-800">
              Selection preview failed
            </h2>
            <p className="mt-1 text-xs leading-5 text-red-700">{error}</p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-red-700 underline underline-offset-2"
            >
              <RotateCw className="h-3 w-3" aria-hidden="true" />
              Try again
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (!preview) {
    return (
      <section
        aria-labelledby="routing-preview-title"
        className="rounded-xl border border-dashed border-surface-300 bg-white px-6 py-8 text-center"
      >
        <Sparkles className="mx-auto h-5 w-5 text-surface-400" aria-hidden="true" />
        <h2 id="routing-preview-title" className="heading-serif mt-3 text-base text-surface-800">
          Preview a task before running it
        </h2>
        <p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-surface-600">
          PaperPilot will search catalog metadata and show the strongest relevant skills. No
          skill body is read by a preview.
        </p>
      </section>
    );
  }

  const selected = preview.selected;
  const selectedContextChars = selected.every(
    (skill) => typeof skill.body_chars === "number",
  )
    ? selected.reduce((total, skill) => total + (skill.body_chars ?? 0), 0)
    : null;

  return (
    <section aria-labelledby="routing-preview-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="routing-preview-title" className="heading-serif text-base text-surface-800">
          Would load for this task
        </h2>
        <details className="group relative">
          <summary className="flex cursor-pointer list-none items-center gap-1 rounded-lg border border-surface-200 bg-white px-2.5 py-1.5 text-2xs font-medium text-surface-600 transition-colors hover:bg-surface-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400">
            Why these?
            <CircleHelp className="h-3.5 w-3.5" aria-hidden="true" />
          </summary>
          <div className="absolute right-0 z-20 mt-2 w-72 rounded-xl border border-surface-200 bg-white p-3 text-xs leading-5 text-surface-600 shadow-lg">
            PaperPilot ranks validated metadata against the task, applies the selected workflow’s
            eligibility rules, and returns only the strongest matches. Previewing never reads
            instruction bodies.
          </div>
        </details>
      </div>

      {selected.length === 0 ? (
        <div className="mt-3 rounded-xl border border-surface-200 bg-white px-6 py-6 text-center">
          <Check className="mx-auto h-5 w-5 text-emerald-600" aria-hidden="true" />
          <p className="mt-2 text-sm font-semibold text-surface-700">No skill needed</p>
          <p className="mt-1 text-xs text-surface-600">
            The base {flowLabel(preview.flow)} workflow is the best match for this task.
          </p>
        </div>
      ) : (
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          {selected.map((skill, index) => {
            return (
              <article
                key={skill.name}
                className="flex min-h-[196px] flex-col rounded-xl border border-surface-200 bg-white p-4 transition-shadow hover:shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <SkillIcon category={skill.category} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="heading-serif break-words text-sm leading-5 text-surface-800">
                        {skill.name}
                      </h3>
                      <span className="whitespace-nowrap text-xs font-semibold text-accent-600">
                        {matchStrength(skill.rank || index + 1)}
                      </span>
                    </div>
                    <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-surface-600">
                      {skill.description}
                    </p>
                  </div>
                </div>

                <div className="mt-auto border-t border-surface-200 pt-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-2xs">
                    <span className="text-surface-600">
                      Matched{" "}
                      <span className="font-medium text-surface-600">
                        {skill.matched_terms.slice(0, 3).join(", ") || "task intent"}
                      </span>
                    </span>
                    <span className="inline-flex items-center gap-1 font-medium text-surface-600">
                      {skill.loaded ? (
                        <Database className="h-3 w-3 text-emerald-600" aria-hidden="true" />
                      ) : (
                        <LockKeyhole className="h-3 w-3 text-accent-600" aria-hidden="true" />
                      )}
                      {skill.loaded ? "Already cached" : "Body not loaded"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => onInspect(skill.name)}
                    className="mt-2 inline-flex items-center gap-1 text-2xs font-medium text-accent-600 hover:text-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
                  >
                    Inspect metadata
                    <ChevronRight className="h-3 w-3" aria-hidden="true" />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
        <p className="font-medium text-surface-700">
          {selected.length} of {availableCount} available skills selected
          <span className="mx-2 text-surface-300" aria-hidden="true">
            ·
          </span>
          <span className="font-normal text-surface-600">
            {selected.length > 0 && selectedContextChars !== null
              ? `${formatContextChars(selectedContextChars)} source body size · `
              : ""}
            0 bodies loaded by preview
          </span>
        </p>
        <span className="font-mono text-2xs text-surface-600">
          source {shortRevision(preview.source_revision)}
        </span>
      </div>
    </section>
  );
}

interface MarketplaceProps {
  skills: SkillMetadata[];
  onInspect: (name: string) => void;
}

function SkillMarketplace({ skills, onInspect }: MarketplaceProps) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [page, setPage] = useState(0);

  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const skill of skills) {
      const value = skill.category || "uncategorized";
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [skills]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return skills.filter((skill) => {
      if (category !== "all" && (skill.category || "uncategorized") !== category) return false;
      if (!needle) return true;
      return [skill.name, skill.description, skill.category, ...skill.tags]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [category, search, skills]);

  useEffect(() => {
    setPage(0);
  }, [category, search]);

  const maxPage = Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1);
  const safePage = Math.min(page, maxPage);
  const visible = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  return (
    <section
      aria-labelledby="skill-marketplace-title"
      className="min-w-0 max-w-full overflow-hidden rounded-xl border border-surface-200 bg-white"
    >
      <div className="border-b border-surface-200 p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 id="skill-marketplace-title" className="heading-serif text-base text-surface-800">
              Skill marketplace
            </h2>
            <p className="mt-0.5 text-2xs text-surface-600">
              Browse validated metadata. Opening a skill does not load its prompt body.
            </p>
          </div>
          <div className="flex flex-col gap-2 lg:flex-row">
            <label className="relative block">
              <span className="sr-only">Search skills</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-surface-400"
                aria-hidden="true"
              />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search skills or capabilities"
                className="h-9 w-full rounded-lg border border-surface-200 bg-surface-50 pl-9 pr-3 text-xs text-surface-700 outline-none transition-shadow placeholder:text-surface-400 focus:border-accent-300 focus:ring-2 focus:ring-accent-100 lg:w-64"
              />
            </label>
            <label>
              <span className="sr-only">Filter by category</span>
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="h-9 w-full rounded-lg border border-surface-200 bg-surface-50 px-3 text-xs text-surface-600 outline-none focus:border-accent-300 focus:ring-2 focus:ring-accent-100 lg:w-44"
              >
                <option value="all">All categories</option>
                {categoryCounts.map(([value, count]) => (
                  <option key={value} value={value}>
                    {friendlyCategory(value)} ({count})
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <div
          className="mt-3 flex gap-1.5 overflow-x-auto pb-1"
          role="group"
          aria-label="Popular skill categories"
        >
          <button
            type="button"
            onClick={() => setCategory("all")}
            aria-pressed={category === "all"}
            className={`whitespace-nowrap rounded-md border px-2.5 py-1 text-2xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 ${
              category === "all"
                ? "border-accent-400 bg-accent-50 text-accent-700"
                : "border-surface-200 bg-surface-50 text-surface-600 hover:bg-surface-100"
            }`}
          >
            All <span className="ml-1 text-surface-600">{skills.length}</span>
          </button>
          {categoryCounts.slice(0, 6).map(([value, count]) => (
            <button
              key={value}
              type="button"
              onClick={() => setCategory(value)}
              aria-pressed={category === value}
              className={`whitespace-nowrap rounded-md border px-2.5 py-1 text-2xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 ${
                category === value
                  ? "border-accent-400 bg-accent-50 text-accent-700"
                  : "border-surface-200 bg-surface-50 text-surface-600 hover:bg-surface-100"
              }`}
            >
              {friendlyCategory(value)} <span className="ml-1 text-surface-600">{count}</span>
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="flex min-h-56 flex-col items-center justify-center px-4 text-center">
          <Search className="h-5 w-5 text-surface-300" aria-hidden="true" />
          <p className="mt-3 text-sm font-semibold text-surface-700">No matching skills</p>
          <p className="mt-1 text-xs text-surface-600">
            Try a broader capability or choose another category.
          </p>
          <button
            type="button"
            onClick={() => {
              setSearch("");
              setCategory("all");
            }}
            className="mt-3 text-xs font-medium text-accent-600 hover:text-accent-700"
          >
            Clear filters
          </button>
        </div>
      ) : (
        <>
          <ul className="divide-y divide-surface-200 min-[1360px]:hidden">
            {visible.map((skill) => {
              const blocked = skill.availability === "blocked";
              return (
                <li key={skill.name}>
                  <button
                    type="button"
                    onClick={() => onInspect(skill.name)}
                    className="flex w-full items-start gap-3 p-3 text-left transition-colors hover:bg-surface-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-400"
                  >
                    <SkillIcon category={skill.category} blocked={blocked} />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-start justify-between gap-2">
                        <span className="break-words text-xs font-medium leading-4 text-surface-800">
                          {skill.name}
                        </span>
                        <span
                          className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-2xs font-medium ${
                            blocked
                              ? "border-red-200 bg-red-50 text-red-700"
                              : "border-emerald-200 bg-emerald-50 text-emerald-700"
                          }`}
                        >
                          {blocked ? (
                            <LockKeyhole className="h-3 w-3" aria-hidden="true" />
                          ) : (
                            <Check className="h-3 w-3" aria-hidden="true" />
                          )}
                          {blocked ? "Blocked" : "Available"}
                        </span>
                      </span>
                      <span className="mt-1 block text-2xs text-surface-600">
                        {friendlyCategory(skill.category)} · {formatContextChars(skill.body_chars)}
                      </span>
                      <span className="mt-2 line-clamp-2 text-2xs leading-4 text-surface-600">
                        {skill.description}
                      </span>
                      <span className="mt-2 inline-flex items-center gap-1 text-2xs font-medium text-surface-600">
                        {skill.loaded && !blocked ? (
                          <Database className="h-3 w-3 text-emerald-600" aria-hidden="true" />
                        ) : (
                          <LockKeyhole
                            className={`h-3 w-3 ${blocked ? "text-red-600" : "text-accent-600"}`}
                            aria-hidden="true"
                          />
                        )}
                        {blocked ? "Policy blocked" : skill.loaded ? "Cached" : "Loads on demand"}
                      </span>
                    </span>
                    <ChevronRight
                      className="mt-2 h-4 w-4 flex-shrink-0 text-surface-500"
                      aria-hidden="true"
                    />
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="hidden w-full min-w-0 max-w-full overflow-x-auto min-[1360px]:block">
          <table className="w-full min-w-[860px] border-collapse text-left">
            <thead>
              <tr className="border-b border-surface-200 bg-surface-50 text-2xs font-medium text-surface-600">
                <th scope="col" className="px-3 py-2.5">
                  Skill
                </th>
                <th scope="col" className="px-3 py-2.5">
                  Category
                </th>
                <th scope="col" className="px-3 py-2.5">
                  Capability
                </th>
                <th scope="col" className="px-3 py-2.5">
                  Source body
                </th>
                <th scope="col" className="px-3 py-2.5">
                  Prompt body
                </th>
                <th scope="col" className="px-3 py-2.5">
                  Status
                </th>
                <th scope="col" className="w-10 px-2 py-2.5">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-200">
              {visible.map((skill) => {
                const blocked = skill.availability === "blocked";
                return (
                  <tr key={skill.name} className="group transition-colors hover:bg-surface-50">
                    <td className="px-3 py-3 align-top">
                      <div className="flex items-start gap-2.5">
                        <SkillIcon category={skill.category} blocked={blocked} />
                        <div className="min-w-0">
                          <button
                            type="button"
                            onClick={() => onInspect(skill.name)}
                            className="max-w-[230px] break-words text-left text-xs font-medium leading-4 text-surface-800 hover:text-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
                          >
                            {skill.name}
                          </button>
                          <p className="mt-1 text-2xs text-surface-600">
                            {skill.version || "Version not specified"} ·{" "}
                            {skill.author || "Upstream"}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <span className="inline-block max-w-36 rounded border border-surface-200 bg-surface-50 px-2 py-1 text-2xs text-surface-600">
                        {friendlyCategory(skill.category)}
                      </span>
                    </td>
                    <td className="max-w-xs px-3 py-3 align-top text-2xs leading-4 text-surface-600">
                      <span className="line-clamp-2">{skill.description}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 align-top text-2xs font-medium text-surface-600">
                      {formatContextChars(skill.body_chars)}
                    </td>
                    <td className="px-3 py-3 align-top">
                      <span className="inline-flex items-center gap-1 text-2xs font-medium text-surface-600">
                        {skill.loaded && !blocked ? (
                          <Database className="h-3 w-3 text-emerald-600" aria-hidden="true" />
                        ) : (
                          <LockKeyhole
                            className={`h-3 w-3 ${blocked ? "text-red-600" : "text-accent-600"}`}
                            aria-hidden="true"
                          />
                        )}
                        {blocked ? "Never loaded" : skill.loaded ? "Cached" : "On demand"}
                      </span>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <span
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-2xs font-medium ${
                          blocked
                            ? "border-red-200 bg-red-50 text-red-700"
                            : "border-emerald-200 bg-emerald-50 text-emerald-700"
                        }`}
                      >
                        {blocked ? (
                          <LockKeyhole className="h-3 w-3" aria-hidden="true" />
                        ) : (
                          <Check className="h-3 w-3" aria-hidden="true" />
                        )}
                        {blocked ? "Blocked" : "Available"}
                      </span>
                    </td>
                    <td className="px-2 py-3 align-top">
                      <button
                        type="button"
                        onClick={() => onInspect(skill.name)}
                        aria-label={`Inspect ${skill.name}`}
                        className="rounded-md p-1.5 text-surface-400 transition-colors hover:bg-surface-100 hover:text-surface-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
                      >
                        <ChevronRight className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}

      <footer className="flex items-center justify-between border-t border-surface-200 bg-surface-50 px-3 py-2.5 text-2xs text-surface-600">
        <span>
          {filtered.length === 0
            ? "0 skills"
            : `${safePage * PAGE_SIZE + 1}–${Math.min(
                (safePage + 1) * PAGE_SIZE,
                filtered.length,
              )} of ${filtered.length} skills`}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setPage((value) => Math.max(0, value - 1))}
            disabled={safePage === 0}
            aria-label="Previous skill page"
            className="rounded-md p-1.5 text-surface-500 hover:bg-surface-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <span className="min-w-16 text-center">
            Page {safePage + 1} of {maxPage + 1}
          </span>
          <button
            type="button"
            onClick={() => setPage((value) => Math.min(maxPage, value + 1))}
            disabled={safePage >= maxPage}
            aria-label="Next skill page"
            className="rounded-md p-1.5 text-surface-500 hover:bg-surface-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </footer>
    </section>
  );
}

export function SkillsPage() {
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [workflow, setWorkflow] = useState<(typeof WORKFLOWS)[number]["value"]>(
    "deep_research",
  );
  const [status, setStatus] = useState<SkillsStatus | null>(null);
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [preview, setPreview] = useState<SkillPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [recentPreviews, setRecentPreviews] = useState<RecentSkillPreview[]>(
    readRecentPreviews,
  );
  const [inspectedName, setInspectedName] = useState<string | null>(null);
  const [inspectedDetail, setInspectedDetail] = useState<SkillDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const initialPreviewStarted = useRef(false);
  const catalogRequestId = useRef(0);
  const catalogRefreshInFlight = useRef(false);
  const previewRequestId = useRef(0);
  const detailRequestId = useRef(0);

  const loadCatalog = useCallback(async (background = false) => {
    if (catalogRefreshInFlight.current) return;
    catalogRefreshInFlight.current = true;
    const requestId = ++catalogRequestId.current;
    if (!background) {
      setCatalogLoading(true);
      setCatalogError(null);
    }
    try {
      const catalog = await skillsApi.list();
      if (requestId !== catalogRequestId.current) return;
      const { skills: catalogSkills, ...catalogStatus } = catalog;
      setStatus(catalogStatus);
      setSkills(catalogSkills);
      setCatalogError(null);
    } catch (error) {
      if (requestId !== catalogRequestId.current) return;
      if (!background) setCatalogError(errorMessage(error));
    } finally {
      catalogRefreshInFlight.current = false;
      if (!background && requestId === catalogRequestId.current) {
        setCatalogLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    const intervalMs = skillCatalogPollInterval(status, catalogError !== null);
    if (intervalMs === null) return;
    const interval = window.setInterval(
      () => void loadCatalog(true),
      intervalMs,
    );
    return () => window.clearInterval(interval);
  }, [catalogError, loadCatalog, status]);

  const updateRecentPreviews = useCallback(
    (result: SkillPreviewResponse) => {
      const entry: RecentSkillPreview = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        query: result.query,
        flow: result.flow,
        createdAt: Date.now(),
        skillNames: result.selected.map((skill) => skill.name),
      };
      setRecentPreviews((current) => {
        return [
          entry,
          ...current.filter(
            (item) => !(item.query === entry.query && item.flow === entry.flow),
          ),
        ].slice(0, 6);
      });
    },
    [],
  );

  useEffect(() => {
    try {
      sessionStorage.setItem(RECENT_PREVIEWS_KEY, JSON.stringify(recentPreviews));
    } catch {
      // Routing remains usable when browser storage is unavailable.
    }
  }, [recentPreviews]);

  const runPreview = useCallback(
    async (
      nextQuery: string = query,
      nextWorkflow: string = workflow,
      record = true,
    ) => {
      const trimmed = nextQuery.trim();
      if (!trimmed) return;
      const requestId = ++previewRequestId.current;
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const result = await skillsApi.preview({
          query: trimmed,
          flow: nextWorkflow,
          max_results: PREVIEW_LIMIT,
        });
        if (requestId !== previewRequestId.current) return;
        setPreview(result);
        setStatus((current) =>
          current &&
          current.revision === result.source_revision &&
          current.catalog_revision === result.catalog_revision
            ? {
                ...current,
                loaded_count: result.cache.loaded_count,
                loaded_bytes: result.cache.loaded_bytes,
                loaded_reference_count: result.cache.loaded_reference_count,
                cache_entry_count: result.cache.entry_count,
                cache_total_bytes: result.cache.total_bytes,
                cache_max_entries: result.cache.max_entries,
                cache_max_bytes: result.cache.max_bytes,
                cache_hits: result.cache.hits,
                cache_misses: result.cache.misses,
                cache_evictions: result.cache.evictions,
              }
            : current,
        );
        if (record) updateRecentPreviews(result);
        if (
          status &&
          (status.revision !== result.source_revision ||
            status.catalog_revision !== result.catalog_revision)
        ) {
          void loadCatalog(true);
        }
      } catch (error) {
        if (requestId !== previewRequestId.current) return;
        setPreviewError(errorMessage(error));
      } finally {
        if (requestId === previewRequestId.current) setPreviewLoading(false);
      }
    },
    [loadCatalog, query, status, updateRecentPreviews, workflow],
  );

  useEffect(() => {
    if (
      !catalogLoading &&
      !catalogError &&
      status?.enabled &&
      (status.state === "ready" || status.state === "stale") &&
      status.available_count > 0 &&
      !initialPreviewStarted.current
    ) {
      initialPreviewStarted.current = true;
      void runPreview(DEFAULT_QUERY, "deep_research", false);
    }
  }, [catalogError, catalogLoading, runPreview, status]);

  useEffect(() => {
    if (
      preview &&
      status?.catalog_revision &&
      preview.catalog_revision !== status.catalog_revision
    ) {
      setPreview(null);
    }
  }, [preview, status?.catalog_revision]);

  const invalidatePreview = useCallback(() => {
    previewRequestId.current += 1;
    setPreview(null);
    setPreviewLoading(false);
    setPreviewError(null);
  }, []);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void runPreview();
  };

  const inspectSkill = useCallback(async (name: string) => {
    const requestId = ++detailRequestId.current;
    setInspectedName(name);
    setInspectedDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const detail = await skillsApi.detail(name);
      if (requestId !== detailRequestId.current) return;
      setInspectedDetail(detail);
    } catch (error) {
      if (requestId !== detailRequestId.current) return;
      setDetailError(errorMessage(error));
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }, []);

  const closeInspector = useCallback(() => {
    detailRequestId.current += 1;
    setInspectedName(null);
    setInspectedDetail(null);
    setDetailError(null);
  }, []);

  const useRecentPreview = useCallback(
    (item: RecentSkillPreview) => {
      const nextWorkflow = WORKFLOWS.some((workflowItem) => workflowItem.value === item.flow)
        ? (item.flow as (typeof WORKFLOWS)[number]["value"])
        : "deep_research";
      setQuery(item.query);
      setWorkflow(nextWorkflow);
      void runPreview(item.query, nextWorkflow);
    },
    [runPreview],
  );

  const date = new Intl.DateTimeFormat(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(new Date());
  const catalogUsable = Boolean(
    status?.enabled &&
      (status.state === "ready" || status.state === "stale") &&
      status.available_count > 0,
  );

  return (
    <div className="flex h-full min-w-0 overflow-hidden bg-surface-50">
      <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div className="mx-auto w-full min-w-0 max-w-[1000px] px-4 py-7 sm:px-6 lg:px-7">
          <header className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Blocks className="h-5 w-5 text-accent-600" aria-hidden="true" />
                <h1 className="heading-serif text-3xl text-surface-900">Skills</h1>
              </div>
              <p className="mt-2 text-sm text-surface-600">
                Preview the guidance PaperPilot will load for a task.
              </p>
            </div>
            <time className="hidden pt-1 text-xs text-surface-600 sm:block">{date}</time>
          </header>

          <div className="mt-6">
            <SkillStatusRail
              status={status}
              recentPreviews={recentPreviews}
              onSelectPreview={useRecentPreview}
              compact
            />
          </div>

          {catalogLoading ? (
            <div className="mt-5">
              <PageSkeleton />
            </div>
          ) : catalogError ? (
            <div className="mt-5">
              <CatalogError message={catalogError} onRetry={() => void loadCatalog()} />
            </div>
          ) : (
            <>
              {status?.state === "stale" && (
                <div className="mt-5 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
                  <p>
                    PaperPilot is using the last validated catalog because the source refresh is
                    unavailable.
                  </p>
                </div>
              )}

              {status?.state === "error" && (
                <div
                  role="alert"
                  className="mt-5 flex flex-col gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-800 sm:flex-row sm:items-start"
                >
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold">Skill loader unavailable</p>
                    <p className="mt-1 break-words leading-5">
                      {status.error ||
                        "PaperPilot is retrying the loader in the background. Routing will resume automatically when a validated catalog is available."}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void loadCatalog()}
                    className="inline-flex flex-shrink-0 items-center justify-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-1.5 font-medium text-red-700 transition-colors hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
                  >
                    <RotateCw className="h-3 w-3" aria-hidden="true" />
                    Check again
                  </button>
                </div>
              )}

              {!status?.enabled && (
                <div className="mt-5 rounded-xl border border-surface-200 bg-white px-5 py-5">
                  <div className="flex items-start gap-3">
                    <LockKeyhole className="mt-0.5 h-5 w-5 text-surface-400" aria-hidden="true" />
                    <div>
                      <h2 className="text-sm font-semibold text-surface-800">
                        Agent skills are disabled
                      </h2>
                      <p className="mt-1 text-xs leading-5 text-surface-600">
                        Enable the loader in the backend configuration to preview and use advisory
                        research skills.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <form onSubmit={handleSubmit} className="mt-7" aria-label="Preview skill routing">
                <div className="rounded-xl border border-accent-400 bg-white p-2 shadow-[0_0_0_2px_rgba(96,144,248,0.08)] focus-within:ring-2 focus-within:ring-accent-100">
                  <div className="flex flex-col gap-2 lg:flex-row">
                    <div className="relative min-w-0 flex-1">
                      <label htmlFor="skill-task-query" className="sr-only">
                        Describe the research task
                      </label>
                      <Sparkles
                        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-accent-500"
                        aria-hidden="true"
                      />
                      <input
                        id="skill-task-query"
                        type="text"
                        value={query}
                        onChange={(event) => {
                          setQuery(event.target.value);
                          invalidatePreview();
                        }}
                        placeholder="Describe the research task…"
                        aria-describedby="skill-preview-help"
                        disabled={!catalogUsable}
                        className="h-11 w-full rounded-lg border-0 bg-transparent pl-10 pr-10 text-sm text-surface-800 outline-none placeholder:text-surface-400 disabled:cursor-not-allowed disabled:opacity-50"
                      />
                      {query && (
                        <button
                          type="button"
                          onClick={() => {
                            setQuery("");
                            invalidatePreview();
                          }}
                          aria-label="Clear task"
                          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-surface-400 hover:bg-surface-100 hover:text-surface-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400"
                        >
                          <X className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                      )}
                    </div>

                    <div className="flex-shrink-0">
                      <label htmlFor="skill-workflow" className="sr-only">
                        Research workflow
                      </label>
                      <select
                        id="skill-workflow"
                        value={workflow}
                        onChange={(event) => {
                          setWorkflow(
                            event.target.value as (typeof WORKFLOWS)[number]["value"],
                          );
                          invalidatePreview();
                        }}
                        disabled={!catalogUsable}
                        className="h-11 w-full rounded-lg border border-surface-200 bg-surface-50 px-3 text-xs font-medium text-surface-600 outline-none focus:border-accent-300 focus:ring-2 focus:ring-accent-100 disabled:opacity-50 lg:w-40"
                      >
                        {WORKFLOWS.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    <button
                      type="submit"
                      disabled={!query.trim() || previewLoading || !catalogUsable}
                      className="btn-primary flex h-11 flex-shrink-0 items-center justify-center gap-2 rounded-lg px-5 text-xs shadow-sm lg:min-w-36"
                    >
                      {previewLoading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      {previewLoading ? "Routing…" : "Preview selection"}
                    </button>
                  </div>
                </div>
                <p
                  id="skill-preview-help"
                  className="mt-2 flex items-center gap-1.5 text-2xs text-surface-600"
                >
                  <Info className="h-3 w-3" aria-hidden="true" />
                  Choose a workflow, then describe the task. Metadata routing selects a small
                  relevant set without loading their bodies.
                </p>
              </form>

              <div className="mt-6" aria-live="polite" aria-busy={previewLoading}>
                <RoutingPreview
                  preview={preview}
                  loading={previewLoading}
                  error={previewError}
                  availableCount={preview?.available_count ?? status?.available_count ?? skills.length}
                  onInspect={inspectSkill}
                  onRetry={() => void runPreview()}
                />
              </div>

              <div className="mt-6 min-w-0 max-w-full pb-8">
                <SkillMarketplace skills={skills} onInspect={inspectSkill} />
              </div>
            </>
          )}
        </div>
      </div>

      <SkillStatusRail
        status={status}
        recentPreviews={recentPreviews}
        onSelectPreview={useRecentPreview}
      />

      <SkillInspector
        skillName={inspectedName}
        detail={inspectedDetail}
        loading={detailLoading}
        error={detailError}
        onClose={closeInspector}
        onRetry={() => {
          if (inspectedName) void inspectSkill(inspectedName);
        }}
      />
    </div>
  );
}

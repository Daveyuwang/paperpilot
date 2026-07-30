import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Compass,
  Download,
  ExternalLink,
  FileText,
  GitBranch,
  Lightbulb,
  ListChecks,
  Loader2,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import clsx from "clsx";
import { api as paperApi } from "@/api/client";
import { researchDirectorApi } from "@/api/researchDirector";
import { useResearchDirectorStore } from "@/store/researchDirectorStore";
import { useSourceStore } from "@/store/sourceStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import type {
  EvidenceReference,
  GenerateResearchPlanRequest,
  ImplementationTask,
  PlanArtifactSection,
  ResearchBrief,
  ResearchDirectorApi,
  ResearchDirectorArtifact,
  ResearchHandoffBundle,
  ResearchPlanBundle,
  ResearchPlanReview,
  ResearchProject,
  ReviewIssue,
} from "@/types/researchDirector";
import { DEFAULT_REVIEW_PERSPECTIVES } from "@/types/researchDirector";

interface ResearchDirectorViewProps {
  workspaceId?: string;
  api?: ResearchDirectorApi;
  initialProject?: ResearchProject | null;
  initialPlan?: ResearchPlanBundle | null;
  onHandoffReady?: (bundle: ResearchHandoffBundle) => void;
}

const ARTIFACTS: Array<{
  id: ResearchDirectorArtifact;
  label: string;
  description: string;
  icon: typeof FileText;
}> = [
  { id: "brief", label: "Research brief", description: "Question and boundaries", icon: Compass },
  { id: "plan", label: "Plan artifacts", description: "Evidence, gaps, and method", icon: FileText },
  { id: "hypotheses", label: "Hypotheses", description: "Inspect proposed directions", icon: GitBranch },
  { id: "implementation", label: "Implementation", description: "External work packages", icon: ListChecks },
  { id: "review", label: "Independent review", description: "Issues and decision", icon: ShieldCheck },
];

const STATUS_LABELS: Record<ResearchPlanBundle["status"], string> = {
  draft: "Draft",
  reviewed: "Reviewed",
  approved: "Approved",
  superseded: "Superseded",
  handed_off: "Handed off",
};

const STATUS_STYLES: Record<ResearchPlanBundle["status"], string> = {
  draft: "border-surface-200 bg-surface-50 text-surface-600",
  reviewed: "border-amber-200 bg-amber-50 text-amber-800",
  approved: "border-emerald-200 bg-emerald-50 text-emerald-800",
  superseded: "border-surface-200 bg-surface-100 text-surface-500",
  handed_off: "border-accent-200 bg-accent-50 text-accent-700",
};

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function sameLines(value: string, items: string[]): boolean {
  const parsed = lines(value);
  return parsed.length === items.length && parsed.every((item, index) => item === items[index]);
}

const MAX_EVIDENCE_SOURCES = 12;
const MAX_EVIDENCE_CHARS = 24_000;
const MAX_EVIDENCE_CHARS_PER_SOURCE = 4_000;

interface EvidenceCollection {
  evidence: EvidenceReference[];
  warnings: string[];
}

interface EvidenceCandidate {
  reference: EvidenceReference | null;
  warnings: string[];
}

interface WorkspaceLoadState {
  workspaceId: string;
  status: "loading" | "ready" | "failed";
  error: string | null;
}

async function evidenceFromWorkspace(
  workspaceId: string,
  excludedEvidenceIds: ReadonlySet<string> = new Set<string>(),
): Promise<EvidenceCollection> {
  const allSources = useSourceStore.getState().getIncludedSources(workspaceId)
    .filter((source) => !excludedEvidenceIds.has(source.id));
  const sources = allSources.slice(0, MAX_EVIDENCE_SOURCES);
  const warnings: string[] = [];
  const sourceLimitOmissions = allSources.slice(MAX_EVIDENCE_SOURCES).map((source) => source.id);
  if (sourceLimitOmissions.length) {
    const listed = sourceLimitOmissions.slice(0, 25).join(", ");
    const remainder = sourceLimitOmissions.length > 25 ? `, and ${sourceLimitOmissions.length - 25} more` : "";
    warnings.push(`Evidence source limit applied: included ${sources.length} of ${allSources.length}; omitted source IDs: ${listed}${remainder}.`);
  }

  const candidates = await Promise.all(sources.map(async (source): Promise<EvidenceCandidate> => {
    const sourceWarnings: string[] = [];
    const base = {
      id: source.id,
      source_id: source.id,
      source_title: source.title.slice(0, 500),
      source_type: source.provider,
      authors: source.authors,
      year: source.year,
      url: source.url,
      relationship: "context" as const,
    };

    if (!source.paper_id) {
      if (!source.abstract?.trim() && !source.url?.trim()) {
        return { reference: null, warnings: [`Source ${source.id} omitted: no abstract, URL, or extracted passage was available.`] };
      }
      const normalizedAbstract = source.abstract?.replace(/\s+/g, " ").trim() ?? "";
      if (normalizedAbstract.length > MAX_EVIDENCE_CHARS_PER_SOURCE) {
        sourceWarnings.push(`Source ${source.id} passage truncated to ${MAX_EVIDENCE_CHARS_PER_SOURCE} characters.`);
      }
      return {
        reference: {
          ...base,
          passage: normalizedAbstract ? normalizedAbstract.slice(0, MAX_EVIDENCE_CHARS_PER_SOURCE) : null,
          locator: ["Abstract", source.provider, source.year].filter(Boolean).join(" · "),
        },
        warnings: sourceWarnings,
      };
    }

    try {
      const paper = await paperApi.getPaper(source.paper_id);
      if (paper.abstract?.trim()) {
        const normalizedAbstract = paper.abstract.replace(/\s+/g, " ").trim();
        if (normalizedAbstract.length > MAX_EVIDENCE_CHARS_PER_SOURCE) {
          sourceWarnings.push(`Source ${source.id} abstract truncated to ${MAX_EVIDENCE_CHARS_PER_SOURCE} characters.`);
        }
        return {
          reference: {
            ...base,
            source_title: (paper.title || source.title).slice(0, 500),
            authors: paper.authors ?? source.authors,
            passage: normalizedAbstract.slice(0, MAX_EVIDENCE_CHARS_PER_SOURCE),
            locator: ["PDF abstract", paper.created_at?.slice(0, 10)].filter(Boolean).join(" · "),
          },
          warnings: sourceWarnings,
        };
      }
    } catch (error) {
      console.warn(`[ResearchDirector] could not load metadata for source ${source.id}`, error);
    }

    try {
      const chunks = (await paperApi.getChunks(source.paper_id))
        .filter((chunk) => chunk.content_type === "text" && chunk.content.trim())
        .sort((left, right) => left.chunk_index - right.chunk_index);
      const excerptParts: string[] = [];
      const usedSections: string[] = [];
      const usedPages: number[] = [];
      const usedChunkIndexes: number[] = [];
      let remaining = MAX_EVIDENCE_CHARS_PER_SOURCE;
      let truncated = false;
      for (const [index, chunk] of chunks.entries()) {
        if (remaining <= 0) {
          truncated = index < chunks.length;
          break;
        }
        const label = [chunk.section_title, chunk.page_number ? `p. ${chunk.page_number}` : null]
          .filter(Boolean)
          .join(" · ");
        const normalizedContent = chunk.content.replace(/\s+/g, " ").trim();
        const content = normalizedContent.slice(0, Math.max(0, remaining - label.length - 4));
        if (content.length < normalizedContent.length) truncated = true;
        if (!content) continue;
        excerptParts.push(`${label ? `[${label}] ` : ""}${content}`);
        remaining -= content.length + label.length + 4;
        usedChunkIndexes.push(chunk.chunk_index);
        if (chunk.section_title && !usedSections.includes(chunk.section_title)) usedSections.push(chunk.section_title);
        if (chunk.page_number) usedPages.push(chunk.page_number);
      }
      if (!excerptParts.length) {
        return { reference: null, warnings: [`Source ${source.id} omitted: no usable PDF text excerpt was available.`] };
      }
      if (truncated) {
        sourceWarnings.push(`Source ${source.id} PDF excerpt truncated to ${MAX_EVIDENCE_CHARS_PER_SOURCE} characters.`);
      }
      const pageRange = usedPages.length
        ? `pp. ${Math.min(...usedPages)}–${Math.max(...usedPages)}`
        : null;
      const chunkRange = usedChunkIndexes.length
        ? `chunks ${Math.min(...usedChunkIndexes)}–${Math.max(...usedChunkIndexes)}`
        : null;
      return {
        reference: {
          ...base,
          passage: excerptParts.join("\n\n"),
          locator: ["PDF excerpt", usedSections.slice(0, 2).join("; ") || null, pageRange, chunkRange]
            .filter(Boolean)
            .join(" · "),
        },
        warnings: sourceWarnings,
      };
    } catch (error) {
      console.warn(`[ResearchDirector] could not extract source ${source.id}`, error);
      return { reference: null, warnings: [`Source ${source.id} omitted: PDF text could not be loaded.`] };
    }
  }));

  const evidence: EvidenceReference[] = [];
  let remaining = MAX_EVIDENCE_CHARS;
  for (const candidate of candidates) {
    warnings.push(...candidate.warnings);
    if (!candidate.reference) continue;
    const passage = candidate.reference.passage?.slice(0, remaining) || null;
    if (candidate.reference.passage && !passage) {
      warnings.push(`Source ${candidate.reference.id} omitted after the total evidence limit of ${MAX_EVIDENCE_CHARS} characters was reached.`);
      continue;
    }
    if (candidate.reference.passage && passage && passage.length < candidate.reference.passage.length) {
      warnings.push(`Source ${candidate.reference.id} passage truncated by the total evidence limit of ${MAX_EVIDENCE_CHARS} characters.`);
    }
    evidence.push({ ...candidate.reference, passage });
    remaining -= passage?.length ?? 0;
  }
  return { evidence, warnings };
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Research Director request failed";
}

function downloadHandoffBundle(bundle: ResearchHandoffBundle) {
  const blob = new Blob([JSON.stringify(bundle.server_content, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `paperpilot-handoff-${bundle.id}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function openBlockingIssues(review: ResearchPlanReview | null): ReviewIssue[] {
  return review?.issues.filter(
    (issue) => issue.status === "open" && (issue.severity === "blocker" || issue.severity === "major")
  ) ?? [];
}

export function ResearchDirectorView({
  workspaceId: workspaceIdProp,
  api: apiProp,
  initialProject = null,
  initialPlan = null,
  onHandoffReady,
}: ResearchDirectorViewProps) {
  const activeWorkspaceId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const workspaceId = workspaceIdProp ?? activeWorkspaceId ?? "default";
  const api = useMemo(() => apiProp ?? researchDirectorApi, [apiProp]);
  const store = useResearchDirectorStore();
  const workspaceSources = useSourceStore((state) => state.sourcesByWorkspace[workspaceId]);
  const requestSequence = useRef(0);
  const forceServerReloadWorkspace = useRef<string | null>(null);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [workspaceLoad, setWorkspaceLoad] = useState<WorkspaceLoadState>({
    workspaceId,
    status: "loading",
    error: null,
  });
  const scopedWorkspaceLoad: WorkspaceLoadState = workspaceLoad.workspaceId === workspaceId
    ? workspaceLoad
    : { workspaceId, status: "loading", error: null };

  function isCurrentRequest(sequence: number): boolean {
    const currentWorkspaceId = workspaceIdProp
      ?? useWorkspaceStore.getState().activeWorkspaceId
      ?? "default";
    return requestSequence.current === sequence && currentWorkspaceId === workspaceId;
  }

  const revisionEvidenceStats = useMemo(() => {
    const included = (workspaceSources ?? []).filter((source) => source.included);
    const existingIds = new Set(store.plan?.evidence_catalog.map((item) => item.id) ?? []);
    return {
      included: included.length,
      newSources: included.filter((source) => !existingIds.has(source.id)).length,
    };
  }, [store.plan?.evidence_catalog, workspaceSources]);

  useEffect(() => {
    const sequence = ++requestSequence.current;
    const state = useResearchDirectorStore.getState();
    const bypassInitialSnapshot = forceServerReloadWorkspace.current === workspaceId;
    forceServerReloadWorkspace.current = null;
    state.reset();
    state.startRequest("loading");
    setRevisionOpen(false);
    setWorkspaceLoad({ workspaceId, status: "loading", error: null });

    const failLoad = (message: string) => {
      if (!isCurrentRequest(sequence)) return;
      useResearchDirectorStore.getState().reset();
      setWorkspaceLoad({ workspaceId, status: "failed", error: message });
    };

    if (initialPlan && !bypassInitialSnapshot) {
      if (initialPlan.workspace_id !== workspaceId) {
        failLoad("The supplied plan belongs to a different workspace. Retry to load this workspace from the server.");
        return;
      }
      state.loadPlan(initialPlan);
      setWorkspaceLoad({ workspaceId, status: "ready", error: null });
      return;
    }
    if (initialProject && !bypassInitialSnapshot) {
      failLoad("The supplied project snapshot has no plan. Retry to load the complete server project before taking any action.");
      return;
    }

    api.loadLatestProject(workspaceId).then((loaded) => {
      if (!isCurrentRequest(sequence)) return;
      if (loaded && (loaded.project.workspace_id !== workspaceId || loaded.plan.workspace_id !== workspaceId)) {
        failLoad("The server returned a project for a different workspace. Retry before taking any action.");
        return;
      }
      if (loaded) useResearchDirectorStore.getState().loadSnapshot(loaded);
      else useResearchDirectorStore.getState().reset();
      setWorkspaceLoad({ workspaceId, status: "ready", error: null });
    }).catch((error) => {
      failLoad(errorText(error));
    });

    return () => {
      if (requestSequence.current === sequence) requestSequence.current += 1;
    };
  }, [api, initialPlan, initialProject, loadAttempt, workspaceId, workspaceIdProp]);

  const requestBusy = store.requestStatus !== "idle";
  const blockingIssues = openBlockingIssues(store.review);
  const canApprove = Boolean(
    scopedWorkspaceLoad.status === "ready"
    && store.plan
    && store.review
    && store.review.status === "reviewed"
    && store.review.verdict === "approve"
    && blockingIssues.length === 0
    && ["reviewed", "draft"].includes(store.plan.status)
  );
  const canPrepareHandoff = store.plan?.status === "approved" && !store.handoff;
  const canConfirmHandoff = store.plan?.status === "approved" && store.handoff?.status === "ready_for_handoff";

  function retryWorkspaceLoad() {
    forceServerReloadWorkspace.current = workspaceId;
    setWorkspaceLoad({ workspaceId, status: "loading", error: null });
    setLoadAttempt((attempt) => attempt + 1);
  }

  async function handleGeneratePlan(brief: ResearchBrief) {
    const sequence = ++requestSequence.current;
    store.startRequest("generating");
    try {
      const collection = brief.source_policy.use_workspace_sources
        ? await evidenceFromWorkspace(workspaceId)
        : { evidence: [], warnings: [] };
      if (!isCurrentRequest(sequence)) return;
      const payload: GenerateResearchPlanRequest = {
        workspace_id: workspaceId,
        research_brief: brief,
        evidence: collection.evidence,
        evidence_warnings: collection.warnings,
        constraints: brief.constraints,
        desired_deliverables: brief.desired_deliverables,
        notes: brief.notes || null,
      };
      const plan = await api.generatePlan(payload);
      if (!isCurrentRequest(sequence)) return;
      store.loadPlan(plan);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  async function handleReviewPlan() {
    if (!store.plan || store.plan.status !== "draft") return;
    const sequence = ++requestSequence.current;
    const requestedVersionId = store.plan.id;
    store.startRequest("reviewing");
    try {
      const review = await api.reviewPlan({
        workspace_id: workspaceId,
        plan: store.plan,
        evidence: [],
        perspectives: [...DEFAULT_REVIEW_PERSPECTIVES],
      });
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      store.setReview(review);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  async function handleRevisePlan() {
    if (!store.plan || !store.review) return;
    const sequence = ++requestSequence.current;
    const requestedVersionId = store.plan.id;
    store.startRequest("revising");
    try {
      const existingEvidenceIds = new Set(store.plan.evidence_catalog.map((item) => item.id));
      const collection = await evidenceFromWorkspace(workspaceId, existingEvidenceIds);
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      const appendedEvidence = collection.evidence.filter((item) => !existingEvidenceIds.has(item.id));
      const plan = await api.revisePlan({
        workspace_id: workspaceId,
        plan: store.plan,
        review: store.review,
        evidence: appendedEvidence,
        evidence_warnings: collection.warnings,
        revision_instructions: store.revisionInstruction || null,
      });
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      store.loadPlan(plan);
      setRevisionOpen(false);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  async function handleApprovePlan() {
    if (!store.plan || !canApprove) return;
    const sequence = ++requestSequence.current;
    const requestedVersionId = store.plan.id;
    store.startRequest("approving");
    try {
      const approved = await api.approvePlan(store.plan);
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      store.markApproved(approved);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  async function handlePrepareHandoff() {
    if (!store.plan || !canPrepareHandoff) return;
    const sequence = ++requestSequence.current;
    const requestedVersionId = store.plan.id;
    store.startRequest("preparing_handoff");
    try {
      const handoff = await api.prepareHandoff(store.plan);
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      store.setPreparedHandoff(handoff);
      onHandoffReady?.(handoff);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  async function handleConfirmHandoff() {
    if (!store.plan || !canConfirmHandoff) return;
    const sequence = ++requestSequence.current;
    const requestedVersionId = store.plan.id;
    store.startRequest("confirming_handoff");
    try {
      const handoff = await api.confirmHandoff(store.plan);
      if (!isCurrentRequest(sequence) || useResearchDirectorStore.getState().plan?.id !== requestedVersionId) return;
      store.setConfirmedHandoff(handoff);
    } catch (error) {
      if (isCurrentRequest(sequence)) store.setError(errorText(error));
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col bg-white">
      <header className="flex flex-shrink-0 flex-wrap items-start gap-3 border-b border-surface-200 bg-white px-5 py-4 sm:px-7">
        <span className="mt-0.5 rounded-lg bg-accent-50 p-2 text-accent-700" aria-hidden="true">
          <Compass className="h-4 w-4" />
        </span>
        <div className="min-w-[240px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-base font-semibold tracking-[-0.015em] text-surface-900">
              Research Director
            </h1>
            {scopedWorkspaceLoad.status === "ready" && store.plan && (
              <>
                <span className="text-xs text-surface-400">v{store.plan.version_number}</span>
                <StatusBadge status={store.plan.status} />
              </>
            )}
          </div>
          <p className="mt-0.5 max-w-2xl text-xs text-surface-500">
            Turn an open question into an evidence-linked, independently reviewed implementation package.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-surface-200 bg-surface-50 px-3 py-2 text-xs text-surface-600">
          <PackageCheck className="h-3.5 w-3.5 text-surface-500" aria-hidden="true" />
          <span>Planning only · external execution required</span>
        </div>
      </header>

      {scopedWorkspaceLoad.status === "loading" ? (
        <WorkspaceLoadBoundary status="loading" error={null} onRetry={retryWorkspaceLoad} />
      ) : scopedWorkspaceLoad.status === "failed" ? (
        <WorkspaceLoadBoundary status="failed" error={scopedWorkspaceLoad.error} onRetry={retryWorkspaceLoad} />
      ) : !store.plan ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <BriefComposer
            brief={store.briefDraft}
            busy={requestBusy}
            error={store.errorMessage}
            onChange={store.setBriefDraft}
            onSourcePolicyChange={store.setSourcePolicy}
            onSubmit={handleGeneratePlan}
          />
        </div>
      ) : (
        <>
          <ActionBar
            plan={store.plan}
            review={store.review}
            handoff={store.handoff}
            requestStatus={store.requestStatus}
            canApprove={canApprove}
            canPrepareHandoff={canPrepareHandoff}
            canConfirmHandoff={canConfirmHandoff}
            blockingIssueCount={blockingIssues.length}
            onReview={handleReviewPlan}
            onRevise={() => setRevisionOpen((open) => !open)}
            onApprove={handleApprovePlan}
            onPrepareHandoff={handlePrepareHandoff}
            onConfirmHandoff={handleConfirmHandoff}
            onStartNew={() => { setRevisionOpen(false); store.reset(); }}
          />

          {revisionOpen && (
            <RevisionComposer
              value={store.revisionInstruction}
              busy={store.requestStatus === "revising"}
              disabled={!store.review}
              includedSourceCount={revisionEvidenceStats.included}
              newSourceCount={revisionEvidenceStats.newSources}
              onChange={store.setRevisionInstruction}
              onCancel={() => setRevisionOpen(false)}
              onSubmit={handleRevisePlan}
            />
          )}

          {store.errorMessage && (
            <div className="mx-5 mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:mx-7" role="alert">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="flex-1">{store.errorMessage}</span>
              <button className="rounded p-0.5 hover:bg-red-100" onClick={store.clearError} aria-label="Dismiss error">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[190px_minmax(0,1fr)] 2xl:grid-cols-[190px_minmax(0,1fr)_320px]">
            <ArtifactNavigator
              active={store.activeArtifact}
              plan={store.plan}
              review={store.review}
              onSelect={store.setActiveArtifact}
            />

            <main className="min-h-0 min-w-0 overflow-y-auto px-5 py-6 sm:px-7" id="research-director-artifact">
              <div className="mx-auto max-w-4xl">
                <ArtifactContent
                  artifact={store.activeArtifact}
                  plan={store.plan}
                  review={store.review}
                  selectedHypothesisId={store.selectedHypothesisId}
                  onSelectHypothesis={store.setSelectedHypothesis}
                  onReview={handleReviewPlan}
                />

                {store.activeArtifact !== "review" && (
                  <div className="mt-8 border-t border-surface-200 pt-6 2xl:hidden">
                    <ReviewPanel review={store.review} busy={requestBusy} canReview={store.plan.status === "draft"} onReview={handleReviewPlan} />
                  </div>
                )}
              </div>
            </main>

            <aside className="hidden min-h-0 overflow-y-auto border-l border-surface-200 bg-surface-50/60 px-5 py-6 2xl:block" aria-label="Independent review summary">
              <ReviewPanel review={store.review} busy={requestBusy} canReview={store.plan.status === "draft"} onReview={handleReviewPlan} />
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: ResearchPlanBundle["status"] }) {
  return (
    <span className={clsx("rounded-full border px-2 py-0.5 text-[11px] font-medium", STATUS_STYLES[status])}>
      {STATUS_LABELS[status]}
    </span>
  );
}

function WorkspaceLoadBoundary({ status, error, onRetry }: {
  status: "loading" | "failed";
  error: string | null;
  onRetry: () => void;
}) {
  const loading = status === "loading";
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-10 sm:px-7">
      <div className="mx-auto flex min-h-64 max-w-lg flex-col items-center justify-center rounded-xl border border-surface-200 bg-surface-50 px-7 py-10 text-center">
        {loading
          ? <Loader2 className="h-6 w-6 animate-spin text-accent-600" aria-hidden="true" />
          : <AlertTriangle className="h-6 w-6 text-amber-600" aria-hidden="true" />}
        <h2 className="mt-4 text-sm font-semibold text-surface-800">
          {loading ? "Loading Research Director" : "Could not load this workspace"}
        </h2>
        <p className="mt-2 max-w-md text-xs leading-5 text-surface-500">
          {loading
            ? "Checking the server for the latest project before enabling any planning action."
            : error || "The latest project could not be verified. Retry before taking any planning action."}
        </p>
        {!loading && (
          <button type="button" className="btn-primary mt-5 gap-1.5 text-xs" onClick={onRetry}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

function BriefComposer({
  brief,
  busy,
  error,
  onChange,
  onSourcePolicyChange,
  onSubmit,
}: {
  brief: ResearchBrief;
  busy: boolean;
  error: string | null;
  onChange: (partial: Partial<ResearchBrief>) => void;
  onSourcePolicyChange: (partial: Partial<ResearchBrief["source_policy"]>) => void;
  onSubmit: (brief: ResearchBrief) => void;
}) {
  const [criteriaText, setCriteriaText] = useState(brief.success_criteria.join("\n"));
  const [constraintsText, setConstraintsText] = useState(brief.constraints.join("\n"));
  const [deliverablesText, setDeliverablesText] = useState(brief.desired_deliverables.join("\n"));

  useEffect(() => {
    if (!sameLines(criteriaText, brief.success_criteria)) setCriteriaText(brief.success_criteria.join("\n"));
  }, [brief.success_criteria, criteriaText]);

  useEffect(() => {
    if (!sameLines(constraintsText, brief.constraints)) setConstraintsText(brief.constraints.join("\n"));
  }, [brief.constraints, constraintsText]);

  useEffect(() => {
    if (!sameLines(deliverablesText, brief.desired_deliverables)) setDeliverablesText(brief.desired_deliverables.join("\n"));
  }, [brief.desired_deliverables, deliverablesText]);
  const valid = Boolean(
    brief.title.trim()
    && brief.research_question.trim()
    && brief.objective.trim()
    && lines(criteriaText).length
    && lines(deliverablesText).length
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    onSubmit({
      ...brief,
      success_criteria: lines(criteriaText),
      constraints: lines(constraintsText),
      desired_deliverables: lines(deliverablesText),
    });
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-5xl px-5 py-7 sm:px-8 sm:py-10">
      <div className="max-w-3xl">
        <h2 className="text-xl font-semibold tracking-[-0.02em] text-surface-900">Define the research contract</h2>
        <p className="mt-2 max-w-[70ch] text-sm leading-6 text-surface-600">
          Frame the decision the research should support. The agent will produce an evidence-aware plan and an external implementation handoff—not execute code, builds, or experiments.
        </p>
      </div>

      {error && (
        <div className="mt-5 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="mt-8 space-y-8">
        <fieldset className="space-y-4">
          <legend className="mb-3 text-sm font-semibold text-surface-800">Question and intended outcome</legend>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Project title" required>
              <input
                className="input-base w-full"
                value={brief.title}
                onChange={(event) => onChange({ title: event.target.value })}
                placeholder="e.g. Long-context retrieval reliability"
                required
              />
            </Field>
            <Field label="Intended contribution">
              <input
                className="input-base w-full"
                value={brief.intended_contribution}
                onChange={(event) => onChange({ intended_contribution: event.target.value })}
                placeholder="What should be new or better understood?"
              />
            </Field>
          </div>
          <Field label="Research question" required hint="Use a falsifiable or decision-oriented question where possible.">
            <textarea
              className="input-base min-h-24 w-full resize-y"
              value={brief.research_question}
              onChange={(event) => onChange({ research_question: event.target.value })}
              placeholder="What specific uncertainty should the research resolve?"
              required
            />
          </Field>
          <Field label="Objective" required>
            <textarea
              className="input-base min-h-20 w-full resize-y"
              value={brief.objective}
              onChange={(event) => onChange({ objective: event.target.value })}
              placeholder="Describe the decision or artifact this research should enable."
              required
            />
          </Field>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Problem statement">
              <textarea
                className="input-base min-h-24 w-full resize-y"
                value={brief.problem_statement}
                onChange={(event) => onChange({ problem_statement: event.target.value })}
                placeholder="What is currently unreliable, unknown, or underspecified?"
              />
            </Field>
            <Field label="Scope">
              <textarea
                className="input-base min-h-24 w-full resize-y"
                value={brief.scope}
                onChange={(event) => onChange({ scope: event.target.value })}
                placeholder="Domains, populations, models, datasets, or time range."
              />
            </Field>
          </div>
        </fieldset>

        <fieldset className="space-y-4 border-t border-surface-200 pt-7">
          <legend className="mb-3 text-sm font-semibold text-surface-800">Definition of done</legend>
          <div className="grid gap-4 lg:grid-cols-3">
            <Field label="Success criteria" hint="One criterion per line." required>
              <textarea className="input-base min-h-32 w-full resize-y" value={criteriaText} onChange={(event) => { const value = event.target.value; setCriteriaText(value); onChange({ success_criteria: lines(value) }); }} placeholder="Evidence coverage ≥ …\nPlan reviewed by …" required />
            </Field>
            <Field label="Constraints" hint="One constraint per line.">
              <textarea className="input-base min-h-32 w-full resize-y" value={constraintsText} onChange={(event) => { const value = event.target.value; setConstraintsText(value); onChange({ constraints: lines(value) }); }} placeholder="No private datasets\nTwo-week implementation window" />
            </Field>
            <Field label="Desired deliverables" hint="One deliverable per line." required>
              <textarea className="input-base min-h-32 w-full resize-y" value={deliverablesText} onChange={(event) => { const value = event.target.value; setDeliverablesText(value); onChange({ desired_deliverables: lines(value) }); }} placeholder="Literature map\nImplementation task DAG" required />
            </Field>
          </div>
        </fieldset>

        <fieldset className="space-y-4 border-t border-surface-200 pt-7">
          <legend className="mb-3 text-sm font-semibold text-surface-800">Source policy</legend>
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            <Checkbox checked={brief.source_policy.use_workspace_sources} onChange={(checked) => onSourcePolicyChange({ use_workspace_sources: checked })} label="Use workspace sources" />
          </div>
          <p className="rounded-lg border border-surface-200 bg-surface-50 px-3 py-2 text-xs leading-5 text-surface-500">
            Add web literature manually in Sources for this release. Automatic external discovery and primary-source filtering are planned for P1.
          </p>
          <Field label="Additional notes">
            <textarea className="input-base min-h-20 w-full resize-y" value={brief.notes} onChange={(event) => onChange({ notes: event.target.value })} placeholder="Known baselines, stakeholders, exclusions, or review expectations." />
          </Field>
        </fieldset>
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-surface-200 pt-6">
        <button type="submit" className="btn-primary gap-2 text-sm" disabled={!valid || busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Compass className="h-4 w-4" />}
          {busy ? "Building research plan…" : "Build implementation plan"}
        </button>
        <p className="text-xs text-surface-500">Creates a reviewable plan; no external execution starts.</p>
      </div>
    </form>
  );
}

function Field({ label, hint, required = false, children }: { label: string; hint?: string; required?: boolean; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 flex items-center gap-1 text-xs font-medium text-surface-700">
        {label}{required && <span className="text-red-600" aria-hidden="true">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1.5 block text-[11px] leading-4 text-surface-500">{hint}</span>}
    </label>
  );
}

function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-surface-700">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 rounded border-surface-300 text-accent-600 focus:ring-accent-500" />
      {label}
    </label>
  );
}

function ActionBar({
  plan,
  review,
  handoff,
  requestStatus,
  canApprove,
  canPrepareHandoff,
  canConfirmHandoff,
  blockingIssueCount,
  onReview,
  onRevise,
  onApprove,
  onPrepareHandoff,
  onConfirmHandoff,
  onStartNew,
}: {
  plan: ResearchPlanBundle;
  review: ResearchPlanReview | null;
  handoff: ResearchHandoffBundle | null;
  requestStatus: ReturnType<typeof useResearchDirectorStore.getState>["requestStatus"];
  canApprove: boolean;
  canPrepareHandoff: boolean;
  canConfirmHandoff: boolean;
  blockingIssueCount: number;
  onReview: () => void;
  onRevise: () => void;
  onApprove: () => void;
  onPrepareHandoff: () => void;
  onConfirmHandoff: () => void;
  onStartNew: () => void;
}) {
  const busy = requestStatus !== "idle";
  const requestLabel: Partial<Record<typeof requestStatus, string>> = {
    reviewing: "Reviewing…",
    revising: "Revising…",
    approving: "Approving…",
    preparing_handoff: "Preparing…",
    confirming_handoff: "Confirming handoff…",
  };

  return (
    <div className="flex flex-shrink-0 flex-wrap items-center gap-2 border-b border-surface-200 bg-surface-50 px-5 py-2.5 sm:px-7" aria-live="polite">
      <div className="mr-auto min-w-0">
        <p className="truncate text-sm font-medium text-surface-800">{plan.research_brief.title}</p>
        <p className="text-[11px] text-surface-500">
          {handoff ? `${handoff.status === "handed_off" ? "Handed off" : "Bundle ready"} · ${handoff.id}` : `${plan.hypotheses.length} hypotheses · ${plan.implementation_plan.tasks.length} work packages`}
        </p>
      </div>

      {busy && (
        <span className="flex items-center gap-1.5 px-2 text-xs text-surface-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {requestLabel[requestStatus] ?? "Working…"}
        </span>
      )}

      <button
        className="btn-secondary gap-1.5 px-3 py-1.5 text-xs"
        onClick={onReview}
        disabled={busy || plan.status !== "draft"}
        title={plan.status === "reviewed" ? "This version is already reviewed. Revise it to create a new draft, or approve it." : undefined}
      >
        <ShieldCheck className="h-3.5 w-3.5" />
        {review ? "Reviewed" : "Review plan"}
      </button>
      <button className="btn-secondary gap-1.5 px-3 py-1.5 text-xs" onClick={onRevise} disabled={busy || !review || plan.status === "approved" || plan.status === "handed_off"}>
        <RefreshCw className="h-3.5 w-3.5" />
        Revise
      </button>
      <button className="btn-primary gap-1.5 px-3 py-1.5 text-xs" onClick={onApprove} disabled={busy || !canApprove} title={blockingIssueCount ? `${blockingIssueCount} blocking review issue(s) remain` : undefined}>
        <Check className="h-3.5 w-3.5" />
        Approve
      </button>
      {!handoff && (
        <button className="btn-secondary gap-1.5 px-3 py-1.5 text-xs" onClick={onPrepareHandoff} disabled={busy || !canPrepareHandoff}>
          <PackageCheck className="h-3.5 w-3.5" />
          Prepare bundle
        </button>
      )}
      {handoff && (
        <button className="btn-secondary gap-1.5 px-3 py-1.5 text-xs" onClick={() => downloadHandoffBundle(handoff)} disabled={busy}>
          <Download className="h-3.5 w-3.5" />
          Download JSON
        </button>
      )}
      {handoff?.status === "ready_for_handoff" && (
        <button className="btn-primary gap-1.5 px-3 py-1.5 text-xs" onClick={onConfirmHandoff} disabled={busy || !canConfirmHandoff} title="Confirm that the prepared package was transferred externally. This does not execute the work.">
          <Check className="h-3.5 w-3.5" />
          Mark handed off
        </button>
      )}
      <button className="btn-ghost px-2 py-1.5 text-xs" onClick={onStartNew} disabled={busy}>New brief</button>
    </div>
  );
}

function RevisionComposer({ value, busy, disabled, includedSourceCount, newSourceCount, onChange, onCancel, onSubmit }: {
  value: string;
  busy: boolean;
  disabled: boolean;
  includedSourceCount: number;
  newSourceCount: number;
  onChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <section className="flex flex-shrink-0 flex-col gap-3 border-b border-surface-200 bg-accent-50/50 px-5 py-4 sm:flex-row sm:items-end sm:px-7" aria-label="Plan revision request">
      <label className="min-w-0 flex-1">
        <span className="mb-1.5 block text-xs font-medium text-surface-700">Revision instructions</span>
        <textarea className="input-base min-h-16 w-full resize-y bg-white" value={value} onChange={(event) => onChange(event.target.value)} placeholder="Address every open Blocker and Major issue in the revised plan…" disabled={disabled || busy} />
        <span className="mt-1.5 block text-[11px] leading-4 text-surface-500">
          {newSourceCount > 0
            ? `${newSourceCount} new included workspace source${newSourceCount === 1 ? " is" : "s are"} available; up to ${Math.min(newSourceCount, MAX_EVIDENCE_SOURCES)} will be extracted and appended without rewriting existing evidence.`
            : includedSourceCount > 0
              ? "All included workspace sources are already present in this plan version. Include new sources before revising to expand the evidence catalog."
              : "No workspace sources are included. The revision will rely on the frozen evidence catalog unless sources are added."}
        </span>
      </label>
      <div className="flex gap-2">
        <button className="btn-ghost px-3 py-2 text-xs" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn-primary gap-1.5 px-3 py-2 text-xs" onClick={onSubmit} disabled={disabled || busy}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Create next version
        </button>
      </div>
    </section>
  );
}

function ArtifactNavigator({ active, plan, review, onSelect }: {
  active: ResearchDirectorArtifact;
  plan: ResearchPlanBundle;
  review: ResearchPlanReview | null;
  onSelect: (artifact: ResearchDirectorArtifact) => void;
}) {
  const counts: Partial<Record<ResearchDirectorArtifact, number>> = {
    plan: plan.artifact_sections.length,
    hypotheses: plan.hypotheses.length,
    implementation: plan.implementation_plan.tasks.length,
    review: review?.issues.filter((issue) => issue.status === "open").length ?? 0,
  };

  return (
    <nav className="flex overflow-x-auto border-b border-surface-200 bg-surface-50 px-3 py-2 lg:min-h-0 lg:flex-col lg:overflow-y-auto lg:border-b-0 lg:border-r lg:px-3 lg:py-5" aria-label="Research artifacts">
      <p className="mb-2 hidden px-2 text-xs font-semibold text-surface-500 lg:block">Artifacts</p>
      {ARTIFACTS.map((item) => {
        const Icon = item.icon;
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
            aria-current={selected ? "page" : undefined}
            className={clsx(
              "flex min-w-fit items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors lg:w-full",
              selected ? "bg-white text-surface-900 ring-1 ring-inset ring-surface-200" : "text-surface-500 hover:bg-surface-100 hover:text-surface-800"
            )}
          >
            <Icon className={clsx("h-4 w-4 shrink-0", selected && "text-accent-600")} />
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-medium">{item.label}</span>
              <span className="hidden truncate text-[10px] text-surface-400 lg:block">{item.description}</span>
            </span>
            {counts[item.id] !== undefined && (
              <span className="rounded-full bg-surface-100 px-1.5 py-0.5 text-[10px] text-surface-500">{counts[item.id]}</span>
            )}
          </button>
        );
      })}
    </nav>
  );
}

function ArtifactContent({
  artifact,
  plan,
  review,
  selectedHypothesisId,
  onSelectHypothesis,
  onReview,
}: {
  artifact: ResearchDirectorArtifact;
  plan: ResearchPlanBundle;
  review: ResearchPlanReview | null;
  selectedHypothesisId: string | null;
  onSelectHypothesis: (id: string | null) => void;
  onReview: () => void;
}) {
  switch (artifact) {
    case "brief": return <BriefArtifact plan={plan} />;
    case "plan": return <PlanArtifact plan={plan} />;
    case "hypotheses": return <HypothesisArtifact plan={plan} selectedId={selectedHypothesisId} onSelect={onSelectHypothesis} />;
    case "implementation": return <ImplementationArtifact plan={plan} />;
    case "review": return <ReviewArtifact plan={plan} review={review} onReview={onReview} />;
  }
}

function ArtifactHeading({ icon, title, description, trailing }: { icon: ReactNode; title: string; description: string; trailing?: ReactNode }) {
  return (
    <div className="mb-6 flex items-start gap-3 border-b border-surface-200 pb-5">
      <span className="mt-0.5 text-accent-600" aria-hidden="true">{icon}</span>
      <div className="min-w-0 flex-1">
        <h2 className="text-lg font-semibold tracking-[-0.015em] text-surface-900">{title}</h2>
        <p className="mt-1 max-w-[70ch] text-sm leading-5 text-surface-500">{description}</p>
      </div>
      {trailing}
    </div>
  );
}

function BriefArtifact({ plan }: { plan: ResearchPlanBundle }) {
  const brief = plan.research_brief;
  return (
    <section>
      <ArtifactHeading icon={<Compass className="h-5 w-5" />} title="Submitted brief and current contract" description="Compare the immutable project brief with the enriched contract that this specific plan version asks you to approve." />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-surface-800">Submitted brief snapshot</h3>
        <span className={clsx(
          "rounded-full px-2 py-0.5 text-[10px] font-medium",
          plan.research_brief_source === "submitted_snapshot"
            ? "bg-accent-50 text-accent-700"
            : "bg-amber-50 text-amber-800",
        )}>
          {plan.research_brief_source === "submitted_snapshot" ? "Original structured submission" : "Legacy contract fallback"}
        </span>
      </div>
      {plan.research_brief_source === "legacy_contract_fallback" && (
        <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
          This older project did not persist a structured brief. The values below were reconstructed from its current contract.
        </p>
      )}
      <DefinitionList items={[
        ["Research question", brief.research_question],
        ["Objective", brief.objective],
        ["Problem", brief.problem_statement || "Not specified"],
        ["Intended contribution", brief.intended_contribution || "Not specified"],
        ["Scope", brief.scope || "Not specified"],
        ["Source policy", `${brief.source_policy.use_workspace_sources ? "Workspace sources enabled" : "Workspace sources disabled"}; ${brief.source_policy.discover_external_sources ? "external discovery requested" : "manual external sources only"}; ${brief.source_policy.prefer_primary_sources ? "primary sources preferred" : "no primary-source preference"}; horizon: ${brief.source_policy.time_horizon}.`],
        ["Notes", brief.notes || "Not specified"],
      ]} />

      <div className="mt-7 grid gap-7 md:grid-cols-2">
        <ListBlock title="Submitted success criteria" items={brief.success_criteria} empty="No submitted success criteria." />
        <ListBlock title="Submitted constraints" items={brief.constraints} empty="No submitted constraints." />
        <ListBlock title="Submitted deliverables" items={brief.desired_deliverables} empty="No submitted deliverables." />
        <ListBlock title="Requested source inclusions" items={brief.source_policy.must_include} empty="No sources explicitly required." />
        <ListBlock title="Requested source exclusions" items={brief.source_policy.must_exclude} empty="No sources explicitly excluded." />
      </div>

      <div className="mt-9 border-t border-surface-200 pt-6">
        <h3 className="text-sm font-semibold text-surface-800">Current research contract · plan v{plan.version_number}</h3>
        <p className="mt-1 text-xs leading-5 text-surface-500">This server-authoritative contract may enrich the submitted brief with assumptions, unknowns, failure criteria, and required human decisions.</p>
        <div className="mt-4">
          <ContractDetails contract={plan.contract} />
        </div>
      </div>
    </section>
  );
}

function ContractDetails({ contract }: { contract: ResearchPlanBundle["contract"] }) {
  return (
    <div>
      <DefinitionList items={[
        ["Title", contract.title],
        ["Research question", contract.research_question],
        ["Objective", contract.objective],
        ["Console execution policy", "Planning only; this console does not execute or verify the proposed work."],
      ]} />
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <ListBlock title="Scope inclusions" items={contract.scope_inclusions} empty="No scope inclusions recorded." />
        <ListBlock title="Scope exclusions" items={contract.scope_exclusions} empty="No scope exclusions recorded." />
        <ListBlock title="Constraints" items={contract.constraints} empty="No constraints recorded." />
        <ListBlock title="Assumptions" items={contract.assumptions} empty="No assumptions recorded." />
        <ListBlock title="Unknowns" items={contract.unknowns} empty="No unknowns recorded." />
        <ListBlock title="Success criteria" items={contract.success_criteria} empty="No success criteria recorded." />
        <ListBlock title="Failure criteria" items={contract.failure_criteria} empty="No failure criteria recorded." />
        <ListBlock title="Required deliverables" items={contract.required_deliverables} empty="No required deliverables recorded." />
        <ListBlock title="Allowed sources" items={contract.allowed_sources} empty="No explicit source allowlist." />
        <ListBlock title="Excluded sources" items={contract.excluded_sources} empty="No explicit source exclusions." />
        <ListBlock title="Human decisions required" items={contract.human_decisions_required} empty="No human decision gates recorded." />
      </div>
    </div>
  );
}

function PlanArtifact({ plan }: { plan: ResearchPlanBundle }) {
  return (
    <section>
      <ArtifactHeading
        icon={<FileText className="h-5 w-5" />}
        title="Plan artifacts"
        description="Versioned research reasoning generated from the server-authoritative plan snapshot. Request a revision to change it."
        trailing={<span className="text-xs text-surface-400">{plan.artifact_sections.length} sections</span>}
      />

      {plan.generation_warnings.length > 0 && (
        <div className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900" role="status">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            Evidence coverage warning{plan.generation_warnings.length === 1 ? "" : "s"}
          </div>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-amber-800">
            {plan.generation_warnings.slice(0, 3).map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}
          </ul>
          {plan.generation_warnings.length > 3 && <p className="mt-1 text-[11px] text-amber-700">+{plan.generation_warnings.length - 3} more in this plan version</p>}
        </div>
      )}

      <div className="space-y-4">
        {plan.artifact_sections.length ? plan.artifact_sections.map((section) => (
          <PlanSectionCard key={section.id} section={section} />
        )) : (
          <EmptyArtifact icon={<FileText className="h-6 w-6" />} title="No plan sections yet" description="Generate or revise the plan to create evidence-linked artifact sections." />
        )}
      </div>

      <EvidenceInspector claims={plan.evidence_claims} />

      <MethodInspector methods={plan.methods} />

      <ExperimentInspector experiments={plan.experiments} methods={plan.methods} />

      <div className="mt-8 border-t border-surface-200 pt-6">
        <h3 className="text-sm font-semibold text-surface-800">Research gaps</h3>
        <div className="mt-3 space-y-3">
          {plan.gaps.map((gap) => (
            <div key={gap.id} className="rounded-lg bg-surface-50 px-4 py-3 ring-1 ring-inset ring-surface-200">
              <div className="flex flex-wrap items-start gap-2">
                <p className="min-w-0 flex-1 text-sm font-medium text-surface-800">{gap.title}</p>
                <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-surface-500 ring-1 ring-inset ring-surface-200">Testability: {gap.testability}</span>
                <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-surface-500 ring-1 ring-inset ring-surface-200">Novelty: {gap.novelty_confidence}</span>
              </div>
              <p className="mt-1.5 text-sm leading-5 text-surface-600">{gap.description}</p>
              {gap.unresolved_questions.length > 0 && <p className="mt-2 text-xs text-amber-800">Open: {gap.unresolved_questions.join(" · ")}</p>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function evidenceSnippet(value: string | null | undefined): string {
  if (!value?.trim()) return "No passage captured for this source.";
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > 320 ? `${compact.slice(0, 320)}…` : compact;
}

function safeSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function EvidenceInspector({ claims }: { claims: ResearchPlanBundle["evidence_claims"] }) {
  return (
    <section className="mt-8 border-t border-surface-200 pt-6" aria-labelledby="claim-evidence-heading">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <h3 id="claim-evidence-heading" className="text-sm font-semibold text-surface-800">Claim and evidence inspector</h3>
          <p className="mt-1 text-xs leading-5 text-surface-500">Inspect each declared relation with its frozen source locator and excerpt. Relation labels are plan assertions, not verified entailment.</p>
        </div>
        <span className="text-xs text-surface-400">{claims.length} claim{claims.length === 1 ? "" : "s"}</span>
      </div>

      {claims.length ? (
        <div className="mt-4 space-y-4">
          {claims.map((claim) => {
            const statusStyle = claim.evidence_status === "supported"
              ? "bg-emerald-50 text-emerald-800"
              : claim.evidence_status === "conflicting"
                ? "bg-amber-50 text-amber-800"
                : "bg-surface-100 text-surface-600";
            return (
              <article key={claim.id} className="rounded-lg bg-white px-4 py-4 ring-1 ring-inset ring-surface-200">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize", statusStyle)}>
                    {claim.evidence_status === "insufficient" ? "insufficient" : `declared ${claim.evidence_status}`}
                  </span>
                  <span className="font-mono text-[10px] text-surface-400">{claim.id}</span>
                </div>
                <p className="mt-3 text-sm font-medium leading-6 text-surface-800">{claim.claim}</p>
                {claim.uncertainty && <p className="mt-1 text-xs leading-5 text-amber-800">Limitation: {claim.uncertainty}</p>}

                {claim.evidence_refs.length ? (
                  <div className="mt-4 space-y-2">
                    {claim.evidence_refs.map((reference) => {
                      const sourceUrl = safeSourceUrl(reference.url);
                      return (
                      <div key={`${claim.id}-${reference.id}`} className="rounded-md bg-surface-50 px-3 py-3 ring-1 ring-inset ring-surface-200">
                        <div className="flex flex-wrap items-center gap-2">
                          {sourceUrl ? (
                            <a href={sourceUrl} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1 text-xs font-semibold text-accent-700 hover:text-accent-800 hover:underline">
                              <span className="truncate">{reference.source_title}</span>
                              <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                            </a>
                          ) : (
                            <span className="min-w-0 truncate text-xs font-semibold text-surface-700">{reference.source_title}</span>
                          )}
                          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-medium capitalize text-surface-600 ring-1 ring-inset ring-surface-200">
                            declared {reference.relationship}
                          </span>
                          {reference.source_type && <span className="text-[10px] capitalize text-surface-400">{reference.source_type.replace(/_/g, " ")}</span>}
                          {reference.year != null && <span className="text-[10px] text-surface-400">{reference.year}</span>}
                          {reference.locator && <span className="text-[10px] text-surface-400">{reference.locator}</span>}
                        </div>
                        {reference.authors && reference.authors.length > 0 && (
                          <p className="mt-1 text-[10px] leading-4 text-surface-500">{reference.authors.join(", ")}</p>
                        )}
                        <p className="mt-2 text-xs leading-5 text-surface-600">{evidenceSnippet(reference.passage)}</p>
                        {reference.passage && reference.passage.replace(/\s+/g, " ").trim().length > 320 && (
                          <details className="mt-2 text-xs text-surface-600">
                            <summary className="cursor-pointer font-medium text-accent-700">Show full frozen excerpt</summary>
                            <p className="mt-2 whitespace-pre-wrap leading-5">{reference.passage}</p>
                          </details>
                        )}
                      </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">No source is linked to this claim.</p>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="mt-4 rounded-lg bg-surface-50 px-4 py-3 text-xs text-surface-500 ring-1 ring-inset ring-surface-200">No material claims were extracted for this plan version.</p>
      )}
    </section>
  );
}

function MethodInspector({ methods }: { methods: ResearchPlanBundle["methods"] }) {
  return (
    <section className="mt-8 border-t border-surface-200 pt-6" aria-labelledby="method-spec-heading">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <h3 id="method-spec-heading" className="text-sm font-semibold text-surface-800">External method specifications</h3>
          <p className="mt-1 text-xs leading-5 text-surface-500">Inspect the complete proposed method, its boundaries, alternatives, selection rationale, and risks. These are specifications only; none has been executed.</p>
        </div>
        <span className="text-xs text-surface-400">{methods.length} method{methods.length === 1 ? "" : "s"}</span>
      </div>

      {methods.length ? (
        <div className="mt-4 space-y-5">
          {methods.map((method) => (
            <article key={method.id} className="rounded-lg bg-white px-4 py-4 ring-1 ring-inset ring-surface-200">
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="text-sm font-semibold text-surface-800">{method.title}</h4>
                    <span className="font-mono text-[10px] text-surface-400">{method.id}</span>
                  </div>
                  <p className="mt-1 text-sm leading-5 text-surface-600">{method.summary}</p>
                </div>
                <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800">Awaiting external execution</span>
              </div>

              <div className="mt-4">
                <DefinitionList items={[
                  ["Hypothesis lineage", method.addresses_hypothesis_ids.join(", ") || "Not specified"],
                  ["Selection rationale", method.selection_rationale],
                ]} />
              </div>

              <div className="mt-6 grid gap-6 md:grid-cols-2">
                <ListBlock title="Components" items={method.components} empty="No components recorded." />
                <ListBlock title="Interfaces and boundaries" items={method.interfaces_or_boundaries} empty="No interfaces or boundaries recorded." />
                <ListBlock title="Assumptions" items={method.assumptions} empty="No method assumptions recorded." />
                <ListBlock title="Risks" items={method.risks} empty="No method risks recorded." />
              </div>

              <div className="mt-6">
                <h5 className="text-xs font-semibold text-surface-700">Ordered procedure</h5>
                {method.procedure.length ? (
                  <ol className="mt-2 space-y-2">
                    {method.procedure.map((step, index) => (
                      <li key={`${method.id}-step-${index}`} className="flex items-start gap-3 text-xs leading-5 text-surface-600">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-surface-100 font-mono text-[10px] text-surface-500">{index + 1}</span>
                        <span>{step}</span>
                      </li>
                    ))}
                  </ol>
                ) : <p className="mt-2 text-xs text-amber-800">No procedure recorded.</p>}
              </div>

              <div className="mt-6">
                <h5 className="text-xs font-semibold text-surface-700">Alternatives considered</h5>
                {method.alternatives_considered.length ? (
                  <div className="mt-2 grid gap-3 md:grid-cols-2">
                    {method.alternatives_considered.map((alternative, index) => (
                      <div key={`${method.id}-alternative-${index}`} className="rounded-md bg-surface-50 px-3 py-3 ring-1 ring-inset ring-surface-200">
                        <p className="text-xs font-semibold text-surface-700">{alternative.title}</p>
                        <p className="mt-1 text-xs leading-5 text-surface-600">{alternative.description}</p>
                        <p className="mt-2 text-[11px] leading-4 text-surface-500"><span className="font-medium">Why not selected:</span> {alternative.rejection_reason}</p>
                        <p className="mt-1 text-[11px] leading-4 text-surface-500"><span className="font-medium">Reconsider when:</span> {alternative.reconsider_when || "No trigger recorded"}</p>
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-2 text-xs text-surface-400">No alternatives recorded.</p>}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="mt-4 rounded-lg bg-amber-50 px-4 py-3 text-xs text-amber-800">No method specification is available for approval.</p>
      )}
    </section>
  );
}

function ExperimentInspector({ experiments, methods }: {
  experiments: ResearchPlanBundle["experiments"];
  methods: ResearchPlanBundle["methods"];
}) {
  const methodsById = new Map(methods.map((method) => [method.id, method]));
  return (
    <section className="mt-8 border-t border-surface-200 pt-6" aria-labelledby="experiment-plan-heading">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <h3 id="experiment-plan-heading" className="text-sm font-semibold text-surface-800">External experiment specifications</h3>
          <p className="mt-1 text-xs leading-5 text-surface-500">Inspect every dataset, leakage, metric, statistical, reproducibility, and acceptance requirement before approving the plan. Nothing shown here has been executed.</p>
        </div>
        <span className="text-xs text-surface-400">{experiments.length} protocol{experiments.length === 1 ? "" : "s"}</span>
      </div>

      {experiments.length ? (
        <div className="mt-4 space-y-5">
          {experiments.map((experiment) => {
            const method = methodsById.get(experiment.method_id);
            return (
            <article key={experiment.id} className="rounded-lg bg-white px-4 py-4 ring-1 ring-inset ring-surface-200">
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h4 className="text-sm font-semibold text-surface-800">{experiment.title}</h4>
                  <p className="mt-1 text-sm leading-5 text-surface-600">{experiment.purpose}</p>
                </div>
                <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800">Awaiting external execution</span>
              </div>

              <div className="mt-4">
                <DefinitionList items={[
                  ["Hypotheses", experiment.hypothesis_ids.join(", ") || "Not specified"],
                  ["Method lineage", method ? `${method.title} (${method.id})` : `${experiment.method_id} (unresolved)`],
                  ["Statistical analysis", experiment.statistical_plan],
                  ["Seeds / repetitions", experiment.seeds_or_repetitions],
                ]} />
              </div>

              <div className="mt-6">
                <h5 className="text-xs font-semibold text-surface-700">Datasets and leakage controls</h5>
                <div className="mt-2 grid gap-3 md:grid-cols-2">
                  {experiment.datasets.length ? experiment.datasets.map((dataset, index) => (
                    <div key={`${experiment.id}-dataset-${dataset.name}-${index}`} className="rounded-md bg-surface-50 px-3 py-3 ring-1 ring-inset ring-surface-200">
                      <p className="text-xs font-semibold text-surface-700">{dataset.name}</p>
                      <p className="mt-1 text-xs leading-5 text-surface-600">{dataset.purpose}</p>
                      <p className="mt-2 text-[11px] leading-4 text-surface-500"><span className="font-medium">Split / sampling:</span> {dataset.split_or_sampling}</p>
                      <p className="mt-1 text-[11px] leading-4 text-surface-500"><span className="font-medium">Access / license:</span> {dataset.access_or_license_notes || "Not specified"}</p>
                      <div className="mt-3"><ListBlock title="Leakage checks" items={dataset.leakage_checks} empty="No leakage checks recorded." /></div>
                    </div>
                  )) : <p className="text-xs text-amber-800">No dataset specification recorded.</p>}
                </div>
              </div>

              <div className="mt-6">
                <h5 className="text-xs font-semibold text-surface-700">Metrics and decision thresholds</h5>
                <div className="mt-2 space-y-2">
                  {experiment.metrics.length ? experiment.metrics.map((metric, index) => (
                    <div key={`${experiment.id}-metric-${metric.name}-${index}`} className="rounded-md bg-surface-50 px-3 py-3 ring-1 ring-inset ring-surface-200">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-xs font-semibold text-surface-700">{metric.name}</p>
                        <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-surface-500 ring-1 ring-inset ring-surface-200">{metric.direction.replace(/_/g, " ")}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-surface-600">{metric.definition}</p>
                      <p className="mt-1 text-[11px] text-surface-500"><span className="font-medium">Success threshold:</span> {metric.success_threshold || "Not specified"}</p>
                    </div>
                  )) : <p className="text-xs text-amber-800">No metric specification recorded.</p>}
                </div>
              </div>

              <div className="mt-6 grid gap-6 md:grid-cols-2">
                <ListBlock title="Baselines" items={experiment.baselines} empty="No baselines recorded." />
                <ListBlock title="Controls" items={experiment.controls} empty="No controls recorded." />
                <ListBlock title="Ablations" items={experiment.ablations} empty="No ablations recorded." />
                <ListBlock title="Negative tests" items={experiment.negative_tests} empty="No negative tests recorded." />
                <ListBlock title="Stop conditions" items={experiment.stop_conditions} empty="No stop conditions recorded." />
                <ListBlock title="Expected artifacts" items={experiment.expected_artifacts} empty="No expected artifacts recorded." />
                <ListBlock title="Acceptance criteria" items={experiment.acceptance_criteria} empty="No acceptance criteria recorded." />
                <ListBlock title="Risks" items={experiment.risks} empty="No experiment risks recorded." />
              </div>
            </article>
            );
          })}
        </div>
      ) : (
        <p className="mt-4 rounded-lg bg-amber-50 px-4 py-3 text-xs text-amber-800">No experiment protocol is available for approval.</p>
      )}
    </section>
  );
}

function PlanSectionCard({ section }: { section: PlanArtifactSection }) {
  return (
    <article className="rounded-lg bg-white ring-1 ring-inset ring-surface-200">
      <div className="flex items-center gap-3 border-b border-surface-200 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-surface-800">{section.title}</h3>
          {section.summary && <p className="mt-0.5 text-xs text-surface-500">{section.summary}</p>}
        </div>
        <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-medium", section.status === "evidence_backed" ? "bg-emerald-50 text-emerald-700" : section.status === "needs_attention" ? "bg-amber-50 text-amber-800" : "bg-surface-100 text-surface-600")}>
          {section.status === "evidence_backed" ? "source linked" : section.status.replace(/_/g, " ")}
        </span>
      </div>
      <div className="px-4 py-4">
        <p className="whitespace-pre-wrap text-sm leading-6 text-surface-700">{section.content}</p>
        <p className="mt-3 text-[11px] text-surface-400">{section.evidence_refs.length} declared claim-to-source link{section.evidence_refs.length === 1 ? "" : "s"}</p>
      </div>
    </article>
  );
}

function HypothesisArtifact({ plan, selectedId, onSelect }: {
  plan: ResearchPlanBundle;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const selected = plan.hypotheses.find((item) => item.id === selectedId) ?? plan.hypotheses[0];
  return (
    <section>
      <ArtifactHeading icon={<GitBranch className="h-5 w-5" />} title="Proposed hypotheses" description="Inspect evidence state, falsifiable predictions, validation needs, and counterarguments. Approving the whole plan accepts its proposed direction; request a revision to change it." />
      {plan.hypotheses.length === 0 ? (
        <EmptyArtifact icon={<Lightbulb className="h-6 w-6" />} title="No proposed hypotheses" description="Revise the plan to generate explicit, falsifiable hypotheses." />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg ring-1 ring-inset ring-surface-200">
            <table className="w-full min-w-[720px] border-collapse text-left text-xs">
              <thead className="bg-surface-50 text-surface-500">
                <tr>
                  <th className="px-3 py-2.5 font-medium">Hypothesis</th>
                  <th className="px-3 py-2.5 font-medium">Evidence state</th>
                  <th className="px-3 py-2.5 text-center font-medium">Predictions</th>
                  <th className="px-3 py-2.5 text-center font-medium">Minimum tests</th>
                  <th className="px-3 py-2.5 text-center font-medium">Dependencies</th>
                  <th className="px-3 py-2.5 text-center font-medium">Risks</th>
                  <th className="px-3 py-2.5 font-medium">Plan state</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-200">
                {plan.hypotheses.map((hypothesis) => (
                  <tr key={hypothesis.id} className={clsx("hover:bg-surface-50", selected?.id === hypothesis.id && "bg-accent-50/50")}>
                    <td className="px-3 py-3">
                      <button type="button" className="w-full text-left" onClick={() => onSelect(hypothesis.id)} aria-pressed={selected?.id === hypothesis.id}>
                        <span className="block font-medium text-surface-800">{hypothesis.title}</span>
                        <span className="mt-0.5 block max-w-md text-[11px] leading-4 text-surface-500">{hypothesis.statement}</span>
                      </button>
                    </td>
                    <td className="px-3 py-3"><span className="rounded-full bg-surface-100 px-2 py-1 text-[10px] font-medium capitalize text-surface-600">{hypothesis.status === "evidence_backed" ? "source linked (declared)" : "proposed"}</span></td>
                    <td className="px-3 py-3 text-center font-mono text-surface-600">{hypothesis.falsifiable_predictions.length}</td>
                    <td className="px-3 py-3 text-center font-mono text-surface-600">{hypothesis.minimum_validation.length}</td>
                    <td className="px-3 py-3 text-center font-mono text-surface-600">{hypothesis.dependencies.length}</td>
                    <td className="px-3 py-3 text-center font-mono text-surface-600">{hypothesis.risks.length}</td>
                    <td className="px-3 py-3">
                      <span className="rounded-full bg-accent-50 px-2 py-1 text-[10px] font-medium text-accent-700">Included in plan</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="mt-6 space-y-5 border-t border-surface-200 pt-6">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-surface-900">{selected.title}</h3>
                <span className="rounded-full bg-surface-100 px-2 py-0.5 text-[11px] font-medium capitalize text-surface-600">{selected.status === "evidence_backed" ? "source linked (declared)" : "proposed"}</span>
              </div>
              <DefinitionList items={[
                ["Rationale", selected.rationale],
                ["Falsifiable predictions", selected.falsifiable_predictions.join("; ")],
                ["Difference from prior work", selected.differentiation_from_prior_work],
                ["Strongest counterargument", selected.strongest_counterargument],
                ["Minimum external tests", selected.minimum_validation.join("; ")],
              ]} />
              <div className="grid gap-6 md:grid-cols-2">
                <ListBlock title="Dependencies" items={selected.dependencies} empty="No hypothesis dependencies recorded." />
                <ListBlock title="Risks" items={selected.risks} empty="No risks recorded." />
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ImplementationArtifact({ plan }: { plan: ResearchPlanBundle }) {
  const implementation = plan.implementation_plan;
  return (
    <section>
      <ArtifactHeading
        icon={<ListChecks className="h-5 w-5" />}
        title="Implementation handoff"
        description="Work packages are specifications for a human, Coding Agent, or external team. No task here represents completed execution."
        trailing={<span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800">Awaiting external execution</span>}
      />

      <DefinitionList items={[
        ["Implementation objective", implementation.objective],
        ["Architecture or method", implementation.summary],
      ]} />
      <div className="space-y-3">
        <h3 className="mt-7 text-sm font-semibold text-surface-800">Work packages</h3>
        {implementation.tasks.length ? implementation.tasks.map((task, index) => (
          <TaskRow key={task.id} task={task} index={index} />
        )) : (
          <EmptyArtifact icon={<ListChecks className="h-6 w-6" />} title="No work packages" description="Revise the plan to generate dependency-aware implementation tasks and acceptance criteria." />
        )}
      </div>

      {implementation.milestones.length > 0 && (
        <div className="mt-8 border-t border-surface-200 pt-6">
          <h3 className="text-sm font-semibold text-surface-800">Milestones</h3>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {implementation.milestones.map((milestone) => (
              <article key={milestone.id} className="rounded-lg bg-surface-50 px-4 py-3 ring-1 ring-inset ring-surface-200">
                <p className="text-sm font-medium text-surface-800">{milestone.title}</p>
                <p className="mt-1 text-[11px] text-surface-500">Work packages: {milestone.task_ids.join(", ") || "None specified"}</p>
                <div className="mt-3"><ListBlock title="Exit criteria" items={milestone.exit_criteria} empty="No exit criteria recorded." /></div>
              </article>
            ))}
          </div>
        </div>
      )}

      <div className="mt-8 grid gap-7 border-t border-surface-200 pt-6 md:grid-cols-3">
        <ListBlock title="Resource assumptions" items={implementation.resource_assumptions} empty="No resource assumptions recorded." />
        <ListBlock title="Fallback strategies" items={implementation.fallback_strategies} empty="No fallback strategies recorded." />
        <ListBlock title="Unresolved decisions" items={implementation.unresolved_decisions} empty="No unresolved decisions recorded." />
      </div>

      <div className="mt-8 border-t border-surface-200 pt-6">
        <h3 className="text-sm font-semibold text-surface-800">External handoff specification</h3>
        <p className="mt-1 text-xs leading-5 text-surface-500">These requirements define what may be transferred and what an external executor must return. They do not indicate execution.</p>
        <div className="mt-4">
          <DefinitionList items={[
            ["Target roles", implementation.handoff.target_roles.join(", ") || "Not specified"],
            ["Human approval", implementation.handoff.human_approval_required ? "Required before external execution" : "Not required by the generated specification"],
            ["Recorded handoff state", implementation.handoff.status.replace(/_/g, " ")],
          ]} />
        </div>
        <div className="mt-6 grid gap-7 md:grid-cols-2">
          <ListBlock title="Prerequisites" items={implementation.handoff.prerequisites} empty="No prerequisites recorded." />
          <ListBlock title="Included artifacts" items={implementation.handoff.included_artifacts} empty="No included artifacts recorded." />
          <ListBlock title="External execution instructions" items={implementation.handoff.execution_instructions} empty="No execution instructions recorded." />
          <ListBlock title="External result contract" items={implementation.handoff.external_result_contract} empty="No external result contract recorded." />
        </div>
      </div>
    </section>
  );
}

function TaskRow({ task, index }: { task: ImplementationTask; index: number }) {
  const [open, setOpen] = useState(index === 0);
  return (
    <article className="rounded-lg bg-white ring-1 ring-inset ring-surface-200">
      <button className="flex w-full items-start gap-3 px-4 py-3 text-left" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="mt-0.5 flex h-5 w-5 items-center justify-center rounded-md bg-surface-100 font-mono text-[10px] text-surface-500">{index + 1}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-surface-800">{task.title}</span>
          <span className="mt-0.5 block text-xs text-surface-500">{task.deliverable}</span>
        </span>
        <span className="rounded-full bg-surface-50 px-2 py-0.5 text-[10px] text-surface-500 ring-1 ring-inset ring-surface-200">{task.owner_role}</span>
        <ChevronRight className={clsx("mt-0.5 h-4 w-4 text-surface-400 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="border-t border-surface-200 px-4 py-4 pl-12">
          <p className="text-sm leading-5 text-surface-700">{task.objective}</p>
          <div className="mt-4 grid gap-5 md:grid-cols-3">
            <ListBlock title="Tasks" items={task.tasks} empty="No task steps recorded." />
            <ListBlock title="Inputs" items={task.inputs} empty="No inputs recorded." />
            <ListBlock title="Outputs" items={task.outputs} empty="No outputs recorded." />
          </div>
          <div className="mt-5">
            <h4 className="text-xs font-semibold text-surface-700">Interface contracts</h4>
            {task.interface_contracts.length ? (
              <div className="mt-2 grid gap-3 lg:grid-cols-2">
                {task.interface_contracts.map((contract, contractIndex) => (
                  <div key={`${task.id}-${contract.name}-${contractIndex}`} className="rounded-md bg-surface-50 px-3 py-3 ring-1 ring-inset ring-surface-200">
                    <p className="text-xs font-semibold text-surface-700">{contract.name}</p>
                    <div className="mt-3 grid gap-4 sm:grid-cols-3">
                      <ListBlock title="Inputs" items={contract.inputs} empty="None specified." />
                      <ListBlock title="Outputs" items={contract.outputs} empty="None specified." />
                      <ListBlock title="Invariants" items={contract.invariants} empty="None specified." />
                    </div>
                  </div>
                ))}
              </div>
            ) : <p className="mt-2 text-xs text-surface-400">No interface contracts recorded.</p>}
          </div>
          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <ListBlock title="Acceptance criteria" items={task.acceptance_criteria} empty="No acceptance criteria." />
            <ListBlock title="Risks" items={task.risks} empty="No risks recorded." />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-surface-100 pt-3">
            <span className="text-[11px] font-medium text-surface-500">Planning state</span>
            <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-800">Planned for external execution</span>
            <span className="text-[11px] text-surface-500"><span className="font-medium">Owner:</span> {task.owner_role}</span>
            <span className="text-[11px] text-surface-500"><span className="font-medium">Effort:</span> {task.effort_estimate || "Not estimated"}</span>
            <span className="text-[11px] text-surface-500"><span className="font-medium">Dependencies:</span> {task.dependencies.join(", ") || "None"}</span>
          </div>
        </div>
      )}
    </article>
  );
}

function ReviewArtifact({ plan, review, onReview }: { plan: ResearchPlanBundle; review: ResearchPlanReview | null; onReview: () => void }) {
  return (
    <section>
      <ArtifactHeading icon={<ShieldCheck className="h-5 w-5" />} title="Independent review" description="A separate review pass checks evidence, novelty, method, experiment design, statistics, implementation, and risk." />
      {!review ? (
        <EmptyArtifact
          icon={<ShieldCheck className="h-6 w-6" />}
          title="Plan has not been independently reviewed"
          description="Run review before approval. Open Blocker and Major issues must be addressed through a revised plan before approval."
          action={<button className="btn-primary gap-1.5 text-xs" onClick={onReview} disabled={plan.status !== "draft"}><ShieldCheck className="h-3.5 w-3.5" />Review plan</button>}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-start gap-3 rounded-lg bg-surface-50 px-4 py-4 ring-1 ring-inset ring-surface-200">
            <ReviewVerdict verdict={review.verdict} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-surface-800">Review round {review.review_round}</p>
              <p className="mt-1 text-sm leading-5 text-surface-600">{review.summary}</p>
            </div>
          </div>
          <div className="mt-6 space-y-3">
            {review.issues.length ? review.issues.map((issue) => <ReviewIssueRow key={issue.id} issue={issue} />) : (
              <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <CheckCircle2 className="h-4 w-4" /> No review issues found.
              </div>
            )}
          </div>
          <div className="mt-8 border-t border-surface-200 pt-6">
            <h3 className="text-sm font-semibold text-surface-800">Current contract for approval</h3>
            <p className="mt-1 text-xs leading-5 text-surface-500">Approval applies to every requirement and boundary in this current plan version.</p>
            <div className="mt-4">
              <ContractDetails contract={plan.contract} />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function ReviewPanel({ review, busy, canReview, onReview }: { review: ResearchPlanReview | null; busy: boolean; canReview: boolean; onReview: () => void }) {
  if (!review) {
    return (
      <div>
        <div className="flex items-center gap-2 text-sm font-semibold text-surface-800"><ShieldCheck className="h-4 w-4 text-accent-600" />Independent review</div>
        <p className="mt-2 text-xs leading-5 text-surface-500">No approval is available until a separate review checks evidence and implementation readiness.</p>
        <button className="btn-secondary mt-4 w-full gap-1.5 text-xs" onClick={onReview} disabled={busy || !canReview}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          Review plan
        </button>
      </div>
    );
  }
  const open = review.issues.filter((issue) => issue.status === "open");
  return (
    <div>
      <div className="flex items-center gap-2"><ReviewVerdict verdict={review.verdict} /><span className="text-xs text-surface-400">Round {review.review_round}</span></div>
      <p className="mt-3 text-xs leading-5 text-surface-600">{review.summary}</p>
      <div className="mt-5 space-y-2">
        <ReviewCount severity="blocker" count={open.filter((issue) => issue.severity === "blocker").length} />
        <ReviewCount severity="major" count={open.filter((issue) => issue.severity === "major").length} />
        <ReviewCount severity="minor" count={open.filter((issue) => issue.severity === "minor").length} />
      </div>
      {open.slice(0, 3).map((issue) => (
        <div key={issue.id} className="mt-4 border-t border-surface-200 pt-3">
          <p className="text-[11px] font-semibold text-surface-700">{issue.artifact}</p>
          <p className="mt-1 text-xs leading-4 text-surface-600">{issue.problem}</p>
        </div>
      ))}
    </div>
  );
}

function ReviewVerdict({ verdict }: { verdict: ResearchPlanReview["verdict"] }) {
  const config = verdict === "approve"
    ? { label: "Approve", style: "bg-emerald-50 text-emerald-800", icon: CheckCircle2 }
    : verdict === "blocked"
      ? { label: "Blocked", style: "bg-red-50 text-red-800", icon: AlertTriangle }
      : { label: "Revise", style: "bg-amber-50 text-amber-800", icon: RefreshCw };
  const Icon = config.icon;
  return <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold", config.style)}><Icon className="h-3 w-3" />{config.label}</span>;
}

function ReviewCount({ severity, count }: { severity: ReviewIssue["severity"]; count: number }) {
  const dot = severity === "blocker" ? "bg-red-500" : severity === "major" ? "bg-amber-500" : "bg-surface-400";
  return <div className="flex items-center gap-2 text-xs text-surface-600"><span className={clsx("h-2 w-2 rounded-full", dot)} /><span className="capitalize">{severity}</span><span className="ml-auto font-mono">{count}</span></div>;
}

function ReviewIssueRow({ issue }: { issue: ReviewIssue }) {
  const severityStyle = issue.severity === "blocker" ? "bg-red-50 text-red-800" : issue.severity === "major" ? "bg-amber-50 text-amber-800" : "bg-surface-100 text-surface-600";
  return (
    <article className="rounded-lg bg-white px-4 py-4 ring-1 ring-inset ring-surface-200">
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize", severityStyle)}>{issue.severity}</span>
        <span className="text-xs font-medium text-surface-700">{issue.artifact}</span>
        <span className="ml-auto text-[11px] capitalize text-surface-400">{issue.status.replace("_", " ")}</span>
      </div>
      <p className="mt-3 text-sm font-medium text-surface-800">{issue.problem}</p>
      <p className="mt-1 text-sm leading-5 text-surface-600">{issue.impact}</p>
      <div className="mt-3 rounded-md bg-surface-50 px-3 py-2 text-xs leading-5 text-surface-700">
        <span className="font-semibold">Required fix:</span> {issue.required_fix}
      </div>
    </article>
  );
}

function DefinitionList({ items }: { items: Array<[string, string]> }) {
  return (
    <dl className="divide-y divide-surface-200 rounded-lg ring-1 ring-inset ring-surface-200">
      {items.map(([label, value]) => (
        <div key={label} className="grid gap-1 px-4 py-3 sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-5">
          <dt className="text-xs font-medium text-surface-500">{label}</dt>
          <dd className="text-sm leading-5 text-surface-700">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ListBlock({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-surface-700">{title}</h3>
      {items.length ? (
        <ul className="mt-2 space-y-1.5">
          {items.map((item, index) => <li key={`${item}-${index}`} className="flex items-start gap-2 text-xs leading-5 text-surface-600"><Circle className="mt-1.5 h-1.5 w-1.5 shrink-0 fill-current text-surface-400" />{item}</li>)}
        </ul>
      ) : <p className="mt-2 text-xs text-surface-400">{empty}</p>}
    </div>
  );
}

function EmptyArtifact({ icon, title, description, action }: { icon: ReactNode; title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-56 flex-col items-center justify-center rounded-lg bg-surface-50 px-6 py-10 text-center ring-1 ring-inset ring-surface-200">
      <span className="text-surface-300" aria-hidden="true">{icon}</span>
      <h3 className="mt-3 text-sm font-semibold text-surface-700">{title}</h3>
      <p className="mt-1 max-w-md text-xs leading-5 text-surface-500">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

import { useState, useCallback, useRef, useEffect } from "react";
import React from "react";
import {
  Plus, Trash2, Copy, ChevronUp, ChevronDown, ChevronRight,
  FileText, Pencil, Link2, X, Check, MoreHorizontal,
  Sparkles, RotateCcw, Loader2, AlertCircle,
} from "lucide-react";
import clsx from "clsx";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useSourceStore } from "@/store/sourceStore";
import { useRunStore, type SectionPreview } from "@/store/runStore";
import { usePaperStore } from "@/store/paperStore";
import { api } from "@/api/client";
import { MarkdownRenderer } from "./shared/MarkdownRenderer";
import type { Deliverable, DeliverableType, DeliverableSection, WorkspaceSource } from "@/types";


const TYPE_LABELS: Record<DeliverableType, string> = {
  deep_research: "Deep Research",
  proposal: "Proposal",
  research_plan: "Research Plan",
  notes: "Notes",
};

const TYPE_DESCRIPTIONS: Record<DeliverableType, string> = {
  deep_research: "Structured brief for exploring a research problem in depth.",
  proposal: "Full proposal draft with problem, method, and evaluation plan.",
  research_plan: "Planning document for organizing your research process.",
  notes: "Freeform notes, questions, and ideas.",
};

function buildSourcesPayload(sources: WorkspaceSource[]) {
  return sources
    .map((s) => ({
      id: s.id,
      title: s.title,
      authors: s.authors,
      year: s.year,
      abstract: s.abstract,
      provider: s.provider,
      paper_id: s.paper_id,
    }));
}

function buildSectionsPayload(sections: DeliverableSection[]) {
  return sections.map((s) => ({
    id: s.id,
    title: s.title,
    content: s.content,
    order: s.order,
    linkedSourceIds: s.linkedSourceIds,
  }));
}

function useRunDraft(deliverable: Deliverable | null, workspaceId: string) {
  const { getIncludedSources } = useSourceStore();
  const { activePaper } = usePaperStore();
  const { applyAIContent } = useDeliverableStore();
  const { startRun, setResult, setFailed, setBlocked } = useRunStore();
  const sources = getIncludedSources(workspaceId);

  const run = useCallback(
    async (action: string, selectedSectionId?: string, revisionInstruction?: string) => {
      if (!deliverable) return;
      startRun(action);
      try {
        const store = useRunStore.getState();
        store.setStatus("generating");

        const payload = {
          action,
          workspace_id: workspaceId,
          deliverable_id: deliverable.id,
          deliverable_type: deliverable.type,
          deliverable_title: deliverable.title,
          sections: buildSectionsPayload(deliverable.sections),
          sources: buildSourcesPayload(sources),
          selected_section_id: selectedSectionId ?? null,
          revision_instruction: revisionInstruction ?? null,
          active_paper_id: activePaper?.id ?? null,
        };

        await api.runDraftStream(payload, (event) => {
          const s = useRunStore.getState();
          const type = event.type as string;

          if (type === "stage") {
            const msg = event.message as string | undefined;
            s.setStatus("generating", msg);
          } else if (type === "blocked") {
            s.setStatus("blocked", (event.message as string) ?? "Blocked");
          } else if (type === "error") {
            s.setStatus("failed", (event.message as string) ?? "Generation failed");
          } else if (type === "result") {
            const data = event.data as Record<string, unknown>;
            const updates = (data.updates as { sectionId: string; mode: string; generatedContent: string; sourceIdsUsed: string[]; notes?: string }[]) ?? [];
            const skipped = (data.skippedSectionIds as string[]) ?? [];
            const message = data.message as string | undefined;

            if (data.status === "blocked") {
              s.setStatus("blocked", message ?? "Blocked");
            } else if (data.status === "failed") {
              s.setStatus("failed", message ?? "Generation failed");
            } else {
              const allPreviews = updates.map((u) => ({
                sectionId: u.sectionId,
                mode: u.mode as "fill_empty" | "preview_replace",
                generatedContent: u.generatedContent,
                sourceIdsUsed: u.sourceIdsUsed,
                notes: u.notes,
              }));

              // Auto-apply fill_empty results (no user review needed for empty sections)
              const fillEmpty = allPreviews.filter((p) => p.mode === "fill_empty");
              const needsReview = allPreviews.filter((p) => p.mode === "preview_replace");

              for (const p of fillEmpty) {
                applyAIContent(workspaceId, deliverable!.id, p.sectionId, p.generatedContent, "draft", p.sourceIdsUsed);
              }

              if (needsReview.length > 0) {
                setResult(needsReview, skipped, message);
              } else {
                setResult([], skipped, message ?? `Auto-applied ${fillEmpty.length} section${fillEmpty.length !== 1 ? "s" : ""}.`);
              }
            }
          }
        });

        // Fallback if stream ended without terminal event
        const finalStatus = useRunStore.getState().status;
        if (finalStatus === "generating") {
          setFailed("Stream ended unexpectedly. Please try again.");
        }
      } catch (err: unknown) {
        setFailed(err instanceof Error ? err.message : "Request failed");
      }
    },
    [deliverable, workspaceId, sources, activePaper, startRun, setResult, setFailed, setBlocked, applyAIContent],
  );

  return run;
}

export function DeliverableView() {
  const { getActiveWorkspace } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";
  const { getDeliverables, getActiveDeliverable } = useDeliverableStore();
  const deliverables = getDeliverables(wid);
  const active = getActiveDeliverable(wid);
  const runDraft = useRunDraft(active, wid);
  const { status, message, previews, reset } = useRunStore();

  if (deliverables.length === 0) return <EmptyState workspaceId={wid} />;

  return (
    <div className="flex flex-col h-full min-w-0">
      <DeliverableHeader
        deliverable={active}
        deliverables={deliverables}
        workspaceId={wid}
        onDraftAll={() => runDraft("draft_deliverable")}
      />
      <RunStatusBar status={status} message={message} onDismiss={reset} />
      {active ? (
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <SectionOutline deliverable={active} workspaceId={wid} previews={previews} />
          <SectionEditor deliverable={active} workspaceId={wid} runDraft={runDraft} />
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-surface-400">
          Select a deliverable to begin editing.
        </div>
      )}
    </div>
  );
}

/* ── Empty state ─────────────────────────────────────────────────────── */

function EmptyState({ workspaceId }: { workspaceId: string }) {
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

/* ── Run status bar ─────────────────────────────────────────────────── */

function RunStatusBar({ status, message, onDismiss }: { status: string; message: string | null; onDismiss: () => void }) {
  if (status === "idle") return null;

  const isActive = status === "preparing" || status === "generating";
  const isError = status === "failed" || status === "blocked";

  return (
    <div
      className={clsx(
        "flex-shrink-0 flex items-center gap-2 px-4 py-1.5 text-xs border-b",
        isActive && "bg-accent-50 border-accent-200 text-accent-700",
        status === "awaiting_apply" && "bg-amber-50 border-amber-200 text-amber-700",
        status === "completed" && "bg-emerald-50 border-emerald-200 text-emerald-700",
        isError && "bg-red-50 border-red-200 text-red-700",
      )}
    >
      {isActive && <Loader2 className="w-3 h-3 animate-spin shrink-0" />}
      {isError && <AlertCircle className="w-3 h-3 shrink-0" />}
      <span className="truncate flex-1">
        {status === "preparing" && "Preparing draft..."}
        {status === "generating" && "Generating content..."}
        {status === "awaiting_apply" && "Review generated content below. Apply or discard each section."}
        {status === "completed" && (message ?? "Done.")}
        {isError && (message ?? "Something went wrong.")}
      </span>
      {!isActive && (
        <button onClick={onDismiss} className="p-0.5 rounded hover:bg-black/5 shrink-0">
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

/* ── Deliverable header + switcher ───────────────────────────────────── */

function DeliverableHeader({
  deliverable,
  deliverables,
  workspaceId,
  onDraftAll,
}: {
  deliverable: Deliverable | null;
  deliverables: Deliverable[];
  workspaceId: string;
  onDraftAll: () => void;
}) {
  const { setActiveDeliverable, deleteDeliverable, duplicateDeliverable, renameDeliverable, createDeliverable } =
    useDeliverableStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const handleStartRename = useCallback(() => {
    if (!deliverable) return;
    setRenameValue(deliverable.title);
    setRenaming(true);
    setMenuOpen(false);
  }, [deliverable]);

  const handleFinishRename = useCallback(() => {
    if (!deliverable || !renameValue.trim()) return;
    renameDeliverable(workspaceId, deliverable.id, renameValue.trim());
    setRenaming(false);
  }, [deliverable, renameValue, workspaceId, renameDeliverable]);

  return (
    <div className="flex-shrink-0 flex items-center gap-2 px-4 py-2 border-b border-surface-200 bg-white min-h-[44px]">
      {/* Switcher */}
      <div className="relative">
        <button
          onClick={() => setSwitcherOpen(!switcherOpen)}
          className="flex items-center gap-1 text-xs text-surface-500 hover:text-surface-700 transition-colors"
        >
          <ChevronRight className={clsx("w-3 h-3 transition-transform", switcherOpen && "rotate-90")} />
          <span className="font-medium">{deliverables.length} deliverable{deliverables.length !== 1 ? "s" : ""}</span>
        </button>
        {switcherOpen && (
          <div className="absolute left-0 top-full mt-1 z-20 bg-white border border-surface-200 rounded-lg shadow-lg py-1 min-w-[200px]">
            {deliverables.map((d) => (
              <button
                key={d.id}
                onClick={() => { setActiveDeliverable(workspaceId, d.id); setSwitcherOpen(false); }}
                className={clsx(
                  "w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2",
                  d.id === deliverable?.id ? "bg-accent-50 text-accent-700" : "text-surface-600 hover:bg-surface-50"
                )}
              >
                <FileText className="w-3 h-3 flex-shrink-0" />
                <span className="truncate">{d.title}</span>
                <span className="ml-auto text-[10px] text-surface-400">{TYPE_LABELS[d.type]}</span>
              </button>
            ))}
            <div className="border-t border-surface-100 mt-1 pt-1">
              {(["deep_research", "proposal", "research_plan", "notes"] as DeliverableType[]).map((type) => (
                <button
                  key={type}
                  onClick={() => { createDeliverable(workspaceId, type); setSwitcherOpen(false); }}
                  className="w-full text-left px-3 py-1.5 text-xs text-surface-500 hover:bg-surface-50 transition-colors flex items-center gap-2"
                >
                  <Plus className="w-3 h-3" />
                  New {TYPE_LABELS[type]}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Title */}
      {deliverable && (
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-surface-200">|</span>
          {renaming ? (
            <form
              onSubmit={(e) => { e.preventDefault(); handleFinishRename(); }}
              className="flex items-center gap-1 flex-1 min-w-0"
            >
              <input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={handleFinishRename}
                className="flex-1 min-w-0 text-xs font-medium text-surface-800 bg-transparent border-b border-accent-400 focus:outline-none py-0.5"
              />
              <button type="submit" className="p-0.5"><Check className="w-3 h-3 text-accent-600" /></button>
            </form>
          ) : (
            <>
              <span className="heading-serif text-sm text-surface-800 truncate">{deliverable.title}</span>
              <span className="text-[10px] text-surface-400 bg-surface-50 border border-surface-200 px-1.5 py-0.5 rounded shrink-0">
                {TYPE_LABELS[deliverable.type]}
              </span>
            </>
          )}
        </div>
      )}

      {/* Actions menu */}
      {deliverable && (
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={onDraftAll}
            disabled={useRunStore.getState().status === "preparing" || useRunStore.getState().status === "generating"}
            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-accent-700 bg-accent-50 border border-accent-200 rounded-md hover:bg-accent-100 disabled:opacity-50 transition-colors"
          >
            <Sparkles className="w-3 h-3" />
            Draft All
          </button>
          <div className="relative">
            <button onClick={() => setMenuOpen(!menuOpen)} className="p-1 rounded hover:bg-surface-100">
              <MoreHorizontal className="w-4 h-4 text-surface-400" />
            </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-20 bg-white border border-surface-200 rounded-lg shadow-lg py-1 min-w-[140px]">
              <button
                onClick={handleStartRename}
                className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              >
                <Pencil className="w-3 h-3" /> Rename
              </button>
              <button
                onClick={() => { duplicateDeliverable(workspaceId, deliverable.id); setMenuOpen(false); }}
                className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              >
                <Copy className="w-3 h-3" /> Duplicate
              </button>
              <button
                onClick={() => { deleteDeliverable(workspaceId, deliverable.id); setMenuOpen(false); }}
                className="w-full text-left px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 flex items-center gap-2"
              >
                <Trash2 className="w-3 h-3" /> Delete
              </button>
            </div>
          )}
        </div>
        </div>
      )}
    </div>
  );
}

/* ── Section outline ─────────────────────────────────────────────────── */

function SectionOutline({ deliverable, workspaceId, previews }: { deliverable: Deliverable; workspaceId: string; previews: SectionPreview[] }) {
  const { selectSection, getSelectedSectionId, addSection, moveSection, deleteSection } = useDeliverableStore();
  const selectedId = getSelectedSectionId(deliverable.id);
  const sorted = [...deliverable.sections].sort((a, b) => a.order - b.order);

  return (
    <div className="flex-shrink-0 w-52 border-r border-surface-200 bg-surface-50 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-100">
        <span className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">Outline</span>
        <button
          onClick={() => addSection(workspaceId, deliverable.id, sorted[sorted.length - 1]?.id)}
          className="p-0.5 rounded hover:bg-surface-200 transition-colors"
          title="Add section"
        >
          <Plus className="w-3.5 h-3.5 text-surface-400" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {sorted.map((sec, idx) => {
          const wordCount = sec.content.trim() ? sec.content.trim().split(/\s+/).length : 0;
          const hasContent = wordCount > 0;
          return (
          <div key={sec.id} className="group">
            <button
              onClick={() => selectSection(deliverable.id, sec.id)}
              className={clsx(
                "w-full text-left px-3 py-1.5 text-xs flex items-start gap-1.5 transition-colors",
                selectedId === sec.id
                  ? "bg-accent-50 text-accent-700 border-l-2 border-accent-500"
                  : "text-surface-600 hover:bg-surface-100 border-l-2 border-transparent"
              )}
            >
              <div className="flex-1 min-w-0">
                <span className="truncate block">{sec.title || "Untitled"}</span>
                <span className={clsx(
                  "text-[10px] mt-0.5 block",
                  hasContent ? "text-surface-400" : "text-surface-300 italic"
                )}>
                  {hasContent ? `${wordCount} words` : "empty"}
                </span>
              </div>
              <div className="flex items-center gap-0.5 shrink-0 mt-0.5">
                {previews.some((p) => p.sectionId === sec.id) && (
                  <Sparkles className="w-2.5 h-2.5 text-amber-500" />
                )}
                {sec.linkedSourceIds.length > 0 && (
                  <Link2 className="w-2.5 h-2.5 text-surface-300" />
                )}
                {hasContent && (
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 ml-0.5" title="Has content" />
                )}
              </div>
            </button>
            {selectedId === sec.id && (
              <div className="flex items-center gap-0.5 px-3 py-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={() => moveSection(workspaceId, deliverable.id, sec.id, "up")}
                  disabled={idx === 0}
                  className="p-0.5 rounded hover:bg-surface-200 disabled:opacity-30"
                  title="Move up"
                >
                  <ChevronUp className="w-3 h-3 text-surface-400" />
                </button>
                <button
                  onClick={() => moveSection(workspaceId, deliverable.id, sec.id, "down")}
                  disabled={idx === sorted.length - 1}
                  className="p-0.5 rounded hover:bg-surface-200 disabled:opacity-30"
                  title="Move down"
                >
                  <ChevronDown className="w-3 h-3 text-surface-400" />
                </button>
                <button
                  onClick={() => addSection(workspaceId, deliverable.id, sec.id)}
                  className="p-0.5 rounded hover:bg-surface-200"
                  title="Add section below"
                >
                  <Plus className="w-3 h-3 text-surface-400" />
                </button>
                {sorted.length > 1 && (
                  <button
                    onClick={() => deleteSection(workspaceId, deliverable.id, sec.id)}
                    className="p-0.5 rounded hover:bg-red-50"
                    title="Delete section"
                  >
                    <Trash2 className="w-3 h-3 text-surface-400 hover:text-red-500" />
                  </button>
                )}
              </div>
            )}
          </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Section editor ──────────────────────────────────────────────────── */

function SectionEditor({ deliverable, workspaceId, runDraft }: { deliverable: Deliverable; workspaceId: string; runDraft: (action: string, sectionId?: string, instruction?: string) => Promise<void> }) {
  const { getSelectedSectionId, applyAIContent } = useDeliverableStore();
  const { getSources } = useSourceStore();
  const sources = getSources(workspaceId);
  const { status, previews, removePreview, reset } = useRunStore();
  const selectedId = getSelectedSectionId(deliverable.id);
  const section = deliverable.sections.find((s) => s.id === selectedId);

  if (!section) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-surface-400">
        Select a section from the outline to start editing.
      </div>
    );
  }

  const preview = previews.find((p) => p.sectionId === section.id);
  const isRunning = status === "preparing" || status === "generating";

  const handleApply = (p: SectionPreview) => {
    const mode = p.mode === "fill_empty" ? "draft" : "revise";
    applyAIContent(workspaceId, deliverable.id, p.sectionId, p.generatedContent, mode as "draft" | "revise", p.sourceIdsUsed);
    removePreview(p.sectionId);
  };

  const handleDiscard = (sectionId: string) => {
    removePreview(sectionId);
  };

  return (
    <div className="flex-1 overflow-y-auto bg-white">
      <div className="max-w-2xl mx-auto px-6 py-6 space-y-4">
        <SectionTitleInput
          key={`title-${section.id}`}
          sectionId={section.id}
          deliverableId={deliverable.id}
          workspaceId={workspaceId}
          initialValue={section.title}
        />

        {/* AI action buttons */}
        <SectionAIActions
          section={section}
          isRunning={isRunning}
          onDraftSection={() => runDraft("draft_section", section.id)}
          onReviseSection={(instruction) => runDraft("revise_section", section.id, instruction)}
        />

        {/* Preview panel */}
        {preview && preview.generatedContent && (
          <PreviewPanel
            preview={preview}
            section={section}
            sources={sources}
            onApply={() => handleApply(preview)}
            onDiscard={() => handleDiscard(preview.sectionId)}
          />
        )}

        <SectionContentEditor
          key={`content-${section.id}`}
          sectionId={section.id}
          deliverableId={deliverable.id}
          workspaceId={workspaceId}
          initialValue={section.content}
        />

        {/* AI metadata */}
        {section.lastUpdatedBy === "ai" && (
          <div className="flex items-center gap-2 text-[10px] text-surface-400">
            <Sparkles className="w-2.5 h-2.5" />
            <span>Last {section.lastAIMode === "revise" ? "revised" : "drafted"} by AI</span>
            {section.lastSourceIdsUsed && section.lastSourceIdsUsed.length > 0 && (
              <span className="text-surface-300">
                · {section.lastSourceIdsUsed.length} source{section.lastSourceIdsUsed.length !== 1 ? "s" : ""} used
              </span>
            )}
          </div>
        )}

        <SourceLinker
          sectionId={section.id}
          deliverableId={deliverable.id}
          workspaceId={workspaceId}
          linkedSourceIds={section.linkedSourceIds}
        />
      </div>
    </div>
  );
}

/* ── Section AI actions ─────────────────────────────────────────────── */

function SectionAIActions({
  section,
  isRunning,
  onDraftSection,
  onReviseSection,
}: {
  section: DeliverableSection;
  isRunning: boolean;
  onDraftSection: () => void;
  onReviseSection: (instruction: string) => void;
}) {
  const [reviseOpen, setReviseOpen] = useState(false);
  const [instruction, setInstruction] = useState("");

  const handleRevise = () => {
    if (!instruction.trim()) return;
    onReviseSection(instruction.trim());
    setInstruction("");
    setReviseOpen(false);
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        onClick={onDraftSection}
        disabled={isRunning}
        className="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-accent-700 bg-accent-50 border border-accent-200 rounded-md hover:bg-accent-100 disabled:opacity-50 transition-colors"
      >
        <Sparkles className="w-3 h-3" />
        {section.content.trim() ? "Redraft Section" : "Draft Section"}
      </button>

      {section.content.trim() && (
        <>
          <button
            onClick={() => setReviseOpen(!reviseOpen)}
            disabled={isRunning}
            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-surface-600 bg-surface-50 border border-surface-200 rounded-md hover:bg-surface-100 disabled:opacity-50 transition-colors"
          >
            <RotateCcw className="w-3 h-3" />
            Revise
          </button>
          {reviseOpen && (
            <div className="w-full flex items-center gap-2 mt-1">
              <input
                autoFocus
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleRevise(); if (e.key === "Escape") setReviseOpen(false); }}
                placeholder="e.g. Make it more concise, add comparison..."
                className="flex-1 text-xs px-2.5 py-1.5 border border-surface-200 rounded-md bg-surface-50 focus:outline-none focus:ring-1 focus:ring-accent-400"
              />
              <button
                onClick={handleRevise}
                disabled={!instruction.trim()}
                className="px-2.5 py-1.5 text-[11px] font-medium text-white bg-accent-600 rounded-md hover:bg-accent-700 disabled:opacity-50 transition-colors"
              >
                Go
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── Preview panel ──────────────────────────────────────────────────── */

function PreviewPanel({
  preview,
  section,
  sources,
  onApply,
  onDiscard,
}: {
  preview: SectionPreview;
  section: DeliverableSection;
  sources: WorkspaceSource[];
  onApply: () => void;
  onDiscard: () => void;
}) {
  const [showDiff, setShowDiff] = useState(preview.mode !== "fill_empty" && !!section.content.trim());
  const usedSources = preview.sourceIdsUsed
    .map((id) => sources.find((s) => s.id === id))
    .filter(Boolean) as WorkspaceSource[];

  const isReplacement = preview.mode !== "fill_empty" && section.content.trim();

  return (
    <div className="border border-amber-200 rounded-lg bg-amber-50/50 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-amber-200 bg-amber-50">
        <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700">
          <Sparkles className="w-3 h-3" />
          {preview.mode === "fill_empty" ? "Generated Draft" : "Suggested Replacement"}
        </div>
        <div className="flex items-center gap-1.5">
          {isReplacement && (
            <button
              onClick={() => setShowDiff(!showDiff)}
              className={clsx(
                "px-1.5 py-0.5 text-[10px] font-medium rounded transition-colors",
                showDiff
                  ? "bg-amber-200 text-amber-800"
                  : "bg-white text-surface-500 border border-surface-200 hover:bg-surface-50"
              )}
            >
              Diff
            </button>
          )}
          <button
            onClick={onApply}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium text-white bg-accent-600 rounded hover:bg-accent-700 transition-colors"
          >
            <Check className="w-3 h-3" /> Apply
          </button>
          <button
            onClick={onDiscard}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium text-surface-600 bg-white border border-surface-200 rounded hover:bg-surface-50 transition-colors"
          >
            <X className="w-3 h-3" /> Discard
          </button>
        </div>
      </div>

      {showDiff && isReplacement ? (
        <DiffView oldText={section.content} newText={preview.generatedContent} />
      ) : (
        <div className="px-3 py-3 text-sm text-surface-700 leading-relaxed max-h-64 overflow-y-auto">
          <MarkdownRenderer content={preview.generatedContent} />
        </div>
      )}

      {usedSources.length > 0 && (
        <div className="px-3 py-2 border-t border-amber-100 flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] text-amber-600 font-medium">Sources used:</span>
          {usedSources.map((s) => (
            <span key={s.id} className="text-[10px] text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded">
              {s.title.length > 40 ? s.title.slice(0, 40) + "..." : s.title}
            </span>
          ))}
        </div>
      )}
      {preview.notes && (
        <div className="px-3 py-1.5 border-t border-amber-100 text-[10px] text-amber-600">
          {preview.notes}
        </div>
      )}
    </div>
  );
}

/* ── Diff view ─────────────────────────────────────────────────────────── */

function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");

  const diff = computeLineDiff(oldLines, newLines);

  return (
    <div className="max-h-72 overflow-y-auto text-xs font-mono leading-relaxed">
      {diff.map((line, i) => (
        <div
          key={i}
          className={clsx(
            "px-3 py-0.5 border-l-2",
            line.type === "removed" && "bg-red-50 border-red-300 text-red-700 line-through",
            line.type === "added" && "bg-emerald-50 border-emerald-300 text-emerald-700",
            line.type === "unchanged" && "border-transparent text-surface-500",
          )}
        >
          <span className="inline-block w-4 text-[10px] text-surface-300 select-none mr-2">
            {line.type === "removed" ? "−" : line.type === "added" ? "+" : " "}
          </span>
          {line.text || "\u00A0"}
        </div>
      ))}
    </div>
  );
}

type DiffLine = { type: "removed" | "added" | "unchanged"; text: string };

function computeLineDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const result: DiffLine[] = [];
  const maxLen = Math.max(oldLines.length, newLines.length);
  let oi = 0, ni = 0;

  while (oi < oldLines.length || ni < newLines.length) {
    if (oi < oldLines.length && ni < newLines.length) {
      if (oldLines[oi] === newLines[ni]) {
        result.push({ type: "unchanged", text: oldLines[oi] });
        oi++;
        ni++;
      } else {
        const lookAheadNew = newLines.indexOf(oldLines[oi], ni);
        const lookAheadOld = oldLines.indexOf(newLines[ni], oi);

        if (lookAheadNew !== -1 && (lookAheadOld === -1 || lookAheadNew - ni <= lookAheadOld - oi)) {
          while (ni < lookAheadNew) {
            result.push({ type: "added", text: newLines[ni] });
            ni++;
          }
        } else if (lookAheadOld !== -1) {
          while (oi < lookAheadOld) {
            result.push({ type: "removed", text: oldLines[oi] });
            oi++;
          }
        } else {
          result.push({ type: "removed", text: oldLines[oi] });
          result.push({ type: "added", text: newLines[ni] });
          oi++;
          ni++;
        }
      }
    } else if (oi < oldLines.length) {
      result.push({ type: "removed", text: oldLines[oi] });
      oi++;
    } else {
      result.push({ type: "added", text: newLines[ni] });
      ni++;
    }
  }

  return result;
}

/* ── Section title input ─────────────────────────────────────────────── */

function SectionTitleInput({
  sectionId, deliverableId, workspaceId, initialValue,
}: {
  sectionId: string; deliverableId: string; workspaceId: string; initialValue: string;
}) {
  const { updateSectionTitle } = useDeliverableStore();
  const [value, setValue] = useState(initialValue);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => { setValue(initialValue); }, [initialValue]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setValue(v);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => updateSectionTitle(workspaceId, deliverableId, sectionId, v), 300);
  }, [workspaceId, deliverableId, sectionId, updateSectionTitle]);

  return (
    <input
      value={value}
      onChange={handleChange}
      placeholder="Section title"
      className="w-full heading-serif text-lg text-surface-800 bg-transparent border-none focus:outline-none focus:ring-0 placeholder:text-surface-300"
    />
  );
}

/* ── Section content editor ──────────────────────────────────────────── */

function SectionContentEditor({
  sectionId, deliverableId, workspaceId, initialValue,
}: {
  sectionId: string; deliverableId: string; workspaceId: string; initialValue: string;
}) {
  const { updateSectionContent } = useDeliverableStore();
  const [value, setValue] = useState(initialValue);
  const [editing, setEditing] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setValue(initialValue); }, [initialValue]);

  // Auto-resize
  useEffect(() => {
    if (!editing) return;
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(el.scrollHeight, 120)}px`;
  }, [value, editing]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setValue(v);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => updateSectionContent(workspaceId, deliverableId, sectionId, v), 300);
  }, [workspaceId, deliverableId, sectionId, updateSectionContent]);

  if (!editing && value.trim()) {
    return (
      <div
        onClick={() => setEditing(true)}
        className="min-h-[120px] text-sm leading-relaxed cursor-text hover:bg-surface-50 rounded-lg px-1 py-1 -mx-1 transition-colors"
        style={{ color: '#3d3830' }}
        title="Click to edit"
      >
        <MarkdownRenderer content={value} />
      </div>
    );
  }

  return (
    <div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onFocus={() => setEditing(true)}
        onBlur={() => { if (value.trim()) setEditing(false); }}
        placeholder="Start writing..."
        autoFocus={editing}
        className="w-full min-h-[120px] text-sm text-surface-700 leading-relaxed bg-transparent border-none focus:outline-none focus:ring-0 resize-none placeholder:text-surface-300"
      />
      {!value.trim() && !editing && (
        <p className="text-[11px] text-surface-400 mt-1">
          Run Deep Research or ask the Console to draft this section
        </p>
      )}
    </div>
  );
}

/* ── Source linker ────────────────────────────────────────────────────── */

function SourceLinker({
  sectionId, deliverableId, workspaceId, linkedSourceIds,
}: {
  sectionId: string; deliverableId: string; workspaceId: string; linkedSourceIds: string[];
}) {
  const { linkSourceToSection, unlinkSourceFromSection } = useDeliverableStore();
  const { getSources } = useSourceStore();
  const sources = getSources(workspaceId);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");

  const linkedSources: WorkspaceSource[] = linkedSourceIds
    .map((id) => sources.find((s) => s.id === id))
    .filter(Boolean) as WorkspaceSource[];

  const available = sources.filter(
    (s) => !linkedSourceIds.includes(s.id)
  );

  const filtered = search.trim()
    ? available.filter((s) => s.title.toLowerCase().includes(search.toLowerCase()))
    : available;

  return (
    <div className="border-t border-surface-100 pt-4 mt-4">
      <div className="flex items-center gap-2 mb-2">
        <Link2 className="w-3.5 h-3.5 text-surface-400" />
        <span className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">Linked Sources</span>
        <button
          onClick={() => setPickerOpen(!pickerOpen)}
          className="ml-auto p-0.5 rounded hover:bg-surface-100 transition-colors"
          title="Link a source"
        >
          <Plus className="w-3.5 h-3.5 text-surface-400" />
        </button>
      </div>

      {linkedSources.length === 0 && !pickerOpen && (
        <p className="text-[11px] text-surface-400">No sources linked to this section.</p>
      )}

      {linkedSources.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {linkedSources.map((s) => (
            <span
              key={s.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-accent-50 border border-accent-200 text-accent-700"
            >
              <span className="truncate max-w-[180px]">{s.title}</span>
              <button
                onClick={() => unlinkSourceFromSection(workspaceId, deliverableId, sectionId, s.id)}
                className="p-0.5 rounded hover:bg-accent-100"
              >
                <X className="w-2.5 h-2.5" />
              </button>
            </span>
          ))}
        </div>
      )}

      {pickerOpen && (
        <div className="border border-surface-200 rounded-lg bg-white shadow-sm overflow-hidden">
          <div className="p-2 border-b border-surface-100">
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search sources..."
              className="w-full text-xs px-2 py-1 border border-surface-200 rounded bg-surface-50 focus:outline-none focus:ring-1 focus:ring-accent-400"
            />
          </div>
          <div className="max-h-32 overflow-y-auto">
            {filtered.length === 0 ? (
              <p className="text-[11px] text-surface-400 px-3 py-2">No sources available.</p>
            ) : (
              filtered.map((s) => (
                <button
                  key={s.id}
                  onClick={() => {
                    linkSourceToSection(workspaceId, deliverableId, sectionId, s.id);
                    setSearch("");
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 transition-colors flex items-center gap-2"
                >
                  <Plus className="w-3 h-3 text-surface-400 shrink-0" />
                  <span className="truncate">{s.title}</span>
                  {s.provider === "upload" && <span className="text-[10px] text-accent-500 ml-auto shrink-0">uploaded</span>}
                </button>
              ))
            )}
          </div>
          <div className="border-t border-surface-100 px-2 py-1.5 flex justify-end">
            <button
              onClick={() => { setPickerOpen(false); setSearch(""); }}
              className="text-[11px] text-surface-500 hover:text-surface-700"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

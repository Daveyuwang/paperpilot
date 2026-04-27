import { useState, useCallback, useRef, useEffect } from "react";
import React from "react";
import {
  Plus, Link2, X, Check,
  Sparkles, RotateCcw,
} from "lucide-react";
import clsx from "clsx";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useRunStore, type SectionPreview } from "@/store/runStore";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import type { Deliverable, DeliverableSection, WorkspaceSource } from "@/types";

/* ── Section editor (main) ──────────────────────────────────────────── */

export function SectionEditor({ deliverable, workspaceId, runDraft }: { deliverable: Deliverable; workspaceId: string; runDraft: (action: string, sectionId?: string, instruction?: string) => Promise<void> }) {
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

type DiffLine = { type: "removed" | "added" | "unchanged"; text: string };

function computeLineDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const result: DiffLine[] = [];
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
            {line.type === "removed" ? "\u2212" : line.type === "added" ? "+" : " "}
          </span>
          {line.text || "\u00A0"}
        </div>
      ))}
    </div>
  );
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
        className="min-h-[120px] text-sm text-surface-700 leading-relaxed cursor-text hover:bg-surface-50 rounded-lg px-1 py-1 -mx-1 transition-colors"
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

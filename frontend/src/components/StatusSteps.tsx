import React from "react";
import { CheckCircle, Loader2 } from "lucide-react";
import type { AnswerJSON } from "../types";

// ── Step label maps per mode ───────────────────────────────────────────────

const MODE_STEP_LABELS: Record<string, Record<string, string>> = {
  paper_understanding: {
    received:  "Received",
    understand: "Understanding request",
    retrieve:  "Retrieving from paper",
    writing:   "Writing grounded response",
    done:      "Done",
  },
  concept_explanation: {
    received:  "Received",
    understand: "Understanding request",
    interpret: "Expanding explanation",
    writing:   "Writing response",
    done:      "Done",
  },
  external_expansion: {
    received:  "Received",
    understand: "Understanding request",
    scope:     "Searching the web",
    writing:   "Writing expanded response",
    done:      "Done",
  },
  expansion: {
    received:  "Received",
    understand: "Understanding request",
    scope:     "Searching the web",
    writing:   "Writing expanded response",
    done:      "Done",
  },
  navigation_or_next_step: {
    received:  "Received",
    understand: "Understanding request",
    scope:     "Mapping next steps",
    writing:   "Writing guidance",
    done:      "Done",
  },
};

const DEFAULT_STEP_LABELS = MODE_STEP_LABELS.paper_understanding;

// Step key order for progress calculation
const MODE_STEP_KEYS: Record<string, string[]> = {
  paper_understanding: ["received", "understand", "retrieve", "writing", "done"],
  concept_explanation: ["received", "understand", "interpret", "writing", "done"],
  external_expansion:  ["received", "understand", "scope", "writing", "done"],
  expansion:               ["received", "understand", "scope", "writing", "done"],
  navigation_or_next_step: ["received", "understand", "scope", "writing", "done"],
};

// Status text → step key (substring matching, lowercase)
const STATUS_TO_STEP: Record<string, string> = {
  "received":                     "received",
  "understanding request":         "understand",
  "retrieving passages":           "retrieve",
  "finding evidence":              "retrieve",
  "writing grounded answer":       "writing",
  "writing grounded response":     "writing",
  "writing response":              "writing",
  "interpreting concept":          "interpret",
  "linking to paper context":      "interpret",
  "writing explanation":           "writing",
  "searching the web":             "scope",
  "synthesizing results":          "writing",
  "determining request scope":     "scope",
  "determining scope":             "scope",
  "determining available scope":   "scope",
  "writing constrained response":  "writing",
  "writing expanded response":     "writing",
  "generating answer":             "writing",
  "mapping next steps":            "scope",
  "writing guidance":              "writing",
  "done":                          "done",
};

function statusToStepKey(statusText: string): string | null {
  const lower = statusText.toLowerCase().replace(/…$/, "").trim();
  for (const [pattern, key] of Object.entries(STATUS_TO_STEP)) {
    if (lower.includes(pattern)) return key;
  }
  return null;
}

// ── Props ──────────────────────────────────────────────────────────────────

interface Props {
  statusText: string;
  mode?: string;
  isDone?: boolean;
  /** Render as a compact fade-out completion marker */
  collapsed?: boolean;
  answerJson?: AnswerJSON | null;
}

// ── Active badge (streaming) ───────────────────────────────────────────────

export function StatusBadge({ statusText, mode }: { statusText: string; mode?: string }) {
  const stepLabels = (mode ? MODE_STEP_LABELS[mode] : null) ?? DEFAULT_STEP_LABELS;
  const stepKey = statusToStepKey(statusText) ?? "received";
  const label = stepLabels[stepKey] ?? stepLabels["received"];

  return (
    <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] bg-blue-500/20 text-blue-300 border border-blue-500/20 whitespace-nowrap">
      <Loader2 className="w-3 h-3 flex-shrink-0 animate-spin" />
      <span>{label}</span>
    </div>
  );
}

/** Progress 0–100 based on current step index */
export function stepProgress(statusText: string, mode?: string): number {
  const keys = (mode ? MODE_STEP_KEYS[mode] : null) ?? MODE_STEP_KEYS.paper_understanding;
  const stepKey = statusToStepKey(statusText) ?? "received";
  const idx = keys.indexOf(stepKey);
  if (idx < 0) return 5;
  return Math.round(((idx + 1) / keys.length) * 100);
}

// ── Collapsed done marker (with fade-out) ─────────────────────────────────

export function DoneMarker({ mode, answerJson }: { mode?: string; answerJson?: AnswerJSON | null }) {
  const [fading, setFading] = React.useState(false);

  React.useEffect(() => {
    // Start fade after 3s, complete in 600ms
    const t = setTimeout(() => setFading(true), 3000);
    return () => clearTimeout(t);
  }, []);

  const evidenceCount = answerJson?.evidence?.filter((e) => e.passage).length ?? 0;
  const scopeLabel = answerJson?.scope_label;
  let label = "Answer ready";
  if (scopeLabel) {
    label = scopeLabel;
  } else if (mode === "paper_understanding" && evidenceCount > 0) {
    label = `Grounded in ${evidenceCount} passage${evidenceCount !== 1 ? "s" : ""}`;
  }

  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 transition-all duration-700"
      style={{
        opacity: fading ? 0 : 1,
        transform: fading ? "translateY(-4px)" : "translateY(0)",
      }}
    >
      <CheckCircle className="w-4 h-4 flex-shrink-0 text-emerald-400" />
      <span className="text-sm font-medium text-emerald-300">{label}</span>
    </div>
  );
}

// ── Default export (backward compat — used by QAPanel for collapsed done footer) ──

export default function StatusSteps({ statusText, mode, isDone, collapsed, answerJson }: Props) {
  if (collapsed || isDone) {
    return <DoneMarker mode={mode} answerJson={answerJson} />;
  }
  return <StatusBadge statusText={statusText} mode={mode} />;
}

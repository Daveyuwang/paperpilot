import React from "react";
import {
  BookOpen,
  Lightbulb,
  Globe,
  ChevronRight,
  AlertCircle,
  FileText,
} from "lucide-react";
import type { AnswerJSON, EvidenceItem } from "../types";
import { MarkdownRenderer } from "./shared/MarkdownRenderer";

// ── Inline markdown renderer ───────────────────────────────────────────────
// Handles **bold**, *italic*, and `code` spans without any external library.

const _INLINE_MD_RE = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g;

function renderInlineMd(text: string): React.ReactNode {
  const parts = text.split(_INLINE_MD_RE);
  if (parts.length === 1) return text; // fast path: no markdown tokens
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          return (
            <strong key={i} className="font-semibold text-surface-800">
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
          return <em key={i}>{part.slice(1, -1)}</em>;
        }
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
          return (
            <code
              key={i}
              className="px-1 py-0.5 rounded bg-surface-200 text-accent-700 text-[0.9em] font-mono"
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        return part || null;
      })}
    </>
  );
}


// ── Text sanitization helpers ──────────────────────────────────────────────

function cleanPassage(raw: string | null | undefined): string {
  if (!raw) return "";
  return raw
    .replace(/b['"](\\x[0-9a-fA-F]{2}|\\[nrt\\'"]|[^'"\\])*['"]/g, "")
    .replace(/[^\x09\x0A\x0D\x20-\x7E\u00A0-\u024F\u4E00-\u9FFF\u3000-\u303F\uFF00-\uFFEF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanSection(raw: string | null | undefined): string {
  if (!raw) return "";
  const cleaned = raw
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!cleaned) return "";

  const hasReadableLetters = /[A-Za-z\u00C0-\u024F\u4E00-\u9FFF]/.test(cleaned);
  const digitCount = (cleaned.match(/\d/g) ?? []).length;
  const binaryishCount = (cleaned.match(/[01]/g) ?? []).length;
  const digitRatio = digitCount / cleaned.length;
  const binaryishRatio = binaryishCount / cleaned.length;
  const hasLongDigitRun = /\d{8,}/.test(cleaned);

  if (!hasReadableLetters && digitRatio > 0.45) return "";
  if (binaryishRatio > 0.7 && cleaned.length > 12) return "";
  if (hasLongDigitRun) return "";

  return cleaned;
}

// ── Skeleton placeholder ───────────────────────────────────────────────────

function SkeletonBlock({ lines = 2, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded bg-surface-200 animate-pulse"
          style={{ width: i === lines - 1 ? "70%" : "100%" }}
        />
      ))}
    </div>
  );
}

// ── Scope badge ────────────────────────────────────────────────────────────

interface ScopeBadgeProps {
  label: string;
  mode?: string;
  canExpand?: boolean;
  onOverride?: (actionType: string) => void;
}

function ScopeBadge({ label, mode, canExpand, onOverride }: ScopeBadgeProps) {
  const badgeStyle =
    mode === "concept_explanation"
      ? "bg-teal-50 text-teal-700 border-teal-200"
      : mode === "external_expansion" || mode === "expansion"
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : "bg-blue-50 text-blue-700 border-blue-200";

  const Icon =
    mode === "concept_explanation" ? Lightbulb
    : mode === "external_expansion" || mode === "expansion" ? Globe
    : BookOpen;

  const overrideActions: { label: string; type: string }[] = [];
  if (mode === "paper_understanding" && canExpand) {
    overrideActions.push({ label: "Search beyond this paper", type: "expand" });
    overrideActions.push({ label: "Explain more broadly", type: "explain" });
  } else if (mode === "concept_explanation") {
    overrideActions.push({ label: "Answer from paper only", type: "paper_only" });
  } else if (mode === "external_expansion" || mode === "expansion") {
    overrideActions.push({ label: "Answer from paper only", type: "paper_only" });
  }

  return (
    <div className="flex items-center gap-2 flex-wrap mb-3">
      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${badgeStyle}`}>
        <Icon className="w-3.5 h-3.5 flex-shrink-0" />
        {label}
      </span>
      {overrideActions.map((a) => (
        <button
          key={a.type}
          onClick={() => onOverride?.(a.type)}
          className="text-xs text-surface-500 hover:text-surface-700 transition-colors underline underline-offset-2"
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}

// ── Phase 1: streaming direct answer ──────────────────────────────────────

function StreamingDirectAnswer({ text }: { text: string }) {
  return (
    <div className="mb-4">
      <p className="text-[15px] leading-relaxed text-surface-700 font-medium">
        {renderInlineMd(text)}
        <span className="inline-block w-0.5 h-[1.1em] bg-accent-500 align-text-bottom ml-0.5 animate-pulse" />
      </p>
    </div>
  );
}

// ── Phase 1: skeleton for secondary sections ──────────────────────────────

function Phase1Skeletons() {
  return (
    <div className="space-y-4 mt-4">
      {/* Evidence skeleton */}
      <div>
        <div className="h-2.5 w-20 rounded bg-surface-200 animate-pulse mb-2" />
        <div className="rounded-lg border border-surface-200 bg-surface-50 px-3 py-2.5">
          <SkeletonBlock lines={3} />
        </div>
      </div>
      {/* Plain language skeleton */}
      <div>
        <div className="h-2.5 w-28 rounded bg-surface-200 animate-pulse mb-2" />
        <SkeletonBlock lines={2} />
      </div>
    </div>
  );
}

// ── Phase 2: revealed secondary blocks ───────────────────────────────────

interface FadeInBlockProps {
  delayMs: number;
  children: React.ReactNode;
}

function FadeInBlock({ delayMs, children }: FadeInBlockProps) {
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => {
    const t = setTimeout(() => setVisible(true), delayMs);
    return () => clearTimeout(t);
  }, [delayMs]);

  return (
    <div
      className="transition-all duration-500"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(6px)",
      }}
    >
      {children}
    </div>
  );
}

// ── Evidence block ─────────────────────────────────────────────────────────

function EvidenceBlock({
  items,
  onCitationClick,
}: {
  items: EvidenceItem[];
  onCitationClick?: (page: number | null, section?: string | null) => void;
}) {
  const valid = items.filter((e) => cleanPassage(e.passage));
  if (!valid.length) return null;

  return (
    <div className="mb-4">
      <h4 className="text-[11px] uppercase tracking-widest text-surface-500 mb-2">Key Evidence</h4>
      <div className="space-y-2">
        {valid.map((item, i) => {
          const passage = cleanPassage(item.passage);
          const section = cleanSection(item.section);
          const isExplicit = item.type === "explicit";
          return (
            <div key={i} className="rounded-lg border border-surface-200 bg-surface-50 px-3 py-2.5">
              <p className="text-sm text-surface-600 leading-snug italic mb-1.5">
                &ldquo;{passage}&rdquo;
              </p>
              <div className="flex items-center gap-1.5 flex-wrap">
                {item.page && (
                  <button
                    onClick={() => onCitationClick?.(item.page, item.section)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 transition-colors"
                  >
                    <FileText className="w-2.5 h-2.5" />
                    p.{item.page}
                  </button>
                )}
                {section && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] bg-surface-100 text-surface-500 border border-surface-200">
                    §{section}
                  </span>
                )}
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] ${
                  isExplicit ? "bg-emerald-50 text-emerald-700" : "bg-purple-50 text-purple-700"
                }`}>
                  {isExplicit ? "explicit" : "inferred"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Paper context (concept_explanation mode) ──────────────────────────────

function PaperContextBlock({ text }: { text: string }) {
  const cleaned = cleanPassage(text);
  if (!cleaned) return null;
  return (
    <div className="mb-4">
      <h4 className="text-[11px] uppercase tracking-widest text-surface-500 mb-2">In This Paper</h4>
      <div className="rounded-lg bg-teal-50 border border-teal-200 px-3 py-2.5">
        <p className="text-sm text-teal-800 leading-relaxed">{cleaned}</p>
      </div>
    </div>
  );
}

// ── Plain language ────────────────────────────────────────────────────────

function PlainLanguageBlock({ text }: { text: string }) {
  if (!text?.trim()) return null;
  return (
    <div className="mb-4">
      <h4 className="text-[11px] uppercase tracking-widest text-surface-500 mb-2">In Plain Language</h4>
      <div className="rounded-lg bg-surface-50 border border-surface-200 px-3 py-2.5">
        <MarkdownRenderer content={text} />
      </div>
    </div>
  );
}

// ── Bigger picture ────────────────────────────────────────────────────────

function BiggerPictureBlock({ text }: { text: string }) {
  if (!text?.trim()) return null;
  return (
    <div className="mb-4">
      <h4 className="text-[11px] uppercase tracking-widest text-surface-500 mb-2">Bigger Picture</h4>
      <MarkdownRenderer content={text} className="text-surface-500" />
    </div>
  );
}

// ── Uncertainty ───────────────────────────────────────────────────────────

function UncertaintyBlock({ text }: { text: string }) {
  const cleaned = cleanPassage(text);
  if (!cleaned) return null;
  return (
    <div className="mb-4 flex items-start gap-2 text-amber-600">
      <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <p className="text-sm leading-relaxed">{cleaned}</p>
    </div>
  );
}

// ── Key points ────────────────────────────────────────────────────────────

function KeyPointsBlock({ points }: { points: string[] }) {
  const valid = points.filter((p) => p && p.trim().length > 0);
  if (!valid.length) return null;
  return (
    <div className="mb-4">
      <ul className="space-y-1.5">
        {valid.map((pt, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-surface-700">
            <ChevronRight className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-accent-500" />
            <span className="leading-snug">{renderInlineMd(pt)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Expansion failure card ────────────────────────────────────────────────

function ExpansionFailureCard({ mode }: { mode: string }) {
  const isExpansion = mode === "external_expansion" || mode === "expansion";
  const title = isExpansion
    ? "Couldn't find enough usable external support"
    : "No answer could be generated";

  const suggestions = isExpansion
    ? [
        { label: "Try a broader question", hint: "Widen the scope or simplify the query" },
        { label: "Search sources first", hint: "Use Deep Research or Sources to gather material" },
        { label: "Ask something more specific", hint: "Narrow down to a concrete sub-question" },
      ]
    : [
        { label: "Rephrase the question", hint: "Try different wording or more context" },
        { label: "Ask about a specific section", hint: "Focus on a particular part of the paper" },
      ];

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0" />
        <span className="text-sm font-medium text-amber-800">{title}</span>
      </div>
      <div className="space-y-1.5 ml-6">
        {suggestions.map((s, i) => (
          <div key={i} className="text-xs">
            <span className="text-amber-700 font-medium">{s.label}</span>
            <span className="text-amber-600 ml-1">— {s.hint}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main AnswerCard ────────────────────────────────────────────────────────

interface AnswerCardProps {
  answer: AnswerJSON;
  /** Partial direct_answer tokens while streaming (phase 1) */
  streamingText?: string;
  /** True once answer_json has arrived — reveals secondary blocks */
  phase1Complete?: boolean;
  /** For the collapsed Done marker */
  evidenceCount?: number;
  /** Whether to show the scope badge (default true; pass false while streaming) */
  showScopeBadge?: boolean;
  /** Console mode: skip structured sections, just render markdown */
  isConsole?: boolean;
  onCitationClick?: (page: number | null, section?: string | null) => void;
  onOverrideAction?: (actionType: string) => void;
}

export default function AnswerCard({
  answer,
  streamingText,
  phase1Complete,
  evidenceCount,
  showScopeBadge = true,
  isConsole = false,
  onCitationClick,
  onOverrideAction,
}: AnswerCardProps) {
  const mode = answer.answer_mode ?? "paper_understanding";
  const scopeLabel = answer.scope_label ?? "Using this paper";

  // Phase 1: streaming tokens present, full JSON not yet decoded into blocks
  const isPhase1 = Boolean(streamingText && !phase1Complete);
  // Phase 2: answerJson arrived — secondary blocks can reveal
  const isPhase2 = Boolean(phase1Complete);

  const directAnswer = answer.direct_answer || streamingText || "";
  const hasAnyContent = Boolean(
    directAnswer ||
    (answer.key_points && answer.key_points.length > 0) ||
    (answer.evidence && answer.evidence.length > 0) ||
    answer.plain_language ||
    answer.bigger_picture ||
    answer.uncertainty
  );

  return (
    <div className="text-sm w-full">
      {/* Scope badge — only shown for paper QA after phase1Complete */}
      {showScopeBadge && !isConsole && (
        <ScopeBadge
          label={scopeLabel}
          mode={mode}
          canExpand={answer.can_expand}
          onOverride={onOverrideAction}
        />
      )}

      {/* ── Phase 1: stream direct_answer with cursor + skeleton placeholders ── */}
      {isPhase1 && (
        <>
          {isConsole ? (
            <div className="mb-2">
              <MarkdownRenderer content={streamingText!} />
              <span className="inline-block w-0.5 h-[1.1em] bg-accent-500 align-text-bottom ml-0.5 animate-pulse" />
            </div>
          ) : (
            <>
              <StreamingDirectAnswer text={streamingText!} />
              <Phase1Skeletons />
            </>
          )}
        </>
      )}

      {/* ── Phase 2 / static: full answer content ── */}
      {(isPhase2 || (!isPhase1 && directAnswer)) && (
        <>
          {/* Direct answer */}
          {directAnswer && (
            <div className="mb-4">
              <MarkdownRenderer content={directAnswer} className={isConsole ? "" : "text-[15px] font-medium"} />
            </div>
          )}

          {/* Structured sections — only for paper QA */}
          {!isConsole && (
            <>
              {answer.key_points && answer.key_points.length > 0 && (
                <FadeInBlock delayMs={0}>
                  <KeyPointsBlock points={answer.key_points} />
                </FadeInBlock>
              )}

              {mode === "concept_explanation" && answer.paper_context && (
                <FadeInBlock delayMs={80}>
                  <PaperContextBlock text={answer.paper_context} />
                </FadeInBlock>
              )}

              {answer.evidence && answer.evidence.length > 0 && (
                <FadeInBlock delayMs={isPhase2 ? 100 : 0}>
                  <EvidenceBlock items={answer.evidence} onCitationClick={onCitationClick} />
                </FadeInBlock>
              )}

              {answer.uncertainty && (
                <FadeInBlock delayMs={isPhase2 ? 200 : 0}>
                  <UncertaintyBlock text={answer.uncertainty} />
                </FadeInBlock>
              )}

              {answer.plain_language && (
                <FadeInBlock delayMs={isPhase2 ? 250 : 0}>
                  <PlainLanguageBlock text={answer.plain_language} />
                </FadeInBlock>
              )}

              {answer.bigger_picture && (
                <FadeInBlock delayMs={isPhase2 ? 400 : 0}>
                  <BiggerPictureBlock text={answer.bigger_picture} />
                </FadeInBlock>
              )}
            </>
          )}
        </>
      )}

      {/* Fallback: phase complete but no renderable content at all */}
      {isPhase2 && !isPhase1 && !hasAnyContent && (
        <ExpansionFailureCard mode={mode} />
      )}
    </div>
  );
}

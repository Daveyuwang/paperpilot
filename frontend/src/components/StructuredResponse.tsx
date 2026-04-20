import React from "react";

/**
 * Renders the structured agent response.
 *
 * Sections recognised:
 *   [Direct Answer]   [Evidence]   [Plain Language]
 *   [What This Means] [Uncertainty] [Term: ...]
 *
 * Within [Evidence], lines prefixed with
 *   • [Author states]  are rendered as verified (green dot)
 *   • [Inferred]       are rendered as inferred (amber dot)
 */
export function StructuredResponse({ content }: { content: string }) {
  const blocks = parseBlocks(content);
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {blocks.map((b, i) => <ResponseBlock key={i} {...b} />)}
    </div>
  );
}

// ── Types ──────────────────────────────────────────────────────────────────

interface Block {
  label: string | null;
  text: string;
}

const LABEL_META: Record<string, { color: string; bg: string; border: string }> = {
  "Direct Answer":  { color: "text-accent-600",   bg: "bg-accent-50",       border: "border-accent-200" },
  "Evidence":       { color: "text-emerald-700",  bg: "bg-emerald-50",      border: "border-emerald-200" },
  "Plain Language": { color: "text-purple-700",   bg: "",                   border: "" },
  "What This Means":{ color: "text-amber-700",    bg: "",                   border: "" },
  "Uncertainty":    { color: "text-red-600",      bg: "bg-red-50",          border: "border-red-200" },
};

// ── Parser ─────────────────────────────────────────────────────────────────

function parseBlocks(content: string): Block[] {
  // Split on [Label] markers at start of line or after newline
  const parts = content.split(/(\[[^\]]{1,40}\])/);
  const blocks: Block[] = [];
  let pendingLabel: string | null = null;

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;

    if (/^\[[^\]]{1,40}\]$/.test(trimmed)) {
      pendingLabel = trimmed.slice(1, -1);
    } else {
      blocks.push({ label: pendingLabel, text: trimmed });
      pendingLabel = null;
    }
  }

  if (blocks.length === 0 && content.trim()) {
    return [{ label: null, text: content.trim() }];
  }
  return blocks;
}

// ── Block renderer ─────────────────────────────────────────────────────────

function ResponseBlock({ label, text }: Block) {
  const meta = label ? LABEL_META[label] : null;

  return (
    <div className={meta?.bg || meta?.border
      ? `rounded-lg px-3 py-2 border ${meta.bg} ${meta.border}`
      : ""
    }>
      {label && (
        <p className={`text-[11px] font-semibold uppercase tracking-wider mb-1.5 ${meta?.color ?? "text-surface-500"}`}>
          {label}
        </p>
      )}
      {label === "Evidence" ? (
        <EvidenceSection text={text} />
      ) : label === "Uncertainty" ? (
        <UncertaintySection text={text} />
      ) : (
        <p className="text-surface-700 whitespace-pre-wrap">{text}</p>
      )}
    </div>
  );
}

// ── Evidence section ───────────────────────────────────────────────────────

function EvidenceSection({ text }: { text: string }) {
  if (text.startsWith("No relevant passages")) {
    return <p className="text-surface-400 italic text-sm">{text}</p>;
  }

  const lines = text.split("\n").filter((l) => l.trim().startsWith("•") || l.trim().startsWith("-"));

  if (lines.length === 0) {
    return <p className="text-surface-600 whitespace-pre-wrap text-sm">{text}</p>;
  }

  return (
    <ul className="space-y-1.5">
      {lines.map((line, i) => {
        const clean = line.replace(/^[•\-]\s*/, "").trim();
        const isAuthor = clean.startsWith("[Author states]");
        const isInferred = clean.startsWith("[Inferred]");
        const body = clean.replace(/^\[(Author states|Inferred)\]\s*/, "");

        // Highlight citations like (§Intro, p.3)
        const highlighted = body.replace(
          /\(§([^,)]+),\s*p\.(\w+)\)/g,
          '<span class="text-accent-400/80 text-xs font-mono ml-1">(§$1, p.$2)</span>'
        );

        return (
          <li key={i} className="flex items-start gap-2">
            {isAuthor && (
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" title="Author explicitly states" />
            )}
            {isInferred && (
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" title="System inference" />
            )}
            {!isAuthor && !isInferred && (
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-surface-400 flex-shrink-0" />
            )}
            <span className="text-surface-600 text-sm flex-1">
              {isInferred && (
                <span className="text-amber-600 text-[10px] font-semibold uppercase mr-1.5">Inferred</span>
              )}
              <span dangerouslySetInnerHTML={{ __html: highlighted }} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// ── Uncertainty section ────────────────────────────────────────────────────

function UncertaintySection({ text }: { text: string }) {
  return (
    <div className="text-sm text-red-600 whitespace-pre-wrap">
      <span className="text-red-500 font-semibold mr-1">⚠</span>
      {text}
    </div>
  );
}

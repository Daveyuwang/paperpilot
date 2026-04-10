import clsx from "clsx";
import type { EvidenceSignal } from "@/types";

interface Props {
  signal: EvidenceSignal;
}

/**
 * Small badge showing evidence confidence level.
 * Displayed next to assistant messages after evidence extraction completes.
 */
export function ConfidenceBadge({ signal }: Props) {
  const { confidence, evidence_count, coverage_gap } = signal;

  const { label, dot, text } =
    confidence >= 0.8 ? { label: "High",     dot: "bg-emerald-400", text: "text-emerald-400" } :
    confidence >= 0.6 ? { label: "Moderate", dot: "bg-amber-400",   text: "text-amber-400"   } :
    confidence >= 0.4 ? { label: "Low",      dot: "bg-orange-400",  text: "text-orange-400"  } :
                        { label: "Weak",     dot: "bg-red-400",     text: "text-red-400"     };

  const title = coverage_gap
    ? `${evidence_count} evidence item(s) · Gap: ${coverage_gap}`
    : `${evidence_count} evidence item(s)`;

  return (
    <div
      className={clsx(
        "flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded",
        "bg-surface-800 border border-white/5 cursor-default",
        text,
      )}
      title={title}
    >
      <span className={clsx("w-1.5 h-1.5 rounded-full", dot)} />
      {label}
    </div>
  );
}

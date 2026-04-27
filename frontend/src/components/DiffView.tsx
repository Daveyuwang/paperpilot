/**
 * Inline diff view for deliverable section edits.
 * Shows green for additions, red for deletions.
 */
import clsx from "clsx";

interface DiffLine {
  type: "add" | "remove" | "unchanged";
  content: string;
}

interface Props {
  oldText: string;
  newText: string;
  className?: string;
}

function computeDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const result: DiffLine[] = [];

  let oi = 0, ni = 0;

  while (oi < oldLines.length || ni < newLines.length) {
    if (oi >= oldLines.length) {
      result.push({ type: "add", content: newLines[ni] });
      ni++;
    } else if (ni >= newLines.length) {
      result.push({ type: "remove", content: oldLines[oi] });
      oi++;
    } else if (oldLines[oi] === newLines[ni]) {
      result.push({ type: "unchanged", content: oldLines[oi] });
      oi++;
      ni++;
    } else {
      result.push({ type: "remove", content: oldLines[oi] });
      oi++;
      if (ni < newLines.length) {
        result.push({ type: "add", content: newLines[ni] });
        ni++;
      }
    }
  }

  return result;
}

export function DiffView({ oldText, newText, className }: Props) {
  const lines = computeDiff(oldText, newText);

  return (
    <div className={clsx("font-mono text-xs overflow-auto rounded-lg border border-surface-200", className)}>
      {lines.map((line, i) => (
        <div
          key={i}
          className={clsx(
            "px-3 py-0.5 whitespace-pre-wrap",
            line.type === "add" && "bg-emerald-50 text-emerald-800",
            line.type === "remove" && "bg-red-50 text-red-800 line-through",
            line.type === "unchanged" && "text-surface-600",
          )}
        >
          <span className="inline-block w-4 mr-2 text-surface-400 select-none">
            {line.type === "add" ? "+" : line.type === "remove" ? "-" : " "}
          </span>
          {line.content || "\u00A0"}
        </div>
      ))}
    </div>
  );
}

import { Search } from "lucide-react";

interface Props {
  text: string | null;
}

export function CurrentActivity({ text }: Props) {
  if (!text) return null;

  return (
    <div
      className="mt-3 flex items-center gap-2 px-3 py-2 rounded-lg border border-surface-200 bg-surface-50 text-xs text-surface-600"
      style={{ animation: "activity-fade 0.2s ease-out" }}
    >
      <Search className="w-3.5 h-3.5 text-accent-500 flex-shrink-0 animate-pulse" />
      <span className="truncate">{text}</span>
    </div>
  );
}

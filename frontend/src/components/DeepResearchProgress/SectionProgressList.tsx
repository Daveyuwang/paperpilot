import { Check, Loader2, Circle, AlertCircle } from "lucide-react";
import clsx from "clsx";
import type { SectionProgressV2 } from "@/store/deepResearchStore";

interface Props {
  sections: SectionProgressV2[];
}

function SectionIcon({ status }: { status: SectionProgressV2["status"] }) {
  if (status === "done") return <Check className="w-3 h-3 text-emerald-500" />;
  if (status === "drafting") return <Loader2 className="w-3 h-3 text-accent-500 animate-spin" />;
  if (status === "failed") return <AlertCircle className="w-3 h-3 text-orange-500" />;
  return <Circle className="w-2.5 h-2.5 text-surface-300" />;
}

export function SectionProgressList({ sections }: Props) {
  return (
    <div className="mt-1.5 space-y-0.5 ml-1 border-l border-surface-100 pl-3">
      {sections.map((sec, i) => (
        <div
          key={i}
          className={clsx(
            "flex items-center gap-2 py-1 rounded-md transition-all duration-300",
            sec.status === "drafting" && "bg-accent-50/50",
          )}
        >
          <div className="flex-shrink-0 w-3.5 flex items-center justify-center">
            <SectionIcon status={sec.status} />
          </div>
          <span
            className={clsx(
              "flex-1 text-xs truncate",
              sec.status === "done" && "text-surface-500",
              sec.status === "drafting" && "text-surface-700 font-medium",
              sec.status === "failed" && "text-orange-700",
              sec.status === "pending" && "text-surface-400",
            )}
          >
            {sec.title}
          </span>
        </div>
      ))}
    </div>
  );
}

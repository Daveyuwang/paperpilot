import clsx from "clsx";
import type { MacroStageStatus } from "@/store/deepResearchStore";

interface Props {
  status: MacroStageStatus;
}

export function TimelineConnector({ status }: Props) {
  return (
    <div className="flex items-stretch ml-[13px] py-0">
      <div
        className={clsx(
          "w-px min-h-[16px] transition-colors duration-300",
          status === "completed" ? "bg-emerald-300" : "bg-surface-200",
        )}
      />
    </div>
  );
}

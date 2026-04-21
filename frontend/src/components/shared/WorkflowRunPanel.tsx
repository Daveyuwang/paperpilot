import { useState, useRef, useEffect } from "react";
import { Check, Circle, Loader2, ChevronDown, Search, BookOpen, PenLine, AlertCircle, WifiOff } from "lucide-react";
import clsx from "clsx";
import type { DynamicStage, ActivityEvent } from "@/store/deepResearchStore";

export interface StageDefinition {
  key: string;
  label: string;
}

export interface SectionProgressItem {
  title: string;
  status: "pending" | "drafting" | "done" | "skipped";
  preview?: string;
}

interface Props {
  stages?: StageDefinition[];
  dynamicStages?: DynamicStage[];
  currentStatus: string;
  isFinished?: boolean;
  stageMessage?: string | null;
  sectionsProgress: SectionProgressItem[];
  sourcesFound?: number;
  sourcesSelected?: number;
  activityLog?: ActivityEvent[];
}

export function WorkflowRunPanel({
  stages,
  dynamicStages,
  currentStatus,
  isFinished = false,
  sectionsProgress,
  sourcesFound,
  sourcesSelected,
  activityLog,
}: Props) {
  const useDynamic = dynamicStages && dynamicStages.length > 0;
  const [stale, setStale] = useState(false);
  const lastEventCount = useRef(0);

  useEffect(() => {
    if (isFinished) { setStale(false); return; }
    const eventCount = (activityLog?.length ?? 0) + (dynamicStages?.length ?? 0) + sectionsProgress.filter(s => s.status !== "pending").length;
    if (eventCount !== lastEventCount.current) {
      lastEventCount.current = eventCount;
      setStale(false);
    }
    const timer = setTimeout(() => setStale(true), 15_000);
    return () => clearTimeout(timer);
  }, [activityLog?.length, dynamicStages?.length, sectionsProgress, isFinished]);

  let allStages: { key: string; label: string; status: "completed" | "active" | "pending" }[] = [];

  if (useDynamic) {
    allStages = dynamicStages.map((s) => ({
      key: s.key,
      label: s.label,
      status: isFinished ? "completed" as const : s.status,
    }));
  } else if (stages) {
    const currentIdx = stages.findIndex((s) => s.key === currentStatus);
    allStages = stages.map((s, i) => ({
      key: s.key,
      label: s.label,
      status: isFinished
        ? "completed" as const
        : i < currentIdx
          ? "completed" as const
          : i === currentIdx
            ? "active" as const
            : "pending" as const,
    }));
  }

  const completedCount = allStages.filter((s) => s.status === "completed").length;
  const activeStage = allStages.find((s) => s.status === "active");

  return (
    <div className="space-y-4">
      {/* ── Stage Track ─────────────────────────────────────── */}
      {allStages.length > 0 && (
        <StageTrack stages={allStages} />
      )}

      {/* ── Activity Feed ───────────────────────────────────── */}
      {activityLog && activityLog.length > 0 && (
        <ActivityFeed events={activityLog} />
      )}

      {/* ── Source stats ────────────────────────────────────── */}
      {(sourcesFound !== undefined && sourcesFound > 0) || (sourcesSelected !== undefined && sourcesSelected > 0) ? (
        <div className="text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg px-3 py-2">
          {sourcesFound !== undefined && sourcesFound > 0 && (
            <span>Found {sourcesFound} candidates</span>
          )}
          {sourcesFound !== undefined && sourcesFound > 0 && sourcesSelected !== undefined && sourcesSelected > 0 && (
            <span> &rarr; </span>
          )}
          {sourcesSelected !== undefined && sourcesSelected > 0 && (
            <span>Selected {sourcesSelected} for drafting</span>
          )}
        </div>
      ) : null}

      {/* ── Per-section progress ────────────────────────────── */}
      {sectionsProgress.length > 0 && (
        <SectionsProgress sections={sectionsProgress} />
      )}

      {/* ── Stale connection warning ──────────────────────── */}
      {stale && !isFinished && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg"
          style={{ animation: "slide-in 300ms ease-out" }}>
          <WifiOff className="w-3.5 h-3.5 flex-shrink-0" />
          <span>No updates received for a while — the connection may be slow or interrupted.</span>
        </div>
      )}

      <style>{`
        @keyframes dash-flow {
          to { stroke-dashoffset: -12; }
        }
        @keyframes node-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.15); opacity: 0.85; }
        }
        @keyframes slide-in {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ── Stage Track: nodes + connecting lines ─────────────────────

function StageTrack({ stages }: { stages: { key: string; label: string; status: "completed" | "active" | "pending" }[] }) {
  return (
    <div className="flex items-center gap-0 w-full">
      {stages.map((stage, i) => (
        <div key={stage.key} className="flex items-center" style={{ flex: i < stages.length - 1 ? 1 : 0 }}>
          {/* Node */}
          <div className="flex flex-col items-center" style={{ minWidth: 32 }}>
            <div
              className={clsx(
                "w-6 h-6 rounded-full flex items-center justify-center transition-all duration-500",
                stage.status === "completed" && "bg-emerald-500 text-white",
                stage.status === "active" && "bg-accent-500 text-white",
                stage.status === "pending" && "bg-surface-200 text-surface-400",
              )}
              style={stage.status === "active" ? { animation: "node-pulse 2s ease-in-out infinite" } : undefined}
            >
              {stage.status === "completed" ? (
                <Check className="w-3 h-3" />
              ) : stage.status === "active" ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Circle className="w-2.5 h-2.5 opacity-50" />
              )}
            </div>
            <span className={clsx(
              "text-[9px] mt-1 text-center leading-tight max-w-[56px] truncate",
              stage.status === "completed" && "text-emerald-600 font-medium",
              stage.status === "active" && "text-accent-600 font-semibold",
              stage.status === "pending" && "text-surface-400",
            )}>
              {stage.label}
            </span>
          </div>

          {/* Connecting line */}
          {i < stages.length - 1 && (
            <div className="flex-1 mx-1 relative" style={{ height: 2, minWidth: 12 }}>
              <svg className="w-full h-full absolute inset-0" preserveAspectRatio="none">
                {stage.status === "completed" ? (
                  <line x1="0" y1="1" x2="100%" y2="1" stroke="#10b981" strokeWidth="2" />
                ) : stage.status === "active" ? (
                  <line
                    x1="0" y1="1" x2="100%" y2="1"
                    stroke="#6366f1" strokeWidth="2"
                    strokeDasharray="4 4"
                    style={{ animation: "dash-flow 0.8s linear infinite" }}
                  />
                ) : (
                  <line x1="0" y1="1" x2="100%" y2="1" stroke="#e2e8f0" strokeWidth="2" />
                )}
              </svg>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Activity Feed ──────────────────────────────────────────────

const ACTIVITY_ICON: Record<string, typeof Search> = {
  searching: Search,
  reading: BookOpen,
  writing: PenLine,
  error: AlertCircle,
};

function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeEvents = events.filter((e) => e.status === "active");
  const doneEvents = events.filter((e) => e.status === "done");
  const latestActive = activeEvents[activeEvents.length - 1];
  const recentDone = expanded ? doneEvents : doneEvents.slice(-3);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  return (
    <div className="rounded-lg border border-surface-200 bg-surface-50 overflow-hidden">
      {/* Current active event — prominent */}
      {latestActive && (
        <div
          className="flex items-center gap-2 px-3 py-2 bg-white border-b border-surface-100"
          style={{ animation: "slide-in 300ms ease-out" }}
        >
          <ActivityIcon type={latestActive.type} active />
          <span className="text-xs text-surface-700 font-medium truncate flex-1">{latestActive.label}</span>
        </div>
      )}

      {/* Done events — collapsible log */}
      {doneEvents.length > 0 && (
        <div
          ref={scrollRef}
          className="overflow-y-auto transition-all duration-300"
          style={{ maxHeight: expanded ? 200 : 72 }}
        >
          <div className="px-3 py-1.5 space-y-0.5">
            {recentDone.map((e) => (
              <div
                key={e.id}
                className="flex items-center gap-1.5 text-[10px] text-surface-400 py-0.5"
                style={{ animation: "slide-in 200ms ease-out" }}
              >
                <Check className="w-2.5 h-2.5 flex-shrink-0 text-emerald-400" />
                <span className="truncate flex-1">{e.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Expand/collapse toggle */}
      {doneEvents.length > 3 && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center justify-center gap-1 px-3 py-1 text-[10px] text-surface-400 hover:text-surface-600 border-t border-surface-100 transition-colors"
        >
          <ChevronDown className={clsx("w-2.5 h-2.5 transition-transform", expanded && "rotate-180")} />
          {expanded ? "Show less" : `${doneEvents.length} steps`}
        </button>
      )}
    </div>
  );
}

function ActivityIcon({ type, active = false }: { type: string; active?: boolean }) {
  if (active && type !== "error") {
    return <Loader2 className="w-3 h-3 flex-shrink-0 text-accent-500 animate-spin" />;
  }
  const Icon = ACTIVITY_ICON[type] || Circle;
  return <Icon className={clsx(
    "w-3 h-3 flex-shrink-0",
    type === "error" ? "text-red-400" : "text-surface-400",
  )} />;
}

// ── Per-section progress ───────────────────────────────────────

function SectionsProgress({ sections }: { sections: SectionProgressItem[] }) {
  return (
    <div className="space-y-0.5">
      {sections.map((sec, i) => (
        <div
          key={i}
          className={clsx(
            "flex items-start gap-2 px-3 py-1.5 rounded-md text-xs transition-all duration-400",
            sec.status === "done" && "text-emerald-600",
            sec.status === "drafting" && "text-accent-700 bg-accent-50 font-medium",
            sec.status === "skipped" && "text-surface-400",
            sec.status === "pending" && "text-surface-300",
          )}
          style={sec.status === "drafting" ? { animation: "slide-in 300ms ease-out" } : undefined}
        >
          {sec.status === "done" ? (
            <Check className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          ) : sec.status === "drafting" ? (
            <Loader2 className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 animate-spin" />
          ) : sec.status === "skipped" ? (
            <span className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-center">—</span>
          ) : (
            <Circle className="w-3 h-3 mt-0.5 flex-shrink-0 opacity-30" />
          )}
          <div className="flex-1 min-w-0">
            <span>{sec.title}</span>
            {sec.status === "done" && sec.preview && (
              <p className="text-[11px] text-surface-400 mt-0.5 line-clamp-1 leading-relaxed">
                {sec.preview}
              </p>
            )}
            {sec.status === "skipped" && (
              <span className="text-[11px] text-surface-400 ml-1">(has content)</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

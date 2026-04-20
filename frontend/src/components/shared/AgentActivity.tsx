import { useState, useEffect, useRef } from "react";
import { Loader2, CheckCircle2, Wrench, Brain } from "lucide-react";
import clsx from "clsx";

interface ToolStep {
  label: string;
  timestamp: number;
}

interface Props {
  statusText: string;
  isActive: boolean;
}

const TOOL_LABELS: Record<string, string> = {
  "Retrieving passages from paper…": "Reading paper",
  "Searching across workspace papers…": "Searching papers",
  "Fetching background knowledge…": "Fetching background",
  "Loading paper metadata…": "Loading metadata",
  "Loading concept map…": "Concept map",
  "Checking reading progress…": "Checking progress",
  "Loading session context…": "Session context",
  "Searching for relevant sources…": "Discovering sources",
  "Managing sources…": "Managing sources",
  "Checking deliverables…": "Checking deliverables",
  "Reading section content…": "Reading section",
  "Initiating draft generation…": "Drafting section",
  "Checking your agenda…": "Checking agenda",
  "Updating agenda…": "Updating agenda",
  "Loading workspace overview…": "Workspace overview",
  "Processing navigation…": "Navigating",
  "Searching academic literature…": "Academic search",
  "Loading citation context…": "Citation context",
  "Searching the web…": "Web search",
  "Analyzing source relevance…": "Analyzing sources",
  "Fetching paper full text…": "Fetching full text",
};

export function AgentActivity({ statusText, isActive }: Props) {
  const [steps, setSteps] = useState<ToolStep[]>([]);
  const prevStatusRef = useRef("");

  useEffect(() => {
    if (!statusText || statusText === prevStatusRef.current) return;
    prevStatusRef.current = statusText;

    const label = TOOL_LABELS[statusText] || statusText.replace(/…$/, "");
    if (label === "Received") return;

    setSteps((prev) => {
      if (prev.length > 0 && prev[prev.length - 1].label === label) return prev;
      return [...prev.slice(-4), { label, timestamp: Date.now() }];
    });
  }, [statusText]);

  useEffect(() => {
    if (!isActive) {
      const t = setTimeout(() => setSteps([]), 2000);
      return () => clearTimeout(t);
    }
  }, [isActive]);

  if (!isActive && steps.length === 0) return null;

  return (
    <div className="flex items-start gap-2 px-3 py-2">
      <div className="flex flex-col gap-1 min-w-0">
        {/* Completed steps */}
        {steps.slice(0, -1).map((step, i) => (
          <div key={i} className="flex items-center gap-1.5 text-[10px] text-surface-400">
            <CheckCircle2 className="w-3 h-3 text-emerald-400 flex-shrink-0" />
            <span className="truncate">{step.label}</span>
          </div>
        ))}

        {/* Current step */}
        {isActive && steps.length > 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-accent-700 font-medium">
            <Loader2 className="w-3 h-3 animate-spin text-accent-500 flex-shrink-0" />
            <span className="truncate">{steps[steps.length - 1].label}</span>
          </div>
        )}

        {/* Thinking state (no tool active) */}
        {isActive && steps.length === 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-surface-500">
            <Brain className="w-3 h-3 animate-pulse text-surface-400 flex-shrink-0" />
            <span>Thinking…</span>
          </div>
        )}
      </div>
    </div>
  );
}

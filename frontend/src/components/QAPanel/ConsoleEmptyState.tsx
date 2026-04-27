import { MessageSquare, Search, GitCompare, PenTool, Sparkles } from "lucide-react";

const CONSOLE_ACTIONS = [
  { label: "Find recent related work", icon: Search },
  { label: "Compare included sources", icon: GitCompare },
  { label: "Improve current draft", icon: PenTool },
  { label: "Summarize active paper", icon: Sparkles },
];

export function ConsoleEmptyState({ onFillInput }: { onFillInput: (text: string) => void }) {
  return (
    <div className="flex items-center justify-center min-h-[300px] py-12">
      <div className="text-center max-w-md">
        <div className="w-10 h-10 rounded-xl bg-surface-100 flex items-center justify-center mx-auto mb-4">
          <MessageSquare className="w-5 h-5 text-surface-400" />
        </div>
        <h3 className="text-sm font-medium text-surface-700">Workspace Console</h3>
        <p className="text-xs text-surface-400 mt-1.5 leading-relaxed">
          Ask about the workspace, compare sources, discover papers, or work on drafts.
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {CONSOLE_ACTIONS.map(({ label, icon: Icon }) => (
            <button
              key={label}
              onClick={() => onFillInput(label)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg hover:bg-surface-100 hover:border-surface-300 transition-colors"
            >
              <Icon className="w-3 h-3 text-surface-400" />
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

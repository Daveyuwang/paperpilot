import { MessageSquare, Search, GitCompare, PenTool, Sparkles } from "lucide-react";

export function ConsoleEmptyState({
  onFillInput,
  sourceCount,
  hasDraft,
  hasActivePaper,
}: {
  onFillInput: (text: string) => void;
  sourceCount: number;
  hasDraft: boolean;
  hasActivePaper: boolean;
}) {
  const actions = sourceCount >= 2
    ? [
        { label: "Identify gaps in the evidence", icon: Search },
        { label: "Compare included sources", icon: GitCompare },
        { label: "Outline a draft from these sources", icon: PenTool },
        { label: "Summarize the key themes", icon: Sparkles },
      ]
    : sourceCount === 1 || hasActivePaper
      ? [
          { label: "Summarize the available evidence", icon: Sparkles },
          { label: "Identify evidence gaps", icon: Search },
          { label: "Outline a research brief", icon: PenTool },
          { label: "Suggest follow-up questions", icon: GitCompare },
        ]
      : [
          { label: "Turn a topic into research questions", icon: Search },
          { label: "Plan a literature search", icon: Sparkles },
          { label: "Outline a research brief", icon: PenTool },
          { label: "Define comparison criteria", icon: GitCompare },
        ];

  if (hasDraft) {
    actions[2] = { label: "Suggest revisions to the current draft", icon: PenTool };
  }

  return (
    <div className="flex min-h-[300px] items-center justify-center py-12">
      <div className="text-center max-w-md">
        <div className="w-10 h-10 rounded-xl bg-surface-100 flex items-center justify-center mx-auto mb-4">
          <MessageSquare className="w-5 h-5 text-surface-400" />
        </div>
        <h2 className="text-base font-semibold text-surface-800">Ask your workspace</h2>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {actions.map(({ label, icon: Icon }) => (
            <button
              key={label}
              onClick={() => onFillInput(label)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-200 bg-white px-3 py-2 text-xs text-surface-600 transition-colors hover:border-surface-300 hover:bg-surface-50"
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

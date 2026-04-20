import { Check, Clock, ListTodo } from "lucide-react";
import { useAgendaStore } from "@/store/agendaStore";
import { useWorkspaceStore } from "@/store/workspaceStore";

interface Props {
  onAsk: (q: { id: string; question: string }) => void;
}

export function UpNextCard({ onAsk }: Props) {
  const upNext = useAgendaStore((s) => s.getUpNext());
  const { markDone, snooze, resolveUpNext } = useAgendaStore();
  const { setActiveViewerTab } = useWorkspaceStore();

  if (!upNext) return null;

  return (
    <div className="flex-shrink-0 px-4 py-2 bg-surface-50 border-b border-surface-200 flex items-center gap-3 min-w-0">
      <span className="text-[9px] font-semibold text-surface-400 uppercase tracking-wider flex-shrink-0">
        Up next
      </span>
      <p className="text-xs text-surface-700 flex-1 min-w-0 leading-snug line-clamp-2" title={upNext.title}>{upNext.title}</p>
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <button
          className="text-[11px] text-accent-600 hover:text-accent-800 font-medium transition-colors px-1.5 py-0.5 rounded hover:bg-accent-100"
          onClick={() => {
            if (upNext.linkedTrailQuestionId) {
              onAsk({ id: upNext.linkedTrailQuestionId, question: upNext.title });
            } else {
              onAsk({ id: upNext.id, question: upNext.title });
            }
          }}
        >
          Ask
        </button>
        <button
          className="p-1 text-surface-400 hover:text-emerald-600 transition-colors rounded hover:bg-surface-100"
          onClick={() => { markDone(upNext.id); resolveUpNext(); }}
          title="Mark done"
        >
          <Check className="w-3 h-3" />
        </button>
        <button
          className="p-1 text-surface-400 hover:text-amber-600 transition-colors rounded hover:bg-surface-100"
          onClick={() => { snooze(upNext.id); resolveUpNext(); }}
          title="Snooze"
        >
          <Clock className="w-3 h-3" />
        </button>
        <button
          className="p-1 text-surface-400 hover:text-surface-600 transition-colors rounded hover:bg-surface-100"
          onClick={() => setActiveViewerTab("agenda")}
          title="Open Agenda"
        >
          <ListTodo className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

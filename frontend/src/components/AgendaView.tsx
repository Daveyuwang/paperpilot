import React from "react";
import clsx from "clsx";
import {
  CheckCircle2,
  Circle,
  Clock,
  Play,
  Pause,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  BookOpen,
} from "lucide-react";
import { useAgendaStore, type AgendaItem } from "@/store/agendaStore";
import { TrailTracker } from "./TrailTracker";

const STATUS_META: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  active:  { icon: Play,         color: "text-accent-600",  bg: "bg-accent-50" },
  pending: { icon: Circle,       color: "text-surface-400", bg: "" },
  done:    { icon: CheckCircle2, color: "text-emerald-600", bg: "bg-emerald-50/50" },
  snoozed: { icon: Pause,        color: "text-amber-600",   bg: "bg-amber-50/50" },
};

const CATEGORY_DOT: Record<string, string> = {
  motivation:  "bg-rose-400",
  approach:    "bg-amber-400",
  experiments: "bg-emerald-400",
  takeaways:   "bg-accent-400",
  custom:      "bg-purple-400",
};

interface Props {
  onAsk: (q: { id: string; question: string }) => void;
}

export function AgendaView({ onAsk }: Props) {
  const { items, markDone, snooze, reactivate, resolveUpNext } = useAgendaStore();
  const [showReadingPath, setShowReadingPath] = React.useState(false);

  const upNext = useAgendaStore((s) => s.getUpNext());
  const agendaItems = items.filter((i) => i.source !== "user_question");
  const pending = agendaItems.filter((i) => i.status === "pending");
  const done = agendaItems.filter((i) => i.status === "done");
  const snoozed = agendaItems.filter((i) => i.status === "snoozed");
  const totalDone = done.length;
  const total = agendaItems.length;

  if (agendaItems.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-32 gap-2 text-surface-400">
        <BookOpen className="w-6 h-6 opacity-40" />
        <p className="text-xs">No agenda items yet. Load a paper to get started.</p>
      </div>
    );
  }

  const progressPct = total > 0 ? Math.round((totalDone / total) * 100) : 0;

  return (
    <div className="space-y-4 py-1">
      {/* Progress */}
      <div className="px-1">
        <div className="flex justify-between items-baseline mb-2">
          <span className="text-xs font-semibold text-surface-500">Agenda</span>
          <span className="text-xs text-surface-400">{totalDone} / {total} done</span>
        </div>
        <div className="h-1 bg-surface-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-accent-500 to-accent-400 rounded-full transition-all duration-700"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Up Next card */}
      {upNext && (
        <div className="rounded-lg border border-accent-200 bg-accent-50/60 px-3 py-2.5">
          <div className="flex items-center gap-1.5 mb-1">
            <Play className="w-3 h-3 text-accent-600" />
            <span className="text-[10px] font-semibold text-accent-600 uppercase tracking-wider">Up Next</span>
          </div>
          <p className="text-xs text-surface-700 leading-snug mb-2">{upNext.title}</p>
          <div className="flex items-center gap-2">
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
              className="text-[11px] text-surface-400 hover:text-emerald-600 transition-colors px-1.5 py-0.5 rounded hover:bg-surface-100"
              onClick={() => { markDone(upNext.id); resolveUpNext(); }}
            >
              Done
            </button>
            <button
              className="text-[11px] text-surface-400 hover:text-amber-600 transition-colors px-1.5 py-0.5 rounded hover:bg-surface-100"
              onClick={() => { snooze(upNext.id); resolveUpNext(); }}
            >
              Snooze
            </button>
          </div>
        </div>
      )}

      {/* Pending items */}
      {pending.length > 0 && (
        <AgendaSection title="Pending" count={pending.length}>
          {pending.map((item) => (
            <AgendaRow
              key={item.id}
              item={item}
              onAsk={onAsk}
              onDone={() => { markDone(item.id); resolveUpNext(); }}
              onSnooze={() => { snooze(item.id); resolveUpNext(); }}
            />
          ))}
        </AgendaSection>
      )}

      {/* Snoozed items */}
      {snoozed.length > 0 && (
        <AgendaSection title="Snoozed" count={snoozed.length}>
          {snoozed.map((item) => (
            <div key={item.id} className="flex items-start gap-2.5 px-3 py-2 text-xs">
              <Clock className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-amber-500" />
              <span className="flex-1 leading-snug text-surface-500">{item.title}</span>
              <button
                className="text-[10px] text-surface-400 hover:text-accent-600 transition-colors"
                onClick={() => reactivate(item.id)}
              >
                <RotateCcw className="w-3 h-3" />
              </button>
            </div>
          ))}
        </AgendaSection>
      )}

      {/* Done items */}
      {done.length > 0 && (
        <AgendaSection title="Completed" count={done.length}>
          {done.map((item) => (
            <div key={item.id} className="flex items-start gap-2.5 px-3 py-2 text-xs">
              <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-emerald-500 opacity-50" />
              <span className="flex-1 leading-snug text-surface-400 line-through decoration-surface-300">
                {item.title}
              </span>
            </div>
          ))}
        </AgendaSection>
      )}

      {/* Reading Path (collapsible) */}
      <div className="border-t border-surface-200 pt-3">
        <button
          className="flex items-center gap-1.5 text-xs font-semibold text-surface-500 hover:text-surface-700 transition-colors mb-2"
          onClick={() => setShowReadingPath(!showReadingPath)}
        >
          {showReadingPath ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          Reading Path
        </button>
        {showReadingPath && <TrailTracker onAsk={onAsk} />}
      </div>
    </div>
  );
}

function AgendaSection({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-1 px-1">
        <span className="text-[10px] font-semibold text-surface-400 uppercase tracking-wider">{title}</span>
        <span className="text-[10px] text-surface-300">{count}</span>
      </div>
      <div className="rounded-lg border border-surface-200 divide-y divide-surface-100 overflow-hidden">
        {children}
      </div>
    </div>
  );
}

function AgendaRow({
  item,
  onAsk,
  onDone,
  onSnooze,
}: {
  item: AgendaItem;
  onAsk: (q: { id: string; question: string }) => void;
  onDone: () => void;
  onSnooze: () => void;
}) {
  const meta = STATUS_META[item.status] ?? STATUS_META.pending;
  const dot = CATEGORY_DOT[item.category ?? "custom"] ?? "bg-surface-400";

  return (
    <div className={clsx("flex items-start gap-2.5 px-3 py-2 text-xs group", meta.bg)}>
      <span className={clsx("w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5", dot)} />
      <button
        className="flex-1 text-left leading-snug text-surface-600 hover:text-surface-800 transition-colors"
        onClick={() => {
          if (item.linkedTrailQuestionId) {
            onAsk({ id: item.linkedTrailQuestionId, question: item.title });
          } else {
            onAsk({ id: item.id, question: item.title });
          }
        }}
      >
        {item.title}
      </button>
      <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          className="text-[10px] text-surface-400 hover:text-emerald-600 transition-colors"
          onClick={onDone}
          title="Mark done"
        >
          <CheckCircle2 className="w-3 h-3" />
        </button>
        <button
          className="text-[10px] text-surface-400 hover:text-amber-600 transition-colors"
          onClick={onSnooze}
          title="Snooze"
        >
          <Pause className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

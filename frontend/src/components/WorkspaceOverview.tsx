import {
  FileText, Library, BookOpen, FlaskConical, ListChecks,
  ArrowRight, Clock,
} from "lucide-react";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { usePaperStore } from "@/store/paperStore";

export function WorkspaceOverview() {
  const { getActiveWorkspace, setSelectedNav, setActiveViewerTab } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const wid = workspace?.id ?? "default";

  const { getDeliverables } = useDeliverableStore();
  const { getIncludedSources, getSources } = useSourceStore();
  const { items: agendaItems } = useAgendaStore();
  const { papers } = usePaperStore();

  const deliverables = getDeliverables(wid);
  const includedSources = getIncludedSources(wid);
  const allSources = getSources(wid);
  const pendingAgenda = agendaItems.filter((i) => i.status === "pending" || i.status === "active");

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="max-w-lg mx-auto space-y-6">
        {/* Workspace summary */}
        <div>
          <h2 className="text-lg font-semibold text-surface-800 font-serif">
            {workspace?.title ?? "Workspace"}
          </h2>
          {workspace?.objective && (
            <p className="text-sm text-surface-500 mt-1">{workspace.objective}</p>
          )}
        </div>

        {/* Quick stats */}
        <div className="grid grid-cols-3 gap-3">
          <StatCard label="Papers" value={papers.length} />
          <StatCard label="Sources" value={`${includedSources.length}/${allSources.length}`} sub="included" />
          <StatCard label="Agenda" value={pendingAgenda.length} sub="pending" />
        </div>

        {/* Active deliverables */}
        {deliverables.length > 0 && (
          <Section title="Deliverables">
            <div className="space-y-1.5">
              {deliverables.slice(0, 5).map((d) => (
                <button
                  key={d.id}
                  onClick={() => {
                    useDeliverableStore.getState().setActiveDeliverable(wid, d.id);
                    setActiveViewerTab("deliverable");
                    setSelectedNav("reader");
                  }}
                  className="w-full text-left flex items-center gap-2 px-3 py-2 rounded-lg border border-surface-200 hover:bg-surface-50 transition-colors group"
                >
                  <FileText className="w-3.5 h-3.5 text-surface-400 flex-shrink-0" />
                  <span className="text-sm text-surface-700 truncate flex-1">{d.title}</span>
                  <span className="text-[10px] text-surface-400">{d.sections.length} sections</span>
                  <ArrowRight className="w-3 h-3 text-surface-300 group-hover:text-surface-500 transition-colors" />
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* Pending agenda */}
        {pendingAgenda.length > 0 && (
          <Section title="Up next">
            <div className="space-y-1">
              {pendingAgenda.slice(0, 4).map((item) => (
                <div key={item.id} className="flex items-start gap-2 px-3 py-1.5 text-xs text-surface-600">
                  <ListChecks className="w-3 h-3 mt-0.5 flex-shrink-0 text-surface-400" />
                  <span className="line-clamp-1">{item.title}</span>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Shortcuts */}
        <Section title="Quick actions">
          <div className="grid grid-cols-2 gap-2">
            <ShortcutCard
              icon={<FlaskConical className="w-4 h-4" />}
              label="Deep Research"
              onClick={() => setSelectedNav("deep-research")}
            />
            <ShortcutCard
              icon={<FileText className="w-4 h-4" />}
              label="Proposal / Plan"
              onClick={() => setSelectedNav("proposal")}
            />
            <ShortcutCard
              icon={<Library className="w-4 h-4" />}
              label="Sources"
              onClick={() => { setActiveViewerTab("sources"); setSelectedNav("reader"); }}
            />
            <ShortcutCard
              icon={<BookOpen className="w-4 h-4" />}
              label="Reader"
              onClick={() => { setActiveViewerTab("reader"); setSelectedNav("reader"); }}
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-lg border border-surface-200 bg-white px-3 py-2.5 text-center">
      <div className="text-lg font-semibold text-surface-800">{value}</div>
      <div className="text-[10px] text-surface-400 uppercase tracking-wider">{label}</div>
      {sub && <div className="text-[9px] text-surface-300">{sub}</div>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-medium text-surface-500 uppercase tracking-wider mb-2">{title}</h3>
      {children}
    </div>
  );
}

function ShortcutCard({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg border border-surface-200 hover:bg-surface-50 hover:border-surface-300 transition-colors text-left"
    >
      <span className="text-accent-500">{icon}</span>
      <span className="text-xs text-surface-600 font-medium">{label}</span>
    </button>
  );
}

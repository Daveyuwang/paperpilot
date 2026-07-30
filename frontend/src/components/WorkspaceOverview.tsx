import {
  ArrowRight,
  BookOpen,
  FileText,
  FlaskConical,
  ListChecks,
  MessageSquare,
} from "lucide-react";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import { useSourceStore } from "@/store/sourceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { usePaperStore } from "@/store/paperStore";
import { UploadZone } from "./UploadZone";

export function WorkspaceOverview() {
  const { getActiveWorkspace, setSelectedNav, setActiveViewerTab } = useWorkspaceStore();
  const workspace = getActiveWorkspace();
  const workspaceId = workspace?.id ?? "default";

  const { getDeliverables, setActiveDeliverable } = useDeliverableStore();
  const { getIncludedSources, getSources } = useSourceStore();
  const { items: agendaItems } = useAgendaStore();
  const { papers, activePaper } = usePaperStore();

  const deliverables = getDeliverables(workspaceId);
  const recentDrafts = [...deliverables].sort((a, b) => b.updatedAt - a.updatedAt);
  const includedSources = getIncludedSources(workspaceId);
  const allSources = getSources(workspaceId);
  const openAgenda = agendaItems.filter((item) => item.status === "pending" || item.status === "active");
  const hasResearchMaterial = papers.length > 0 || allSources.length > 0 || recentDrafts.length > 0 || openAgenda.length > 0;

  const openLibrary = () => {
    setActiveViewerTab("reader");
    setSelectedNav("reader");
  };

  const openDraft = (deliverableId: string) => {
    setActiveDeliverable(workspaceId, deliverableId);
    setActiveViewerTab("deliverable");
    setSelectedNav("reader");
  };

  return (
    <div className="h-full overflow-y-auto bg-white">
      <header className="border-b border-surface-200 px-5 py-5 sm:px-8">
        <h1 className="text-lg font-semibold tracking-[-0.02em] text-surface-900">Overview</h1>
        <p className="mt-1 max-w-2xl text-sm text-surface-500">
          {workspace?.objective || "Collect the evidence, then turn it into a clear answer or draft."}
        </p>
      </header>

      <div className="mx-auto max-w-5xl px-5 py-8 sm:px-8 sm:py-10">
        {!hasResearchMaterial ? (
          <section className="max-w-xl" aria-labelledby="start-title">
            <h2 id="start-title" className="text-xl font-semibold tracking-[-0.02em] text-surface-900">
              Start with a paper
            </h2>
            <p className="mt-2 text-sm leading-6 text-surface-500">
              Upload a PDF to read, ask grounded questions, and build a source-backed draft.
            </p>
            <div className="mt-6 max-w-sm">
              <UploadZone />
            </div>
          </section>
        ) : (
          <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_280px]">
            <div className="space-y-10">
              <section aria-labelledby="continue-title">
                <SectionHeading id="continue-title">Continue</SectionHeading>
                <div className="divide-y divide-surface-200 border-y border-surface-200">
                  {papers.length > 0 && (activePaper ? (
                    <WorkRow
                      icon={<BookOpen />}
                      label="Continue reading"
                      title={activePaper.title ?? activePaper.filename}
                      onClick={openLibrary}
                    />
                  ) : (
                    <WorkRow
                      icon={<BookOpen />}
                      label="Open your library"
                      title={`${papers.length} ${papers.length === 1 ? "paper" : "papers"} ready`}
                      onClick={openLibrary}
                    />
                  ))}
                  <WorkRow
                    icon={<MessageSquare />}
                    label="Ask across sources"
                    title={`${includedSources.length} included ${includedSources.length === 1 ? "source" : "sources"}`}
                    onClick={() => setSelectedNav("console")}
                  />
                  {recentDrafts[0] && (
                    <WorkRow
                      icon={<FileText />}
                      label="Continue drafting"
                      title={recentDrafts[0].title}
                      onClick={() => openDraft(recentDrafts[0].id)}
                    />
                  )}
                </div>
              </section>

              {recentDrafts.length > 1 && (
                <section aria-labelledby="drafts-title">
                  <SectionHeading id="drafts-title">Drafts</SectionHeading>
                  <div className="divide-y divide-surface-200 border-y border-surface-200">
                    {recentDrafts.slice(1, 6).map((deliverable) => (
                      <button
                        key={deliverable.id}
                        onClick={() => openDraft(deliverable.id)}
                        className="group flex w-full items-center gap-3 px-1 py-3 text-left"
                      >
                        <FileText className="h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-surface-700">
                          {deliverable.title}
                        </span>
                        <span className="text-xs text-surface-400">
                          {deliverable.sections.length} {deliverable.sections.length === 1 ? "section" : "sections"}
                        </span>
                        <ArrowRight className="h-4 w-4 flex-shrink-0 text-surface-300 transition-transform group-hover:translate-x-0.5 group-hover:text-surface-500" aria-hidden="true" />
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {openAgenda.length > 0 && (
                <section aria-labelledby="agenda-title">
                  <SectionHeading id="agenda-title">Up next</SectionHeading>
                  <ul className="divide-y divide-surface-200 border-y border-surface-200">
                    {openAgenda.slice(0, 5).map((item) => (
                      <li key={item.id} className="flex items-start gap-3 px-1 py-3">
                        <ListChecks className="mt-0.5 h-4 w-4 flex-shrink-0 text-surface-400" aria-hidden="true" />
                        <span className="text-sm leading-5 text-surface-600">{item.title}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>

            <aside className="space-y-7" aria-label="Workspace summary">
              <section>
                <SectionHeading>Workspace</SectionHeading>
                <dl className="divide-y divide-surface-200 border-y border-surface-200 text-sm">
                  <SummaryRow label="Papers" value={papers.length} />
                  <SummaryRow label="Sources included" value={`${includedSources.length}/${allSources.length}`} />
                  <SummaryRow label="Open items" value={openAgenda.length} />
                </dl>
              </section>

              <section>
                <SectionHeading>Make something</SectionHeading>
                <div className="space-y-1">
                  <ActionLink icon={<FlaskConical />} label="Run research" onClick={() => setSelectedNav("deep-research")} />
                  <ActionLink icon={<FileText />} label="Build a draft" onClick={() => setSelectedNav("proposal")} />
                </div>
              </section>
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}

function SectionHeading({ id, children }: { id?: string; children: React.ReactNode }) {
  return <h2 id={id} className="mb-3 text-sm font-semibold text-surface-700">{children}</h2>;
}

function WorkRow({ icon, label, title, onClick }: {
  icon: React.ReactElement;
  label: string;
  title: string;
  onClick: () => void;
}) {
  return (
    <button className="group flex w-full items-center gap-3 px-1 py-3.5 text-left" onClick={onClick}>
      <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-surface-100 text-surface-500 [&>svg]:h-4 [&>svg]:w-4" aria-hidden="true">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-surface-700">{label}</span>
        <span className="mt-0.5 block truncate text-xs text-surface-400">{title}</span>
      </span>
      <ArrowRight className="h-4 w-4 flex-shrink-0 text-surface-300 transition-transform group-hover:translate-x-0.5 group-hover:text-surface-500" aria-hidden="true" />
    </button>
  );
}

function SummaryRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="text-surface-500">{label}</dt>
      <dd className="font-semibold tabular-nums text-surface-700">{value}</dd>
    </div>
  );
}

function ActionLink({ icon, label, onClick }: {
  icon: React.ReactElement;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className="group flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm font-medium text-surface-600 hover:bg-surface-100 hover:text-surface-800" onClick={onClick}>
      <span className="text-surface-400 [&>svg]:h-4 [&>svg]:w-4" aria-hidden="true">{icon}</span>
      <span className="flex-1 text-left">{label}</span>
      <ArrowRight className="h-3.5 w-3.5 text-surface-300 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
    </button>
  );
}

import { useState, useEffect, useCallback, useRef } from "react";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useAgendaStore } from "@/store/agendaStore";
import { useSourceStore } from "@/store/sourceStore";
import { useChatStore } from "@/store/chatStore";
import type { Citation } from "@/types";
import { api } from "@/api/client";

import { SidebarNav } from "@/components/SidebarNav";
import { WorkspaceHeader } from "@/components/WorkspaceHeader";
import { WorkspaceHome } from "@/components/WorkspaceHome";
import { WorkspaceOverview } from "@/components/WorkspaceOverview";
import { ConsolePage } from "@/components/ConsolePage";
import { ReaderPage } from "@/components/ReaderPage";
import { UploadZone } from "@/components/UploadZone";
import { PaperList } from "@/components/PaperList";
import { SettingsModal } from "@/components/SettingsModal";
import { DeepResearchView } from "@/components/DeepResearchView";
import { ProposalPlanView } from "@/components/ProposalPlanView";
import clsx from "clsx";
import { WifiOff, RefreshCw } from "lucide-react";

type QueuedQuestion = { id?: string; question: string; nonce: number } | null;

export default function App() {
  const { papers, activePaper, questions, loadPapers, restoreActive, selectPaper } =
    usePaperStore();
  const { setActivePaperId, setActiveViewerTab, selectedNav, setSelectedNav, appView, getActiveWorkspace } = useWorkspaceStore();
  const { bootstrapFromTrail, switchPaperAgenda } = useAgendaStore();
  const { syncUploads } = useSourceStore();

  const [highlights, setHighlights] = useState<Citation[]>([]);
  const [targetPage, setTargetPage] = useState<number | undefined>();
  const [jumpCounter, setJumpCounter] = useState(0);
  const [queuedQuestion, setQueuedQuestion] = useState<QueuedQuestion>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const healthRetryRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const activeWs = getActiveWorkspace();

  // Load papers for active workspace
  useEffect(() => {
    if (activeWs?.id) {
      loadPapers(activeWs.id);
      restoreActive(activeWs.id).catch(() => {});
    }
  }, [activeWs?.id, loadPapers, restoreActive]);

  // Health check + settings bootstrap
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      const ok = await api.healthCheck();
      if (cancelled) return;
      if (!ok) {
        setBackendDown(true);
        startHealthRetry();
        return;
      }
      setBackendDown(false);
      try {
        const s = await api.getLLMSettings();
        if (!cancelled && !s.has_key) setSettingsOpen(true);
      } catch (err) {
        console.warn("[PaperPilot] settings fetch failed", err);
      }
    }

    function startHealthRetry() {
      if (healthRetryRef.current) return;
      healthRetryRef.current = setInterval(async () => {
        const ok = await api.healthCheck();
        if (ok) {
          setBackendDown(false);
          if (healthRetryRef.current) {
            clearInterval(healthRetryRef.current);
            healthRetryRef.current = null;
          }
          if (activeWs?.id) loadPapers(activeWs.id);
          try {
            const s = await api.getLLMSettings();
            if (!s.has_key) setSettingsOpen(true);
          } catch { /* settings will load when modal opens */ }
        }
      }, 10000);
    }

    bootstrap();

    return () => {
      cancelled = true;
      if (healthRetryRef.current) {
        clearInterval(healthRetryRef.current);
        healthRetryRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRetryHealth = useCallback(async () => {
    setRetrying(true);
    const ok = await api.healthCheck();
    setRetrying(false);
    if (ok) {
      setBackendDown(false);
      if (healthRetryRef.current) {
        clearInterval(healthRetryRef.current);
        healthRetryRef.current = null;
      }
      if (activeWs?.id) loadPapers(activeWs.id);
    }
  }, [loadPapers, activeWs?.id]);

  // Keep workspace store in sync with active paper
  useEffect(() => {
    setActivePaperId(activePaper?.id ?? null);
    if (!activePaper) {
      useAgendaStore.getState().clearVolatile();
    }
  }, [activePaper?.id, setActivePaperId]);

  // Bootstrap agenda when paper + questions are ready
  useEffect(() => {
    if (activePaper?.id && questions.length > 0) {
      bootstrapFromTrail(activePaper.id, questions);
    }
  }, [activePaper?.id, questions, bootstrapFromTrail]);

  // Sync uploaded papers into workspace sources
  useEffect(() => {
    if (papers.length > 0 && activeWs?.id) syncUploads(activeWs.id, papers);
  }, [papers, activeWs?.id, syncUploads]);

  // Bootstrap workspace console session (always, regardless of paper selection)
  const { setConsoleSessionId, getConsoleSessionId } = useChatStore();
  useEffect(() => {
    if (!activeWs?.id) return;
    const existing = getConsoleSessionId(activeWs.id);
    if (existing) return;

    let cancelled = false;
    api.createWorkspaceSession(activeWs.id).then((session) => {
      if (!cancelled) {
        setConsoleSessionId(activeWs.id, session.id);
      }
    }).catch((err) => {
      console.warn("[PaperPilot] console session creation failed", err);
    });
    return () => { cancelled = true; };
  }, [activeWs?.id, setConsoleSessionId, getConsoleSessionId]);

  const handleSelectPaper = useCallback(async (id: string) => {
    setHighlights([]);
    setTargetPage(undefined);
    setActiveViewerTab("reader");
    switchPaperAgenda(id);
    await selectPaper(id);
    setSelectedNav("reader");
  }, [selectPaper, setActiveViewerTab, switchPaperAgenda, setSelectedNav]);

  const handleHighlight = useCallback((citations: Citation[]) => {
    setHighlights(citations);
    const firstPage =
      citations.find((c) => c.bbox?.page)?.bbox?.page ??
      citations.find((c) => c.page_number)?.page_number;
    if (firstPage) {
      setTargetPage(firstPage);
      setJumpCounter((k) => k + 1);
    }
    setActiveViewerTab("reader");
    setSelectedNav("reader");
  }, [setActiveViewerTab, setSelectedNav]);

  const handleExplainConcept = useCallback((label: string) => {
    setQueuedQuestion({ question: `Explain the concept "${label}" as used in this paper.`, nonce: Date.now() });
    // If on reader, the queued question goes to reader's QA panel
    // Otherwise route to console
    const currentNav = useWorkspaceStore.getState().selectedNav;
    if (currentNav !== "reader") {
      setSelectedNav("console");
    }
  }, [setSelectedNav]);

  const handleShowInPaper = useCallback((page: number) => {
    setTargetPage(page);
    setJumpCounter((k) => k + 1);
    setActiveViewerTab("reader");
    setSelectedNav("reader");
  }, [setActiveViewerTab, setSelectedNav]);

  const handleTrailAsk = useCallback((q: { id: string; question: string }) => {
    setQueuedQuestion({ id: q.id, question: q.question, nonce: Date.now() });
    // If on reader, the queued question goes to reader's QA panel (paper-scoped)
    const currentNav = useWorkspaceStore.getState().selectedNav;
    if (currentNav !== "reader") {
      setSelectedNav("console");
    }
  }, [setSelectedNav]);

  const bboxes = highlights.map((c) => c.bbox).filter(Boolean) as NonNullable<Citation["bbox"]>[];

  return (
    <>
      {appView === "home" ? (
        <WorkspaceHome />
      ) : (
      <div className="flex flex-col h-screen overflow-hidden bg-surface-50">
        {/* Backend connectivity banner */}
        {backendDown && (
          <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2 bg-red-50 border-b border-red-200 text-red-700 text-xs">
            <WifiOff className="w-3.5 h-3.5 flex-shrink-0" />
            <span>Cannot reach the PaperPilot server. Check that Docker containers are running.</span>
            <button
              onClick={handleRetryHealth}
              disabled={retrying}
              className="ml-auto flex items-center gap-1 px-2 py-1 rounded bg-red-100 hover:bg-red-200 text-red-700 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={clsx("w-3 h-3", retrying && "animate-spin")} />
              {retrying ? "Checking…" : "Retry"}
            </button>
          </div>
        )}

        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Global left nav */}
          <SidebarNav onSettingsClick={() => setSettingsOpen(true)} />

          {/* Main area */}
          <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
            {/* Workspace header */}
            <WorkspaceHeader />

            {/* Body: source rail + page content */}
            <div className="flex flex-1 min-h-0 overflow-hidden">
              {/* Source Rail: upload + paper list */}
              <aside className="flex-shrink-0 w-48 flex flex-col border-r border-surface-200 bg-surface-50 overflow-hidden">
                <div className="px-3 py-3 border-b border-surface-200">
                  <UploadZone />
                </div>
                <div className="flex-1 overflow-y-auto px-2 py-2">
                  <PaperList onSelect={handleSelectPaper} />
                </div>
              </aside>

              {/* Page content — single page per nav item */}
              <main className="flex-1 min-w-0 overflow-hidden">
                {selectedNav === "workspace" && <WorkspaceOverview />}
                {selectedNav === "console" && (
                  <ConsolePage
                    onHighlight={handleHighlight}
                    queuedQuestion={queuedQuestion}
                    onQueuedQuestionHandled={(nonce) =>
                      setQueuedQuestion((cur) => (cur?.nonce === nonce ? null : cur))
                    }
                    onTrailAsk={handleTrailAsk}
                  />
                )}
                {selectedNav === "reader" && (
                  <ReaderPage
                    highlightBboxes={bboxes}
                    targetPage={targetPage}
                    jumpCounter={jumpCounter}
                    onExplainConcept={handleExplainConcept}
                    onShowInPaper={handleShowInPaper}
                    onTrailAsk={handleTrailAsk}
                    onHighlight={handleHighlight}
                    queuedQuestion={queuedQuestion}
                    onQueuedQuestionHandled={(nonce) =>
                      setQueuedQuestion((cur) => (cur?.nonce === nonce ? null : cur))
                    }
                  />
                )}
                {selectedNav === "deep-research" && <DeepResearchView />}
                {selectedNav === "proposal" && <ProposalPlanView />}
              </main>
            </div>
          </div>
        </div>
      </div>
      )}

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  );
}

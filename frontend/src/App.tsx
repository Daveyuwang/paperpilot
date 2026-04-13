import { useState, useEffect, useCallback } from "react";
import clsx from "clsx";
import { BookOpen, Map, ListChecks, FileText, PanelLeftClose, PanelLeft, RotateCcw, Settings, SquareX } from "lucide-react";

import { usePaperStore } from "@/store/paperStore";
import { useChatStore } from "@/store/chatStore";
import type { Citation } from "@/types";
import { api } from "@/api/client";

import { UploadZone } from "@/components/UploadZone";
import { PaperList } from "@/components/PaperList";
import { PDFViewer } from "@/components/PDFViewer";
import { QAPanel } from "@/components/QAPanel";
import { TrailTracker } from "@/components/TrailTracker";
import { ConceptMap } from "@/components/ConceptMap";
import { SettingsModal } from "@/components/SettingsModal";

type AssistantTab = "answer" | "trail" | "concepts";
type QueuedQuestion = { id?: string; question: string; nonce: number } | null;

const SIDEBAR_COLLAPSED_KEY = "pp_sidebar_collapsed";

function renderAssistantTab(tab: AssistantTab) {
  if (tab === "answer") {
    return (
      <>
        <BookOpen className="w-3.5 h-3.5" />
        Answer
      </>
    );
  }
  if (tab === "trail") {
    return (
      <>
        <ListChecks className="w-3.5 h-3.5" />
        Trail
      </>
    );
  }
  return (
    <>
      <Map className="w-3.5 h-3.5" />
      Concepts
    </>
  );
}

export default function App() {
  const { activePaper, activeSession, questions, loadPapers, restoreActive, selectPaper, newSession, endSession } = usePaperStore();
  const { messages } = useChatStore();

  const [highlights, setHighlights] = useState<Citation[]>([]);
  const [targetPage, setTargetPage] = useState<number | undefined>();
  const [jumpCounter, setJumpCounter] = useState(0);
  const [activeTab, setActiveTab] = useState<AssistantTab>("answer");
  const [queuedQuestion, setQueuedQuestion] = useState<QueuedQuestion>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [endConfirmOpen, setEndConfirmOpen] = useState(false);
  const [newChatConfirmOpen, setNewChatConfirmOpen] = useState(false);
  const [endLockSeconds, setEndLockSeconds] = useState(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true"
  );

  useEffect(() => {
    loadPapers();
    // Restore last active paper/session — do NOT call initSession() here,
    // because Zustand persist has already rehydrated messages from localStorage.
    restoreActive().catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.getLLMSettings()
      .then((s) => {
        if (!s.has_key) setSettingsOpen(true);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!endConfirmOpen) return;
    setEndLockSeconds(5);
    const t = setInterval(() => {
      setEndLockSeconds((v) => (v <= 1 ? 0 : v - 1));
    }, 1000);
    return () => clearInterval(t);
  }, [endConfirmOpen]);

  const handleSelectPaper = useCallback(async (id: string) => {
    // Clicking the currently active paper is a no-op
    if (id === activePaper?.id) return;
    setHighlights([]);
    setTargetPage(undefined);
    setActiveTab("answer");
    await selectPaper(id);
  }, [selectPaper, activePaper?.id]);

  const handleRestart = useCallback(async () => {
    setHighlights([]);
    setTargetPage(undefined);
    setActiveTab("answer");
    await newSession();
  }, [newSession]);

  const handleHighlight = useCallback((citations: Citation[]) => {
    setHighlights(citations);
    const firstPage = citations.find((c) => c.bbox?.page)?.bbox?.page
      ?? citations.find((c) => c.page_number)?.page_number;
    if (firstPage) {
      setTargetPage(firstPage);
      setJumpCounter((k) => k + 1);
    }
    setActiveTab("answer");
  }, []);

  const handleExplainConcept = useCallback((label: string) => {
    setQueuedQuestion({
      question: `Explain the concept "${label}" as used in this paper.`,
      nonce: Date.now(),
    });
    setActiveTab("answer");
  }, []);

  const handleShowInPaper = useCallback((page: number) => {
    setTargetPage(page);
    setJumpCounter((k) => k + 1);
    setActiveTab("answer");
  }, []);

  const toggleSidebar = () => {
    setSidebarCollapsed((v) => {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(!v));
      return !v;
    });
  };

  const bboxes = highlights
    .map((c) => c.bbox)
    .filter(Boolean) as NonNullable<Citation["bbox"]>[];

  const isConceptsWorkspace = activeTab === "concepts";
  const effectiveSidebarCollapsed = sidebarCollapsed;

  return (
    <>
    <div className="flex h-screen overflow-hidden bg-surface-900 text-gray-100">
      {/* ── Left sidebar ──────────────────────────────────────────────── */}
      <aside className={clsx(
        "flex-shrink-0 border-r border-white/5 flex flex-col transition-all duration-200",
        effectiveSidebarCollapsed ? "w-14" : "w-56"
      )}>
        <div className={clsx("px-3 py-3 border-b border-white/5 flex-shrink-0", effectiveSidebarCollapsed ? "items-center" : "")}>
          <div className="flex items-center justify-between mb-3">
            <div className={clsx("flex items-center gap-2", effectiveSidebarCollapsed && "justify-center w-full")}>
              <BookOpen className="w-5 h-5 text-accent-400 flex-shrink-0" />
              {!effectiveSidebarCollapsed && <span className="font-semibold text-sm">PaperPilot</span>}
            </div>
            {!effectiveSidebarCollapsed && (
              <div className="flex items-center gap-1">
                <button className="btn-ghost p-1" onClick={() => setSettingsOpen(true)} title="Settings">
                  <Settings className="w-4 h-4 text-gray-500" />
                </button>
                <button className="btn-ghost p-1" onClick={toggleSidebar} title="Collapse sidebar">
                  <PanelLeftClose className="w-4 h-4 text-gray-500" />
                </button>
              </div>
            )}
          </div>
          {!effectiveSidebarCollapsed && <UploadZone />}
        </div>
        {effectiveSidebarCollapsed ? (
          <div className="flex-1 flex flex-col items-center pt-2">
            <button className="btn-ghost p-2" onClick={toggleSidebar} title="Expand sidebar">
              <PanelLeft className="w-4 h-4 text-gray-500" />
            </button>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-2 py-2">
            <PaperList onSelect={handleSelectPaper} />
          </div>
        )}
      </aside>

      {/* ── Main ──────────────────────────────────────────────────────── */}
      {!activePaper ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-3">
            <FileText className="w-12 h-12 text-gray-600 mx-auto" />
            <p className="text-gray-400 font-medium">Upload a paper to get started</p>
            <p className="text-sm text-gray-600">PaperPilot will guide you through it</p>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 min-w-0 overflow-hidden">
          {activeTab === "concepts" ? (
            // ── Concepts workspace: graph takes center stage ──
            <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
              <div className="flex-1 min-h-0">
                <ConceptMap
                  paperId={activePaper.id}
                  paperTitle={activePaper.title ?? activePaper.filename}
                  activeTab={activeTab}
                  onTabChange={setActiveTab}
                  onExplainConcept={handleExplainConcept}
                  onShowInPaper={handleShowInPaper}
                />
              </div>
            </div>
          ) : (
            // ── Reading workspace: PDF + assistant pane ──
            <>
              {/* ── PDF panel ────────────────────────────────────────────── */}
              <div className={clsx(
                "min-w-0 flex flex-col border-r border-white/5 transition-opacity duration-200",
                sidebarCollapsed ? "flex-[55]" : "flex-1"
              )}>
                <PDFViewer
                  paperId={activePaper.id}
                  highlightBboxes={bboxes}
                  targetPage={targetPage}
                  jumpCounter={jumpCounter}
                  key={activePaper.id}
                />
              </div>

              {/* ── Assistant pane ───────────────────────────────────────── */}
              <div className={clsx(
                "flex-shrink-0 flex flex-col transition-opacity duration-200",
                sidebarCollapsed
                  ? "flex-[45] min-w-[580px] max-w-[780px]"
                  : "w-[620px]"
              )}>
                {/* Paper header */}
                <div className="flex-shrink-0 px-4 py-3 border-b border-white/5 flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <h2 className="text-sm font-semibold text-gray-300 truncate">
                      {activePaper.title ?? activePaper.filename}
                    </h2>
                    {activePaper.abstract && (
                      <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">
                        {activePaper.abstract}
                      </p>
                    )}
                  </div>
                  <div className="flex-shrink-0 flex items-center gap-3 mt-0.5">
                    {activeSession && (
                      <button
                        onClick={() => setEndConfirmOpen(true)}
                        className="flex items-center gap-1 text-xs text-gray-500 hover:text-rose-300 transition-colors"
                        title="End this session"
                      >
                        <SquareX className="w-3.5 h-3.5" />
                        End
                      </button>
                    )}
                    {messages.length > 0 && (
                      <button
                        onClick={() => setNewChatConfirmOpen(true)}
                        className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
                        title="Start a new chat for this paper"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        New chat
                      </button>
                    )}
                  </div>
                </div>

                {/* Tab bar */}
                <div className="flex-shrink-0 border-b border-white/5 px-4 py-2">
                  <div className="flex items-center rounded-xl border border-white/10 bg-white/[0.03] p-1">
                  {(["answer", "trail", "concepts"] as AssistantTab[]).map((tab) => (
                    <button
                      key={tab}
                      className={clsx(
                        "flex-1 flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                        activeTab === tab
                          ? "bg-white/10 text-gray-100 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]"
                          : "text-gray-500 hover:bg-white/[0.04] hover:text-gray-300"
                      )}
                      onClick={() => setActiveTab(tab)}
                    >
                      {renderAssistantTab(tab)}
                    </button>
                  ))}
                  </div>
                </div>

                {/* Tab content */}
                <div className="flex-1 min-h-0 relative overflow-hidden">
                  {/* Answer tab — always mounted so WebSocket stays alive */}
                  <div className={clsx("h-full", activeTab !== "answer" && "hidden")}>
                    <QAPanel
                      onHighlight={handleHighlight}
                      queuedQuestion={queuedQuestion}
                      onQueuedQuestionHandled={(nonce) => {
                        setQueuedQuestion((current) => (
                          current?.nonce === nonce ? null : current
                        ));
                      }}
                    />
                  </div>

                  {activeTab === "trail" && (
                    <div className="h-full overflow-y-auto p-3">
                      <TrailTracker
                        onAsk={(q) => {
                          setActiveTab("answer");
                          (window as any).__askGuideQuestion?.(q);
                        }}
                      />
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
    <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    {endConfirmOpen && (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/60" onClick={() => setEndConfirmOpen(false)} />
        <div className="relative w-[460px] max-w-[calc(100vw-24px)] rounded-2xl border border-white/10 bg-surface-900 shadow-xl">
          <div className="px-4 py-3 border-b border-white/10">
            <div className="text-sm font-semibold text-gray-100">End session?</div>
            <div className="mt-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
              This will delete all uploaded papers and clear local history.
            </div>
          </div>
          <div className="px-4 py-3 flex items-center justify-end gap-2">
            <button className="btn-ghost px-3 py-2 text-xs" onClick={() => setEndConfirmOpen(false)}>
              Cancel
            </button>
            <button
              className={clsx(
                "px-3 py-2 rounded-xl text-xs font-semibold",
                endLockSeconds > 0
                  ? "bg-white/5 text-gray-600 cursor-not-allowed"
                  : "bg-rose-600/30 text-rose-200 hover:bg-rose-600/40"
              )}
              disabled={endLockSeconds > 0}
              onClick={async () => {
                setEndConfirmOpen(false);
                setHighlights([]);
                setTargetPage(undefined);
                setActiveTab("answer");
                await endSession();
              }}
            >
              {endLockSeconds > 0 ? `End session (${endLockSeconds})` : "End session"}
            </button>
          </div>
        </div>
      </div>
    )}
    {newChatConfirmOpen && (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/60" onClick={() => setNewChatConfirmOpen(false)} />
        <div className="relative w-[460px] max-w-[calc(100vw-24px)] rounded-2xl border border-white/10 bg-surface-900 shadow-xl">
          <div className="px-4 py-3 border-b border-white/10">
            <div className="text-sm font-semibold text-gray-100">Start a new chat?</div>
            <div className="text-xs text-gray-500 mt-1">
              This will create a new session for this paper. Your previous chat will still be available in history.
            </div>
          </div>
          <div className="px-4 py-3 flex items-center justify-end gap-2">
            <button className="btn-ghost px-3 py-2 text-xs" onClick={() => setNewChatConfirmOpen(false)}>
              Cancel
            </button>
            <button
              className="px-3 py-2 rounded-xl text-xs font-semibold bg-white/10 text-gray-100 hover:bg-white/15"
              onClick={async () => {
                setNewChatConfirmOpen(false);
                await handleRestart();
              }}
            >
              New chat
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

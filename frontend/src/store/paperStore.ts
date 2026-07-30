import { create } from "zustand";
import type { Paper, PaperListItem, GuideQuestion, Chunk, Session } from "@/types";
import { api } from "@/api/client";

// Track which paper IDs are already being polled to avoid duplicate intervals
const _activePolls = new Set<string>();
let _workspaceGeneration = 0;
let _selectionGeneration = 0;

// ── localStorage helpers (workspace-scoped) ──────────────────────────────

function activeKey(wsId: string) { return `pp_active_${wsId}`; }
function sessionMapKey(wsId: string) { return `pp_session_by_paper_${wsId}`; }

function saveActive(paperId: string, sessionId: string, wsId: string) {
  localStorage.setItem(activeKey(wsId), JSON.stringify({ paperId, sessionId }));
  const map = getSessionByPaperMap(wsId);
  map[paperId] = sessionId;
  localStorage.setItem(sessionMapKey(wsId), JSON.stringify(map));
}

function loadActive(wsId: string): { paperId: string; sessionId: string } | null {
  try {
    const raw = localStorage.getItem(activeKey(wsId));
    if (raw) return JSON.parse(raw);
    // Migration: check old global key
    const old = localStorage.getItem("pp_active");
    if (old) {
      localStorage.removeItem("pp_active");
      localStorage.setItem(activeKey(wsId), old);
      return JSON.parse(old);
    }
    return null;
  } catch { return null; }
}

function clearActive(wsId: string) {
  localStorage.removeItem(activeKey(wsId));
}

function getSessionByPaperMap(wsId: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(sessionMapKey(wsId));
    if (raw) return JSON.parse(raw);
    // Migration: check old global key
    const old = localStorage.getItem("pp_session_by_paper");
    if (old) {
      localStorage.removeItem("pp_session_by_paper");
      localStorage.setItem(sessionMapKey(wsId), old);
      return JSON.parse(old);
    }
    return {};
  } catch { return {}; }
}

function clearSessionForPaper(paperId: string, wsId: string) {
  const map = getSessionByPaperMap(wsId);
  if (map[paperId]) {
    delete map[paperId];
    localStorage.setItem(sessionMapKey(wsId), JSON.stringify(map));
  }
}

export function clearWorkspaceStorage(wsId: string) {
  localStorage.removeItem(activeKey(wsId));
  localStorage.removeItem(sessionMapKey(wsId));
}

// ── Store ─────────────────────────────────────────────────────────────────

interface PaperStore {
  papers: PaperListItem[];
  activePaper: Paper | null;
  activeSession: Session | null;
  questions: GuideQuestion[];
  chunks: Chunk[];
  isLoading: boolean;
  error: string | null;
  currentWorkspaceId: string | null;
  loadedWorkspaceId: string | null;

  loadPapers: (workspaceId?: string) => Promise<void>;
  uploadPaper: (file: File, workspaceId?: string) => Promise<Paper>;
  selectPaper: (id: string) => Promise<string[] | null>;
  deselectPaper: () => Promise<void>;
  deletePaper: (id: string) => Promise<void>;
  pollPaperStatus: (id: string) => void;
  newSession: () => Promise<void>;
  endSession: () => Promise<void>;
  restoreActive: (workspaceId?: string) => Promise<string[] | null>;
  resetForWorkspace: (workspaceId: string) => Promise<void>;
}

export const usePaperStore = create<PaperStore>((set, get) => ({
  papers: [],
  activePaper: null,
  activeSession: null,
  questions: [],
  chunks: [],
  isLoading: false,
  error: null,
  currentWorkspaceId: null,
  loadedWorkspaceId: null,

  loadPapers: async (workspaceId) => {
    const requestedWorkspaceId = workspaceId ?? null;
    const requestGeneration = _workspaceGeneration;
    set({ isLoading: true, error: null, currentWorkspaceId: requestedWorkspaceId });
    try {
      const papers = await api.listPapers(workspaceId);
      if (get().currentWorkspaceId !== requestedWorkspaceId || _workspaceGeneration !== requestGeneration) return;
      set({ papers, isLoading: false, loadedWorkspaceId: requestedWorkspaceId });
      papers.forEach((p) => {
        if (p.status === "pending" || p.status === "processing") {
          get().pollPaperStatus(p.id);
        }
      });
    } catch (e) {
      if (get().currentWorkspaceId !== requestedWorkspaceId || _workspaceGeneration !== requestGeneration) return;
      set({ error: String(e), isLoading: false });
    }
  },

  uploadPaper: async (file, workspaceId) => {
    const requestedWorkspaceId = workspaceId ?? null;
    const requestGeneration = _workspaceGeneration;
    set({ isLoading: true, error: null });
    try {
      const paper = await api.uploadPaper(file, workspaceId);
      if (get().currentWorkspaceId !== requestedWorkspaceId || _workspaceGeneration !== requestGeneration) {
        return paper;
      }
      set((state) => ({
        papers: state.papers.some((candidate) => candidate.id === paper.id)
          ? state.papers.map((candidate) => candidate.id === paper.id ? paper : candidate)
          : [paper, ...state.papers],
        isLoading: false,
      }));
      get().pollPaperStatus(paper.id);
      try {
        const papers = await api.listPapers(workspaceId);
        if (get().currentWorkspaceId === requestedWorkspaceId && _workspaceGeneration === requestGeneration) {
          set({ papers, loadedWorkspaceId: requestedWorkspaceId });
        }
      } catch (refreshError) {
        console.warn("[PaperPilot] paper list refresh failed after upload", refreshError);
      }
      return paper;
    } catch (e) {
      if (get().currentWorkspaceId !== requestedWorkspaceId || _workspaceGeneration !== requestGeneration) throw e;
      set({ error: String(e), isLoading: false });
      throw e;
    }
  },

  deselectPaper: async () => {
    const { currentWorkspaceId } = get();
    if (currentWorkspaceId) clearActive(currentWorkspaceId);
    set({ activePaper: null, activeSession: null, questions: [], chunks: [] });

    const { useChatStore } = await import("@/store/chatStore");
    const { useWorkspaceStore } = await import("@/store/workspaceStore");
    const ws = useWorkspaceStore.getState().getActiveWorkspace();
    if (ws) {
      const consoleSessionId = useChatStore.getState().getConsoleSessionId(ws.id);
      if (consoleSessionId) {
        useChatStore.getState().switchToSession(consoleSessionId, []);
      } else {
        useChatStore.getState().clearChat();
      }
    } else {
      useChatStore.getState().clearChat();
    }

    const { useAgendaStore } = await import("@/store/agendaStore");
    useAgendaStore.getState().clearVolatile();
  },

  selectPaper: async (id) => {
    const { activePaper, activeSession, currentWorkspaceId } = get();
    const wsId = currentWorkspaceId ?? "default";
    const requestGeneration = _workspaceGeneration;
    const selectionGeneration = ++_selectionGeneration;
    const isCurrentSelection = () => (
      get().currentWorkspaceId === wsId
      && _workspaceGeneration === requestGeneration
      && _selectionGeneration === selectionGeneration
    );
    if (!get().papers.some((paper) => paper.id === id)) return null;

    if (activePaper?.id === id) {
      const sessionState = await api
        .getSessionState(activeSession?.id ?? "")
        .catch(() => ({}));
      if (!isCurrentSelection() || get().activePaper?.id !== id) return null;
      return (sessionState as Record<string, unknown>).covered_question_ids as string[] ?? [];
    }

    set({ isLoading: true, error: null });
    try {
      const { useChatStore } = await import("@/store/chatStore");
      const sessionMap = getSessionByPaperMap(wsId);
      const lastSessionId = sessionMap[id];

      let session: Session;
      if (lastSessionId) {
        try {
          session = await api.getSession(lastSessionId);
          if (session.paper_id !== id) throw new Error("Stored session belongs to another paper");
        } catch {
          session = await api.createSession(id);
        }
      } else {
        session = await api.createSession(id);
      }

      const [paper, questions, chunks, sessionState] = await Promise.all([
        api.getPaper(id),
        api.getQuestions(id),
        api.getChunks(id),
        api.getSessionState(session.id).catch(() => ({})),
      ]);

      if (!isCurrentSelection()) return null;

      saveActive(id, session.id, wsId);
      const coveredIds = (sessionState as any).covered_question_ids ?? [];

      useChatStore.getState().switchToSession(session.id, coveredIds);

      set({ activePaper: paper, activeSession: session, questions, chunks, isLoading: false });
      if (paper.status !== "ready") get().pollPaperStatus(id);

      return coveredIds;
    } catch (e) {
      if (!isCurrentSelection()) return null;
      set({ error: String(e), isLoading: false });
      return null;
    }
  },

  deletePaper: async (id) => {
    const prev = get();
    const isActive = prev.activePaper?.id === id;
    const wsId = prev.currentWorkspaceId ?? "default";
    const requestGeneration = _workspaceGeneration;
    set({ isLoading: true, error: null });

    try {
      const [{ useChatStore }, { useSourceStore }] = await Promise.all([
        import("@/store/chatStore"),
        import("@/store/sourceStore"),
      ]);
      await api.deletePaper(id);
      if (get().currentWorkspaceId !== prev.currentWorkspaceId || _workspaceGeneration !== requestGeneration) return;

      if (isActive) {
        _selectionGeneration += 1;
        clearActive(wsId);
        const sessionId = prev.activeSession?.id;
        if (sessionId) useChatStore.getState().clearSession(sessionId);
        else useChatStore.getState().clearChat();
      }
      clearSessionForPaper(id, wsId);
      set({
        papers: get().papers.filter((paper) => paper.id !== id),
        isLoading: false,
        ...(isActive ? { activePaper: null, activeSession: null, questions: [], chunks: [] } : {}),
      });

      const matchingSource = useSourceStore.getState().getSources(wsId).find((source) => source.paper_id === id);
      if (matchingSource) useSourceStore.getState().removeSource(wsId, matchingSource.id);
    } catch (e) {
      console.debug("[PaperPilot] delete_fail", { paperId: id, error: String(e) });
      if (get().currentWorkspaceId === prev.currentWorkspaceId && _workspaceGeneration === requestGeneration) {
        set({ error: "Could not delete this paper. Try again.", isLoading: false });
      }
    }
  },

  pollPaperStatus: (id) => {
    if (_activePolls.has(id)) return;
    _activePolls.add(id);
    const interval = setInterval(async () => {
      try {
        const paper = await api.getPaper(id);
        set((s) => ({
          papers: s.papers.map((p) =>
            p.id === id ? { ...p, status: paper.status, title: paper.title } : p
          ),
          activePaper: s.activePaper?.id === id ? paper : s.activePaper,
        }));
        if (paper.status === "ready" || paper.status === "error") {
          clearInterval(interval);
          _activePolls.delete(id);
        }
      } catch {
        clearInterval(interval);
        _activePolls.delete(id);
      }
    }, 3000);
  },

  newSession: async () => {
    const { activePaper, currentWorkspaceId } = get();
    if (!activePaper) return;
    const wsId = currentWorkspaceId ?? "default";
    const paperId = activePaper.id;
    const requestGeneration = _workspaceGeneration;
    const selectionGeneration = _selectionGeneration;
    try {
      const [{ useChatStore }, session] = await Promise.all([
        import("@/store/chatStore"),
        api.createSession(paperId),
      ]);
      if (
        get().currentWorkspaceId !== currentWorkspaceId
        || get().activePaper?.id !== paperId
        || _workspaceGeneration !== requestGeneration
        || _selectionGeneration !== selectionGeneration
      ) return;
      saveActive(paperId, session.id, wsId);

      useChatStore.getState().switchToSession(session.id, []);

      set({ activeSession: session });
    } catch (e) {
      console.debug("[PaperPilot] new_session_fail", { paperId: activePaper.id, error: String(e) });
    }
  },

  endSession: async () => {
    const prev = get();
    const wsId = prev.currentWorkspaceId ?? "default";
    try {
      const papers = await api.listPapers(prev.currentWorkspaceId ?? undefined).catch(() => prev.papers);
      for (const p of papers) {
        try {
          await api.deletePaper(p.id);
        } catch {}
      }
    } finally {
      clearActive(wsId);
      for (const p of prev.papers) clearSessionForPaper(p.id, wsId);
      const { useChatStore } = await import("@/store/chatStore");
      try { localStorage.removeItem("paperpilot-chat"); } catch {}
      useChatStore.getState().clearChat();
      set({ papers: [], activePaper: null, activeSession: null, questions: [], chunks: [] });
    }
  },

  restoreActive: async (workspaceId) => {
    const wsId = workspaceId ?? get().currentWorkspaceId ?? "default";
    const requestGeneration = _workspaceGeneration;
    const selectionGeneration = ++_selectionGeneration;
    const isCurrentRestore = () => (
      get().currentWorkspaceId === wsId
      && _workspaceGeneration === requestGeneration
      && _selectionGeneration === selectionGeneration
    );
    const stored = loadActive(wsId);
    if (!stored) return null;
    if (!get().papers.some((paper) => paper.id === stored.paperId)) {
      clearActive(wsId);
      return null;
    }

    let paper: Paper;
    try {
      paper = await api.getPaper(stored.paperId);
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes("404")) clearActive(wsId);
      return null;
    }
    if (!isCurrentRestore()) return null;
    if (paper.status !== "ready") return null;

    let session: Session;
    try {
      session = await api.getSession(stored.sessionId);
      if (session.paper_id !== stored.paperId) throw new Error("Stored session belongs to another paper");
    } catch {
      try {
        session = await api.createSession(stored.paperId);
        saveActive(stored.paperId, session.id, wsId);
      } catch {
        return null;
      }
    }
    if (!isCurrentRestore()) return null;

    try {
      const [questions, chunks, sessionState] = await Promise.all([
        api.getQuestions(stored.paperId),
        api.getChunks(stored.paperId),
        api.getSessionState(session.id).catch(() => ({})),
      ]);

      if (!isCurrentRestore()) return null;

      const coveredIds = (sessionState as any).covered_question_ids ?? [];
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, coveredIds);

      set({ activePaper: paper, activeSession: session, questions, chunks });
      return coveredIds;
    } catch {
      if (!isCurrentRestore()) return null;
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, []);
      set({ activePaper: paper, activeSession: session, questions: [], chunks: [] });
      return [];
    }
  },

  resetForWorkspace: async (workspaceId) => {
    const requestGeneration = ++_workspaceGeneration;
    _selectionGeneration += 1;
    set({
      activePaper: null,
      activeSession: null,
      questions: [],
      chunks: [],
      papers: [],
      error: null,
      currentWorkspaceId: workspaceId,
      loadedWorkspaceId: null,
    });
    const { useAgendaStore } = await import("@/store/agendaStore");
    useAgendaStore.getState().clearVolatile();
    await get().loadPapers(workspaceId);
    if (get().currentWorkspaceId !== workspaceId || _workspaceGeneration !== requestGeneration) return;
    await get().restoreActive(workspaceId);
  },
}));

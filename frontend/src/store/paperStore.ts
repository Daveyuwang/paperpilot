import { create } from "zustand";
import type { Paper, PaperListItem, GuideQuestion, Chunk, Session } from "@/types";
import { api } from "@/api/client";

// Track which paper IDs are already being polled to avoid duplicate intervals
const _activePolls = new Set<string>();

// ── localStorage helpers ──────────────────────────────────────────────────

const ACTIVE_KEY = "pp_active";
const SESSION_BY_PAPER_KEY = "pp_session_by_paper";

function saveActive(paperId: string, sessionId: string) {
  localStorage.setItem(ACTIVE_KEY, JSON.stringify({ paperId, sessionId }));
  // Also update the session-by-paper map so switching back to this paper restores it
  const map = getSessionByPaperMap();
  map[paperId] = sessionId;
  localStorage.setItem(SESSION_BY_PAPER_KEY, JSON.stringify(map));
}

function loadActive(): { paperId: string; sessionId: string } | null {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function clearActive() {
  localStorage.removeItem(ACTIVE_KEY);
}

function getSessionByPaperMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(SESSION_BY_PAPER_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function clearSessionForPaper(paperId: string) {
  const map = getSessionByPaperMap();
  if (map[paperId]) {
    delete map[paperId];
    localStorage.setItem(SESSION_BY_PAPER_KEY, JSON.stringify(map));
  }
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

  loadPapers: () => Promise<void>;
  uploadPaper: (file: File) => Promise<Paper>;
  /**
   * Select a paper. Returns the covered question IDs from Redis session state.
   * - If id === activePaper.id: no-op (returns current covered IDs without any state change).
   * - If different paper: restores the paper's last session (or creates one if none exists),
   *   then calls chatStore.switchToSession to load persisted messages for that session.
   */
  selectPaper: (id: string) => Promise<string[]>;
  deletePaper: (id: string) => Promise<void>;
  pollPaperStatus: (id: string) => void;
  /** Create a fresh session for the currently active paper (for "New chat"). */
  newSession: () => Promise<void>;
  /** Explicitly end the current session. */
  endSession: () => Promise<void>;
  // Restore active paper+session from localStorage on app startup
  restoreActive: () => Promise<string[] | null>;
}

export const usePaperStore = create<PaperStore>((set, get) => ({
  papers: [],
  activePaper: null,
  activeSession: null,
  questions: [],
  chunks: [],
  isLoading: false,
  error: null,

  loadPapers: async () => {
    set({ isLoading: true, error: null });
    try {
      const papers = await api.listPapers();
      set({ papers, isLoading: false });
      // Auto-poll any papers still being processed (survives page refresh)
      papers.forEach((p) => {
        if (p.status === "pending" || p.status === "processing") {
          get().pollPaperStatus(p.id);
        }
      });
    } catch (e) {
      set({ error: String(e), isLoading: false });
    }
  },

  uploadPaper: async (file) => {
    set({ isLoading: true, error: null });
    try {
      const paper = await api.uploadPaper(file);
      const papers = await api.listPapers();
      set({ papers, isLoading: false });
      get().pollPaperStatus(paper.id);
      return paper;
    } catch (e) {
      set({ error: String(e), isLoading: false });
      throw e;
    }
  },

  selectPaper: async (id) => {
    const { activePaper, activeSession } = get();

    // Clicking the currently active paper is a no-op — no new chat, no reset
    if (activePaper?.id === id) {
      const sessionState = await api
        .getSessionState(activeSession?.id ?? "")
        .catch(() => ({}));
      return (sessionState as any).covered_question_ids ?? [];
    }

    set({ isLoading: true, error: null });
    try {
      // Look up the last session for this paper (if any)
      const sessionMap = getSessionByPaperMap();
      const lastSessionId = sessionMap[id];

      let session: Session;
      if (lastSessionId) {
        try {
          session = await api.getSession(lastSessionId);
        } catch {
          // Session expired — create a fresh one
          session = await api.createSession(id);
        }
      } else {
        // First time selecting this paper — create a session
        session = await api.createSession(id);
      }

      const [paper, questions, chunks, sessionState] = await Promise.all([
        api.getPaper(id),
        api.getQuestions(id),
        api.getChunks(id),
        api.getSessionState(session.id).catch(() => ({})),
      ]);

      saveActive(id, session.id); // also updates pp_session_by_paper

      const coveredIds = (sessionState as any).covered_question_ids ?? [];

      // Switch session in chatStore: saves current session's messages, loads new session's
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, coveredIds);

      set({ activePaper: paper, activeSession: session, questions, chunks, isLoading: false });
      if (paper.status !== "ready") get().pollPaperStatus(id);

      return coveredIds;
    } catch (e) {
      set({ error: String(e), isLoading: false });
      return [];
    }
  },

  deletePaper: async (id) => {
    const prev = get();
    const isActive = prev.activePaper?.id === id;

    // Optimistic: immediately remove from list and clear active state
    set({
      papers: prev.papers.filter((p) => p.id !== id),
      ...(isActive
        ? { activePaper: null, activeSession: null, questions: [], chunks: [] }
        : {}),
    });
    if (isActive) {
      clearActive();
      const sessionId = prev.activeSession?.id;
      const { useChatStore } = await import("@/store/chatStore");
      if (sessionId) {
        useChatStore.getState().clearSession(sessionId);
      } else {
        useChatStore.getState().clearChat();
      }
    }

    try {
      await api.deletePaper(id);
    } catch (e) {
      // Rollback on failure: re-fetch from server
      console.debug("[PaperPilot] delete_fail", { paperId: id, error: String(e) });
      const papers = await api.listPapers().catch(() => prev.papers);
      set({ papers });
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
    const { activePaper } = get();
    if (!activePaper) return;
    try {
      const session = await api.createSession(activePaper.id);
      saveActive(activePaper.id, session.id); // also updates pp_session_by_paper

      // Switch to new (empty) session — saves current messages, starts fresh
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, []);

      set({ activeSession: session });
    } catch (e) {
      console.debug("[PaperPilot] new_session_fail", { paperId: activePaper.id, error: String(e) });
    }
  },

  endSession: async () => {
    const { activePaper, activeSession } = get();
    if (!activePaper || !activeSession) return;
    try {
      await api.deleteSession(activeSession.id).catch(() => {});
    } finally {
      clearActive();
      clearSessionForPaper(activePaper.id);
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().clearSession(activeSession.id);
      set({ activeSession: null });
    }
  },

  restoreActive: async () => {
    const stored = loadActive();
    if (!stored) return null;

    // Paper: if definitely gone (404), clear stored ref. On network error, leave it.
    let paper: Paper;
    try {
      paper = await api.getPaper(stored.paperId);
    } catch (e: any) {
      if (String(e?.message ?? "").includes("404")) clearActive();
      return null; // network error: keep pp_active ref, retry next time
    }
    if (paper.status !== "ready") return null; // still processing, keep ref

    // Session: create fresh one if expired; don't bail if creation also fails
    let session: Session;
    try {
      session = await api.getSession(stored.sessionId);
    } catch {
      try {
        session = await api.createSession(stored.paperId);
        saveActive(stored.paperId, session.id);
      } catch {
        return null;
      }
    }

    // Secondary data: questions/chunks failure is non-fatal — still restore active paper
    try {
      const [questions, chunks, sessionState] = await Promise.all([
        api.getQuestions(stored.paperId),
        api.getChunks(stored.paperId),
        api.getSessionState(session.id).catch(() => ({})),
      ]);

      const coveredIds = (sessionState as any).covered_question_ids ?? [];

      // Switch session in chatStore to restore this session's persisted messages
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, coveredIds);

      set({ activePaper: paper, activeSession: session, questions, chunks });
      return coveredIds;
    } catch {
      const { useChatStore } = await import("@/store/chatStore");
      useChatStore.getState().switchToSession(session.id, []);
      set({ activePaper: paper, activeSession: session, questions: [], chunks: [] });
      return [];
    }
  },
}));

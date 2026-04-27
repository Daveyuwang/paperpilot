import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ChatMessage, AnswerJSON, SuggestedQuestion, Citation } from "../types";

let _msgCounter = 0;
const newId = () => `msg_${Date.now()}_${++_msgCounter}`;

function buildPersistedSessionMap(
  messagesBySession: Record<string, ChatMessage[]>,
  activeSessionId: string | null,
  messages: ChatMessage[]
): Record<string, ChatMessage[]> {
  const next = { ...messagesBySession };
  if (activeSessionId) {
    next[activeSessionId] = messages;
  }
  return next;
}

interface ChatState {
  // Current session's messages (volatile — restored by switchToSession on startup)
  messages: ChatMessage[];
  // All sessions' messages keyed by session ID (persisted)
  messages_by_session: Record<string, ChatMessage[]>;
  // Currently active session ID (persisted so onRehydrateStorage can restore messages)
  activeSessionId: string | null;
  // Workspace console session IDs (persisted)
  consoleSessionIdByWorkspace: Record<string, string>;

  statusText: string;
  isGenerating: boolean;
  activeQuestionId: string | null;
  coveredQuestionIds: string[];
  suggestedQuestions: SuggestedQuestion[];
  currentMode: string;
  currentScopeLabel: string;

  // Edit + resubmit
  editingMessageId: string | null;
  setEditingMessageId: (id: string | null) => void;
  updateMessageContent: (id: string, content: string) => void;
  resubmitFrom: (id: string, newContent: string) => void;

  // Actions
  switchToSession: (newSessionId: string, coveredIds?: string[]) => void;
  clearSession: (sessionId: string) => void;
  setConsoleSessionId: (workspaceId: string, sessionId: string) => void;
  getConsoleSessionId: (workspaceId: string) => string | null;
  clearWorkspace: (workspaceId: string) => void;
  initSession: () => void;
  addUserMessage: (text: string) => string;
  startAssistantMessage: () => string;
  startSilentAssistantMessage: () => string;
  appendContent: (id: string, chunk: string) => void;
  setStreamingText: (id: string, text: string) => void;
  setStatus: (text: string) => void;
  setAnswerJson: (id: string, json: AnswerJSON) => void;
  setCitations: (id: string, citations: Citation[]) => void;
  finalizeMessage: (id: string) => void;
  failMessage: (id: string, errText: string) => void;
  stopGeneration: (id: string) => void;
  keepPartial: (id: string) => void;
  discardPartial: (id: string) => void;
  setSuggestedQuestions: (qs: SuggestedQuestion[]) => void;
  setActiveQuestionId: (id: string | null) => void;
  setCurrentMode: (mode: string) => void;
  setCurrentScopeLabel: (label: string) => void;
  markQuestionCovered: (id: string) => void;
  clearChat: () => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      messages: [],
      messages_by_session: {},
      activeSessionId: null,
      consoleSessionIdByWorkspace: {},
      statusText: "",
      isGenerating: false,
      activeQuestionId: null,
      coveredQuestionIds: [],
      suggestedQuestions: [],
      currentMode: "paper_understanding",
      currentScopeLabel: "Using this paper",
      editingMessageId: null,

      setEditingMessageId: (id) => set({ editingMessageId: id }),

      updateMessageContent: (id, content) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, content } : m
          ),
        })),

      resubmitFrom: (id, newContent) =>
        set((s) => {
          const idx = s.messages.findIndex((m) => m.id === id);
          if (idx === -1) return s;
          const trimmed = s.messages.slice(0, idx + 1);
          trimmed[idx] = { ...trimmed[idx], content: newContent };
          return { messages: trimmed, editingMessageId: null };
        }),

      switchToSession: (newSessionId, coveredIds = []) =>
        set((s) => {
          // Flush current messages into the old session's bucket
          const bySession = { ...s.messages_by_session };
          if (s.activeSessionId) {
            bySession[s.activeSessionId] = s.messages;
          }
          // Load new session's messages (empty array if first visit)
          const newMessages = bySession[newSessionId] ?? [];
          return {
            messages_by_session: bySession,
            messages: newMessages,
            activeSessionId: newSessionId,
            coveredQuestionIds: coveredIds,
            suggestedQuestions: [],
            activeQuestionId: null,
            statusText: "",
            isGenerating: false,
            currentMode: "paper_understanding",
            currentScopeLabel: "Using this paper",
          };
        }),

      clearSession: (sessionId) =>
        set((s) => {
          const bySession = { ...s.messages_by_session };
          delete bySession[sessionId];
          const isActive = s.activeSessionId === sessionId;
          return {
            messages_by_session: bySession,
            ...(isActive
              ? {
                  messages: [],
                  activeSessionId: null,
                  coveredQuestionIds: [],
                  suggestedQuestions: [],
                  activeQuestionId: null,
                  statusText: "",
                  isGenerating: false,
                  currentMode: "paper_understanding",
                  currentScopeLabel: "Using this paper",
                }
              : {}),
          };
        }),

      setConsoleSessionId: (workspaceId, sessionId) =>
        set((s) => ({
          consoleSessionIdByWorkspace: { ...s.consoleSessionIdByWorkspace, [workspaceId]: sessionId },
        })),

      getConsoleSessionId: (workspaceId) => {
        return get().consoleSessionIdByWorkspace[workspaceId] ?? null;
      },

      clearWorkspace: (workspaceId) =>
        set((s) => {
          const { [workspaceId]: removedSessionId, ...restConsole } = s.consoleSessionIdByWorkspace;
          const bySession = { ...s.messages_by_session };
          if (removedSessionId) delete bySession[removedSessionId];
          return {
            consoleSessionIdByWorkspace: restConsole,
            messages_by_session: bySession,
          };
        }),

      initSession: () =>
        set({
          messages: [],
          statusText: "",
          isGenerating: false,
          activeQuestionId: null,
          coveredQuestionIds: [],
          suggestedQuestions: [],
          currentMode: "paper_understanding",
          currentScopeLabel: "Using this paper",
        }),

      addUserMessage: (text) => {
        const id = newId();
        set((s) => ({
          messages: [
            ...s.messages,
            {
              id,
              role: "user",
              content: text,
              streamingText: "",
              answerJson: null,
              citations: [],
              timestamp: new Date(),
            },
          ],
        }));
        return id;
      },

      startAssistantMessage: () => {
        const id = newId();
        set((s) => ({
          isGenerating: true,
          statusText: "",
          messages: [
            ...s.messages,
            {
              id,
              role: "assistant",
              content: "",
              streamingText: "",
              answerJson: null,
              citations: [],
              timestamp: new Date(),
              isStreaming: true,
              isDone: false,
            },
          ],
        }));
        return id;
      },

      startSilentAssistantMessage: () => {
        const id = newId();
        set((s) => ({
          isGenerating: true,
          statusText: "",
          messages: [
            ...s.messages,
            {
              id,
              role: "assistant",
              content: "",
              streamingText: "",
              answerJson: null,
              citations: [],
              timestamp: new Date(),
              isStreaming: true,
              isDone: false,
            },
          ],
        }));
        return id;
      },

      appendContent: (id, chunk) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, content: m.content + chunk } : m
          ),
        })),

      setStreamingText: (id, text) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, streamingText: text } : m
          ),
        })),

      setStatus: (text) => set({ statusText: text }),

      setAnswerJson: (id, json) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, answerJson: json, phase1Complete: true } : m
          ),
        })),

      setCitations: (id, citations) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, citations } : m
          ),
        })),

      finalizeMessage: (id) => {
        // Mark as done briefly (triggers Done step render + Done footer animation)
        set((s) => ({
          isGenerating: false,
          statusText: "done",
          messages: s.messages.map((m) =>
            m.id === id
              ? { ...m, isStreaming: false, isDone: true }
              : m
          ),
        }));

        // After 4000ms, clear the "done" status and isDone flag (matches DoneMarker 3s + 600ms fade)
        setTimeout(() => {
          set((s) => ({
            statusText: "",
            messages: s.messages.map((m) =>
              m.id === id ? { ...m, isDone: false } : m
            ),
          }));
        }, 4000);
      },

      failMessage: (id, errText) =>
        set((s) => ({
          isGenerating: false,
          statusText: "",
          messages: s.messages.map((m) =>
            m.id === id
              ? {
                  ...m,
                  content: errText,
                  streamingText: "",
                  isStreaming: false,
                  isDone: false,
                  isPartial: false,
                }
              : m
          ),
        })),

      stopGeneration: (id) =>
        set((s) => {
          const msg = s.messages.find((m) => m.id === id);
          const hasContent = !!(msg?.streamingText || msg?.answerJson || msg?.content);
          return {
            isGenerating: false,
            statusText: "",
            activeQuestionId: null,  // clear trail spinner
            // If content exists, keep the partial answer as-is (no buttons shown)
            // If nothing was generated yet, remove the empty bubble
            messages: hasContent
              ? s.messages.map((m) =>
                  m.id === id ? { ...m, isStreaming: false, isPartial: false } : m
                )
              : s.messages.filter((m) => m.id !== id),
          };
        }),

      keepPartial: (id) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, isPartial: false } : m
          ),
        })),

      discardPartial: (id) =>
        set((s) => ({
          messages: s.messages.filter((m) => m.id !== id),
          isGenerating: false,
          statusText: "",
          activeQuestionId: null,
        })),

      setSuggestedQuestions: (qs) => set({ suggestedQuestions: qs }),

      setActiveQuestionId: (id) => set({ activeQuestionId: id }),

      setCurrentMode: (mode) => set({ currentMode: mode }),

      setCurrentScopeLabel: (label) => set({ currentScopeLabel: label }),

      markQuestionCovered: (id) =>
        set((s) => ({
          coveredQuestionIds: s.coveredQuestionIds.includes(id)
            ? s.coveredQuestionIds
            : [...s.coveredQuestionIds, id],
        })),

      clearChat: () =>
        set({
          messages: [],
          statusText: "",
          isGenerating: false,
          activeQuestionId: null,
          coveredQuestionIds: [],
          suggestedQuestions: [],
          currentMode: "paper_understanding",
          currentScopeLabel: "Using this paper",
          editingMessageId: null,
        }),

    }),
    {
      name: "paperpilot-chat",
      storage: createJSONStorage(() => localStorage),
      // Persist per-session messages and the active session ID.
      // messages, coveredQuestionIds, suggestedQuestions are NOT persisted directly —
      // they are restored by switchToSession on startup via restoreActive().
      partialize: (s) => ({
        messages_by_session: buildPersistedSessionMap(
          s.messages_by_session,
          s.activeSessionId,
          s.messages
        ),
        activeSessionId: s.activeSessionId,
        consoleSessionIdByWorkspace: s.consoleSessionIdByWorkspace,
      }),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (!state.messages_by_session) state.messages_by_session = {};
        if (state.activeSessionId === undefined) state.activeSessionId = null;
        if (!state.consoleSessionIdByWorkspace) state.consoleSessionIdByWorkspace = {};

        // Clean up stale streaming messages across all sessions
        for (const sid of Object.keys(state.messages_by_session)) {
          state.messages_by_session[sid] = (state.messages_by_session[sid] ?? [])
            .filter((m) => {
              if (!m.isStreaming) return true;
              return !!(m.streamingText || m.answerJson || m.content);
            })
            .map((m) =>
              m.isStreaming
                ? { ...m, isStreaming: false, isDone: false, isPartial: false }
                : m
            );
        }

        // Restore current session's messages so there's no flash before restoreActive() fires
        if (state.activeSessionId && state.messages_by_session[state.activeSessionId]) {
          state.messages = state.messages_by_session[state.activeSessionId];
        } else {
          state.messages = [];
        }

        state.isGenerating = false;
        state.statusText = "";
      },
    }
  )
);

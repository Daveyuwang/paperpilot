import React, { useRef, useEffect, useState, useCallback } from "react";
import { Send, Loader2, Square, RotateCcw, Pencil } from "lucide-react";
import clsx from "clsx";
import { useWebSocket } from "@/hooks/useWebSocket";
import { api } from "@/api/client";
import { useChatStore } from "@/store/chatStore";
import { usePaperStore } from "@/store/paperStore";
import { useAgendaStore } from "@/store/agendaStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useSourceStore } from "@/store/sourceStore";
import { useDeliverableStore } from "@/store/deliverableStore";
import type {
  AnswerJSON,
  SuggestedQuestion,
  WSMessage,
  Citation,
  EvidenceSignal,
  ModeInfo,
} from "@/types";
import AnswerCard from "./AnswerCard";
import { WelcomePanel } from "./WelcomePanel";
import { MarkdownRenderer } from "./shared/MarkdownRenderer";
import { AgentActivity } from "./shared/AgentActivity";
import { EditableUserMessage } from "./QAPanel/EditableUserMessage";
import { SuggestionsBlock } from "./QAPanel/SuggestionsBlock";
import { ConsoleEmptyState } from "./QAPanel/ConsoleEmptyState";

interface Props {
  onHighlight: (citations: Citation[]) => void;
  onNextQuestion?: (q: { id: string; question: string; stage: string }) => void;
  queuedQuestion?: { id?: string; question: string; nonce: number } | null;
  onQueuedQuestionHandled?: (nonce: number) => void;
  forceConsole?: boolean;
  centered?: boolean;
}

let lastAutoSubmittedQueuedNonce: number | null = null;
const SLOW_STATUS_DELAY_MS = 12000;

function cleanCitationSection(raw: string | null | undefined): string {
  if (!raw) return "";
  const cleaned = raw
    .replace(/^§+\s*/, "")
    .replace(/^\d+(\.\d+)*\.?\s+/, "")
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!cleaned) return "";

  const hasReadableLetters = /[A-Za-z\u00C0-\u024F\u4E00-\u9FFF]/.test(cleaned);
  const digitCount = (cleaned.match(/\d/g) ?? []).length;
  const binaryishCount = (cleaned.match(/[01]/g) ?? []).length;
  const digitRatio = digitCount / cleaned.length;
  const binaryishRatio = binaryishCount / cleaned.length;
  const hasLongDigitRun = /\d{8,}/.test(cleaned);

  if (!hasReadableLetters && digitRatio > 0.45) return "";
  if (binaryishRatio > 0.7 && cleaned.length > 12) return "";
  if (hasLongDigitRun) return "";

  return cleaned;
}

export function QAPanel({
  onHighlight,
  onNextQuestion,
  queuedQuestion = null,
  onQueuedQuestionHandled,
  forceConsole = false,
  centered = false,
}: Props) {
  const { activePaper, activeSession, questions, newSession } = usePaperStore();
  const { getActiveWorkspace } = useWorkspaceStore();
  const {
    messages,
    isGenerating,
    statusText,
    suggestedQuestions,
    startAssistantMessage,
    startSilentAssistantMessage,
    setStreamingText,
    setAnswerJson,
    finalizeMessage,
    failMessage,
    stopGeneration,
    setCitations,
    setSuggestedQuestions,
    setActiveQuestionId,
    addUserMessage,
    setStatus,
    setCurrentMode,
    setCurrentScopeLabel,
    markQuestionCovered,
    getConsoleSessionId,
    setConsoleSessionId,
    switchToSession,
    editingMessageId,
    setEditingMessageId,
    resubmitFrom,
  } = useChatStore();

  const { markDoneByTrailId, resolveUpNext } = useAgendaStore();
  const { getIncludedSources } = useSourceStore();
  const { getActiveDeliverable, getSelectedSectionId } = useDeliverableStore();

  const activeWs = getActiveWorkspace();
  const consoleSessionId = activeWs ? getConsoleSessionId(activeWs.id) : null;
  const effectiveSessionId = forceConsole
    ? consoleSessionId
    : (activeSession?.id ?? null);

  // Switch chatStore messages to the correct session when this panel mounts or session changes
  useEffect(() => {
    if (!effectiveSessionId) return;
    const state = useChatStore.getState();
    const current = state.activeSessionId;
    if (current !== effectiveSessionId) {
      if (state.isGenerating) {
        const unfinished = [...state.messages].reverse().find((message) => message.role === "assistant" && message.isStreaming);
        if (unfinished) stopGeneration(unfinished.id);
        else useChatStore.setState({ isGenerating: false, statusText: "", activeQuestionId: null });
      }
      switchToSession(effectiveSessionId);
    }
  }, [effectiveSessionId, stopGeneration, switchToSession]);

  const wid = activeWs?.id ?? "default";
  const activeDeliverable = getActiveDeliverable(wid);
  const focusedSectionId = activeDeliverable ? getSelectedSectionId(activeDeliverable.id) : null;
  const focusedSection = activeDeliverable?.sections.find((s) => s.id === focusedSectionId);
  const includedSourceCount = getIncludedSources(wid).length;

  const [input, setInput] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showSlowStatusHint, setShowSlowStatusHint] = useState(false);
  const [newChatConfirmOpen, setNewChatConfirmOpen] = useState(false);
  const newChatDialogRef = useRef<HTMLDivElement>(null);
  const newChatPreviousFocusRef = useRef<HTMLElement | null>(null);
  const pendingAssistantId = useRef<string | null>(null);
  const pendingCitationsRef = useRef<Citation[]>([]);
  const streamingTextRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const slowHintTimerRef = useRef<number | null>(null);
  const stuckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const recoveringConsoleSessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!newChatConfirmOpen) return;
    newChatPreviousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      newChatDialogRef.current?.querySelector<HTMLElement>("button")?.focus();
    });
    const handleDialogKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setNewChatConfirmOpen(false);
        return;
      }
      if (event.key !== "Tab" || !newChatDialogRef.current) return;
      const controls = Array.from(newChatDialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
      if (controls.length === 0) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleDialogKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", handleDialogKeyDown);
      newChatPreviousFocusRef.current?.focus();
    };
  }, [newChatConfirmOpen]);

  const resetStuckTimer = useCallback(() => {
    if (stuckTimerRef.current) clearTimeout(stuckTimerRef.current);
    if (!pendingAssistantId.current) return;
    stuckTimerRef.current = setTimeout(() => {
      const id = pendingAssistantId.current;
      if (!id) return;
      const msg = useChatStore.getState().messages.find((m) => m.id === id);
      if (msg && !msg.isDone) {
        if (!msg.answerJson && !msg.streamingText && !msg.content) {
          failMessage(id, "No response received — the connection may have dropped.");
        } else {
          finalizeMessage(id);
        }
        pendingAssistantId.current = null;
        pendingCitationsRef.current = [];
        streamingTextRef.current = "";
      }
    }, 20_000);
  }, [failMessage, finalizeMessage]);

  useEffect(() => {
    return () => {
      if (stuckTimerRef.current) clearTimeout(stuckTimerRef.current);
      const pendingId = pendingAssistantId.current;
      if (pendingId) stopGeneration(pendingId);
      pendingAssistantId.current = null;
      pendingCitationsRef.current = [];
      streamingTextRef.current = "";
    };
  }, [stopGeneration]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleWSMessage = useCallback((msg: WSMessage) => {
    resetStuckTimer();
    switch (msg.type) {
      case "status":
        if (!pendingAssistantId.current) return;
        setStatus(msg.content as string);
        break;

      case "mode_info": {
        if (!pendingAssistantId.current) return;
        const info = msg.content as ModeInfo;
        setCurrentMode(info.answer_mode);
        if (info.scope_label) setCurrentScopeLabel(info.scope_label);
        break;
      }

      case "token": {
        const id = pendingAssistantId.current;
        if (!id) return;
        streamingTextRef.current += msg.content as string;
        setStreamingText(id, streamingTextRef.current);
        break;
      }

      case "evidence_ready":
        break;

      case "answer_json": {
        const id = pendingAssistantId.current;
        if (!id) return;
        setAnswerJson(id, msg.content as AnswerJSON);
        streamingTextRef.current = "";
        setStreamingText(id, "");
        break;
      }

      case "chunk_refs": {
        const id = pendingAssistantId.current;
        if (!id) return;
        const citations = msg.content as Citation[];
        pendingCitationsRef.current = citations;
        setCitations(id, citations);
        break;
      }

      case "answer_done": {
        if (stuckTimerRef.current) { clearTimeout(stuckTimerRef.current); stuckTimerRef.current = null; }
        const id = pendingAssistantId.current;
        if (!id) return;
        const doneMsg = useChatStore.getState().messages.find((m) => m.id === id);
        if (doneMsg && !doneMsg.answerJson && !doneMsg.streamingText && !doneMsg.content) {
          setAnswerJson(id, {
            direct_answer: "",
            key_points: null,
            evidence: [],
            plain_language: null,
            bigger_picture: null,
            uncertainty: null,
          } as AnswerJSON);
        }
        finalizeMessage(id);
        pendingCitationsRef.current = [];
        pendingAssistantId.current = null;
        streamingTextRef.current = "";
        const activeQId = useChatStore.getState().activeQuestionId;
        if (activeQId) {
          markDoneByTrailId(activeQId);
          resolveUpNext();
        }
        setTimeout(() => setShowSuggestions(true), 200);
        break;
      }

      case "next_question": {
        const q = msg.content as { id: string; question: string; stage: string };
        onNextQuestion?.(q);
        const activeId = useChatStore.getState().activeQuestionId;
        if (activeId) markQuestionCovered(activeId);
        break;
      }

      case "suggested_questions":
        setSuggestedQuestions(msg.content as SuggestedQuestion[]);
        break;

      case "error": {
        if (stuckTimerRef.current) { clearTimeout(stuckTimerRef.current); stuckTimerRef.current = null; }
        const id = pendingAssistantId.current;
        const errText = `[Error]\n${String(msg.content ?? "Unknown error")}`;
        if (id) {
          failMessage(id, errText);
          pendingCitationsRef.current = [];
          pendingAssistantId.current = null;
          streamingTextRef.current = "";
        }
        break;
      }
    }
  }, [
    setStatus, setCurrentMode, setCurrentScopeLabel, setStreamingText, setAnswerJson, setCitations,
    setSuggestedQuestions, finalizeMessage, failMessage, markQuestionCovered,
    onHighlight, onNextQuestion, markDoneByTrailId, resolveUpNext,
  ]);

  const { sendMessage, disconnect, reconnect, connectionState, closeInfo } = useWebSocket(effectiveSessionId, handleWSMessage);
  const connectionReady = !!effectiveSessionId && connectionState === "open";

  useEffect(() => {
    if (!forceConsole || closeInfo?.code !== 4404 || !activeWs || !effectiveSessionId) return;
    const recoveryKey = `${activeWs.id}:${effectiveSessionId}:${closeInfo.sequence}`;
    if (recoveringConsoleSessionRef.current === recoveryKey) return;
    recoveringConsoleSessionRef.current = recoveryKey;
    let cancelled = false;

    api.createWorkspaceSession(activeWs.id)
      .then((session) => {
        if (cancelled) return;
        const state = useChatStore.getState();
        if (state.getConsoleSessionId(activeWs.id) !== effectiveSessionId) return;
        state.setConsoleSessionId(activeWs.id, session.id);
        state.switchToSession(session.id, []);
      })
      .catch((error) => {
        if (!cancelled) console.warn("[PaperPilot] console session recovery failed", error);
      })
      .finally(() => {
        if (recoveringConsoleSessionRef.current === recoveryKey) {
          recoveringConsoleSessionRef.current = null;
        }
      });

    return () => { cancelled = true; };
  }, [activeWs, closeInfo, effectiveSessionId, forceConsole, setConsoleSessionId, switchToSession]);

  useEffect(() => {
    if (!effectiveSessionId) return;
    const state = useChatStore.getState();
    if (state.activeSessionId !== effectiveSessionId || !state.isGenerating || pendingAssistantId.current) return;
    const unfinished = [...state.messages].reverse().find((message) => message.role === "assistant" && message.isStreaming);
    if (unfinished) stopGeneration(unfinished.id);
    else useChatStore.setState({ isGenerating: false, statusText: "", activeQuestionId: null });
  }, [effectiveSessionId, stopGeneration]);

  useEffect(() => {
    if (connectionState !== "closed" && connectionState !== "error") return;
    const id = pendingAssistantId.current;
    if (!id) return;
    if (stuckTimerRef.current) clearTimeout(stuckTimerRef.current);
    failMessage(id, "[Error]\nConnection lost before the answer completed. Retry when the connection is restored.");
    pendingAssistantId.current = null;
    pendingCitationsRef.current = [];
    streamingTextRef.current = "";
  }, [connectionState, failMessage]);

  const submit = useCallback((question: string, questionId?: string, opts?: { skipAddMessage?: boolean }) => {
    if (!question.trim() || !connectionReady || isGenerating || (!forceConsole && activePaper && activePaper.status !== "ready")) return;
    console.debug("[PaperPilot] question_submit", { question: question.slice(0, 80), questionId });
    const recentMessages = useChatStore.getState().messages
      .filter((message) => message.content.trim() || message.streamingText?.trim() || message.answerJson?.direct_answer?.trim())
      .slice(-8)
      .map((message) => ({
        role: message.role,
        content: (message.answerJson?.direct_answer || message.streamingText || message.content).slice(0, 1600),
      }));
    setShowSuggestions(false);
    setActiveQuestionId(questionId ?? null);
    if (!opts?.skipAddMessage) {
      addUserMessage(question);
    }
    const assistantId = startAssistantMessage();
    pendingAssistantId.current = assistantId;
    pendingCitationsRef.current = [];
    streamingTextRef.current = "";
    resetStuckTimer();

    const context = forceConsole && activeWs ? {
      active_paper_id: activePaper?.id ?? null,
      active_paper_session_id: activeSession?.id ?? null,
      active_deliverable_id: activeDeliverable?.id ?? null,
      focused_section_id: focusedSectionId ?? null,
      focused_section: focusedSection ? {
        id: focusedSection.id,
        title: focusedSection.title,
        content: focusedSection.content,
      } : null,
      recent_messages: recentMessages,
      included_sources: getIncludedSources(wid).map(s => ({
        id: s.id,
        title: s.title,
        authors: s.authors ?? [],
        year: s.year ?? null,
        abstract: s.abstract ?? null,
        provider: s.provider ?? "",
        label: s.label ?? "",
      })),
      deliverables: useDeliverableStore.getState().getDeliverables(activeWs.id).map((d) => ({
        id: d.id,
        title: d.title,
        type: d.type,
        sections: d.sections.map((s) => ({ id: s.id, title: s.title, status: s.content.trim() ? "has_content" : "empty" })),
      })),
    } : undefined;

    sendMessage(question, questionId, undefined, context);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }, [
    isGenerating, forceConsole, setActiveQuestionId, addUserMessage, startAssistantMessage,
    sendMessage, activePaper, activeWs, activeDeliverable, focusedSectionId, focusedSection,
    getIncludedSources, wid, effectiveSessionId, activeSession?.id, connectionReady, resetStuckTimer,
  ]);

  const handleStop = useCallback(() => {
    const id = pendingAssistantId.current;
    if (!id) return;
    disconnect();
    reconnect();
    pendingAssistantId.current = null;
    streamingTextRef.current = "";
    stopGeneration(id);
    const msgs = useChatStore.getState().messages;
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (lastUser) {
      setEditingMessageId(lastUser.id);
    }
    if (useChatStore.getState().suggestedQuestions.length > 0) {
      setTimeout(() => setShowSuggestions(true), 300);
    }
  }, [disconnect, reconnect, stopGeneration, setEditingMessageId]);

  const handleOverrideAction = useCallback((actionType: string, originalQuestion: string) => {
    if (isGenerating) return;
    const modeOverride =
      actionType === "expand"     ? "external_expansion"  :
      actionType === "explain"    ? "concept_explanation" :
      actionType === "paper_only" ? "paper_understanding" : null;
    if (!modeOverride) return;

    // Mode override is a backend action — no new user bubble, just a new assistant message
    const assistantId = startSilentAssistantMessage();
    pendingAssistantId.current = assistantId;
    pendingCitationsRef.current = [];
    streamingTextRef.current = "";
    sendMessage(originalQuestion, undefined, modeOverride);
  }, [isGenerating, startSilentAssistantMessage, sendMessage]);

  useEffect(() => {
    const win = window as Window & { __askGuideQuestion?: (q: { id?: string; question: string }) => void };
    win.__askGuideQuestion = (q: { id?: string; question: string }) => {
      submit(q.question, q.id);
    };
    return () => { delete win.__askGuideQuestion; };
  });

  useEffect(() => {
    if (!queuedQuestion || isGenerating || !connectionReady) return;
    if (lastAutoSubmittedQueuedNonce === queuedQuestion.nonce) {
      onQueuedQuestionHandled?.(queuedQuestion.nonce);
      return;
    }
    lastAutoSubmittedQueuedNonce = queuedQuestion.nonce;
    submit(queuedQuestion.question, queuedQuestion.id);
    onQueuedQuestionHandled?.(queuedQuestion.nonce);
  }, [queuedQuestion, isGenerating, connectionReady, onQueuedQuestionHandled, submit]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, statusText]);

  useEffect(() => {
    if (slowHintTimerRef.current != null) {
      window.clearTimeout(slowHintTimerRef.current);
      slowHintTimerRef.current = null;
    }

    setShowSlowStatusHint(false);

    if (!isGenerating || !shouldShowSlowHint(statusText)) {
      return;
    }

    slowHintTimerRef.current = window.setTimeout(() => {
      setShowSlowStatusHint(true);
    }, SLOW_STATUS_DELAY_MS);

    return () => {
      if (slowHintTimerRef.current != null) {
        window.clearTimeout(slowHintTimerRef.current);
        slowHintTimerRef.current = null;
      }
    };
  }, [isGenerating, statusText]);

  const handleTextareaInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const ta = e.target;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  };

  const colClass = centered ? "max-w-[820px] mx-auto w-full" : "";

  return (
    <div className="flex h-full flex-col">
      {/* ── New chat header (paper mode only) ──────────────────────────────── */}
      {messages.length > 0 && activePaper && !forceConsole && (
        <div className="flex-shrink-0 px-4 py-1.5 border-b border-surface-100 flex items-center justify-end">
          <button
            onClick={() => setNewChatConfirmOpen(true)}
            className="text-[11px] text-surface-400 hover:text-surface-600 flex items-center gap-1 transition-colors"
            aria-label="Start a new paper chat"
          >
            <RotateCcw className="w-3 h-3" />
            New chat
          </button>
        </div>
      )}

      {/* ── Scrollable messages ──────────────────────────────────────────── */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-5" role="log" aria-live="polite" aria-busy={isGenerating}>
        <div className={clsx("space-y-5", colClass)}>

        {messages.length === 0 && !forceConsole && activePaper?.status === "ready" && (
          <WelcomePanel
            paper={activePaper}
            questions={questions}
            onAsk={(question, questionId) => submit(question, questionId)}
          />
        )}

        {messages.length === 0 && !forceConsole && activePaper && activePaper.status !== "ready" && (
          <div className="flex items-center justify-center h-full text-surface-500 text-sm text-center">
            <div>
              <p className="font-medium">Preparing paper…</p>
              <p className="text-xs mt-1 text-surface-400">Questions will be available when it’s ready.</p>
            </div>
          </div>
        )}

        {messages.length === 0 && !forceConsole && !activePaper && (
          <div className="flex items-center justify-center h-full text-surface-500 text-sm text-center">
            <div>
              <p className="font-medium">Choose a paper</p>
              <p className="text-xs mt-1 text-surface-400">Select one from the library.</p>
            </div>
          </div>
        )}

        {messages.length === 0 && forceConsole && (
          <ConsoleEmptyState
            onFillInput={(text) => { setInput(text); textareaRef.current?.focus(); }}
            sourceCount={includedSourceCount}
            hasDraft={!!activeDeliverable}
            hasActivePaper={!!activePaper}
          />
        )}

        {messages.map((msg, idx) => {
          const isCurrentlyStreaming = msg.isStreaming && pendingAssistantId.current === msg.id;
          const precedingUserMsg = msg.role === "assistant"
            ? messages.slice(0, idx).reverse().find((m) => m.role === "user")
            : null;
          const originalQuestion = precedingUserMsg?.content ?? "";

          return (
            <div
              key={msg.id}
              className={clsx(
                "flex gap-3",
                msg.role === "user" ? "justify-end" : "justify-start w-full"
              )}
            >
              {/* Assistant avatar dot */}
              {msg.role === "assistant" && (
                <div className="flex-shrink-0 pt-1">
                  <div className="w-7 h-7 rounded-full bg-accent-100 flex items-center justify-center">
                    <span className="text-accent-600 text-xs font-bold">P</span>
                  </div>
                </div>
              )}

              {/* User bubble — with edit support */}
              {msg.role === "user" && (
                editingMessageId === msg.id ? (
                  <EditableUserMessage
                    content={msg.content}
                    onResubmit={(newContent) => {
                      resubmitFrom(msg.id, newContent);
                      submit(newContent, undefined, { skipAddMessage: true });
                    }}
                    onCancel={() => setEditingMessageId(null)}
                  />
                ) : (
                  <div className="group relative">
                    <div className={clsx(
                      "bg-accent-100 rounded-2xl rounded-tr-sm px-4 py-3 text-sm text-accent-700",
                      "max-w-full"
                    )}>
                      {msg.content}
                    </div>
                    {!isGenerating && (
                      <button
                        onClick={() => setEditingMessageId(msg.id)}
                        className="absolute -left-7 top-1/2 -translate-y-1/2 text-surface-400 opacity-100 transition-opacity hover:text-surface-600 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                        title="Edit message"
                        aria-label="Edit message"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                )
              )}

              {/* Assistant message — no bubble wrapper */}
              {msg.role === "assistant" && (() => {
                const hasContent = !!(msg.streamingText || msg.answerJson);

                if (isCurrentlyStreaming && !hasContent) {
                  return (
                    <div className="flex items-center gap-3 py-2.5 flex-1 min-w-0">
                      <AgentActivity statusText={statusText} isActive />
                      <button
                        onClick={handleStop}
                        className="flex items-center gap-1 text-xs text-surface-400 hover:text-red-500 transition-colors ml-auto flex-shrink-0"
                        title="Stop generating"
                      >
                        <Square className="w-3 h-3" />
                        Stop
                      </button>
                    </div>
                  );
                }

                return (
                  <FadeInUp animate={isCurrentlyStreaming && hasContent}>
                    <div className="min-w-0 flex-1 py-2">

                      {isCurrentlyStreaming && !msg.phase1Complete && (
                        <div className="flex items-center justify-between gap-2 mb-3">
                          <AgentActivity statusText={statusText} isActive />
                          <button
                            onClick={handleStop}
                            className="flex items-center gap-1 text-xs text-surface-400 hover:text-red-500 transition-colors flex-shrink-0"
                            title="Stop generating"
                          >
                            <Square className="w-3 h-3" />
                            Stop
                          </button>
                        </div>
                      )}

                      {hasContent && (
                        <AnswerCard
                          answer={
                            msg.answerJson ?? {
                              direct_answer: "",
                              key_points: null,
                              evidence: [],
                              plain_language: null,
                              bigger_picture: null,
                              uncertainty: null,
                            }
                          }
                          streamingText={msg.streamingText || undefined}
                          phase1Complete={msg.phase1Complete}
                          evidenceCount={msg.answerJson?.evidence?.length}
                          showScopeBadge={!!msg.phase1Complete}
                          isConsole={forceConsole}
                          onCitationClick={(page, section) => {
                            console.debug("[PaperPilot] citation_click", { page, section });
                            const citation = msg.citations.find((c) => c.page_number === page);
                            onHighlight(
                              citation
                                ? [citation]
                                : [{ chunk_id: "", section_title: section ?? null, page_number: page, bbox: null }]
                            );
                          }}
                          onOverrideAction={(actionType) =>
                            handleOverrideAction(actionType, originalQuestion)
                          }
                        />
                      )}

                      {!msg.answerJson && !msg.streamingText && msg.content.startsWith("[Error]") && (
                        <div className="mt-2 text-sm text-red-700" role="alert">
                          <p className="font-semibold mb-1">Something went wrong</p>
                          <p className="text-xs">Try again or edit your question.</p>
                          <details className="mt-2 text-xs text-surface-500">
                            <summary className="cursor-pointer">Details</summary>
                            <pre className="mt-1 whitespace-pre-wrap font-mono">{msg.content.replace("[Error]\n", "")}</pre>
                          </details>
                        </div>
                      )}

                      {!msg.answerJson && !msg.isStreaming && !msg.streamingText && msg.content && !msg.content.startsWith("[Error]") && (
                        <MarkdownRenderer content={msg.content} />
                      )}

                      {msg.isPartial && (
                        <p className="mt-2 text-xs font-medium text-surface-500">Stopped</p>
                      )}

                      {!isCurrentlyStreaming && msg.citations.length > 0 && (msg.answerJson?.evidence?.length ?? 0) === 0 && (
                        <div className="mt-3 pt-3 border-t border-surface-200 flex flex-wrap gap-1.5">
                          {msg.citations.map((c, i) => {
                            const sec = cleanCitationSection(c.section_title);
                            return (
                              <button
                                key={i}
                                className="text-xs text-accent-600 bg-accent-50 px-2 py-0.5 rounded hover:bg-accent-100 transition-colors"
                                onClick={() => onHighlight([c])}
                                title="Jump to in PDF"
                              >
                                {sec ? `§${sec}` : "?"}
                                {c.page_number != null && ` · p.${c.page_number}`}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </FadeInUp>
                );
              })()}
            </div>
          );
        })}

        {/* Suggested questions appear after the answer settles. */}
        {showSuggestions && suggestedQuestions.length > 0 && messages.length > 0 && (
          <FadeInSlide>
            <SuggestionsBlock
              suggestions={suggestedQuestions}
              onAsk={(q, id) => submit(q, id)}
            />
          </FadeInSlide>
        )}

        <div className="h-1" />
        </div>
      </div>

      {/* ── Composer ─────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-4 py-3 bg-white border-t border-surface-100">
        <div className={clsx(colClass)}>
          <div className={clsx(
            "flex items-end gap-2 rounded-xl border border-surface-300 bg-white px-3 py-2",
            "focus-within:border-accent-400 focus-within:ring-2 focus-within:ring-accent-200 transition-colors"
          )}>
            <textarea
              ref={textareaRef}
              aria-label={activePaper && !forceConsole ? "Ask this paper" : "Ask your workspace"}
              className={clsx(
                "flex-1 bg-transparent text-sm text-surface-800 resize-none",
                "focus:outline-none placeholder:text-surface-400 leading-snug",
                isGenerating && "opacity-60"
              )}
              style={{ minHeight: "36px", maxHeight: "120px" }}
              rows={1}
              placeholder={!connectionReady ? "Connecting…" : activePaper && !forceConsole ? "Ask this paper…" : "Ask your workspace…"}
              value={input}
              onChange={handleTextareaInput}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(input);
                }
              }}
              disabled={!connectionReady || isGenerating || (!forceConsole && activePaper?.status !== "ready")}
              {...(forceConsole ? { "data-console-input": "" } : {})}
            />
            <button
              className={clsx(
                "flex-shrink-0 rounded-xl transition-all duration-150",
                "w-9 h-9 flex items-center justify-center",
                !input.trim() || !connectionReady || isGenerating
                  ? "text-surface-300"
                  : "bg-accent-600 text-white hover:bg-accent-700 shadow-sm"
              )}
              onClick={() => submit(input)}
              disabled={!input.trim() || !connectionReady || isGenerating}
              aria-label={isGenerating ? "Generating response" : "Send message"}
            >
              {isGenerating
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <Send className="w-3.5 h-3.5" />
              }
            </button>
          </div>
          <div className="mt-1.5 flex items-center gap-3 px-1 text-xs text-surface-400">
            <span>
              {!connectionReady
                ? (connectionState === "closed" || connectionState === "error" ? "Connection unavailable" : "Connecting…")
                : forceConsole
                ? `${includedSourceCount} included ${includedSourceCount === 1 ? "source" : "sources"}`
                : "Scoped to this paper"}
              {forceConsole && focusedSection ? ` · Draft: ${focusedSection.title}` : ""}
            </span>
            {effectiveSessionId && (connectionState === "closed" || connectionState === "error") && (
              <button className="text-xs font-medium text-accent-700 hover:text-accent-800" onClick={reconnect}>
                Retry connection
              </button>
            )}
            <span className="ml-auto hidden sm:inline">Enter to send · Shift + Enter for a new line</span>
          </div>
        </div>
      </div>

      {/* ── New chat confirmation ─────────────────────────────────────────── */}
      {newChatConfirmOpen && (
        <div className="absolute inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/20" onClick={() => setNewChatConfirmOpen(false)} />
          <div ref={newChatDialogRef} className="relative w-[360px] max-w-[calc(100%-24px)] rounded-xl border border-surface-200 bg-white shadow-md" role="alertdialog" aria-modal="true" aria-labelledby="new-chat-title">
            <div className="px-4 py-3 border-b border-surface-100">
              <div id="new-chat-title" className="text-sm font-semibold text-surface-800">Start a new chat?</div>
              <div className="text-xs text-surface-500 mt-1">
                This clears the current conversation for this paper.
              </div>
            </div>
            <div className="px-4 py-2.5 flex items-center justify-end gap-2">
              <button
                className="px-3 py-1.5 rounded-lg text-xs text-surface-600 hover:bg-surface-100 transition-colors"
                onClick={() => setNewChatConfirmOpen(false)}
              >
                Cancel
              </button>
              <button
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent-600 text-white hover:bg-accent-700 transition-colors"
                onClick={async () => {
                  setNewChatConfirmOpen(false);
                  await newSession();
                }}
              >
                Start new chat
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Utilities ──────────────────────────────────────────────────────────────

function shouldShowSlowHint(statusText: string): boolean {
  const s = (statusText ?? "").toLowerCase();
  if (!s) return false;
  if (s.includes("writing") || s.includes("generating") || s.includes("creating response")) {
    return false;
  }
  return true;
}

// ── Fade-up wrapper (Phase B: bubble entrance) ────────────────────────────

function FadeInUp({ children, animate = true }: { children: React.ReactNode; animate?: boolean }) {
  const [visible, setVisible] = React.useState(!animate);
  React.useEffect(() => {
    if (!animate) return;
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, [animate]);
  return (
    <div
      className="transition-all duration-400"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(6px)",
      }}
    >
      {children}
    </div>
  );
}

// ── Fade+slide in wrapper (for suggestions) ───────────────────────────────

function FadeInSlide({ children }: { children: React.ReactNode }) {
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <div
      className="transition-all duration-500"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(8px)",
      }}
    >
      {children}
    </div>
  );
}

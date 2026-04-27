import React, { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { Send, Loader2, Square, RotateCcw, Pencil, BookOpen } from "lucide-react";
import clsx from "clsx";
import { useWebSocket } from "@/hooks/useWebSocket";
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
import { DoneMarker } from "./StatusSteps";
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
  fillInputRef?: React.MutableRefObject<((text: string) => void) | null>;
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
  fillInputRef,
}: Props) {
  const { activePaper, activeSession, questions, newSession } = usePaperStore();
  const { getActiveWorkspace } = useWorkspaceStore();
  const {
    messages,
    isGenerating,
    statusText,
    suggestedQuestions,
    currentMode,
    currentScopeLabel,
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
    switchToSession,
    discardPartial,
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
    : (activeSession?.id ?? consoleSessionId);

  // Switch chatStore messages to the correct session when this panel mounts or session changes
  useEffect(() => {
    if (!effectiveSessionId) return;
    const current = useChatStore.getState().activeSessionId;
    if (current !== effectiveSessionId) {
      switchToSession(effectiveSessionId);
    }
  }, [effectiveSessionId, switchToSession]);

  const wid = activeWs?.id ?? "default";
  const activeDeliverable = getActiveDeliverable(wid);
  const focusedSectionId = activeDeliverable ? getSelectedSectionId(activeDeliverable.id) : null;
  const focusedSection = activeDeliverable?.sections.find((s) => s.id === focusedSectionId);
  const includedSourceCount = getIncludedSources(wid).length;

  const [input, setInput] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showSlowStatusHint, setShowSlowStatusHint] = useState(false);
  const [newChatConfirmOpen, setNewChatConfirmOpen] = useState(false);
  const pendingAssistantId = useRef<string | null>(null);
  const pendingCitationsRef = useRef<Citation[]>([]);
  const streamingTextRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const slowHintTimerRef = useRef<number | null>(null);
  const stuckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const latestCitations = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant" && messages[i].citations.length > 0) {
        return messages[i].citations;
      }
    }
    return [] as Citation[];
  }, [messages]);

  useEffect(() => {
    if (fillInputRef) {
      fillInputRef.current = (text: string) => {
        setInput(text);
        textareaRef.current?.focus();
      };
    }
  }, [fillInputRef]);

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
    };
  }, []);

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
        onHighlight(citations);
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
        setTimeout(() => setShowSuggestions(true), 3800);
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

  const { sendMessage, disconnect, reconnect } = useWebSocket(effectiveSessionId, handleWSMessage);

  const submit = useCallback((question: string, questionId?: string, opts?: { skipAddMessage?: boolean }) => {
    if (!question.trim() || isGenerating) return;
    console.debug("[PaperPilot] question_submit", { question: question.slice(0, 80), questionId });
    setShowSuggestions(false);
    setActiveQuestionId(questionId ?? null);
    if (!opts?.skipAddMessage) {
      addUserMessage(question);
    }
    const assistantId = startAssistantMessage();
    pendingAssistantId.current = assistantId;
    pendingCitationsRef.current = [];
    streamingTextRef.current = "";

    const context = !activePaper && activeWs ? {
      active_paper_id: null,
      active_deliverable_id: activeDeliverable?.id ?? null,
      focused_section_id: focusedSectionId ?? null,
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
  }, [isGenerating, setActiveQuestionId, addUserMessage, startAssistantMessage, sendMessage, activePaper?.id, activeWs]);

  const handleStop = useCallback(() => {
    const id = pendingAssistantId.current;
    if (!id) return;
    disconnect();
    reconnect();
    pendingAssistantId.current = null;
    streamingTextRef.current = "";
    discardPartial(id);
    const msgs = useChatStore.getState().messages;
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (lastUser) {
      setEditingMessageId(lastUser.id);
    }
    if (useChatStore.getState().suggestedQuestions.length > 0) {
      setTimeout(() => setShowSuggestions(true), 300);
    }
  }, [disconnect, reconnect, discardPartial, setEditingMessageId]);

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
    if (!queuedQuestion || isGenerating) return;
    if (lastAutoSubmittedQueuedNonce === queuedQuestion.nonce) {
      onQueuedQuestionHandled?.(queuedQuestion.nonce);
      return;
    }
    lastAutoSubmittedQueuedNonce = queuedQuestion.nonce;
    submit(queuedQuestion.question, queuedQuestion.id);
    onQueuedQuestionHandled?.(queuedQuestion.nonce);
  }, [queuedQuestion, isGenerating, onQueuedQuestionHandled, submit]);

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
    <div className="flex flex-col h-full">
      {/* ── New chat header (paper mode only) ──────────────────────────────── */}
      {messages.length > 0 && activePaper && !forceConsole && (
        <div className="flex-shrink-0 px-4 py-1.5 border-b border-surface-100 flex items-center justify-end">
          <button
            onClick={() => setNewChatConfirmOpen(true)}
            className="text-[11px] text-surface-400 hover:text-surface-600 flex items-center gap-1 transition-colors"
            title="Start a new chat session for this paper"
          >
            <RotateCcw className="w-3 h-3" />
            New chat
          </button>
        </div>
      )}

      {/* ── Scrollable messages ──────────────────────────────────────────── */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-5">
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
              <p className="font-medium">Ask anything about the paper</p>
              <p className="text-xs mt-1 text-surface-400">Or follow the guided question trail</p>
            </div>
          </div>
        )}

        {messages.length === 0 && !forceConsole && !activePaper && (
          <div className="flex items-center justify-center h-full text-surface-500 text-sm text-center">
            <div>
              <p className="font-medium">Paper QA</p>
              <p className="text-xs mt-1 text-surface-400">Select a paper to start asking questions</p>
            </div>
          </div>
        )}

        {messages.length === 0 && forceConsole && (
          <ConsoleEmptyState onFillInput={(text) => { setInput(text); textareaRef.current?.focus(); }} />
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
                        className="absolute -left-7 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-surface-400 hover:text-surface-600"
                        title="Edit message"
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
                    <div className="flex-1 min-w-0 pl-1 border-l-2 border-accent-200 py-2">

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
                          showScopeBadge={!!msg.phase1Complete && !forceConsole}
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
                        <div className="text-red-400 text-sm mt-2">
                          <p className="font-semibold mb-1">Something went wrong</p>
                          <p className="text-xs text-red-300/70 font-mono whitespace-pre-wrap">
                            {msg.content.replace("[Error]\n", "")}
                          </p>
                        </div>
                      )}

                      {!msg.answerJson && !msg.isStreaming && !msg.streamingText && msg.content && !msg.content.startsWith("[Error]") && (
                        <MarkdownRenderer content={msg.content} />
                      )}

                      {!isCurrentlyStreaming && msg.citations.length > 0 && (
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

        {/* Done confirmation — between last answer and suggestions, outside the bubble */}
        {messages.some((m) => m.isDone) && (() => {
          const doneMsg = messages.find((m) => m.isDone);
          return (
            <div className="pl-11">
              <DoneMarker
                mode={doneMsg?.answerJson?.answer_mode ?? currentMode}
                answerJson={doneMsg?.answerJson}
              />
            </div>
          );
        })()}

        {/* Suggested questions — fade+slide in after done confirmation fades */}
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

      {/* ── Citation Bar ─────────────────────────────────────────────────── */}
      {latestCitations.length > 0 && !isGenerating && (
        <div className="flex-shrink-0 px-4 py-2 bg-accent-50/80 border-t border-accent-200/60">
          <div className={clsx(colClass)}>
            <div className="flex items-center gap-2">
              <BookOpen className="w-3.5 h-3.5 text-accent-600 flex-shrink-0" />
              <span className="text-[11px] font-semibold text-accent-700 flex-shrink-0">Sources</span>
              <div className="flex gap-1.5 overflow-x-auto scrollbar-hide">
                {latestCitations.map((c, i) => {
                  const sec = cleanCitationSection(c.section_title);
                  return (
                    <button
                      key={i}
                      className="flex-shrink-0 text-[11px] font-medium text-accent-700 bg-white border border-accent-300 px-2 py-0.5 rounded-md hover:bg-accent-100 hover:border-accent-400 transition-colors shadow-sm"
                      onClick={() => onHighlight([c])}
                      title="Jump to in PDF"
                    >
                      {sec ? `${sec}` : `Ref ${i + 1}`}
                      {c.page_number != null && ` · p.${c.page_number}`}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Composer ─────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-4 py-3 bg-white border-t border-surface-100">
        <div className={clsx(colClass)}>
          <div className={clsx(
            "flex items-end gap-2 rounded-2xl border border-surface-200 bg-surface-50 px-3 py-2",
            "focus-within:border-accent-400 focus-within:ring-1 focus-within:ring-accent-400 transition-all"
          )}>
            <textarea
              ref={textareaRef}
              className={clsx(
                "flex-1 bg-transparent text-sm text-surface-800 resize-none",
                "focus:outline-none placeholder:text-surface-400 leading-snug",
                isGenerating && "opacity-60"
              )}
              style={{ minHeight: "36px", maxHeight: "120px" }}
              rows={1}
              placeholder={activePaper && !forceConsole ? "Ask about this paper..." : "Ask about this workspace..."}
              value={input}
              onChange={handleTextareaInput}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(input);
                }
              }}
              disabled={isGenerating}
              {...(forceConsole ? { "data-console-input": "" } : {})}
            />
            <button
              className={clsx(
                "flex-shrink-0 rounded-xl transition-all duration-150",
                "w-8 h-8 flex items-center justify-center focus:outline-none",
                !input.trim() || isGenerating
                  ? "text-surface-300"
                  : "bg-accent-600 text-white hover:bg-accent-700 shadow-sm"
              )}
              onClick={() => submit(input)}
              disabled={!input.trim() || isGenerating}
            >
              {isGenerating
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <Send className="w-3.5 h-3.5" />
              }
            </button>
          </div>
        </div>
      </div>

      {/* ── New chat confirmation ─────────────────────────────────────────── */}
      {newChatConfirmOpen && (
        <div className="absolute inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/20" onClick={() => setNewChatConfirmOpen(false)} />
          <div className="relative w-[360px] max-w-[calc(100%-24px)] rounded-xl border border-surface-200 bg-white shadow-lg">
            <div className="px-4 py-3 border-b border-surface-100">
              <div className="text-sm font-semibold text-surface-800">Start a new chat?</div>
              <div className="text-xs text-surface-500 mt-1">
                This will create a new session. Your previous chat is saved.
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
                New chat
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
import React, { useRef, useEffect, useState, useCallback } from "react";
import { Send, Loader2, ArrowRight, Square, RotateCcw, MessageSquare, Search, GitCompare, PenTool, Sparkles } from "lucide-react";
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

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleWSMessage = useCallback((msg: WSMessage) => {
    switch (msg.type) {
      case "status":
        setStatus(msg.content as string);
        break;

      case "mode_info": {
        const info = msg.content as ModeInfo;
        setCurrentMode(info.answer_mode);
        if (info.scope_label) setCurrentScopeLabel(info.scope_label);
        break;
      }

      case "token": {
        const id = pendingAssistantId.current;
        if (id) {
          streamingTextRef.current += msg.content as string;
          setStreamingText(id, streamingTextRef.current);
        }
        break;
      }

      case "evidence_ready":
        // no-op: we removed ConfidenceBadge; evidence arrives via answer_json
        break;

      case "answer_json": {
        const id = pendingAssistantId.current;
        if (id) {
          setAnswerJson(id, msg.content as AnswerJSON);
          streamingTextRef.current = "";
          setStreamingText(id, "");
        }
        break;
      }

      case "chunk_refs": {
        const citations = msg.content as Citation[];
        pendingCitationsRef.current = citations;
        const id = pendingAssistantId.current;
        if (id) setCitations(id, citations);
        onHighlight(citations);
        break;
      }

      case "answer_done": {
        const id = pendingAssistantId.current;
        if (id) {
          // If we never received answer_json or streaming text, set a fallback
          const msg = useChatStore.getState().messages.find((m) => m.id === id);
          if (msg && !msg.answerJson && !msg.streamingText && !msg.content) {
            const fallbackJson = (typeof msg === "object" && msg.content === "")
              ? (msg as any) : null;
            if (!fallbackJson?.answerJson) {
              setAnswerJson(id, {
                direct_answer: "",
                key_points: null,
                evidence: [],
                plain_language: null,
                bigger_picture: null,
                uncertainty: null,
              } as AnswerJSON);
            }
          }
          finalizeMessage(id);
          pendingCitationsRef.current = [];
          pendingAssistantId.current = null;
          streamingTextRef.current = "";
          // Mark the active trail question's agenda item as done
          const activeQId = useChatStore.getState().activeQuestionId;
          if (activeQId) {
            markDoneByTrailId(activeQId);
            resolveUpNext();
          }
          // Delay suggestions until done confirmation has faded (3s visible + 600ms fade + buffer)
          setTimeout(() => setShowSuggestions(true), 3800);
        }
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

  const submit = useCallback((question: string, questionId?: string) => {
    if (!question.trim() || isGenerating) return;
    console.debug("[PaperPilot] question_submit", { question: question.slice(0, 80), questionId });
    setShowSuggestions(false);
    setActiveQuestionId(questionId ?? null);
    addUserMessage(question);
    const assistantId = startAssistantMessage();
    pendingAssistantId.current = assistantId;
    pendingCitationsRef.current = [];
    streamingTextRef.current = "";

    const context = !activePaper && activeWs ? {
      active_paper_id: null,
      active_deliverable_id: null,
      focused_section_id: null,
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
    reconnect();  // restore WS so next query isn't stuck
    stopGeneration(id);
    pendingAssistantId.current = null;
    streamingTextRef.current = "";
    // Show any previously available suggestions after stopping
    if (useChatStore.getState().suggestedQuestions.length > 0) {
      setTimeout(() => setShowSuggestions(true), 300);
    }
  }, [disconnect, reconnect, stopGeneration]);

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
    (window as any).__askGuideQuestion = (q: { id?: string; question: string }) => {
      submit(q.question, q.id);
    };
    return () => { delete (window as any).__askGuideQuestion; };
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
          // Find the preceding user message to use as the original question for override actions
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

              {/* User bubble */}
              {msg.role === "user" && (
                <div className="max-w-[80%] bg-accent-100 rounded-2xl rounded-tr-sm px-4 py-3 text-sm text-accent-700">
                  {msg.content}
                </div>
              )}

              {/* Assistant message */}
              {msg.role === "assistant" && (() => {
                const hasContent = !!(msg.streamingText || msg.answerJson);

                // Phase A: waiting for first token — agent activity steps, no bubble
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

                // Phase B: content available — full bubble, animate in on first appearance
                return (
                  <FadeInUp animate={isCurrentlyStreaming && hasContent}>
                    <div className="flex-1 min-w-0 bg-surface-50 border border-surface-200 rounded-2xl rounded-tl-sm px-5 py-4">

                      {/* Minimal in-bubble streaming indicator (before phase1Complete) */}
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

                      {/* AnswerCard — scope badge only after phase1Complete */}
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

                      {/* Error state */}
                      {!msg.answerJson && !msg.streamingText && msg.content.startsWith("[Error]") && (
                        <div className="text-red-400 text-sm mt-2">
                          <p className="font-semibold mb-1">Something went wrong</p>
                          <p className="text-xs text-red-300/70 font-mono whitespace-pre-wrap">
                            {msg.content.replace("[Error]\n", "")}
                          </p>
                        </div>
                      )}

                      {/* Legacy plain-text fallback */}
                      {!msg.answerJson && !msg.isStreaming && !msg.streamingText && msg.content && !msg.content.startsWith("[Error]") && (
                        <MarkdownRenderer content={msg.content} />
                      )}

                      {/* Persistent citation chips */}
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


// ── Activity strip (Phase A: before first token) ──────────────────────────

function getActivityLabel(statusText: string): string {
  const s = (statusText ?? "").toLowerCase();
  if (s.includes("classifying") || s.includes("intent")) return "Thinking…";
  if (s.includes("searching the web"))                    return "Searching the web…";
  if (s.includes("searching academic") || s.includes("academic literature")) return "Searching academic papers…";
  if (s.includes("citation context"))                     return "Checking citations…";
  if (s.includes("analyzing source"))                     return "Analyzing sources…";
  if (s.includes("fetching paper full") || s.includes("reading paper")) return "Reading paper…";
  if (s.includes("coherence"))                            return "Reviewing document…";
  if (s.includes("transition"))                           return "Improving flow…";
  if (s.includes("synthesizing"))                         return "Synthesizing results…";
  if (s.includes("retrieving") || s.includes("passage"))  return "Reading the paper…";
  if (s.includes("searching across") || s.includes("workspace paper")) return "Searching workspace…";
  if (s.includes("background") || s.includes("external")) return "Fetching background…";
  if (s.includes("concept map"))                          return "Loading concepts…";
  if (s.includes("metadata"))                             return "Loading paper info…";
  if (s.includes("writing") || s.includes("generating"))  return "Writing response…";
  if (s.includes("understanding") || s.includes("enriching")) return "Understanding question…";
  if (s.includes("discover") || s.includes("finding"))    return "Discovering sources…";
  if (s.includes("draft"))                                return "Drafting content…";
  return "Thinking…";
}

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

// ── Suggestions block ──────────────────────────────────────────────────────

const STAGE_DOT: Record<string, string> = {
  motivation:  "bg-rose-400",
  approach:    "bg-amber-400",
  experiments: "bg-emerald-400",
  takeaways:   "bg-accent-400",
};

const STAGE_LABEL: Record<string, string> = {
  motivation: "Motivation",
  approach: "Approach",
  experiments: "Experiments",
  takeaways: "Takeaways",
};

function SuggestionsBlock({
  suggestions,
  onAsk,
}: {
  suggestions: SuggestedQuestion[];
  onAsk: (q: string, id: string) => void;
}) {
  const primary = suggestions.find((s) => s.is_primary);
  const secondary = suggestions.filter((s) => !s.is_primary);

  return (
    <div className="pl-11 space-y-2">
      {primary && (
        <button
          className="w-full text-left px-4 py-3 rounded-xl border border-accent-200 bg-accent-50 hover:bg-accent-100 flex items-start gap-3 group transition-colors"
          onClick={() => onAsk(primary.question, primary.id)}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-1">
              <span className={clsx("w-1.5 h-1.5 rounded-full flex-shrink-0", STAGE_DOT[primary.stage] ?? "bg-surface-400")} />
              <span className="text-[10px] font-semibold text-surface-500 uppercase tracking-wider">
                Up next · {STAGE_LABEL[primary.stage] ?? primary.stage}
              </span>
            </div>
            <p className="text-sm text-surface-700 leading-snug">{primary.question}</p>
          </div>
          <ArrowRight className="w-4 h-4 flex-shrink-0 mt-0.5 text-accent-400 group-hover:text-accent-600 transition-colors" />
        </button>
      )}

      {secondary.length > 0 && (
        <div className="space-y-1.5">
          {secondary.map((q) => (
            <button
              key={q.id}
              className="w-full text-left px-3 py-2.5 rounded-lg border border-surface-200 bg-surface-50 hover:bg-surface-100 transition-colors"
              onClick={() => onAsk(q.question, q.id)}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className={clsx("w-1 h-1 rounded-full flex-shrink-0", STAGE_DOT[q.stage] ?? "bg-surface-400")} />
                <span className="text-[10px] text-surface-500 uppercase tracking-wide">
                  {STAGE_LABEL[q.stage] ?? q.stage}
                </span>
              </div>
              <p className="text-xs text-surface-600 leading-snug line-clamp-2">{q.question}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Console empty state ───────────────────────────────────────────────────

const CONSOLE_ACTIONS = [
  { label: "Find recent related work", icon: Search },
  { label: "Compare included sources", icon: GitCompare },
  { label: "Improve current draft", icon: PenTool },
  { label: "Summarize active paper", icon: Sparkles },
];

function ConsoleEmptyState({ onFillInput }: { onFillInput: (text: string) => void }) {
  return (
    <div className="flex items-center justify-center min-h-[300px] py-12">
      <div className="text-center max-w-md">
        <div className="w-10 h-10 rounded-xl bg-surface-100 flex items-center justify-center mx-auto mb-4">
          <MessageSquare className="w-5 h-5 text-surface-400" />
        </div>
        <h3 className="text-sm font-medium text-surface-700">Workspace Console</h3>
        <p className="text-xs text-surface-400 mt-1.5 leading-relaxed">
          Ask about the workspace, compare sources, discover papers, or work on drafts.
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {CONSOLE_ACTIONS.map(({ label, icon: Icon }) => (
            <button
              key={label}
              onClick={() => onFillInput(label)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-surface-600 bg-surface-50 border border-surface-200 rounded-lg hover:bg-surface-100 hover:border-surface-300 transition-colors"
            >
              <Icon className="w-3 h-3 text-surface-400" />
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

import { getGuestId } from "@/store/guestStore";
import {
  DeepResearchProtocolError,
  parseArtifactVersionRef,
  parseDeepResearchEvent,
  parseDeepResearchSnapshot,
  type ArtifactVersionRef,
  type DeepResearchRunEvent,
  type DeepResearchRunSnapshot,
} from "@/types/deepResearch";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

export interface DeepResearchRunRequest {
  input: {
    topic: string;
    focus?: string | null;
    time_horizon?: string;
    output_length?: string;
    use_workspace_sources?: boolean;
    discover_new_sources?: boolean;
    must_include?: string | null;
    must_exclude?: string | null;
    notes?: string | null;
    target_deliverable_id?: string | null;
  };
  workspace_id: string;
  workspace_sources: Array<{
    id: string;
    title: string;
    authors: string[];
    year: number | null;
    abstract: string | null;
    provider: string;
    paper_id: string | null;
    label: string;
  }>;
  existing_sections?: Array<{
    id: string;
    title: string;
    content: string;
    order: number;
    linkedSourceIds: string[];
  }>;
  active_paper_id?: string | null;
  pre_plan?: {
    sub_questions: Array<{
      id: string;
      question: string;
      search_queries: string[];
      priority: number;
      rationale: string;
    }>;
    depth: string;
  } | null;
}

export class DeepResearchHttpError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly retryAfter: string | null = null,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "DeepResearchHttpError";
  }
}

export interface ResearchStreamCallbacks {
  onEvent: (event: DeepResearchRunEvent) => void;
  onProtocolError: (error: DeepResearchProtocolError) => void;
  onCursor?: (eventId: string, seq: number) => void;
}

export interface ResearchStreamOptions extends ResearchStreamCallbacks {
  signal?: AbortSignal;
  expectedRunId?: string;
  lastEventId?: string | null;
}

export interface ResearchEventPage {
  events: DeepResearchRunEvent[];
  next_after_seq: number;
  has_more: boolean;
}

function requestHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set("Accept", "application/json");
  headers.set("X-Guest-Id", getGuestId());
  return headers;
}

async function responseError(response: Response): Promise<DeepResearchHttpError> {
  const raw = await response.text().catch(() => "");
  let detail = raw || response.statusText || "Request failed";
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // Keep the safe plain-text response.
  }
  return new DeepResearchHttpError(response.status, detail.slice(0, 500), response.headers.get("Retry-After"));
}

async function jsonRequest(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: requestHeaders(init?.headers),
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

interface ParsedSSEBlock {
  id: string | null;
  event: string | null;
  data: string;
}

export function parseSSEBlock(block: string): ParsedSSEBlock | null {
  let id: string | null = null;
  let event: string | null = null;
  const data: string[] = [];

  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const separator = rawLine.indexOf(":");
    const field = separator < 0 ? rawLine : rawLine.slice(0, separator);
    let value = separator < 0 ? "" : rawLine.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  return { id, event, data: data.join("\n") };
}

function dispatchBlock(block: string, options: ResearchStreamOptions): boolean {
  const parsedBlock = parseSSEBlock(block);
  if (!parsedBlock || parsedBlock.data === "[DONE]") return false;

  let raw: unknown;
  try {
    raw = JSON.parse(parsedBlock.data);
  } catch {
    options.onProtocolError(new DeepResearchProtocolError(
      "Deep Research stream returned malformed JSON.",
      "invalid_json",
    ));
    return false;
  }

  try {
    const event = parseDeepResearchEvent(raw);
    if (parsedBlock.id && parsedBlock.id !== event.event_id) {
      throw new DeepResearchProtocolError(
        "SSE event ID does not match the event envelope.",
        "event_id_mismatch",
        raw,
      );
    }
    if (options.expectedRunId && event.run_id !== options.expectedRunId) {
      throw new DeepResearchProtocolError(
        "The stream returned an event for a different run.",
        "run_mismatch",
        raw,
      );
    }
    options.onEvent(event);
    options.onCursor?.(event.event_id, event.seq);
    return event.type === "run_finished";
  } catch (error) {
    options.onProtocolError(
      error instanceof DeepResearchProtocolError
        ? error
        : new DeepResearchProtocolError("Invalid Deep Research event.", "invalid_event", raw),
    );
    return false;
  }
}

async function readEventStream(response: Response, options: ResearchStreamOptions): Promise<void> {
  if (!response.ok) throw await responseError(response);
  if (!response.body) {
    throw new DeepResearchProtocolError("Deep Research stream has no response body.", "missing_body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminalEvent = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        sawTerminalEvent = dispatchBlock(block, options) || sawTerminalEvent;
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) sawTerminalEvent = dispatchBlock(buffer, options) || sawTerminalEvent;
  } finally {
    reader.releaseLock();
  }

  if (!sawTerminalEvent && !options.signal?.aborted) {
    throw new DeepResearchProtocolError(
      "Deep Research stream ended before a run_finished event.",
      "unexpected_eof",
    );
  }
}

async function streamRequest(path: string, init: RequestInit, options: ResearchStreamOptions): Promise<void> {
  const headers = requestHeaders(init.headers);
  headers.set("Accept", "text/event-stream");
  if (options.lastEventId) headers.set("Last-Event-ID", options.lastEventId);
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    signal: options.signal,
  });
  await readEventStream(response, options);
}

export const deepResearchApi = {
  /**
   * Starts a new run. The first canonical event must be run_started and expose
   * the durable workflow/thread ID before any work is displayed.
   */
  streamNewRun(request: DeepResearchRunRequest, options: ResearchStreamOptions): Promise<void> {
    return streamRequest(
      "/api/deep-research/run/stream",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
      options,
    );
  },

  async getRun(runId: string, workspaceId: string): Promise<DeepResearchRunSnapshot> {
    const query = new URLSearchParams({ workspace_id: workspaceId });
    return parseDeepResearchSnapshot(
      await jsonRequest(`/api/deep-research/runs/${encodeURIComponent(runId)}?${query.toString()}`),
    );
  },

  async getArtifacts(runId: string, workspaceId: string, snapshot = false): Promise<ArtifactVersionRef[]> {
    const query = new URLSearchParams({ workspace_id: workspaceId, snapshot: String(snapshot) });
    const raw = await jsonRequest(`/api/deep-research/runs/${encodeURIComponent(runId)}/artifacts?${query.toString()}`);
    const values = Array.isArray(raw)
      ? raw
      : typeof raw === "object" && raw !== null && Array.isArray((raw as { artifacts?: unknown }).artifacts)
        ? (raw as { artifacts: unknown[] }).artifacts
        : null;
    if (!values) throw new DeepResearchProtocolError("Invalid artifact list.", "invalid_artifact_list", raw);
    return values.map(parseArtifactVersionRef);
  },

  async getEvents(runId: string, workspaceId: string, afterSeq: number, limit = 200): Promise<ResearchEventPage> {
    const query = new URLSearchParams({ workspace_id: workspaceId, after_seq: String(afterSeq), limit: String(limit) });
    const raw = await jsonRequest(`/api/deep-research/runs/${encodeURIComponent(runId)}/events?${query.toString()}`);
    const record = typeof raw === "object" && raw !== null && !Array.isArray(raw) ? raw as Record<string, unknown> : null;
    const values = Array.isArray(raw) ? raw : record && Array.isArray(record.events) ? record.events : null;
    if (!values) throw new DeepResearchProtocolError("Invalid run event page.", "invalid_event_page", raw);
    const events = values.map(parseDeepResearchEvent);
    if (events.some((event) => event.run_id !== runId)) throw new DeepResearchProtocolError("Run event page contains another run.", "run_mismatch", raw);
    const lastSeq = events.at(-1)?.seq ?? afterSeq;
    return {
      events,
      next_after_seq: typeof record?.next_after_seq === "number" ? record.next_after_seq : lastSeq,
      has_more: typeof record?.has_more === "boolean" ? record.has_more : events.length >= limit,
    };
  },

  resumeRun(runId: string, workspaceId: string, options: ResearchStreamOptions): Promise<void> {
    return streamRequest(
      `/api/deep-research/runs/${encodeURIComponent(runId)}/resume/stream`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceId }),
      },
      { ...options, expectedRunId: runId },
    );
  },
};

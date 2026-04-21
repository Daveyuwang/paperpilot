import type {
  Paper, PaperListItem, Session, GuideQuestion, Chunk, ConceptMap, LLMSettingsOut, LLMSettingsIn,
  DiscoveredSource, Deliverable, DeliverableSection, WorkspaceSource, DeepResearchRunResult,
  ProposalPlanRunResult,
} from "@/types";
import { getGuestId } from "@/store/guestStore";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

async function healthCheck(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    const res = await fetch(`${API_BASE}/api/settings/llm`, {
      signal: ctrl.signal,
      headers: { "X-Guest-Id": getGuestId() },
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Guest-Id": getGuestId(),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  // 204 No Content — nothing to parse
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as unknown as T;
  }
  return res.json() as Promise<T>;
}

export const api = {
  healthCheck,

  // Workspaces
  listWorkspaces(): Promise<{ id: string; title: string; objective: string | null; created_at: string; updated_at: string }[]> {
    return request("/api/workspaces/");
  },

  createWorkspace(title: string, objective?: string): Promise<{ id: string; title: string; objective: string | null; created_at: string; updated_at: string }> {
    return request("/api/workspaces/", { method: "POST", body: JSON.stringify({ title, objective }) });
  },

  updateWorkspace(id: string, data: { title?: string; objective?: string }): Promise<{ id: string; title: string; objective: string | null; created_at: string; updated_at: string }> {
    return request(`/api/workspaces/${id}`, { method: "PUT", body: JSON.stringify(data) });
  },

  deleteWorkspace(id: string): Promise<void> {
    return request(`/api/workspaces/${id}`, { method: "DELETE" });
  },

  // Papers
  async uploadPaper(file: File, workspaceId?: string): Promise<Paper> {
    const form = new FormData();
    form.append("file", file);
    if (workspaceId) form.append("workspace_id", workspaceId);
    const res = await fetch(`${API_BASE}/api/papers/upload`, {
      method: "POST",
      body: form,
      headers: {
        "X-Guest-Id": getGuestId(),
      },
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  },

  listPapers(workspaceId?: string): Promise<PaperListItem[]> {
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request(`/api/papers/${qs}`);
  },

  getPaper(id: string): Promise<Paper> {
    return request(`/api/papers/${id}`);
  },

  deletePaper(id: string): Promise<void> {
    return request(`/api/papers/${id}`, { method: "DELETE" });
  },

  getQuestions(paperId: string): Promise<GuideQuestion[]> {
    return request(`/api/papers/${paperId}/questions`);
  },

  getChunks(paperId: string): Promise<Chunk[]> {
    return request(`/api/papers/${paperId}/chunks`);
  },

  getPdfUrl(paperId: string): string {
    return `${API_BASE}/api/papers/${paperId}/pdf?guest_id=${encodeURIComponent(getGuestId())}`;
  },

  // Sessions
  createSession(paperId: string): Promise<Session> {
    return request(`/api/sessions/${paperId}`, { method: "POST" });
  },

  createWorkspaceSession(workspaceId: string): Promise<Session> {
    return request("/api/sessions/workspace/console", { method: "POST", body: JSON.stringify({ workspace_id: workspaceId }) });
  },

  getSession(sessionId: string): Promise<Session> {
    return request(`/api/sessions/${sessionId}`);
  },

  deleteSession(sessionId: string): Promise<void> {
    return request(`/api/sessions/${sessionId}`, { method: "DELETE" });
  },

  getSessionState(sessionId: string): Promise<Record<string, unknown>> {
    return request(`/api/sessions/${sessionId}/state`);
  },

  // Concepts
  getConceptMap(paperId: string): Promise<ConceptMap> {
    return request(`/api/concepts/${paperId}`);
  },

  regenerateConceptMap(paperId: string): Promise<{ status: string; paper_id: string }> {
    return request(`/api/concepts/${paperId}/regenerate`, { method: "POST" });
  },

  // Settings
  getLLMSettings(): Promise<LLMSettingsOut> {
    return request("/api/settings/llm");
  },

  setLLMSettings(payload: LLMSettingsIn): Promise<LLMSettingsOut> {
    return request("/api/settings/llm", { method: "PUT", body: JSON.stringify(payload) });
  },

  clearLLMSettings(): Promise<LLMSettingsOut> {
    return request("/api/settings/llm", { method: "DELETE" });
  },

  // Sources
  discoverSources(query: string): Promise<{ results: DiscoveredSource[]; query: string }> {
    return request(`/api/sources/discover?q=${encodeURIComponent(query)}`);
  },

  // Drafts
  runDraft(payload: {
    action: string;
    workspace_id: string;
    deliverable_id: string;
    deliverable_type: string;
    deliverable_title: string;
    sections: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
    sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null }[];
    selected_section_id?: string | null;
    revision_instruction?: string | null;
    active_paper_id?: string | null;
  }): Promise<{
    runId: string;
    action: string;
    status: string;
    updates: { sectionId: string; mode: string; generatedContent: string; sourceIdsUsed: string[]; notes?: string }[];
    skippedSectionIds: string[];
    message?: string;
  }> {
    return request("/api/drafts/run", { method: "POST", body: JSON.stringify(payload) });
  },

  // Drafts — SSE streaming
  async runDraftStream(
    payload: {
      action: string;
      workspace_id: string;
      deliverable_id: string;
      deliverable_type: string;
      deliverable_title: string;
      sections: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
      sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null }[];
      selected_section_id?: string | null;
      revision_instruction?: string | null;
      active_paper_id?: string | null;
    },
    onEvent: (event: Record<string, unknown>) => void,
  ): Promise<void> {
    const response = await fetch(`${API_BASE}/api/drafts/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Guest-Id": getGuestId() },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`API ${response.status}: ${text}`);
    }
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const event = JSON.parse(line.slice(6));
            onEvent(event);
          } catch { /* ignore parse errors */ }
        }
      }
    }
  },

  // Deep Research — generate plan
  async generateDRPlan(payload: {
    topic: string;
    workspace_id: string;
    workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
    active_paper_id?: string | null;
  }): Promise<{
    sub_questions: { id: string; question: string; rationale: string; search_queries: string[]; priority: number }[];
    overall_approach: string;
    recommended_depth: string;
    sources_strategy: string;
    focus_note: string | null;
  }> {
    return request("/api/deep-research/generate-plan", { method: "POST", body: JSON.stringify(payload) });
  },

  // Proposal/Plan — generate plan
  async generatePPPlan(payload: {
    mode: string;
    topic: string;
    workspace_id: string;
    workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
    active_paper_id?: string | null;
  }): Promise<{
    outline_sections: { title: string; description: string }[];
    overall_approach: string;
    recommended_depth: string;
    sources_strategy: string;
    focus_note: string | null;
  }> {
    return request("/api/proposal-plan/generate-plan", { method: "POST", body: JSON.stringify(payload) });
  },

  // Deep Research
  runDeepResearch(payload: {
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
    workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
    existing_sections?: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
    active_paper_id?: string | null;
  }): Promise<DeepResearchRunResult> {
    return request("/api/deep-research/run", { method: "POST", body: JSON.stringify(payload) });
  },

  // Deep Research — SSE streaming
  async runDeepResearchStream(
    payload: {
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
      workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
      existing_sections?: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
      active_paper_id?: string | null;
      pre_plan?: {
        sub_questions: { id: string; question: string; search_queries: string[]; priority: number; rationale: string }[];
        depth: string;
      } | null;
    },
    onEvent: (event: Record<string, unknown>) => void,
  ): Promise<void> {
    const response = await fetch(`${API_BASE}/api/deep-research/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Guest-Id": getGuestId() },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`API ${response.status}: ${text}`);
    }
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const event = JSON.parse(line.slice(6));
            onEvent(event);
          } catch { /* ignore parse errors */ }
        }
      }
    }
  },

  // Proposal / Research Plan
  runProposalPlan(payload: {
    input: {
      mode: string;
      topic: string;
      problem_statement?: string | null;
      focus?: string | null;
      target_deliverable_id?: string | null;
      use_workspace_sources?: boolean;
      use_deep_research_context?: boolean;
      deep_research_deliverable_ids?: string[];
      notes?: string | null;
      motivation?: string | null;
      proposed_idea?: string | null;
      evaluation_direction?: string | null;
      constraints?: string | null;
      planning_horizon?: string | null;
      intended_deliverables?: string | null;
      risks?: string | null;
      milestone_notes?: string | null;
    };
    workspace_id: string;
    workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
    existing_sections?: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
    deep_research_context?: { deliverable_id: string; title: string; sections: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[] }[];
    active_paper_id?: string | null;
  }): Promise<ProposalPlanRunResult> {
    return request("/api/proposal-plan/run", { method: "POST", body: JSON.stringify(payload) });
  },

  // Proposal / Research Plan — SSE streaming
  async runProposalPlanStream(
    payload: {
      input: Record<string, unknown>;
      workspace_id: string;
      workspace_sources: { id: string; title: string; authors: string[]; year: number | null; abstract: string | null; provider: string; paper_id: string | null; label: string }[];
      existing_sections?: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[];
      deep_research_context?: { deliverable_id: string; title: string; sections: { id: string; title: string; content: string; order: number; linkedSourceIds: string[] }[] }[];
      active_paper_id?: string | null;
      pre_plan?: {
        outline_sections: string[];
        depth: string;
      } | null;
    },
    onEvent: (event: Record<string, unknown>) => void,
  ): Promise<void> {
    const response = await fetch(`${API_BASE}/api/proposal-plan/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Guest-Id": getGuestId() },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`API ${response.status}: ${text}`);
    }
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const event = JSON.parse(line.slice(6));
            onEvent(event);
          } catch { /* ignore parse errors */ }
        }
      }
    }
  },
};

import type {
  Paper, PaperListItem, Session, GuideQuestion, Chunk, ConceptMap, LLMSettingsOut, LLMSettingsIn
} from "@/types";
import { getGuestId } from "@/store/guestStore";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

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
  // Papers
  async uploadPaper(file: File): Promise<Paper> {
    const form = new FormData();
    form.append("file", file);
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

  listPapers(): Promise<PaperListItem[]> {
    return request("/api/papers/");
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
};

import { getGuestId } from "@/store/guestStore";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

export type SkillAvailability = "available" | "blocked";

export interface SkillMetadata {
  name: string;
  description: string;
  version: string;
  author: string;
  license: string;
  tags: string[];
  category: string;
  dependencies: string[];
  source_path: string;
  content_sha256: string;
  byte_size?: number;
  body_chars?: number;
  reference_count?: number;
  reference_paths?: string[];
  availability: SkillAvailability;
  blocked_reason: string | null;
  loaded: boolean;
}

export interface SkillDetailResponse extends SkillMetadata {
  source_url: string;
  source_revision: string | null;
  catalog_revision: string | null;
}

export interface SkillDiagnostic {
  code: string;
  severity: string;
  path: string;
  message: string;
}

export interface SkillsStatus {
  enabled: boolean;
  state: "disabled" | "empty" | "loading" | "ready" | "stale" | "error" | string;
  source_url: string;
  requested_ref: string;
  revision: string | null;
  catalog_revision: string | null;
  source_status: string | null;
  refreshed_at: number | string | null;
  skill_count: number;
  available_count: number;
  blocked_count: number;
  quarantined_count: number;
  diagnostic_count: number;
  error: string | null;
  cache_scope: "process" | string;
  loaded_count: number;
  loaded_bytes: number;
  loaded_reference_count: number;
  cache_entry_count: number;
  cache_total_bytes: number;
  cache_max_entries: number;
  cache_max_bytes: number;
  cache_hits: number;
  cache_misses: number;
  cache_evictions: number;
  diagnostics?: SkillDiagnostic[];
}

export interface SkillsListResponse extends SkillsStatus {
  skills: SkillMetadata[];
}

export interface SkillPreviewItem extends SkillMetadata {
  rank: number;
  score: number;
  matched_terms: string[];
  match_reason: string;
}

export interface SkillCacheSnapshot {
  loaded_count: number;
  loaded_bytes: number;
  loaded_reference_count: number;
  entry_count: number;
  total_bytes: number;
  max_entries: number;
  max_bytes: number;
  hits: number;
  misses: number;
  evictions: number;
}

export interface SkillPreviewResponse {
  query: string;
  flow: string;
  source_revision: string | null;
  catalog_revision: string | null;
  selected_count: number;
  available_count: number;
  loaded_count: number;
  selected: SkillPreviewItem[];
  cache: SkillCacheSnapshot;
}

export interface SkillPreviewRequest {
  query: string;
  flow: string;
  max_results?: number;
}

async function skillRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Guest-Id": getGuestId(),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `Skills API returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

/**
 * Isolated adapter for the skills metadata surface.
 *
 * Keeping these calls outside the main API client makes it straightforward to
 * evolve the loader contract without coupling it to research-run requests.
 */
export const skillsApi = {
  status(): Promise<SkillsStatus> {
    return skillRequest("/api/skills/status");
  },

  list(): Promise<SkillsListResponse> {
    return skillRequest("/api/skills");
  },

  detail(name: string): Promise<SkillDetailResponse> {
    return skillRequest(`/api/skills/${encodeURIComponent(name)}`);
  },

  preview(payload: SkillPreviewRequest): Promise<SkillPreviewResponse> {
    return skillRequest("/api/skills/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};

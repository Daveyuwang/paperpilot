export type PaperStatus = "pending" | "processing" | "ready" | "error";

export interface Paper {
  id: string;
  filename: string;
  title: string | null;
  abstract: string | null;
  authors: string[] | null;
  section_headers: string[] | null;
  page_count: number | null;
  parse_confidence: number | null;
  used_nougat_fallback: boolean;
  status: PaperStatus;
  error_message: string | null;
  created_at: string;
}

export interface PaperListItem {
  id: string;
  filename: string;
  title: string | null;
  status: PaperStatus;
  created_at: string;
}

export interface Chunk {
  id: string;
  qdrant_id: string | null;
  content: string;
  section_title: string | null;
  page_number: number | null;
  chunk_index: number;
  content_type: "text" | "figure" | "table" | "caption";
  bbox: ChunkBBox | null;
}

export interface ChunkBBox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface GuideQuestion {
  id: string;
  question: string;
  stage: "motivation" | "approach" | "experiments" | "takeaways";
  order_index: number;
  anchor_sections: string[] | null;
}

export interface Session {
  id: string;
  guest_id: string | null;
  paper_id: string | null;
  workspace_id: string | null;
  created_at: string;
  last_active: string;
}

export type LLMProtocol = "openai" | "openai_compatible" | "anthropic" | "gemini";

export interface LLMSettingsOut {
  protocol: LLMProtocol;
  base_url: string | null;
  has_key: boolean;
  model: string;
  language: string;
}

export interface LLMSettingsIn {
  protocol: LLMProtocol;
  base_url: string | null;
  api_key?: string;
  model: string;
  language: string;
}

export type ConceptNodeType =
  | "Problem" | "Method" | "Component" | "Baseline"
  | "Dataset" | "Metric" | "Finding" | "Limitation";

export type ConceptEdgeRelation =
  | "addresses" | "consists_of" | "compared_with" | "evaluated_on"
  | "measured_by" | "leads_to" | "limited_by";

export interface ConceptNode {
  id: string;
  label: string;
  type: ConceptNodeType;
  short_description: string;
  evidence: string[];
  section: string | null;
  page: number | null;
}

export interface ConceptEdge {
  source: string;
  target: string;
  relation: ConceptEdgeRelation;
  evidence: string[];
}

export interface ConceptMap {
  nodes: ConceptNode[];
  edges: ConceptEdge[];
  generated: boolean;
}

// ── Deliverables ──────────────────────────────────────────────────────────

export type DeliverableType = "deep_research" | "proposal" | "research_plan" | "notes";

export interface DeliverableSection {
  id: string;
  title: string;
  content: string;
  order: number;
  linkedSourceIds: string[];
  createdAt: number;
  updatedAt: number;
  lastUpdatedBy?: "user" | "ai";
  lastAIMode?: "draft" | "revise";
  lastSourceIdsUsed?: string[];
}

export interface Deliverable {
  id: string;
  workspaceId: string;
  type: DeliverableType;
  title: string;
  sections: DeliverableSection[];
  createdAt: number;
  updatedAt: number;
}

// ── Sources ───────────────────────────────────────────────────────────────

export type SourceLabel = "core" | "background" | "general";

export interface WorkspaceSource {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string | null;
  url: string | null;
  citation_count: number | null;
  provider: "upload" | "openalex" | "arxiv";
  paper_id: string | null;
  label: SourceLabel;
  added_at: string;
  included: boolean;
}

export interface DiscoveredSource {
  external_id: string;
  provider: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string | null;
  url: string | null;
  citation_count: number | null;
}

// ── Answer JSON ────────────────────────────────────────────────────────────

export interface EvidenceItem {
  type: "explicit" | "inferred";
  passage: string;
  section: string | null;
  page: number | null;
}

export type AnswerMode =
  | "paper_understanding"
  | "concept_explanation"
  | "external_expansion"
  | "expansion"; // backward compat alias

export interface FollowUpAction {
  label: string;
  action_type: "expand" | "explain" | "paper_only" | "custom";
  query?: string;
}

export interface AnswerJSON {
  direct_answer: string;
  key_points: string[] | null;
  evidence: EvidenceItem[];
  plain_language: string | null;
  bigger_picture: string | null;
  uncertainty: string | null;
  // Mode fields
  answer_mode?: AnswerMode | string;
  scope_label?: string;
  can_expand?: boolean;
  paper_context?: string | null;
  follow_up_actions?: FollowUpAction[];
}

export interface SuggestedQuestion {
  id: string;
  question: string;
  stage: string;
  is_primary: boolean;
}

// ── WebSocket Messages ─────────────────────────────────────────────────────

export type WSMessageType =
  | "token"
  | "chunk_refs"
  | "answer_done"
  | "answer_json"
  | "next_question"
  | "suggested_questions"
  | "evidence_ready"
  | "mode_info"
  | "status"
  | "error";

export interface ModeInfo {
  answer_mode: string;
  scope_label: string;
}

export interface EvidenceSignal {
  confidence: number;       // 0–1
  evidence_count: number;
  coverage_gap: string;
}

export interface WSMessage {
  type: WSMessageType;
  content: unknown;
}

// ── Chat ───────────────────────────────────────────────────────────────────

export interface Citation {
  chunk_id: string;
  section_title: string | null;
  page_number: number | null;
  bbox: ChunkBBox | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;           // legacy streaming buffer
  streamingText: string;     // partial direct_answer text while streaming
  answerJson: AnswerJSON | null;
  citations: Citation[];
  timestamp: Date;
  isStreaming?: boolean;
  isDone?: boolean;          // true briefly after finalize, for Done animation
  isPartial?: boolean;       // user stopped generation mid-stream
  phase1Complete?: boolean;  // answerJson arrived; secondary blocks may reveal
}

// ── Deep Research ─────────────────────────────────────────────────────────

export interface ClarificationQuestion {
  field: string;
  question: string;
  suggestion: string | null;
}

export interface DeepResearchSectionUpdate {
  section_index: number;
  title: string;
  mode: string;
  generated_content: string;
  source_ids_used: string[];
  notes: string | null;
}

export interface DeepResearchFollowUp {
  title: string;
  description: string | null;
  category: string | null;
  priority: number;
}

export interface DeepResearchRunResult {
  run_id: string;
  status: string;
  clarification_questions: ClarificationQuestion[];
  generated_title: string | null;
  generated_outline: string[] | null;
  section_updates: DeepResearchSectionUpdate[];
  discovered_sources: DiscoveredSource[];
  saved_source_ids: string[];
  selected_source_ids: string[];
  unresolved_questions: string[];
  follow_up_items: DeepResearchFollowUp[];
  summary: string | null;
  message: string | null;
}

// ── Proposal / Research Plan ─────────────────────────────────────────────

export type ProposalPlanMode = "proposal" | "research_plan";

export interface PPSectionUpdate {
  section_id: string;
  mode: string;
  generated_content: string;
  source_ids_used: string[];
  notes: string | null;
}

export interface PPFollowUp {
  title: string;
  description: string | null;
  category: string | null;
  priority: number;
}

export interface ProposalPlanRunResult {
  run_id: string;
  mode: ProposalPlanMode;
  status: string;
  clarification_questions: ClarificationQuestion[];
  generated_title: string | null;
  generated_outline: string[] | null;
  section_updates: PPSectionUpdate[];
  updated_section_ids: string[];
  skipped_section_ids: string[];
  selected_source_ids: string[];
  deep_research_context_ids: string[];
  unresolved_questions: string[];
  follow_up_items: PPFollowUp[];
  summary: string | null;
  message: string | null;
}

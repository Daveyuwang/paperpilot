import { ApiRequestError, apiRequest } from "@/api/client";
import type {
  EvidenceReference,
  GenerateResearchPlanRequest,
  ImplementationPlan,
  PlanArtifactSection,
  ResearchBrief,
  ResearchDirectorApi,
  ResearchDirectorSnapshot,
  ResearchHandoffBundle,
  ResearchPlanBundle,
  ResearchPlanReview,
  ResearchPlanStatus,
  ReviewResearchPlanRequest,
  ReviseResearchPlanRequest,
} from "@/types/researchDirector";

type BackendPlanStatus = "draft" | "reviewed" | "approved" | "superseded" | "handed_off";

interface BackendEvidenceItem {
  id: string;
  title: string;
  source_uri: string | null;
  source_type: string;
  authors: string[];
  year: number | null;
  excerpt: string | null;
  summary: string | null;
  locator: string | null;
}

interface BackendResearchContract {
  title: string;
  research_question: string;
  objective: string;
  scope_inclusions: string[];
  scope_exclusions: string[];
  constraints: string[];
  assumptions: string[];
  unknowns: string[];
  success_criteria: string[];
  failure_criteria: string[];
  allowed_sources: string[];
  excluded_sources: string[];
  required_deliverables: string[];
  human_decisions_required: string[];
}

interface BackendResearchContractSeed {
  title: string;
  research_question: string;
  objective: string;
  scope_inclusions?: string[];
  constraints: string[];
  success_criteria: string[];
  allowed_sources?: string[];
  excluded_sources?: string[];
  required_deliverables: string[];
  human_decisions_required: string[];
}

interface BackendEvidenceClaim {
  id: string;
  statement: string;
  evidence_item_ids: string[];
  relation: "supports" | "refutes" | "conflicts" | "unknown";
  status: "supported" | "contested" | "unsupported" | "unknown";
  confidence: number;
  limitations: string[];
}

interface BackendResearchGap {
  id: string;
  description: string;
  evidence_claim_ids: string[];
  contrary_claim_ids: string[];
  impact: string;
  testability: string;
  novelty_assessment: string;
  novelty_confidence: number;
  uncertainties: string[];
}

interface BackendResearchHypothesis {
  id: string;
  statement: string;
  rationale: string;
  evidence_claim_ids: string[];
  status: "proposed" | "evidence_backed";
  falsifiable_predictions: string[];
  differentiation_from_prior_work: string;
  strongest_counterargument: string;
  minimum_validation: string[];
  dependencies: string[];
  risks: string[];
}

interface BackendMethod {
  id: string;
  title: string;
  summary: string;
  hypothesis_ids: string[];
  components: string[];
  procedure: string[];
  interfaces_or_boundaries: string[];
  assumptions: string[];
  alternatives_considered: Array<{
    title: string;
    description: string;
    rejection_reason: string;
    reconsider_when: string | null;
  }>;
  selection_rationale: string;
  risks: string[];
}

interface BackendExperiment {
  id: string;
  title: string;
  research_question: string;
  hypothesis_ids: string[];
  method_id: string;
  datasets: Array<{
    name: string;
    purpose: string;
    split_or_sampling: string;
    access_or_license_notes: string | null;
    leakage_checks: string[];
  }>;
  baselines: string[];
  metrics: Array<{
    name: string;
    definition: string;
    direction: string;
    success_threshold: string | null;
  }>;
  controls: string[];
  ablations: string[];
  negative_tests: string[];
  statistical_analysis: string;
  seeds_or_repetitions: string;
  stop_conditions: string[];
  expected_artifacts: string[];
  acceptance_criteria: string[];
  risks: string[];
  execution_status: "awaiting_external_execution";
}

interface BackendWorkPackage {
  id: string;
  title: string;
  objective: string;
  tasks: string[];
  inputs: string[];
  outputs: string[];
  interface_contracts: Array<{
    name: string;
    inputs: string[];
    outputs: string[];
    invariants: string[];
  }>;
  acceptance_criteria: string[];
  dependency_ids: string[];
  owner_role: string;
  effort_estimate: string | null;
  risks: string[];
  execution_status: "planned_for_external_execution";
}

interface BackendImplementationPlan {
  objective: string;
  architecture_or_method_summary: string;
  work_packages: BackendWorkPackage[];
  milestones: Array<{
    name: string;
    work_package_ids: string[];
    exit_criteria: string[];
  }>;
  unresolved_decisions: string[];
  resource_assumptions: string[];
  fallback_strategies: string[];
  handoff: {
    target_roles: string[];
    prerequisites: string[];
    included_artifacts: string[];
    execution_instructions: string[];
    external_result_contract: string[];
    human_approval_required: boolean;
    handoff_status: "not_handed_off" | "handed_off";
  };
  execution_status: "awaiting_external_execution";
}

interface BackendResearchPlan {
  schema_version: "1.0";
  plan_id: string;
  version: number;
  supersedes_plan_id: string | null;
  lifecycle_status: "draft" | "review_required" | "approved_for_handoff" | "handed_off";
  generation_mode: "model" | "deterministic_fallback";
  contract: BackendResearchContract;
  evidence_catalog: BackendEvidenceItem[];
  evidence_claims: BackendEvidenceClaim[];
  gaps: BackendResearchGap[];
  hypotheses: BackendResearchHypothesis[];
  methods: BackendMethod[];
  experiments: BackendExperiment[];
  implementation_plan: BackendImplementationPlan;
  limitations: string[];
  unresolved_questions: string[];
  generation_warnings: string[];
  revision_record: {
    review_id: string;
    source_plan_digest: string | null;
    source_review_digest: string | null;
    addressed_issue_ids: string[];
    unresolved_issue_ids: string[];
    changes: string[];
  } | null;
}

interface BackendReviewReport {
  schema_version: "1.0";
  review_id: string;
  reviewed_plan_id: string;
  reviewed_plan_version: number;
  reviewed_plan_digest: string | null;
  review_state: "independent_review_complete" | "fallback_requires_human_review";
  verdict: "blocked" | "revision_required" | "approvable_for_handoff";
  perspectives_completed: string[];
  issues: Array<{
    id: string;
    perspective: string;
    severity: "blocker" | "major" | "minor";
    artifact_path: string;
    problem: string;
    evidence: string;
    impact: string;
    required_fix: string;
    status: "open" | "addressed" | "accepted_risk";
  }>;
  strengths: string[];
  summary: string;
  required_next_step: string;
}

interface BackendProjectSummary {
  id: string;
  workspace_id: string;
  title: string;
  objective: string | null;
  brief_snapshot?: ResearchBrief | null;
  status: BackendPlanStatus;
  latest_version_number: number | null;
  created_at: string;
  updated_at: string;
}

interface BackendPlanVersion {
  id: string;
  version_number: number;
  status: BackendPlanStatus;
  content: BackendResearchPlan;
  created_at: string;
  updated_at: string;
}

interface BackendReviewRecord {
  id: string;
  plan_version_id: string;
  review_round: number;
  status: string;
  review: BackendReviewReport;
  created_at: string;
}

interface BackendHandoffContent {
  [key: string]: unknown;
  bundle_id: string;
  project_id: string;
  plan_version_id: string;
  plan_version_number: number;
  status: "ready_for_handoff" | "handed_off";
  execution_status: "awaiting_external_execution";
  research_contract: BackendResearchContract;
  plan_snapshot: BackendResearchPlan;
  implementation_plan: BackendImplementationPlan;
  independent_review: BackendReviewReport;
  boundary: string;
}

interface BackendHandoffRecord {
  id: string;
  plan_version_id: string;
  version_number: number;
  status: string;
  content: BackendHandoffContent;
  created_at: string;
}

interface BackendProjectDetail {
  project: BackendProjectSummary;
  plan_versions: BackendPlanVersion[];
  reviews: BackendReviewRecord[];
  handoff_bundles: BackendHandoffRecord[];
  operation?: {
    kind: string;
    plan_version_id: string | null;
    review_id: string | null;
    handoff_bundle_id: string | null;
  } | null;
}

interface SnapshotTarget {
  planVersionId?: string;
  versionNumber?: number;
  exact?: boolean;
}

const IDEMPOTENCY_HEADER = "Idempotency-Key";
const PENDING_KEY_STORAGE = "paperpilot:research-director:idempotency:v1";
const PENDING_KEY_LOCK_NAME = "paperpilot:research-director:idempotency-lock:v1";
const PENDING_KEY_LOCK_DB = "paperpilot-research-director-locks";
const PENDING_KEY_LOCK_STORE = "locks";
const PENDING_KEY_TTL_MS = 30 * 24 * 60 * 60 * 1_000;
const MAX_PENDING_KEYS = 64;
const MAX_TRANSPORT_RETRIES = 1;
const MAX_TOO_EARLY_RETRIES = 30;
const MAX_RETRY_AFTER_MS = 5_000;

interface PendingMutationKey {
  key: string;
  lastUsedAt: number;
}

interface BrowserLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T> | T,
  ): Promise<T>;
}

function pendingKeyStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function restorePendingMutationKeys(storage: Storage): Map<string, PendingMutationKey> {
  const restored = new Map<string, PendingMutationKey>();
  try {
    const parsed: unknown = JSON.parse(storage.getItem(PENDING_KEY_STORAGE) ?? "[]");
    if (!Array.isArray(parsed)) return restored;
    for (const item of parsed.slice(-MAX_PENDING_KEYS)) {
      if (
        Array.isArray(item)
        && item.length === 2
        && typeof item[0] === "string"
        && item[0].length <= 128
        && typeof item[1] === "object"
        && item[1] !== null
      ) {
        const entry = item[1] as Partial<PendingMutationKey>;
        if (
          typeof entry.key === "string"
          && entry.key.length > 0
          && entry.key.length <= 255
          && typeof entry.lastUsedAt === "number"
          && Number.isFinite(entry.lastUsedAt)
        ) {
          restored.set(item[0], { key: entry.key, lastUsedAt: entry.lastUsedAt });
        }
      }
    }
  } catch {
    try {
      storage.removeItem(PENDING_KEY_STORAGE);
    } catch {
      // The caller will fail closed if storage cannot be rewritten.
    }
  }
  return restored;
}

function persistPendingMutationKeys(storage: Storage, keys: Map<string, PendingMutationKey>): void {
  try {
    storage.setItem(PENDING_KEY_STORAGE, JSON.stringify([...keys.entries()]));
  } catch {
    throw new Error("Persistent idempotency storage is unavailable; the request was not sent.");
  }
}

function createIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();

  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  // Idempotency keys are uniqueness tokens, not secrets. This compatibility
  // fallback is only for runtimes without the Web Crypto API.
  return `rd-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isTransportFailure(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  return typeof DOMException !== "undefined"
    && error instanceof DOMException
    && (error.name === "NetworkError" || error.name === "TimeoutError");
}

async function canonicalMutationSignature(path: string, init: RequestInit): Promise<string> {
  const method = (init.method ?? "POST").toUpperCase();
  if (method === "GET" || method === "HEAD") {
    throw new Error("Idempotent mutation helper cannot be used for read requests.");
  }
  if (init.body != null && typeof init.body !== "string") {
    throw new Error("Research Director mutations require a canonical string body.");
  }
  const canonical = `${method}\n${path}\n${init.body ?? ""}`;
  const bytes = new TextEncoder().encode(canonical);
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  }

  // The fallback avoids persisting request bodies. A collision is fail-safe:
  // the backend fingerprint rejects mismatched payloads before any mutation.
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (const byte of bytes) {
    first = Math.imul(first ^ byte, 0x01000193) >>> 0;
    second = Math.imul(second ^ byte, 0x85ebca6b) >>> 0;
  }
  return `fallback-${bytes.length}-${first.toString(16).padStart(8, "0")}${second.toString(16).padStart(8, "0")}`;
}

function prunePendingMutationKeys(keys: Map<string, PendingMutationKey>, now: number): void {
  for (const [signature, entry] of keys) {
    if (now - entry.lastUsedAt > PENDING_KEY_TTL_MS) keys.delete(signature);
  }
}

function browserLockManager(): BrowserLockManager | null {
  if (typeof navigator === "undefined") return null;
  return (navigator as Navigator & { locks?: BrowserLockManager }).locks ?? null;
}

let pendingKeyLockDatabasePromise: Promise<IDBDatabase> | null = null;

function openPendingKeyLockDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("Cross-tab idempotency coordination is unavailable; the request was not sent."));
  }
  if (pendingKeyLockDatabasePromise) return pendingKeyLockDatabasePromise;

  const opening = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(PENDING_KEY_LOCK_DB, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(PENDING_KEY_LOCK_STORE)) {
        request.result.createObjectStore(PENDING_KEY_LOCK_STORE);
      }
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => reject(request.error ?? new Error("Could not open idempotency lock storage."));
    request.onblocked = () => reject(new Error("Idempotency lock storage is blocked by another tab."));
  });
  pendingKeyLockDatabasePromise = opening;
  void opening.catch(() => {
    if (pendingKeyLockDatabasePromise === opening) pendingKeyLockDatabasePromise = null;
  });
  return opening;
}

function idbResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Idempotency lock request failed."));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("Idempotency lock transaction was aborted."));
    transaction.onerror = () => reject(transaction.error ?? new Error("Idempotency lock transaction failed."));
  });
}

async function withIndexedDbPendingKeyLock<T>(
  storage: Storage,
  criticalSection: (storage: Storage) => T,
): Promise<T> {
  const database = await openPendingKeyLockDatabase();
  const transaction = database.transaction(PENDING_KEY_LOCK_STORE, "readwrite");
  const completion = transactionDone(transaction);
  const objectStore = transaction.objectStore(PENDING_KEY_LOCK_STORE);
  await idbResult(objectStore.get(PENDING_KEY_LOCK_NAME));

  let result: T;
  try {
    // This must stay synchronous while the readwrite transaction owns the
    // fallback mutex. localStorage reads and writes satisfy that constraint.
    result = criticalSection(storage);
    objectStore.put(Date.now(), PENDING_KEY_LOCK_NAME);
  } catch (error) {
    try {
      transaction.abort();
    } catch {
      // It may already have completed or aborted.
    }
    await completion.catch(() => undefined);
    throw error;
  }
  await completion;
  return result;
}

async function withPendingKeyStorageLock<T>(criticalSection: (storage: Storage) => T): Promise<T> {
  const storage = pendingKeyStorage();
  if (!storage) {
    throw new Error("Browser storage is unavailable; the mutation cannot be safely deduplicated.");
  }
  const locks = browserLockManager();
  if (locks) {
    // Every signature shares one localStorage record, so the lock must cover
    // the complete record rather than only a single request fingerprint.
    return locks.request(PENDING_KEY_LOCK_NAME, { mode: "exclusive" }, () => criticalSection(storage));
  }
  return withIndexedDbPendingKeyLock(storage, criticalSection);
}

async function pendingKeyFor(signature: string): Promise<string> {
  return withPendingKeyStorageLock((storage) => {
    // Re-read after acquiring the cross-tab lock; a module-level snapshot can
    // be stale and cause two tabs to issue different keys for one operation.
    const keys = restorePendingMutationKeys(storage);
    const now = Date.now();
    prunePendingMutationKeys(keys, now);
    const existing = keys.get(signature);
    if (existing) {
      existing.lastUsedAt = now;
      persistPendingMutationKeys(storage, keys);
      return existing.key;
    }
    if (keys.size >= MAX_PENDING_KEYS) {
      throw new Error(
        "Too many unresolved Research Director mutations; retry after an existing operation completes.",
      );
    }
    const key = createIdempotencyKey();
    keys.set(signature, { key, lastUsedAt: now });
    persistPendingMutationKeys(storage, keys);
    return key;
  });
}

async function touchPendingKey(signature: string, key: string): Promise<void> {
  await withPendingKeyStorageLock((storage) => {
    const keys = restorePendingMutationKeys(storage);
    const entry = keys.get(signature);
    if (entry?.key === key) {
      entry.lastUsedAt = Date.now();
      persistPendingMutationKeys(storage, keys);
    }
  });
}

async function clearPendingKey(signature: string, key: string): Promise<void> {
  await withPendingKeyStorageLock((storage) => {
    const keys = restorePendingMutationKeys(storage);
    if (keys.get(signature)?.key === key) {
      keys.delete(signature);
      persistPendingMutationKeys(storage, keys);
    }
  });
}

function retryAfterMilliseconds(value: string | null, retryNumber: number): number {
  if (value) {
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.min(seconds * 1_000, MAX_RETRY_AFTER_MS);
    }
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) {
      return Math.min(Math.max(0, timestamp - Date.now()), MAX_RETRY_AFTER_MS);
    }
  }
  return Math.min(500 * (2 ** Math.max(0, retryNumber - 1)), MAX_RETRY_AFTER_MS);
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function idempotentMutation<TWire, TResult>(
  path: string,
  init: RequestInit,
  validateAndMap: (response: TWire) => TResult | Promise<TResult>,
): Promise<TResult> {
  const signature = await canonicalMutationSignature(path, init);
  const idempotencyKey = await pendingKeyFor(signature);
  const headers = Object.fromEntries(new Headers(init.headers).entries());
  headers[IDEMPOTENCY_HEADER] = idempotencyKey;
  const mutationInit: RequestInit = { ...init, headers };
  let transportRetries = 0;
  let tooEarlyRetries = 0;

  while (true) {
    let response: TWire;
    try {
      response = await apiRequest<TWire>(path, mutationInit);
    } catch (error) {
      await touchPendingKey(signature, idempotencyKey).catch(() => undefined);
      if (error instanceof ApiRequestError) {
        if (error.status === 425) {
          if (tooEarlyRetries < MAX_TOO_EARLY_RETRIES) {
            tooEarlyRetries += 1;
            await wait(retryAfterMilliseconds(error.retryAfter, tooEarlyRetries));
            continue;
          }
          throw error;
        }
        // Any HTTP failure may hide a committed response at an intermediary.
        // A changed payload hashes to a different slot; an identical retry must
        // keep this key until a validated response proves the result is usable.
        throw error;
      }

      if (isTransportFailure(error)) {
        if (transportRetries < MAX_TRANSPORT_RETRIES) {
          transportRetries += 1;
          continue;
        }
      }
      throw error;
    }

    let result: TResult;
    try {
      // Snapshot selection and operation-specific DTO assertions belong inside
      // the receipt boundary: mapping can fail after the server has committed.
      result = await validateAndMap(response);
    } catch (error) {
      await touchPendingKey(signature, idempotencyKey).catch(() => undefined);
      throw error;
    }

    try {
      await clearPendingKey(signature, idempotencyKey);
    } catch (error) {
      // A validated server response is safe to display. Retaining the key is
      // conservative: a later identical retry replays instead of duplicating.
      console.warn("[ResearchDirector] could not clear a completed idempotency key", error);
    }
    return result;
  }
}

function planStatus(status: BackendPlanStatus): ResearchPlanStatus {
  return status;
}

function backendEvidence(reference: EvidenceReference): BackendEvidenceItem {
  return {
    id: reference.source_id || reference.id,
    title: reference.source_title,
    source_uri: reference.url || null,
    source_type: reference.source_type || "other",
    authors: reference.authors ?? [],
    year: reference.year ?? null,
    excerpt: reference.passage || null,
    summary: null,
    locator: reference.locator || null,
  };
}

function researchBriefText(brief: ResearchBrief): string {
  return [
    `Title: ${brief.title}`,
    `Research question: ${brief.research_question}`,
    `Objective: ${brief.objective}`,
    brief.problem_statement && `Problem statement: ${brief.problem_statement}`,
    brief.intended_contribution && `Intended contribution: ${brief.intended_contribution}`,
    brief.scope && `Scope: ${brief.scope}`,
    `Success criteria:\n${brief.success_criteria.map((item) => `- ${item}`).join("\n")}`,
    brief.constraints.length ? `Constraints:\n${brief.constraints.map((item) => `- ${item}`).join("\n")}` : "",
    `Desired deliverables:\n${brief.desired_deliverables.map((item) => `- ${item}`).join("\n")}`,
    `Source policy: workspace sources ${brief.source_policy.use_workspace_sources ? "enabled" : "disabled"}; external discovery ${brief.source_policy.discover_external_sources ? "enabled" : "disabled"}; primary sources ${brief.source_policy.prefer_primary_sources ? "preferred" : "not required"}; horizon ${brief.source_policy.time_horizon}.`,
    brief.notes && `Notes: ${brief.notes}`,
  ].filter(Boolean).join("\n");
}

function backendContractSeed(brief: ResearchBrief): BackendResearchContractSeed {
  return {
    title: brief.title,
    research_question: brief.research_question,
    objective: brief.objective,
    ...(brief.scope.trim() ? { scope_inclusions: [brief.scope.trim()] } : {}),
    constraints: brief.constraints,
    success_criteria: brief.success_criteria,
    ...(brief.source_policy.must_include.length ? { allowed_sources: brief.source_policy.must_include } : {}),
    ...(brief.source_policy.must_exclude.length ? { excluded_sources: brief.source_policy.must_exclude } : {}),
    required_deliverables: brief.desired_deliverables,
    human_decisions_required: ["Approve the reviewed plan before preparing an external handoff."],
  };
}

function uiBrief(contract: BackendResearchContract): ResearchBrief {
  return {
    title: contract.title,
    research_question: contract.research_question,
    objective: contract.objective,
    problem_statement: "",
    intended_contribution: "",
    scope: contract.scope_inclusions.join("\n"),
    success_criteria: contract.success_criteria,
    constraints: contract.constraints,
    desired_deliverables: contract.required_deliverables,
    source_policy: {
      use_workspace_sources: true,
      discover_external_sources: false,
      prefer_primary_sources: true,
      time_horizon: "broad",
      must_include: contract.allowed_sources,
      must_exclude: contract.excluded_sources,
    },
    notes: "",
  };
}

function evidenceReference(
  item: BackendEvidenceItem,
  relation: EvidenceReference["relationship"],
): EvidenceReference {
  return {
    id: item.id,
    source_id: item.id,
    source_title: item.title,
    source_type: item.source_type,
    authors: item.authors,
    year: item.year,
    passage: item.excerpt || item.summary,
    locator: item.locator,
    url: item.source_uri,
    relationship: relation,
  };
}

function confidenceBand(value: number): "high" | "medium" | "low" {
  if (value >= 0.67) return "high";
  if (value >= 0.34) return "medium";
  return "low";
}

function testabilityBand(value: string): "high" | "medium" | "low" {
  const normalized = value.toLowerCase();
  if (normalized.includes("high")) return "high";
  if (normalized.includes("low")) return "low";
  return "medium";
}

function ownerRole(value: string): "human" | "coding_agent" | "external_team" {
  const normalized = value.toLowerCase();
  if (normalized.includes("human") || normalized.includes("researcher")) return "human";
  if (normalized.includes("code") || normalized.includes("engineer")) return "coding_agent";
  return "external_team";
}

function uiImplementation(plan: BackendImplementationPlan): ImplementationPlan {
  return {
    objective: plan.objective,
    summary: plan.architecture_or_method_summary,
    tasks: plan.work_packages.map((item) => ({
      id: item.id,
      title: item.title,
      objective: item.objective,
      tasks: item.tasks,
      inputs: item.inputs,
      outputs: item.outputs,
      interface_contracts: item.interface_contracts,
      deliverable: item.outputs.join("; "),
      dependencies: item.dependency_ids,
      acceptance_criteria: item.acceptance_criteria,
      risks: item.risks,
      owner_role: item.owner_role,
      effort_estimate: item.effort_estimate,
      suggested_owner: ownerRole(item.owner_role),
      status: "planned",
    })),
    milestones: plan.milestones.map((item, index) => ({
      id: `milestone-${index + 1}`,
      title: item.name,
      task_ids: item.work_package_ids,
      exit_criteria: item.exit_criteria,
    })),
    resource_assumptions: plan.resource_assumptions,
    fallback_strategies: plan.fallback_strategies,
    handoff_instructions: plan.handoff.execution_instructions,
    handoff: {
      target_roles: plan.handoff.target_roles,
      prerequisites: plan.handoff.prerequisites,
      included_artifacts: plan.handoff.included_artifacts,
      execution_instructions: plan.handoff.execution_instructions,
      external_result_contract: plan.handoff.external_result_contract,
      human_approval_required: plan.handoff.human_approval_required,
      status: plan.handoff.handoff_status,
    },
    unresolved_decisions: plan.unresolved_decisions,
    execution_status: "awaiting_external_execution",
  };
}

function artifactSections(plan: BackendResearchPlan): ResearchPlanBundle["artifact_sections"] {
  const evidence = plan.evidence_claims.map((claim) =>
    `[${claim.status}] ${claim.statement}${claim.limitations.length ? `\nLimitations: ${claim.limitations.join("; ")}` : ""}`
  ).join("\n\n");
  const methods = plan.methods.map((method) =>
    `${method.title}\n${method.summary}\nProcedure: ${method.procedure.join("; ")}\nSelection rationale: ${method.selection_rationale}`
  ).join("\n\n");
  const experiments = plan.experiments.map((experiment) =>
    `${experiment.title}\nQuestion: ${experiment.research_question}\nBaselines: ${experiment.baselines.join("; ")}\nMetrics: ${experiment.metrics.map((metric) => metric.name).join("; ")}\nStatistics: ${experiment.statistical_analysis}`
  ).join("\n\n");
  const limitations = [
    ...plan.limitations,
    ...plan.unresolved_questions.map((item) => `Open question: ${item}`),
    ...plan.generation_warnings.map((item) => `Generation warning: ${item}`),
  ].join("\n");

  const sections: PlanArtifactSection[] = [];
  if (evidence) {
    const claimEvidenceLinks = plan.evidence_claims.flatMap((claim) => claim.evidence_item_ids);
    sections.push({
      id: "evidence-synthesis",
      title: "Evidence synthesis",
      content: evidence,
      // Preserve one entry per declared claim-to-source relationship. Repeated
      // source IDs represent separate declared links and must count separately.
      evidence_refs: claimEvidenceLinks,
      status: plan.evidence_claims.length > 0 && plan.evidence_claims.every((claim) => claim.status === "supported")
        ? "evidence_backed"
        : "needs_attention",
    });
  }
  if (methods) {
    sections.push({
      id: "method-design",
      title: "Method design",
      content: methods,
      evidence_refs: [],
      status: "draft",
    });
  }
  if (experiments) {
    sections.push({
      id: "experiment-design",
      title: "Experiment design",
      content: experiments,
      evidence_refs: [],
      status: "draft",
    });
  }
  if (limitations) {
    sections.push({
      id: "limitations",
      title: "Limitations and open questions",
      content: limitations,
      evidence_refs: [],
      status: "needs_attention",
    });
  }
  return sections;
}

function uiReview(
  record: BackendReviewRecord,
  projectId: string,
): ResearchPlanReview {
  const report = record.review;
  return {
    id: record.id,
    research_project_id: projectId,
    research_plan_version_id: record.plan_version_id,
    review_round: record.review_round,
    status: "reviewed",
    verdict: report.verdict === "approvable_for_handoff"
      ? "approve"
      : report.verdict === "revision_required"
        ? "revise"
        : "blocked",
    summary: report.summary,
    perspectives: report.perspectives_completed,
    issues: report.issues.map((issue) => ({
      id: issue.id,
      severity: issue.severity,
      artifact: issue.artifact_path,
      problem: issue.problem,
      evidence: issue.evidence,
      impact: issue.impact,
      required_fix: issue.required_fix,
      status: issue.status,
    })),
    created_at: record.created_at,
    updated_at: record.created_at,
  };
}

function uiPlan(
  detail: BackendProjectDetail,
  version: BackendPlanVersion,
  review: ResearchPlanReview | null,
): ResearchPlanBundle {
  const plan = version.content;
  const submittedBrief = detail.project.brief_snapshot ?? uiBrief(plan.contract);
  const briefSource = detail.project.brief_snapshot
    ? "submitted_snapshot" as const
    : "legacy_contract_fallback" as const;
  const evidenceById = new Map(plan.evidence_catalog.map((item) => [item.id, item]));
  return {
    id: version.id,
    research_project_id: detail.project.id,
    workspace_id: detail.project.workspace_id,
    version_number: version.version_number,
    status: planStatus(version.status),
    execution_status: "awaiting_external_execution",
    research_brief: submittedBrief,
    research_brief_source: briefSource,
    contract: {
      title: plan.contract.title,
      research_question: plan.contract.research_question,
      objective: plan.contract.objective,
      scope_inclusions: plan.contract.scope_inclusions,
      scope_exclusions: plan.contract.scope_exclusions,
      constraints: plan.contract.constraints,
      assumptions: plan.contract.assumptions,
      unknowns: plan.contract.unknowns,
      success_criteria: plan.contract.success_criteria,
      failure_criteria: plan.contract.failure_criteria,
      allowed_sources: plan.contract.allowed_sources,
      excluded_sources: plan.contract.excluded_sources,
      required_deliverables: plan.contract.required_deliverables,
      human_decisions_required: plan.contract.human_decisions_required,
    },
    artifact_sections: artifactSections(plan),
    evidence_catalog: plan.evidence_catalog.map((item) => evidenceReference(item, "context")),
    evidence_claims: plan.evidence_claims.map((claim) => ({
      id: claim.id,
      claim: claim.statement,
      evidence_status: claim.status === "supported" ? "supported" : claim.status === "contested" ? "conflicting" : "insufficient",
      evidence_refs: claim.evidence_item_ids
        .map((id) => evidenceById.get(id))
        .filter((item): item is BackendEvidenceItem => Boolean(item))
        .map((item) => evidenceReference(item, claim.relation)),
      uncertainty: claim.limitations.join("; ") || null,
    })),
    gaps: plan.gaps.map((gap, index) => ({
      id: gap.id,
      title: `Gap ${index + 1}`,
      description: gap.description,
      evidence_refs: [...gap.evidence_claim_ids, ...gap.contrary_claim_ids],
      impact: gap.impact,
      testability: testabilityBand(gap.testability),
      novelty_confidence: confidenceBand(gap.novelty_confidence),
      unresolved_questions: gap.uncertainties,
    })),
    hypotheses: plan.hypotheses.map((hypothesis, index) => ({
      id: hypothesis.id,
      title: `Hypothesis ${index + 1}`,
      statement: hypothesis.statement,
      rationale: hypothesis.rationale,
      falsifiable_predictions: hypothesis.falsifiable_predictions,
      strongest_counterargument: hypothesis.strongest_counterargument,
      minimum_validation: hypothesis.minimum_validation,
      differentiation_from_prior_work: hypothesis.differentiation_from_prior_work,
      risks: hypothesis.risks,
      evidence_refs: hypothesis.evidence_claim_ids,
      dependencies: hypothesis.dependencies,
      status: hypothesis.status,
    })),
    methods: plan.methods.map((method) => ({
      id: method.id,
      title: method.title,
      summary: method.summary,
      addresses_hypothesis_ids: method.hypothesis_ids,
      components: method.components,
      procedure: method.procedure,
      interfaces_or_boundaries: method.interfaces_or_boundaries,
      assumptions: method.assumptions,
      alternatives_considered: method.alternatives_considered.map((alternative) => ({
        title: alternative.title,
        description: alternative.description,
        rejection_reason: alternative.rejection_reason,
        reconsider_when: alternative.reconsider_when,
      })),
      selection_rationale: method.selection_rationale,
      risks: method.risks,
      execution_status: "awaiting_external_execution",
    })),
    experiments: plan.experiments.map((experiment) => ({
      id: experiment.id,
      title: experiment.title,
      purpose: experiment.research_question,
      hypothesis_ids: experiment.hypothesis_ids,
      method_id: experiment.method_id,
      datasets: experiment.datasets.map((item) => ({
        name: item.name,
        purpose: item.purpose,
        split_or_sampling: item.split_or_sampling,
        access_or_license_notes: item.access_or_license_notes,
        leakage_checks: item.leakage_checks,
      })),
      baselines: experiment.baselines,
      metrics: experiment.metrics.map((item) => ({
        name: item.name,
        definition: item.definition,
        direction: item.direction,
        success_threshold: item.success_threshold,
      })),
      controls: experiment.controls,
      ablations: experiment.ablations,
      negative_tests: experiment.negative_tests,
      statistical_plan: experiment.statistical_analysis,
      seeds_or_repetitions: experiment.seeds_or_repetitions,
      stop_conditions: experiment.stop_conditions,
      expected_artifacts: experiment.expected_artifacts,
      acceptance_criteria: experiment.acceptance_criteria,
      risks: experiment.risks,
      execution_status: "awaiting_external_execution",
    })),
    implementation_plan: uiImplementation(plan.implementation_plan),
    generation_warnings: plan.generation_warnings,
    review,
    created_at: version.created_at,
    updated_at: version.updated_at,
  };
}

function snapshot(detail: BackendProjectDetail, target?: SnapshotTarget): ResearchDirectorSnapshot {
  const versions = [...detail.plan_versions].sort((left, right) => left.version_number - right.version_number);
  const operationVersionId = detail.operation?.plan_version_id ?? undefined;
  const version = operationVersionId
    ? versions.find((item) => item.id === operationVersionId)
    : target?.planVersionId
      ? versions.find((item) => item.id === target.planVersionId)
      : target?.versionNumber != null
        ? versions.find((item) => item.version_number === target.versionNumber)
        : versions[versions.length - 1];
  if (!version) {
    const targetDescription = operationVersionId || target?.planVersionId || target?.versionNumber;
    throw new Error(target?.exact
      ? `The server response did not contain the requested plan version (${targetDescription}).`
      : "Research project has no plan version.");
  }
  const versionReviews = detail.reviews
    .filter((item) => item.plan_version_id === version.id)
    .sort((left, right) => left.review_round - right.review_round);
  const operationReviewId = detail.operation?.review_id;
  const reviewRecord = operationReviewId
    ? versionReviews.find((item) => item.id === operationReviewId)
    : versionReviews[versionReviews.length - 1];
  if (operationReviewId && !reviewRecord) {
    throw new Error(`The server response did not contain the requested review (${operationReviewId}).`);
  }
  const review = reviewRecord ? uiReview(reviewRecord, detail.project.id) : null;
  const plan = uiPlan(detail, version, review);
  const versionHandoffs = detail.handoff_bundles
    .filter((item) => item.plan_version_id === version.id);
  const operationHandoffId = detail.operation?.handoff_bundle_id;
  const handoffRecord = operationHandoffId
    ? versionHandoffs.find((item) => item.id === operationHandoffId)
    : versionHandoffs[versionHandoffs.length - 1];
  if (operationHandoffId && !handoffRecord) {
    throw new Error(`The server response did not contain the requested handoff bundle (${operationHandoffId}).`);
  }
  const handoff: ResearchHandoffBundle | null = handoffRecord ? {
    id: handoffRecord.id,
    research_project_id: detail.project.id,
    research_plan_version_id: version.id,
    version_number: version.version_number,
    status: handoffRecord.content.status,
    execution_status: "awaiting_external_execution",
    title: handoffRecord.content.research_contract.title,
    summary: handoffRecord.content.implementation_plan.architecture_or_method_summary,
    implementation_plan: uiImplementation(handoffRecord.content.implementation_plan),
    open_risks: handoffRecord.content.implementation_plan.unresolved_decisions,
    external_instructions: handoffRecord.content.implementation_plan.handoff.execution_instructions,
    server_content: handoffRecord.content,
    created_at: handoffRecord.created_at,
  } : null;

  return {
    project: {
      id: detail.project.id,
      workspace_id: detail.project.workspace_id,
      title: detail.project.title,
      objective: detail.project.objective || plan.research_brief.objective,
      brief_snapshot: plan.research_brief,
      brief_snapshot_source: plan.research_brief_source,
      status: planStatus(detail.project.status),
      created_at: detail.project.created_at,
      updated_at: detail.project.updated_at,
    },
    plan,
    review,
    handoff,
  };
}

function projectPath(plan: ResearchPlanBundle, action: string): string {
  return `/api/research-director/projects/${encodeURIComponent(plan.research_project_id)}/versions/${plan.version_number}/${action}`;
}

export const researchDirectorApi: ResearchDirectorApi = {
  async loadLatestProject(workspaceId) {
    const projects = await apiRequest<BackendProjectSummary[]>(
      `/api/research-director/projects?workspace_id=${encodeURIComponent(workspaceId)}`,
    );
    const latest = projects[0];
    if (!latest) return null;
    return snapshot(await apiRequest<BackendProjectDetail>(
      `/api/research-director/projects/${encodeURIComponent(latest.id)}`,
    ));
  },

  async generatePlan(request) {
    return idempotentMutation<BackendProjectDetail, ResearchPlanBundle>("/api/research-director/projects", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: request.workspace_id,
        title: request.research_brief.title,
        brief_snapshot: request.research_brief,
        plan_request: {
          research_brief: researchBriefText(request.research_brief),
          contract: backendContractSeed(request.research_brief),
          evidence: request.evidence.map(backendEvidence),
          evidence_warnings: request.evidence_warnings ?? [],
          constraints: request.constraints,
          desired_deliverables: request.desired_deliverables,
          notes: request.notes,
        },
      }),
    }, (detail) => snapshot(detail, { versionNumber: 1, exact: true }).plan);
  },

  async reviewPlan(request: ReviewResearchPlanRequest) {
    return idempotentMutation<BackendProjectDetail, ResearchPlanReview>(projectPath(request.plan, "review"), {
      method: "POST",
      body: JSON.stringify({
        evidence: request.evidence.map(backendEvidence),
        perspectives: request.perspectives,
        review_instructions: request.review_instructions || null,
      }),
    }, (detail) => {
      const result = snapshot(detail, { planVersionId: request.plan.id, exact: true }).review;
      if (!result) throw new Error("The server returned no independent review.");
      return result;
    });
  },

  async revisePlan(request: ReviseResearchPlanRequest) {
    return idempotentMutation<BackendProjectDetail, ResearchPlanBundle>(projectPath(request.plan, "revise"), {
      method: "POST",
      body: JSON.stringify({
        review_id: request.review.id,
        evidence: request.evidence.map(backendEvidence),
        evidence_warnings: request.evidence_warnings ?? [],
        revision_instructions: request.revision_instructions || null,
      }),
    }, (detail) => snapshot(detail, { versionNumber: request.plan.version_number + 1, exact: true }).plan);
  },

  async approvePlan(plan) {
    return idempotentMutation<BackendProjectDetail, ResearchPlanBundle>(
      projectPath(plan, "approve"),
      { method: "POST" },
      (detail) => snapshot(detail, { planVersionId: plan.id, exact: true }).plan,
    );
  },

  async prepareHandoff(plan) {
    return idempotentMutation<BackendProjectDetail, ResearchHandoffBundle>(
      projectPath(plan, "prepare-handoff"),
      { method: "POST" },
      (detail) => {
        const result = snapshot(detail, { planVersionId: plan.id, exact: true }).handoff;
        if (!result) throw new Error("The server returned no handoff bundle.");
        return result;
      },
    );
  },

  async confirmHandoff(plan) {
    return idempotentMutation<BackendProjectDetail, ResearchHandoffBundle>(projectPath(plan, "handoff"), {
      method: "POST",
      body: JSON.stringify({ confirm_transfer: true }),
    }, (detail) => {
      const result = snapshot(detail, { planVersionId: plan.id, exact: true }).handoff;
      if (!result || result.status !== "handed_off") throw new Error("The server did not confirm the handoff.");
      return result;
    });
  },
};

export function mapResearchProjectDetailForTest(detail: BackendProjectDetail): ResearchDirectorSnapshot {
  return snapshot(detail);
}

export type { BackendProjectDetail };

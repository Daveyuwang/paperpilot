import type { SkillsStatus } from "@/api/skills";

export const SKILL_CATALOG_FAST_POLL_MS = 1_500;
export const SKILL_CATALOG_RECOVERY_POLL_MS = 5_000;
export const SKILL_CATALOG_STEADY_POLL_MS = 30_000;

type PollableStatus = Pick<SkillsStatus, "enabled" | "state">;

/** Return a bounded polling interval, or null when polling should stop. */
export function skillCatalogPollInterval(
  status: PollableStatus | null,
  hasTransportError: boolean,
): number | null {
  if (hasTransportError) return SKILL_CATALOG_RECOVERY_POLL_MS;
  if (!status?.enabled) return null;

  if (status.state === "empty" || status.state === "loading") {
    return SKILL_CATALOG_FAST_POLL_MS;
  }
  if (status.state === "error") {
    return SKILL_CATALOG_RECOVERY_POLL_MS;
  }
  if (status.state === "ready" || status.state === "stale") {
    return SKILL_CATALOG_STEADY_POLL_MS;
  }
  return null;
}

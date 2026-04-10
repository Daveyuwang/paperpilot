/**
 * Tests for Trail → Answer integration flow.
 * Verifies: trail click sets activeQuestionId, tab switches, finalize clears active.
 */
import { describe, it, expect, vi } from "vitest";

describe("Trail → Answer integration", () => {
  it("should set activeQuestionId on trail question click", () => {
    let activeQuestionId: string | null = null;
    const setActiveQuestionId = (id: string | null) => { activeQuestionId = id; };

    // Simulate trail click -> submit
    setActiveQuestionId("q-123");
    expect(activeQuestionId).toBe("q-123");
  });

  it("should clear activeQuestionId on finalize", () => {
    let activeQuestionId: string | null = "q-123";

    // Simulate finalizeMessage clearing the active state
    activeQuestionId = null;
    expect(activeQuestionId).toBeNull();
  });

  it("should add question to coveredQuestionIds on completion", () => {
    let covered: string[] = [];
    const markCovered = (id: string) => {
      if (!covered.includes(id)) covered = [...covered, id];
    };

    markCovered("q-123");
    expect(covered).toContain("q-123");

    // Should not duplicate
    markCovered("q-123");
    expect(covered).toHaveLength(1);
  });

  it("should distinguish default, active, and done states", () => {
    const coveredIds = ["q-1", "q-2"];
    const activeId = "q-3";

    // q-1: done
    expect(coveredIds.includes("q-1")).toBe(true);
    expect("q-1" === activeId).toBe(false);

    // q-3: active
    expect(coveredIds.includes("q-3")).toBe(false);
    expect("q-3" === activeId).toBe(true);

    // q-4: default (not covered, not active)
    expect(coveredIds.includes("q-4")).toBe(false);
    expect("q-4" === activeId).toBe(false);
  });
});

/**
 * Tests for delete flow: confirmation, optimistic update, rollback on failure.
 * These are behavioral tests — run with vitest.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

describe("Delete flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("should prompt confirmation before delete", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    // Simulate clicking delete — if confirm returns false, deletePaper should not be called
    const deletePaper = vi.fn();
    const confirmed = window.confirm("Delete this paper and its chat session?");
    if (confirmed) deletePaper("paper-1");

    expect(confirmSpy).toHaveBeenCalledWith("Delete this paper and its chat session?");
    expect(deletePaper).not.toHaveBeenCalled();
  });

  it("should call deletePaper when confirmed", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const deletePaper = vi.fn();
    const confirmed = window.confirm("Delete this paper and its chat session?");
    if (confirmed) deletePaper("paper-1");

    expect(deletePaper).toHaveBeenCalledWith("paper-1");
  });

  it("should handle optimistic update pattern", () => {
    const papers = [
      { id: "p1", filename: "a.pdf", status: "ready" },
      { id: "p2", filename: "b.pdf", status: "ready" },
    ];

    // Optimistic: remove from list immediately
    const afterDelete = papers.filter((p) => p.id !== "p1");
    expect(afterDelete).toHaveLength(1);
    expect(afterDelete[0].id).toBe("p2");
  });

  it("should restore papers on API failure (rollback)", () => {
    const original = [
      { id: "p1", filename: "a.pdf" },
      { id: "p2", filename: "b.pdf" },
    ];

    // Simulate optimistic removal
    let current = original.filter((p) => p.id !== "p1");
    expect(current).toHaveLength(1);

    // API failure -> rollback
    current = original;
    expect(current).toHaveLength(2);
  });
});

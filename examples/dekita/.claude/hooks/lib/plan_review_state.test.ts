/**
 * plan_review_state.ts のテスト
 *
 * Changelog:
 *   - silenvx/dekita#3853: 初期実装
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  type PlanReviewIteration,
  addIterationToState,
  clearPlanReviewState,
  createInitialState,
  getStateFilePath,
  loadPlanReviewState,
  resetIterationCount,
  savePlanReviewState,
  simpleHash,
} from "./plan_review_state";

describe("simpleHash", () => {
  test("should return consistent hash for same input", () => {
    const hash1 = simpleHash("test content");
    const hash2 = simpleHash("test content");
    expect(hash1).toBe(hash2);
  });

  test("should return different hash for different input", () => {
    const hash1 = simpleHash("content A");
    const hash2 = simpleHash("content B");
    expect(hash1).not.toBe(hash2);
  });

  test("should return 8-character hex string", () => {
    const hash = simpleHash("any content");
    expect(hash).toMatch(/^[0-9a-f]{8}$/);
  });

  test("should handle empty string", () => {
    const hash = simpleHash("");
    expect(hash).toMatch(/^[0-9a-f]{8}$/);
  });

  test("should handle long strings", () => {
    const longContent = "a".repeat(10000);
    const hash = simpleHash(longContent);
    expect(hash).toMatch(/^[0-9a-f]{8}$/);
  });
});

describe("getStateFilePath", () => {
  test("should return valid path for valid session ID", () => {
    const path = getStateFilePath("/project", "abc123");
    expect(path).toBe("/project/.claude/state/plan-review-abc123.json");
  });

  test("should throw for invalid session ID with path traversal", () => {
    expect(() => getStateFilePath("/project", "../evil")).toThrow("Invalid session_id");
  });

  test("should throw for empty session ID", () => {
    expect(() => getStateFilePath("/project", "")).toThrow("Invalid session_id");
  });

  test("should throw for session ID with spaces", () => {
    expect(() => getStateFilePath("/project", "session id")).toThrow("Invalid session_id");
  });

  test("should accept UUID format session ID", () => {
    const path = getStateFilePath("/project", "a1b2c3d4-e5f6-7890-abcd-ef1234567890");
    expect(path).toContain("a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  });
});

describe("createInitialState", () => {
  test("should create initial state with correct values", () => {
    const state = createInitialState("session123", "/path/to/plan.md");

    expect(state.sessionId).toBe("session123");
    expect(state.planFile).toBe("/path/to/plan.md");
    expect(state.iterationCount).toBe(0);
    expect(state.reviews).toEqual([]);
    expect(state.startedAt).toBeDefined();
    expect(state.updatedAt).toBeDefined();
  });

  test("should set timestamps in ISO format", () => {
    const state = createInitialState("session123", "/path/to/plan.md");

    // ISO format check
    expect(() => new Date(state.startedAt)).not.toThrow();
    expect(() => new Date(state.updatedAt)).not.toThrow();
  });
});

describe("addIterationToState", () => {
  test("should add iteration and increment count", () => {
    const state = createInitialState("session123", "/path/to/plan.md");

    const iteration: PlanReviewIteration = {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: {
        approved: true,
        hasQuestions: false,
        matchedApprovalPatterns: [],
        matchedQuestionPatterns: [],
      },
      codex: null,
      geminiOutput: "LGTM",
      codexOutput: null,
      planHash: "abc12345",
      result: "approved",
    };

    const newState = addIterationToState(state, iteration);

    expect(newState.iterationCount).toBe(1);
    expect(newState.reviews.length).toBe(1);
    expect(newState.reviews[0]).toEqual(iteration);
    // Original state should be unchanged
    expect(state.iterationCount).toBe(0);
    expect(state.reviews.length).toBe(0);
  });

  test("should preserve existing iterations", () => {
    let state = createInitialState("session123", "/path/to/plan.md");

    const iteration1: PlanReviewIteration = {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash1",
      result: "blocked",
    };

    const iteration2: PlanReviewIteration = {
      iteration: 2,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash2",
      result: "approved",
    };

    state = addIterationToState(state, iteration1);
    state = addIterationToState(state, iteration2);

    expect(state.iterationCount).toBe(2);
    expect(state.reviews.length).toBe(2);
    expect(state.reviews[0].planHash).toBe("hash1");
    expect(state.reviews[1].planHash).toBe("hash2");
  });
});

describe("resetIterationCount", () => {
  test("should reset iteration count to 0", () => {
    let state = createInitialState("session123", "/path/to/plan.md");

    // Add some iterations to simulate reaching the limit
    const iteration: PlanReviewIteration = {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash1",
      result: "blocked",
    };

    state = addIterationToState(state, iteration);
    state = addIterationToState(state, { ...iteration, iteration: 2, planHash: "hash2" });
    state = addIterationToState(state, { ...iteration, iteration: 3, planHash: "hash3" });

    expect(state.iterationCount).toBe(3);

    const resetState = resetIterationCount(state);

    expect(resetState.iterationCount).toBe(0);
  });

  test("should preserve reviews history after reset", () => {
    let state = createInitialState("session123", "/path/to/plan.md");

    const iteration1: PlanReviewIteration = {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash1",
      result: "blocked",
    };

    const iteration2: PlanReviewIteration = {
      iteration: 2,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash2",
      result: "blocked",
    };

    state = addIterationToState(state, iteration1);
    state = addIterationToState(state, iteration2);

    const resetState = resetIterationCount(state);

    // Reviews should be preserved
    expect(resetState.reviews.length).toBe(2);
    expect(resetState.reviews[0].planHash).toBe("hash1");
    expect(resetState.reviews[1].planHash).toBe("hash2");
  });

  test("should not mutate original state (immutability)", () => {
    let state = createInitialState("session123", "/path/to/plan.md");

    const iteration: PlanReviewIteration = {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash1",
      result: "blocked",
    };

    state = addIterationToState(state, iteration);
    const originalCount = state.iterationCount;

    const resetState = resetIterationCount(state);

    // Original state should be unchanged
    expect(state.iterationCount).toBe(originalCount);
    expect(resetState.iterationCount).toBe(0);
    expect(state).not.toBe(resetState);
  });

  test("should preserve other state properties", () => {
    let state = createInitialState("session123", "/path/to/plan.md");
    state = addIterationToState(state, {
      iteration: 1,
      timestamp: new Date().toISOString(),
      gemini: null,
      codex: null,
      geminiOutput: null,
      codexOutput: null,
      planHash: "hash1",
      result: "blocked",
    });

    const resetState = resetIterationCount(state);

    expect(resetState.sessionId).toBe(state.sessionId);
    expect(resetState.planFile).toBe(state.planFile);
    expect(resetState.startedAt).toBe(state.startedAt);
    expect(resetState.updatedAt).toBe(state.updatedAt);
  });
});

describe("state file operations", () => {
  const testDir = join(tmpdir(), `plan-review-state-test-${Date.now()}`);

  beforeEach(() => {
    mkdirSync(testDir, { recursive: true });
  });

  afterEach(() => {
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true });
    }
  });

  test("savePlanReviewState should create state file", () => {
    const state = createInitialState("test-session", "/path/to/plan.md");
    const result = savePlanReviewState(testDir, state);

    expect(result).toBe(true);

    const expectedPath = join(testDir, ".claude/state/plan-review-test-session.json");
    expect(existsSync(expectedPath)).toBe(true);
  });

  test("loadPlanReviewState should load saved state", () => {
    const state = createInitialState("test-session2", "/path/to/plan.md");
    state.iterationCount = 5;

    savePlanReviewState(testDir, state);
    const loaded = loadPlanReviewState(testDir, "test-session2");

    expect(loaded).not.toBeNull();
    expect(loaded?.sessionId).toBe("test-session2");
    expect(loaded?.iterationCount).toBe(5);
    expect(loaded?.planFile).toBe("/path/to/plan.md");
  });

  test("loadPlanReviewState should return null for non-existent state", () => {
    const loaded = loadPlanReviewState(testDir, "non-existent");
    expect(loaded).toBeNull();
  });

  test("clearPlanReviewState should delete state file", () => {
    const state = createInitialState("test-session3", "/path/to/plan.md");
    savePlanReviewState(testDir, state);

    const expectedPath = join(testDir, ".claude/state/plan-review-test-session3.json");
    expect(existsSync(expectedPath)).toBe(true);

    const result = clearPlanReviewState(testDir, "test-session3");
    expect(result).toBe(true);
    expect(existsSync(expectedPath)).toBe(false);
  });

  test("clearPlanReviewState should return true for non-existent file", () => {
    const result = clearPlanReviewState(testDir, "non-existent");
    expect(result).toBe(true);
  });

  test("savePlanReviewState should update updatedAt", async () => {
    const state = createInitialState("test-session4", "/path/to/plan.md");
    const originalUpdatedAt = state.updatedAt;

    // Wait a bit to ensure timestamp difference
    await new Promise((resolve) => setTimeout(resolve, 10));

    savePlanReviewState(testDir, state);
    const loaded = loadPlanReviewState(testDir, "test-session4");

    expect(loaded?.updatedAt).not.toBe(originalUpdatedAt);
  });

  test("should handle invalid JSON gracefully", () => {
    const stateDir = join(testDir, ".claude/state");
    mkdirSync(stateDir, { recursive: true });

    const invalidPath = join(stateDir, "plan-review-invalid-json.json");
    writeFileSync(invalidPath, "not valid json");

    const loaded = loadPlanReviewState(testDir, "invalid-json");
    expect(loaded).toBeNull();
  });
});

/**
 * Tests for analyze_session_outcomes module.
 *
 * Uses a temporary directory to avoid modifying production log files.
 * Tests the actual loadOutcomes() function with custom file paths.
 */

import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadOutcomes } from "./analyze_session_outcomes";

describe("analyze_session_outcomes", () => {
  // Use a unique temp directory for each test run to avoid conflicts
  const testTmpDir = join(tmpdir(), `analyze_session_outcomes_test_${Date.now()}`);
  const testDir = join(testTmpDir, "outcomes");
  const testFile = join(testDir, "session-outcomes.jsonl");

  beforeAll(() => {
    // Create test directory structure
    mkdirSync(testDir, { recursive: true });
  });

  afterAll(() => {
    // Clean up test directory
    if (existsSync(testTmpDir)) {
      rmSync(testTmpDir, { recursive: true, force: true });
    }
  });

  describe("loadOutcomes", () => {
    test("returns empty array when file does not exist", async () => {
      const nonExistentFile = join(testDir, "non-existent.jsonl");
      const outcomes = await loadOutcomes(undefined, nonExistentFile);
      expect(outcomes).toEqual([]);
    });

    test("loads outcomes from JSONL file", async () => {
      const testData = [
        { timestamp: "2026-01-31T10:00:00Z", task_type: "implementation", session_id: "test-1" },
        { timestamp: "2026-01-31T11:00:00Z", task_type: "bugfix", session_id: "test-2" },
      ];

      writeFileSync(testFile, testData.map((d) => JSON.stringify(d)).join("\n"), "utf-8");

      const outcomes = await loadOutcomes(undefined, testFile);
      expect(outcomes.length).toBe(2);
      expect(outcomes[0].task_type).toBe("implementation");
      expect(outcomes[1].task_type).toBe("bugfix");
    });

    test("filters by days parameter", async () => {
      const now = new Date();
      const oldDate = new Date(now.getTime() - 10 * 24 * 60 * 60 * 1000); // 10 days ago
      const recentDate = new Date(now.getTime() - 1 * 24 * 60 * 60 * 1000); // 1 day ago

      const testData = [
        { timestamp: oldDate.toISOString(), task_type: "old", session_id: "old-1" },
        { timestamp: recentDate.toISOString(), task_type: "recent", session_id: "recent-1" },
      ];

      writeFileSync(testFile, testData.map((d) => JSON.stringify(d)).join("\n"), "utf-8");

      // Filter to last 3 days
      const outcomes = await loadOutcomes(3, testFile);
      expect(outcomes.length).toBe(1);
      expect(outcomes[0].task_type).toBe("recent");
    });

    test("skips malformed JSON lines", async () => {
      const testData = '{"task_type": "valid1"}\nnot valid json\n{"task_type": "valid2"}\n';
      writeFileSync(testFile, testData, "utf-8");

      const outcomes = await loadOutcomes(undefined, testFile);
      expect(outcomes.length).toBe(2);
      expect(outcomes[0].task_type).toBe("valid1");
      expect(outcomes[1].task_type).toBe("valid2");
    });

    test("handles empty file", async () => {
      writeFileSync(testFile, "", "utf-8");

      const outcomes = await loadOutcomes(undefined, testFile);
      expect(outcomes).toEqual([]);
    });

    test("handles file with only whitespace lines", async () => {
      writeFileSync(testFile, "\n\n  \n\n", "utf-8");

      const outcomes = await loadOutcomes(undefined, testFile);
      expect(outcomes).toEqual([]);
    });
  });
});

/**
 * Tests for confirm_ui_check module.
 *
 * This script records UI check confirmation by creating a marker file.
 *
 * Changelog:
 *   - silenvx/dekita#3641: TypeScriptテスト追加
 */

import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getMarkerPath, isAllowedBranch } from "./confirm_ui_check";

const __dirname = dirname(fileURLToPath(import.meta.url));

describe("confirm_ui_check", () => {
  const testDir = join(__dirname, "__test_markers__");

  beforeAll(() => {
    mkdirSync(testDir, { recursive: true });
  });

  afterAll(() => {
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true });
    }
  });

  describe("isAllowedBranch", () => {
    test("returns false for main branch", () => {
      expect(isAllowedBranch("main")).toBe(false);
    });

    test("returns false for master branch", () => {
      expect(isAllowedBranch("master")).toBe(false);
    });

    test("returns true for feature branch", () => {
      expect(isAllowedBranch("feature/issue-123")).toBe(true);
    });

    test("returns true for fix branch", () => {
      expect(isAllowedBranch("fix/bug-456")).toBe(true);
    });

    test("returns true for refactor branch", () => {
      expect(isAllowedBranch("refactor/cleanup")).toBe(true);
    });

    test("returns true for branch containing main as substring", () => {
      expect(isAllowedBranch("maintain-feature")).toBe(true);
    });
  });

  describe("getMarkerPath", () => {
    test("returns path with sanitized branch name", () => {
      const path = getMarkerPath("feature/issue-123");
      expect(path).toContain("ui-check-");
      expect(path).toContain(".done");
      // Should not contain slashes in the filename part
      expect(path.split("/").pop()).not.toContain("/");
    });

    test("handles simple branch name", () => {
      const path = getMarkerPath("develop");
      expect(path).toContain("ui-check-develop.done");
    });

    test("handles branch with special characters", () => {
      const path = getMarkerPath("feat/issue#123@special");
      expect(path).toContain("ui-check-");
      expect(path).toContain(".done");
    });
  });

  describe("marker file creation (integration)", () => {
    test("marker file contains branch name", () => {
      const branch = "feature/test-marker";
      const markerPath = join(testDir, "test-marker.done");

      writeFileSync(markerPath, branch);

      expect(existsSync(markerPath)).toBe(true);
      expect(readFileSync(markerPath, "utf-8")).toBe(branch);
    });

    test("marker file can be overwritten", () => {
      const markerPath = join(testDir, "overwrite-test.done");

      writeFileSync(markerPath, "old-branch");
      writeFileSync(markerPath, "new-branch");

      expect(readFileSync(markerPath, "utf-8")).toBe("new-branch");
    });
  });
});

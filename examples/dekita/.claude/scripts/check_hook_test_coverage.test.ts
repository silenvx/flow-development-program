/**
 * Tests for check_hook_test_coverage module.
 *
 * Migrated from Python: .claude/scripts/tests/test_check_hook_test_coverage.py
 */

import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import * as fs from "node:fs";
import * as path from "node:path";
import {
  type CheckResult,
  checkHooks,
  getProjectRoot,
  getTestFileForPythonHook,
  getTestFileForTsHook,
  hasTestFilesForPythonHook,
  hasTestFilesForTsHook,
  normalizePath,
} from "./check_hook_test_coverage";

describe("check_hook_test_coverage", () => {
  describe("normalizePath", () => {
    test("returns path unchanged on Unix (forward slashes)", () => {
      // On Unix, sep is "/" so no conversion needed
      const input = "path/to/file.ts";
      const result = normalizePath(input);
      expect(result).toBe("path/to/file.ts");
    });

    test("handles empty string", () => {
      expect(normalizePath("")).toBe("");
    });

    test("handles single component path", () => {
      expect(normalizePath("file.ts")).toBe("file.ts");
    });

    test("handles path with multiple forward slashes", () => {
      const input = "a/b/c/d/e.ts";
      expect(normalizePath(input)).toBe("a/b/c/d/e.ts");
    });
  });

  describe("getProjectRoot", () => {
    test("returns a valid project root path", () => {
      const root = getProjectRoot();
      // Should end with project directory, not .claude/scripts
      const normalizedRoot = path.normalize(root);
      expect(normalizedRoot).not.toContain(path.join(".claude", "scripts"));
      // Should be an absolute path
      expect(path.isAbsolute(root)).toBe(true);
    });
  });

  describe("getTestFileForPythonHook", () => {
    test("generates correct test path for Python hook", () => {
      const hookFile = "/project/.claude/hooks/my_hook.py";
      const testPath = getTestFileForPythonHook(hookFile);

      expect(testPath).toContain("tests/test_my_hook.py");
    });

    test("converts hyphens to underscores", () => {
      const hookFile = "/project/.claude/hooks/my-test-hook.py";
      const testPath = getTestFileForPythonHook(hookFile);

      expect(testPath).toContain("test_my_test_hook.py");
    });
  });

  describe("getTestFileForTsHook", () => {
    test("generates correct test path for TypeScript hook", () => {
      const hookFile = "/project/.claude/hooks/handlers/my_hook.ts";
      const testPath = getTestFileForTsHook(hookFile);

      expect(testPath).toContain("tests/my_hook.test.ts");
    });
  });

  describe("hasTestFilesForPythonHook", () => {
    let existsSyncSpy: ReturnType<typeof spyOn>;
    let readdirSyncSpy: ReturnType<typeof spyOn>;

    beforeEach(() => {
      existsSyncSpy = spyOn(fs, "existsSync");
      readdirSyncSpy = spyOn(fs, "readdirSync");
    });

    afterEach(() => {
      existsSyncSpy.mockRestore();
      readdirSyncSpy.mockRestore();
    });

    test("returns true when exact test file exists", () => {
      existsSyncSpy.mockImplementation((path: string) => {
        if (typeof path === "string" && path.includes("test_my_hook.py")) {
          return true;
        }
        return false;
      });

      const result = hasTestFilesForPythonHook("/project/.claude/hooks/my_hook.py");
      expect(result).toBe(true);
    });

    test("returns true when split test files exist", () => {
      existsSyncSpy.mockImplementation((path: string) => {
        // Exact test file doesn't exist
        if (typeof path === "string" && path.includes("test_my_hook.py")) {
          return false;
        }
        // But tests directory exists
        if (typeof path === "string" && path.includes("tests")) {
          return true;
        }
        return false;
      });
      readdirSyncSpy.mockReturnValue([
        "test_my_hook_basic.py",
        "test_my_hook_advanced.py",
      ] as unknown as fs.Dirent[]);

      const result = hasTestFilesForPythonHook("/project/.claude/hooks/my_hook.py");
      expect(result).toBe(true);
    });

    test("returns false when no test files exist", () => {
      existsSyncSpy.mockReturnValue(false);

      const result = hasTestFilesForPythonHook("/project/.claude/hooks/my_hook.py");
      expect(result).toBe(false);
    });
  });

  describe("hasTestFilesForTsHook", () => {
    let existsSyncSpy: ReturnType<typeof spyOn>;
    let readdirSyncSpy: ReturnType<typeof spyOn>;

    beforeEach(() => {
      existsSyncSpy = spyOn(fs, "existsSync");
      readdirSyncSpy = spyOn(fs, "readdirSync");
    });

    afterEach(() => {
      existsSyncSpy.mockRestore();
      readdirSyncSpy.mockRestore();
    });

    test("returns true when exact test file exists", () => {
      existsSyncSpy.mockImplementation((path: string) => {
        if (typeof path === "string" && path.includes("my_hook.test.ts")) {
          return true;
        }
        return false;
      });

      const result = hasTestFilesForTsHook("/project/.claude/hooks/handlers/my_hook.ts");
      expect(result).toBe(true);
    });

    test("returns true when split test files exist", () => {
      existsSyncSpy.mockImplementation((path: string) => {
        // Exact test file doesn't exist
        if (typeof path === "string" && path.includes("my_hook.test.ts")) {
          return false;
        }
        // But tests directory exists
        if (typeof path === "string" && path.includes("tests")) {
          return true;
        }
        return false;
      });
      readdirSyncSpy.mockReturnValue([
        "my_hook_basic.test.ts",
        "my_hook_advanced.test.ts",
      ] as unknown as fs.Dirent[]);

      const result = hasTestFilesForTsHook("/project/.claude/hooks/handlers/my_hook.ts");
      expect(result).toBe(true);
    });

    test("returns false when no test files exist", () => {
      existsSyncSpy.mockReturnValue(false);

      const result = hasTestFilesForTsHook("/project/.claude/hooks/handlers/my_hook.ts");
      expect(result).toBe(false);
    });
  });

  describe("checkHooks", () => {
    // Use actual project root for testing
    const projectRoot = getProjectRoot();

    test("identifies new hooks without tests", () => {
      const hookFiles = [`${projectRoot}/.claude/hooks/new_hook.py`];
      const changedFiles = [".claude/hooks/new_hook.py"];
      const hasTestFunc = () => false;

      const result: CheckResult = checkHooks(hookFiles, hasTestFunc, changedFiles);

      expect(result.newHooksWithoutTests.length).toBe(1);
      expect(result.existingHooksWithoutTests.length).toBe(0);
    });

    test("identifies existing hooks without tests as warnings", () => {
      const hookFiles = [`${projectRoot}/.claude/hooks/existing_hook.py`];
      const changedFiles: string[] = []; // Not in changed files
      const hasTestFunc = () => false;

      const result: CheckResult = checkHooks(hookFiles, hasTestFunc, changedFiles);

      expect(result.newHooksWithoutTests.length).toBe(0);
      expect(result.existingHooksWithoutTests.length).toBe(1);
    });

    test("treats all hooks as changed when git diff fails", () => {
      const hookFiles = [
        `${projectRoot}/.claude/hooks/hook1.py`,
        `${projectRoot}/.claude/hooks/hook2.py`,
      ];
      const changedFiles = null; // git diff failed
      const hasTestFunc = () => false;

      const result: CheckResult = checkHooks(hookFiles, hasTestFunc, changedFiles);

      // All hooks should be treated as new/changed
      expect(result.newHooksWithoutTests.length).toBe(2);
      expect(result.existingHooksWithoutTests.length).toBe(0);
    });

    test("does not report hooks with tests", () => {
      const hookFiles = [`${projectRoot}/.claude/hooks/tested_hook.py`];
      const changedFiles = [".claude/hooks/tested_hook.py"];
      const hasTestFunc = () => true;

      const result: CheckResult = checkHooks(hookFiles, hasTestFunc, changedFiles);

      expect(result.newHooksWithoutTests.length).toBe(0);
      expect(result.existingHooksWithoutTests.length).toBe(0);
    });
  });
});

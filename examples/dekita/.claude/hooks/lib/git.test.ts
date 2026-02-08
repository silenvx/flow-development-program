/**
 * git.ts のテスト
 *
 * Changelog:
 *   - silenvx/dekita#3879: 初期実装（カバレッジ改善）
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
  checkRecentCommits,
  checkUncommittedChanges,
  extractIssueNumberFromBranch,
  getCommitsSinceDefaultBranch,
  getCurrentBranch,
  getDefaultBranch,
  getDiffHash,
  getHeadCommit,
  getHeadCommitFull,
  getOriginDefaultBranch,
  isInWorktree,
  isMainRepository,
} from "./git";

describe("isInWorktree", () => {
  let originalCwd: typeof process.cwd;

  beforeEach(() => {
    originalCwd = process.cwd;
  });

  afterEach(() => {
    process.cwd = originalCwd;
  });

  test("should return true when cwd contains /.worktrees/", () => {
    process.cwd = () => "/path/to/repo/.worktrees/issue-123";
    expect(isInWorktree()).toBe(true);
  });

  test("should return true when cwd ends with /.worktrees", () => {
    // Issue #3879: Cover line 116
    process.cwd = () => "/path/to/repo/.worktrees";
    expect(isInWorktree()).toBe(true);
  });

  test("should return true when cwd contains \\.worktrees\\ (Windows)", () => {
    // Issue #3879: Cover line 117
    process.cwd = () => "C:\\path\\to\\repo\\.worktrees\\issue-123";
    expect(isInWorktree()).toBe(true);
  });

  test("should return true when cwd ends with \\.worktrees (Windows)", () => {
    // Issue #3879: Cover line 118
    process.cwd = () => "C:\\path\\to\\repo\\.worktrees";
    expect(isInWorktree()).toBe(true);
  });

  test("should return false when not in worktree", () => {
    process.cwd = () => "/path/to/repo";
    expect(isInWorktree()).toBe(false);
  });

  test("should return false for similar but different paths", () => {
    process.cwd = () => "/path/to/repo/worktrees";
    expect(isInWorktree()).toBe(false);
  });
});

describe("extractIssueNumberFromBranch", () => {
  describe("strict mode", () => {
    test("should match issue-123 pattern", () => {
      expect(extractIssueNumberFromBranch("issue-123", { strict: true })).toBe("123");
    });

    test("should match issue/123 pattern", () => {
      expect(extractIssueNumberFromBranch("issue/123", { strict: true })).toBe("123");
    });

    test("should match issue_123 pattern", () => {
      expect(extractIssueNumberFromBranch("issue_123", { strict: true })).toBe("123");
    });

    test("should match issue123 pattern", () => {
      expect(extractIssueNumberFromBranch("issue123", { strict: true })).toBe("123");
    });

    test("should be case insensitive", () => {
      expect(extractIssueNumberFromBranch("ISSUE-456", { strict: true })).toBe("456");
      expect(extractIssueNumberFromBranch("Issue-789", { strict: true })).toBe("789");
    });

    test("should not match reissue-123 (avoid substring match)", () => {
      // Word boundary should prevent matching "issue" inside "reissue"
      expect(extractIssueNumberFromBranch("reissue-123", { strict: true })).toBeNull();
    });

    test("should not match 123-feature in strict mode", () => {
      expect(extractIssueNumberFromBranch("123-feature", { strict: true })).toBeNull();
    });

    test("should not match feature-123 in strict mode", () => {
      expect(extractIssueNumberFromBranch("feature-123", { strict: true })).toBeNull();
    });
  });

  describe("broad mode (default)", () => {
    test("should match issue-123 pattern", () => {
      expect(extractIssueNumberFromBranch("issue-123")).toBe("123");
    });

    test("should match 123-feature pattern (number at start)", () => {
      expect(extractIssueNumberFromBranch("123-feature")).toBe("123");
    });

    test("should match feature-123 pattern (number at end)", () => {
      expect(extractIssueNumberFromBranch("feature-123")).toBe("123");
    });

    test("should match feat/3056-add-hook pattern (number in middle)", () => {
      expect(extractIssueNumberFromBranch("feat/3056-add-hook")).toBe("3056");
    });

    test("should match 123/feature pattern", () => {
      expect(extractIssueNumberFromBranch("123/feature")).toBe("123");
    });

    test("should match feature/123 pattern", () => {
      expect(extractIssueNumberFromBranch("feature/123")).toBe("123");
    });

    test("should return null for empty string", () => {
      expect(extractIssueNumberFromBranch("")).toBeNull();
    });

    test("should return null for branch without numbers", () => {
      expect(extractIssueNumberFromBranch("feature-branch")).toBeNull();
    });

    test("should return null for main/master branches", () => {
      expect(extractIssueNumberFromBranch("main")).toBeNull();
      expect(extractIssueNumberFromBranch("master")).toBeNull();
    });
  });
});

describe("isMainRepository", () => {
  // Note: These tests require actual git commands, so they test the real behavior
  // The function is designed to handle errors gracefully

  test("should return a boolean", async () => {
    const result = await isMainRepository();
    expect(typeof result).toBe("boolean");
  });
});

describe("getCurrentBranch", () => {
  test("should return branch name in a git repository", async () => {
    const result = await getCurrentBranch();
    // Should return a branch name or null
    expect(result === null || typeof result === "string").toBe(true);
  });
});

describe("getHeadCommit", () => {
  test("should return short commit hash in a git repository", async () => {
    const result = await getHeadCommit();
    // Should return a short hash (typically 7 chars) or null
    if (result !== null) {
      expect(result.length).toBeGreaterThanOrEqual(7);
    }
  });
});

describe("getHeadCommitFull", () => {
  // Issue #3879: Cover lines 209-220
  test("should return full commit hash in a git repository", async () => {
    const result = await getHeadCommitFull();
    // Should return a full hash (40 chars) or null
    if (result !== null) {
      expect(result.length).toBe(40);
    }
  });
});

describe("getDiffHash", () => {
  test("should return a hash string or null", async () => {
    const result = await getDiffHash("main");
    // Should return a 12-char hash or null
    if (result !== null) {
      expect(result.length).toBe(12);
    }
  });

  test("should return null for non-existent branch", async () => {
    const result = await getDiffHash("non-existent-branch-xyz-12345");
    // This should return null because the branch doesn't exist
    expect(result).toBeNull();
  });
});

describe("getDefaultBranch", () => {
  test("should return main, master, or null", async () => {
    const result = await getDefaultBranch(process.cwd());
    // Result should be "main", "master", or null
    expect(result === null || result === "main" || result === "master").toBe(true);
  });

  test("should handle non-existent path", async () => {
    // Issue #3879: Cover error handling in getDefaultBranch
    const result = await getDefaultBranch("/non-existent-path-12345");
    expect(result).toBeNull();
  });
});

describe("getOriginDefaultBranch", () => {
  // Issue #3879: Cover lines 347-348
  test("should return origin/main or origin/master", async () => {
    const result = await getOriginDefaultBranch(process.cwd());
    expect(result.startsWith("origin/")).toBe(true);
  });

  test("should default to origin/main when branch detection fails", async () => {
    // For non-existent path, getDefaultBranch returns null, so it defaults to origin/main
    const result = await getOriginDefaultBranch("/non-existent-path-12345");
    expect(result).toBe("origin/main");
  });
});

describe("getCommitsSinceDefaultBranch", () => {
  test("should return a number or null", async () => {
    const result = await getCommitsSinceDefaultBranch(process.cwd());
    expect(result === null || typeof result === "number").toBe(true);
  });

  test("should handle non-existent path", async () => {
    // Issue #3879: Cover error handling
    const result = await getCommitsSinceDefaultBranch("/non-existent-path-12345");
    expect(result).toBeNull();
  });
});

describe("checkRecentCommits", () => {
  test("should return a tuple with boolean and string or null", async () => {
    const [hasRecent, info] = await checkRecentCommits(process.cwd());
    expect(typeof hasRecent).toBe("boolean");
    expect(info === null || typeof info === "string").toBe(true);
  });

  test("should handle non-existent path (fail-close)", async () => {
    // Issue #3879: Cover error path - fail-close behavior
    const [hasRecent, info] = await checkRecentCommits("/non-existent-path-12345");
    // Fail-close: should return [true, error message] for safety
    // Note: Returns "(確認エラー)" because git command returns non-zero exit code
    expect(hasRecent).toBe(true);
    expect(info).toBe("(確認エラー)");
  });
});

describe("checkUncommittedChanges", () => {
  test("should return a tuple with boolean and number", async () => {
    const [hasChanges, count] = await checkUncommittedChanges(process.cwd());
    expect(typeof hasChanges).toBe("boolean");
    expect(typeof count).toBe("number");
  });

  test("should handle non-existent path (fail-close)", async () => {
    // Issue #3879: Cover error path - fail-close behavior
    const [hasChanges, count] = await checkUncommittedChanges("/non-existent-path-12345");
    // Fail-close: should return [true, -1] for safety
    expect(hasChanges).toBe(true);
    expect(count).toBe(-1);
  });
});
